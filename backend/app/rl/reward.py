"""Per-trade risk-adjusted reward for the SP-4 PPO policy (L10).

Spec sec 4 — reward = clip(realized_R − 0.5 × σ_R, −3, +3), where:

* ``realized_R`` is the trade's P&L expressed in R-multiples
  (``pnl_quote / initial_risk_quote``)
* ``σ_R`` is the std-dev of R-multiples on this asset's last
  ``RECENT_TRADES_WINDOW`` closed trades (with a prior of 1.0 until
  ``MIN_TRADES_FOR_SIGMA`` history accumulates)

Trade-level (not bar-level) credit assignment — trade close is the natural
episode boundary for this swing-trading horizon. Per-asset σ normalises
across the universe so a +2R BTC trade and a +2R SOL trade contribute
the same to the brain's learning signal.
"""
from __future__ import annotations

from typing import Protocol, Sequence

import numpy as np


# Trailing window over which σ_R is estimated. 20 = ~1-2 weeks of trades
# at the platform's typical 1-2 trades/day cadence per asset.
RECENT_TRADES_WINDOW: int = 20

# Risk-aversion coefficient on the σ penalty term. Tunable; locked at 0.5
# for v1 per spec sec 4. Higher = brain more cautious.
RISK_AVERSION_WEIGHT: float = 0.5

# Used until enough trades accumulate to estimate σ from data.
PRIOR_SIGMA: float = 1.0

# Symmetric clip on the reward to keep PPO advantages numerically stable.
REWARD_CLIP: float = 3.0

# Minimum trades on this asset before we trust the empirical σ.
MIN_TRADES_FOR_SIGMA: int = 5


class _TradeLike(Protocol):
    """Anything with ``pnl_quote`` (signed) + ``initial_risk_quote`` (>0)."""
    pnl_quote: float
    initial_risk_quote: float


def compute_reward(trade: _TradeLike, *, recent: Sequence[_TradeLike]) -> float:
    """Compute risk-adjusted reward in R-multiples for a closed trade.

    ``recent`` is the trailing list of CLOSED trades on the same asset,
    oldest first; only the last ``RECENT_TRADES_WINDOW`` are used.
    Trades with ``initial_risk_quote == 0`` in ``recent`` are skipped
    (defensive — they shouldn't reach the buffer but if they do they
    don't break σ estimation).
    """
    if trade.initial_risk_quote <= 0:
        raise ValueError(
            f"initial_risk_quote must be > 0, got {trade.initial_risk_quote}"
        )
    realized_R = trade.pnl_quote / trade.initial_risk_quote

    if len(recent) >= MIN_TRADES_FOR_SIGMA:
        slice_ = list(recent)[-RECENT_TRADES_WINDOW:]
        Rs = np.array([
            t.pnl_quote / t.initial_risk_quote
            for t in slice_
            if t.initial_risk_quote > 0
        ], dtype=np.float64)
        sigma = float(np.std(Rs)) if Rs.size > 0 else PRIOR_SIGMA
    else:
        sigma = PRIOR_SIGMA

    raw = realized_R - RISK_AVERSION_WEIGHT * sigma
    return float(np.clip(raw, -REWARD_CLIP, REWARD_CLIP))


__all__ = [
    "RECENT_TRADES_WINDOW",
    "RISK_AVERSION_WEIGHT",
    "PRIOR_SIGMA",
    "REWARD_CLIP",
    "MIN_TRADES_FOR_SIGMA",
    "compute_reward",
]
