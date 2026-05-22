"""MVP holdout backtest for the SP-4 PPO brain (Phase B5).

Runs as part of ``tools/ml/train_brain.py``'s training loop. Splits the
replay buffer chronologically (last 20% as held-out), runs the trained
policy on the holdout in deterministic mode, and emits Sharpe / max_dd /
win_rate / total_trades on the synthetic-reward series

    synthetic_reward[i] = transitions[i].reward * brain_adjust[i]

where ``brain_adjust`` comes from :func:`app.rl.inference.action_to_brain_adjust`
applied to the policy's chosen action vs the trade's recorded direction.

Why this MVP over a full OOS replay:
* Free — the buffer + policy are already in memory at train time
* Honest — held-out window is chronologically AFTER training data
* Implementable in ~150 LoC, unlocks the champion-challenger Sharpe gate

Known limitations (documented for follow-ups):
* Brain reward is already a clipped risk-adjusted R-multiple, so Sharpe of
  reward is a SCALED proxy of PnL Sharpe — not raw $ returns
* Small holdouts (a few dozen trades) give noisy Sharpe — fine for the
  bootstrap "any model beats no model" gate, NOT for fine-grained
  promotion decisions
* The synthetic-reward formula assumes brain_adjust scales reward directly;
  a real deployment also affects position sizing, signal admission, etc.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import torch

from app.rl.inference import action_to_brain_adjust
from app.rl.policy import PolicyNetwork
from app.rl.replay_buffer import ALL_ACTIONS, Transition


# Floor below which the holdout is too small for a stable Sharpe.
MIN_HOLDOUT_TRADES: int = 20

# Avoids divide-by-zero on degenerate (zero-variance) reward series.
SHARPE_EPS: float = 1e-8


@dataclass(frozen=True)
class BacktestResult:
    """Backtest output the trainer persists into ``rl_checkpoints.eval_results``."""

    sharpe: float
    max_dd: float
    win_rate: float
    total_trades: int
    window_start: str
    window_end: str

    def as_eval_dict(self) -> dict:
        """Serialise for JSON storage in ``eval_results`` JSONB column."""
        return {
            "sharpe": self.sharpe,
            "max_dd": self.max_dd,
            "win_rate": self.win_rate,
            "total_trades": self.total_trades,
            "window_start": self.window_start,
            "window_end": self.window_end,
        }


def _infer_proposed_direction(action: str) -> str:
    """Map a trade's recorded action to a Direction.

    The trade's recorded action is what the RULE-BASED system chose
    (LONG_FULL or SHORT_FULL — Half is not yet emitted by the rule
    system, per replay_buffer._direction_to_action).
    """
    if action.startswith("LONG_"):
        return "LONG"
    if action.startswith("SHORT_"):
        return "SHORT"
    return "NEUTRAL"


def _compute_sharpe(rewards: np.ndarray) -> float:
    """Annualised-ish Sharpe on a reward series.

    Note this is NOT calendar-annualised — we multiply by sqrt(N) since
    N reflects the holdout size, not literal trading days. The result
    is a relative score, comparable across checkpoints with the SAME
    holdout window size.
    """
    if rewards.size == 0:
        return 0.0
    mean = float(rewards.mean())
    std = float(rewards.std())
    return (mean / (std + SHARPE_EPS)) * float(np.sqrt(rewards.size))


def _compute_max_drawdown(rewards: np.ndarray) -> float:
    """Max peak-to-trough drawdown on the CUMULATIVE reward series."""
    if rewards.size == 0:
        return 0.0
    cumulative = np.cumsum(rewards)
    running_peak = np.maximum.accumulate(cumulative)
    drawdown = running_peak - cumulative
    return float(drawdown.max())


def evaluate_brain_on_holdout(
    *,
    policy: PolicyNetwork,
    transitions: Sequence[Transition],
    holdout_fraction: float = 0.2,
    device: torch.device | None = None,
) -> BacktestResult | dict:
    """Run the trained policy on the last ``holdout_fraction`` of transitions.

    Returns a :class:`BacktestResult` when the holdout has at least
    :data:`MIN_HOLDOUT_TRADES` rows; otherwise returns
    ``{"_status": "insufficient_data", ...}`` so the caller can surface
    the gap in the eval JSON without crashing the cron.

    The transitions list MUST be chronologically ordered (oldest first)
    so the trailing slice is genuinely out-of-sample. This matches the
    order ``replay_buffer.load_from_shadow_trades`` produces (SQL
    ``ORDER BY t.symbol, t.opened_at``; per-symbol it's chronological,
    and the cross-symbol blend is acceptable for an MVP).
    """
    n = len(transitions)
    holdout_start = int(n * (1.0 - holdout_fraction))
    holdout = list(transitions[holdout_start:])
    if len(holdout) < MIN_HOLDOUT_TRADES:
        return {
            "_status": "insufficient_data",
            "min_required": MIN_HOLDOUT_TRADES,
            "had": len(holdout),
            "total_transitions": n,
        }

    dev = device or torch.device("cpu")
    policy = policy.to(dev)
    policy.eval()

    synthetic_rewards: list[float] = []
    with torch.no_grad():
        for tr in holdout:
            obs_t = torch.from_numpy(tr.obs).unsqueeze(0).to(dev)
            action_t, _log_prob, _value = policy.act(obs_t, deterministic=True)
            action_idx = int(action_t.squeeze(0).item())
            if not 0 <= action_idx < len(ALL_ACTIONS):
                continue
            action_str = ALL_ACTIONS[action_idx]
            proposed = _infer_proposed_direction(tr.action)
            brain_adjust = action_to_brain_adjust(
                action_str, proposed_direction=proposed,
            )
            synthetic_rewards.append(float(tr.reward) * float(brain_adjust))

    rewards = np.asarray(synthetic_rewards, dtype=np.float64)
    return BacktestResult(
        sharpe=_compute_sharpe(rewards),
        max_dd=_compute_max_drawdown(rewards),
        win_rate=float((rewards > 0).mean()) if rewards.size else 0.0,
        total_trades=int(rewards.size),
        window_start=str(holdout[0].opened_at_iso),
        window_end=str(holdout[-1].opened_at_iso),
    )


__all__ = [
    "BacktestResult",
    "MIN_HOLDOUT_TRADES",
    "evaluate_brain_on_holdout",
]
