"""Tests for app.rl.ppo (SP-4 Phase B2)."""
from __future__ import annotations

import numpy as np
import pytest
import torch

from app.rl.obs import OBS_DIM
from app.rl.policy import N_ACTIONS, PolicyNetwork
from app.rl.ppo import (
    DEFAULT_CLIP_EPS,
    DEFAULT_LR,
    TrainConfig,
    TrainResult,
    action_to_index,
    index_to_action,
    train_ppo,
)
from app.rl.replay_buffer import ALL_ACTIONS, Transition


def _synthetic_transitions(
    n: int, *, seed: int = 7, reward_for_action: dict[int, float] | None = None,
) -> list[Transition]:
    """Build N synthetic Transitions with controllable reward signal.

    Default: reward depends on action index (action 2 = high reward).
    The trainer should learn to favour action 2.
    """
    rng = np.random.default_rng(seed)
    rewards_for = reward_for_action or {0: -1.0, 1: -0.5, 2: 1.0, 3: -0.5, 4: -1.0}
    out: list[Transition] = []
    for i in range(n):
        action_idx = int(rng.integers(0, N_ACTIONS))
        obs = rng.standard_normal(OBS_DIM).astype(np.float32) * 0.1
        out.append(Transition(
            obs=obs,
            action=ALL_ACTIONS[action_idx],
            reward=rewards_for[action_idx] + float(rng.standard_normal()) * 0.05,
            next_obs=obs.copy(),
            done=True,
            asset_id=0,
            symbol="TEST",
            opened_at_iso="2024-01-01T00:00:00+00:00",
        ))
    return out


def test_action_index_round_trip() -> None:
    for i, name in enumerate(ALL_ACTIONS):
        assert action_to_index(name) == i
        assert index_to_action(i) == name


def test_action_to_index_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown action"):
        action_to_index("INVALID")


def test_index_to_action_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="out of range"):
        index_to_action(99)


def test_train_config_defaults_match_spec() -> None:
    c = TrainConfig()
    assert c.lr == DEFAULT_LR
    assert c.clip_eps == DEFAULT_CLIP_EPS
    assert c.epochs == 30
    assert c.value_coef == 0.5
    assert c.entropy_coef == 0.01


def test_train_one_epoch_runs_without_crash() -> None:
    """Smoke: trainer completes 1 epoch on small batch and updates weights."""
    torch.manual_seed(0)
    policy = PolicyNetwork()
    initial_w = policy.policy_head.weight.detach().clone()

    transitions = _synthetic_transitions(64)
    cfg = TrainConfig(epochs=1, batch_size=32, ppo_epochs_per_batch=2)
    result = train_ppo(policy=policy, transitions=transitions, config=cfg)

    assert isinstance(result, TrainResult)
    assert result.epochs_completed == 1
    assert len(result.history) == 1
    final_w = policy.policy_head.weight.detach()
    # SOMETHING should have changed (weights updated).
    assert not torch.equal(initial_w, final_w)


def test_train_returns_state_dict_snapshot() -> None:
    policy = PolicyNetwork()
    transitions = _synthetic_transitions(64)
    cfg = TrainConfig(epochs=1, batch_size=32, ppo_epochs_per_batch=1)
    result = train_ppo(policy=policy, transitions=transitions, config=cfg)
    assert result.final_state_dict is not None
    assert "policy_head.weight" in result.final_state_dict


def test_empty_transitions_raises() -> None:
    policy = PolicyNetwork()
    with pytest.raises(ValueError, match="empty transitions"):
        train_ppo(policy=policy, transitions=[], config=TrainConfig(epochs=1))


def test_history_records_loss_per_epoch() -> None:
    policy = PolicyNetwork()
    transitions = _synthetic_transitions(64)
    cfg = TrainConfig(epochs=3, batch_size=16, ppo_epochs_per_batch=1)
    result = train_ppo(policy=policy, transitions=transitions, config=cfg)
    assert [s.epoch for s in result.history] == [1, 2, 3]
    for s in result.history:
        assert isinstance(s.policy_loss, float)
        assert isinstance(s.value_loss, float)
        assert isinstance(s.entropy, float)


def test_trainer_learns_to_favour_high_reward_action() -> None:
    """End-to-end smoke: with strong, deterministic rewards, the policy
    learns to assign higher probability to the high-reward action.

    Action 2 has reward +1.0; all others have -0.5 to -1.0. After
    training, the policy should put more probability mass on action 2
    than on a random baseline (1/5 = 0.2) when given an average obs.
    """
    torch.manual_seed(123)
    np.random.seed(123)

    policy = PolicyNetwork()
    transitions = _synthetic_transitions(
        512,
        reward_for_action={0: -1.0, 1: -1.0, 2: 2.0, 3: -1.0, 4: -1.0},
    )
    cfg = TrainConfig(epochs=8, batch_size=64, ppo_epochs_per_batch=4, seed=7)
    train_ppo(policy=policy, transitions=transitions, config=cfg)

    policy.eval()
    avg_obs = torch.from_numpy(
        np.mean([t.obs for t in transitions], axis=0)
    ).float().unsqueeze(0)
    with torch.no_grad():
        out = policy(avg_obs)
        probs = torch.softmax(out.logits, dim=-1).squeeze(0)
    # Action 2 should be the most likely action after training.
    best = int(probs.argmax().item())
    assert best == 2, (
        f"policy didn't learn to prefer action 2; argmax={best}, probs={probs.tolist()}"
    )
    # And materially above the uniform baseline (0.2). 8-epoch synthetic run
    # typically reaches 0.27-0.32; 0.25 leaves headroom for stochastic variance
    # while still proving the policy learned (>25% above uniform).
    assert float(probs[2]) > 0.25


def test_trainer_respects_seed_for_reproducibility() -> None:
    """Same seed + same data → same final weights."""
    transitions = _synthetic_transitions(128, seed=11)
    cfg = TrainConfig(epochs=2, batch_size=32, ppo_epochs_per_batch=2, seed=99)

    torch.manual_seed(99)
    p1 = PolicyNetwork()
    train_ppo(policy=p1, transitions=transitions, config=cfg)

    torch.manual_seed(99)
    p2 = PolicyNetwork()
    train_ppo(policy=p2, transitions=transitions, config=cfg)

    for k in p1.state_dict():
        assert torch.allclose(
            p1.state_dict()[k], p2.state_dict()[k], atol=1e-6,
        ), f"mismatch on {k}"


def test_entropy_warmup_uses_higher_coef_first_n_epochs() -> None:
    """Sanity check: with WARMUP_EPOCHS >= EPOCHS, entropy stays high."""
    transitions = _synthetic_transitions(64)
    policy = PolicyNetwork()
    cfg = TrainConfig(
        epochs=2, batch_size=16, ppo_epochs_per_batch=1,
        entropy_warmup_epochs=99,  # never exit warmup
        entropy_warmup_coef=0.5,   # huge entropy bonus
        entropy_coef=0.0,
    )
    result = train_ppo(policy=policy, transitions=transitions, config=cfg)
    # With a 0.5 entropy coef, the log uniform-distribution entropy is
    # log(5) ≈ 1.6. Entropy should stay > 1.0.
    for stat in result.history:
        assert stat.entropy > 1.0
