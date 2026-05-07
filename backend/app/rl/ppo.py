"""SP-4 Phase B2 — PPO-clip trainer.

Implements the PPO-clip variant of Schulman et al 2017 against the offline
replay buffer of trade transitions from app.rl.replay_buffer. The trainer
treats each closed trade as a single-step episode (done=True) with the
risk-adjusted reward from app.rl.reward — so advantage = reward - V(s).

This is intentionally simpler than full multi-step PPO. Trade-level credit
assignment (one transition = one trade) means we don't need GAE — there's
no future bootstrap to compute. The advantage is just (reward - value).

Spec sec 5.2 — 4 epochs per batch, ε=0.2 clip, c1=0.5 value loss, c2=0.01
entropy bonus (5x during first 10 epochs to prevent FLAT-collapse). Adam
lr=3e-4, batch=256, grad clip 0.5.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import torch
from torch import nn

from app.rl.policy import N_ACTIONS, PolicyNetwork
from app.rl.replay_buffer import ALL_ACTIONS, Transition


log = logging.getLogger(__name__)


# Spec sec 5.2 — PPO hyperparameters.
DEFAULT_LR: float = 3e-4
DEFAULT_BATCH_SIZE: int = 256
DEFAULT_EPOCHS: int = 30
DEFAULT_PPO_EPOCHS_PER_BATCH: int = 4
DEFAULT_CLIP_EPS: float = 0.2
DEFAULT_VALUE_COEF: float = 0.5
DEFAULT_ENTROPY_COEF: float = 0.01
DEFAULT_ENTROPY_WARMUP_COEF: float = 0.05  # 5x the steady-state value
DEFAULT_ENTROPY_WARMUP_EPOCHS: int = 10
DEFAULT_GRAD_CLIP: float = 0.5
DEFAULT_ADVANTAGE_NORM_EPS: float = 1e-8


def action_to_index(action: str) -> int:
    if action not in ALL_ACTIONS:
        raise ValueError(f"unknown action {action!r}; expected one of {ALL_ACTIONS}")
    return ALL_ACTIONS.index(action)


def index_to_action(index: int) -> str:
    if not 0 <= index < N_ACTIONS:
        raise ValueError(f"action index {index} out of range [0, {N_ACTIONS})")
    return ALL_ACTIONS[index]


@dataclass(frozen=True)
class TrainConfig:
    """Hyperparameters for a single PPO training run.

    All defaults match spec sec 5.2; override via keyword for ablation.
    """

    lr: float = DEFAULT_LR
    batch_size: int = DEFAULT_BATCH_SIZE
    epochs: int = DEFAULT_EPOCHS
    ppo_epochs_per_batch: int = DEFAULT_PPO_EPOCHS_PER_BATCH
    clip_eps: float = DEFAULT_CLIP_EPS
    value_coef: float = DEFAULT_VALUE_COEF
    entropy_coef: float = DEFAULT_ENTROPY_COEF
    entropy_warmup_coef: float = DEFAULT_ENTROPY_WARMUP_COEF
    entropy_warmup_epochs: int = DEFAULT_ENTROPY_WARMUP_EPOCHS
    grad_clip: float = DEFAULT_GRAD_CLIP
    seed: int = 42


@dataclass(frozen=True)
class EpochStats:
    epoch: int
    policy_loss: float
    value_loss: float
    entropy: float
    total_loss: float


@dataclass
class TrainResult:
    epochs_completed: int = 0
    history: list[EpochStats] = field(default_factory=list)
    final_state_dict: dict | None = None


def _stack_transitions(
    transitions: Sequence[Transition],
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Stack a list of Transitions into batched torch tensors.

    Returns (obs, actions, rewards) on CPU; caller moves to device.
    Actions are mapped from string to integer index here once so the
    inner training loop doesn't repeat the lookup.
    """
    if not transitions:
        raise ValueError("empty transitions list — nothing to train on")
    obs = torch.from_numpy(np.stack([t.obs for t in transitions])).float()
    actions = torch.tensor(
        [action_to_index(t.action) for t in transitions], dtype=torch.long,
    )
    rewards = torch.tensor([t.reward for t in transitions], dtype=torch.float32)
    return obs, actions, rewards


def train_ppo(
    *,
    policy: PolicyNetwork,
    transitions: Sequence[Transition],
    config: TrainConfig | None = None,
    device: torch.device | None = None,
) -> TrainResult:
    """Train ``policy`` IN-PLACE on the supplied transitions.

    Returns a :class:`TrainResult` with per-epoch stats + final state_dict
    snapshot. The policy weights are also updated on the input object.
    """
    cfg = config or TrainConfig()
    dev = device or torch.device("cpu")
    torch.manual_seed(cfg.seed)
    np.random.seed(cfg.seed)

    obs, actions, rewards = _stack_transitions(transitions)
    obs = obs.to(dev)
    actions = actions.to(dev)
    rewards = rewards.to(dev)
    n = obs.shape[0]
    log.info(
        "PPO training: n_transitions=%d batch=%d epochs=%d device=%s",
        n, cfg.batch_size, cfg.epochs, dev,
    )

    policy = policy.to(dev)
    opt = torch.optim.Adam(policy.parameters(), lr=cfg.lr)

    # ---- Compute the OLD log-probs + values BEFORE training begins. ----
    # PPO needs π_θ_old (the policy at rollout time) for the importance ratio.
    # Since we're doing an offline batch update on existing trades, we treat
    # the policy at function-entry as π_θ_old.
    policy.eval()
    with torch.no_grad():
        old_log_probs, _, old_values = policy.evaluate_actions(obs, actions)
    policy.train()

    # Advantages on this single-step setup: A = R - V(s) (no GAE needed —
    # every transition is a one-step episode).
    advantages = rewards - old_values
    # Standard PPO normalisation across the buffer to stabilise gradient scale.
    adv_mean = advantages.mean()
    adv_std = advantages.std()
    if float(adv_std) > cfg.eps_safe():
        advantages = (advantages - adv_mean) / (adv_std + DEFAULT_ADVANTAGE_NORM_EPS)
    returns = rewards  # episodic reward = return for single-step episodes

    history: list[EpochStats] = []
    for epoch in range(1, cfg.epochs + 1):
        # Warmup: 5x entropy bonus during first ENTROPY_WARMUP_EPOCHS epochs.
        if epoch <= cfg.entropy_warmup_epochs:
            entropy_coef = cfg.entropy_warmup_coef
        else:
            entropy_coef = cfg.entropy_coef

        # Shuffle once per epoch
        perm = torch.randperm(n, device=dev)
        epoch_pol_losses: list[float] = []
        epoch_val_losses: list[float] = []
        epoch_entropies: list[float] = []

        # Inner loop: cfg.ppo_epochs_per_batch passes over each minibatch
        for _inner in range(cfg.ppo_epochs_per_batch):
            for start in range(0, n, cfg.batch_size):
                idx = perm[start : start + cfg.batch_size]
                if len(idx) < 2:
                    continue
                b_obs = obs[idx]
                b_actions = actions[idx]
                b_advantages = advantages[idx]
                b_returns = returns[idx]
                b_old_log_probs = old_log_probs[idx]

                new_log_probs, entropy, new_values = policy.evaluate_actions(
                    b_obs, b_actions,
                )
                ratio = torch.exp(new_log_probs - b_old_log_probs)
                clip_low = 1.0 - cfg.clip_eps
                clip_high = 1.0 + cfg.clip_eps
                surrogate1 = ratio * b_advantages
                surrogate2 = torch.clamp(ratio, clip_low, clip_high) * b_advantages
                policy_loss = -torch.min(surrogate1, surrogate2).mean()

                value_loss = (new_values - b_returns).pow(2).mean()
                entropy_loss = -entropy.mean()  # negative because we MAXIMIZE entropy

                total_loss = (
                    policy_loss
                    + cfg.value_coef * value_loss
                    + entropy_coef * entropy_loss
                )

                opt.zero_grad()
                total_loss.backward()
                nn.utils.clip_grad_norm_(policy.parameters(), max_norm=cfg.grad_clip)
                opt.step()

                epoch_pol_losses.append(float(policy_loss.item()))
                epoch_val_losses.append(float(value_loss.item()))
                epoch_entropies.append(float(entropy.mean().item()))

        epoch_stat = EpochStats(
            epoch=epoch,
            policy_loss=float(np.mean(epoch_pol_losses)),
            value_loss=float(np.mean(epoch_val_losses)),
            entropy=float(np.mean(epoch_entropies)),
            total_loss=float(
                np.mean(epoch_pol_losses)
                + cfg.value_coef * np.mean(epoch_val_losses)
                - entropy_coef * np.mean(epoch_entropies)
            ),
        )
        history.append(epoch_stat)
        if epoch == 1 or epoch % 5 == 0 or epoch == cfg.epochs:
            log.info(
                "epoch %02d  pol_loss=%+.4f  val_loss=%.4f  entropy=%.3f",
                epoch, epoch_stat.policy_loss,
                epoch_stat.value_loss, epoch_stat.entropy,
            )

    return TrainResult(
        epochs_completed=cfg.epochs,
        history=history,
        final_state_dict={
            k: v.detach().cpu().clone() for k, v in policy.state_dict().items()
        },
    )


# Tiny helper kept on the dataclass to side-step Python's "method on frozen
# dataclass" idiom complaints in older versions.
def _eps_safe(self: TrainConfig) -> float:
    return DEFAULT_ADVANTAGE_NORM_EPS

TrainConfig.eps_safe = _eps_safe  # type: ignore[attr-defined]


__all__ = [
    "DEFAULT_LR",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_EPOCHS",
    "DEFAULT_CLIP_EPS",
    "DEFAULT_ENTROPY_COEF",
    "EpochStats",
    "TrainConfig",
    "TrainResult",
    "action_to_index",
    "index_to_action",
    "train_ppo",
]
