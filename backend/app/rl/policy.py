"""SP-4 Phase B1 — PPO PolicyNetwork.

Spec sec 3.3 — small actor-critic MLP that consumes the 53-dim observation
vector and outputs:

* policy logits over the 5 discrete actions (LONG_FULL, LONG_HALF, FLAT,
  SHORT_HALF, SHORT_FULL)
* a value-function estimate V(s) used by PPO's advantage calculation

Architecture:

    Linear(53 → 128) + ReLU
    Linear(128 → 128) + ReLU
    Dropout(0.1)
    ├── policy_head: Linear(128 → 5)        — action logits
    └── value_head:  Linear(128 → 1)        — V(s)

~50K params total. Fits comfortably in any GPU; trains in ~30-90 min on
Colab T4 / 1-2 hours on Hetzner 4 vCPU CPU.

Per-asset specialization comes from the AssetEmbeddingTable
(``app.rl.adapter``) — the trainer concatenates the asset embedding
into the observation BEFORE this network sees it. The policy itself is
asset-agnostic; the embedding does the per-asset routing.
"""
from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from app.rl.obs import OBS_DIM


N_ACTIONS: int = 5
HIDDEN_DIM: int = 128
DROPOUT: float = 0.1


@dataclass(frozen=True)
class PolicyOutput:
    """One forward pass result."""

    logits: torch.Tensor   # (batch, 5)
    value: torch.Tensor    # (batch,)


class PolicyNetwork(nn.Module):
    """Actor-critic MLP for the SP-4 PPO brain.

    Input:  ``obs`` of shape (batch, OBS_DIM).
    Output: :class:`PolicyOutput` with logits (batch, 5) + value (batch,).
    """

    def __init__(
        self,
        obs_dim: int = OBS_DIM,
        n_actions: int = N_ACTIONS,
        hidden_dim: int = HIDDEN_DIM,
        dropout: float = DROPOUT,
    ) -> None:
        super().__init__()
        self.obs_dim = obs_dim
        self.n_actions = n_actions
        self.shared = nn.Sequential(
            nn.Linear(obs_dim, hidden_dim),
            nn.ReLU(inplace=True),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
        )
        self.policy_head = nn.Linear(hidden_dim, n_actions)
        self.value_head = nn.Linear(hidden_dim, 1)
        self._init_weights()

    def _init_weights(self) -> None:
        """Orthogonal init with small policy-head gain — standard PPO recipe."""
        for module in self.shared:
            if isinstance(module, nn.Linear):
                nn.init.orthogonal_(module.weight, gain=2 ** 0.5)
                nn.init.zeros_(module.bias)
        # Policy head: small gain so initial action distribution is near-uniform
        # (entropy maximised), letting the entropy bonus do its job during
        # warm-up without runaway specialization.
        nn.init.orthogonal_(self.policy_head.weight, gain=0.01)
        nn.init.zeros_(self.policy_head.bias)
        nn.init.orthogonal_(self.value_head.weight, gain=1.0)
        nn.init.zeros_(self.value_head.bias)

    def forward(self, obs: torch.Tensor) -> PolicyOutput:
        if obs.dim() != 2 or obs.shape[1] != self.obs_dim:
            raise ValueError(
                f"expected obs shape (batch, {self.obs_dim}), got {tuple(obs.shape)}"
            )
        h = self.shared(obs)
        logits = self.policy_head(h)
        value = self.value_head(h).squeeze(-1)
        return PolicyOutput(logits=logits, value=value)

    def act(
        self, obs: torch.Tensor, *, deterministic: bool = False,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Sample an action. Returns (action, log_prob, value).

        ``deterministic=True`` selects the argmax action (used in
        production inference); ``False`` samples from the categorical
        distribution (used during PPO rollouts to maintain exploration).
        """
        out = self.forward(obs)
        dist = torch.distributions.Categorical(logits=out.logits)
        if deterministic:
            action = out.logits.argmax(dim=-1)
        else:
            action = dist.sample()
        log_prob = dist.log_prob(action)
        return action, log_prob, out.value

    def evaluate_actions(
        self, obs: torch.Tensor, actions: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Score given (obs, actions) — used by PPO for the policy loss.

        Returns (log_probs, entropy, values).
        """
        out = self.forward(obs)
        dist = torch.distributions.Categorical(logits=out.logits)
        log_probs = dist.log_prob(actions)
        entropy = dist.entropy()
        return log_probs, entropy, out.value


def count_params(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


__all__ = [
    "DROPOUT",
    "HIDDEN_DIM",
    "N_ACTIONS",
    "PolicyNetwork",
    "PolicyOutput",
    "count_params",
]
