"""Per-direction online calibration from prediction_validations.

Background — the ConvLSTM + 9-layer ensemble has a structural LONG bias
(verified empirically: over 95 historical predictions p99 = +0.353,
p05 = -0.074; 0/8 SHORT predictions ever cleared a -0.50 entry threshold).
PR #121 makes the entry thresholds and aggregator math symmetric, but
the model itself still rarely produces strongly-negative scores because
its weights were trained on bull-skewed crypto data.

This module is a meta-learner above the aggregator. It looks at the
realized outcomes recorded in ``prediction_validations`` (the ledger
added in PR #109) and adjusts the aggregator's per-direction multiplier
so that:

- When SHORT predictions historically WIN more often than LONG, SHORT
  scores get amplified (multiplier > 1.0) so more of them cross the
  entry gate.
- When SHORT predictions historically LOSE more often than LONG, SHORT
  scores get dampened (multiplier < 1.0) so fewer fire.
- Same for LONG.

The multiplier is **capped to a configurable band** (default 0.7–1.3)
so the calibration cannot run away from a few lucky/unlucky trades.

This is online supervised calibration, NOT model retraining. It changes
the aggregator's score-output magnitude per direction; it does NOT
change the ConvLSTM weights or any layer's internal logic. To actually
retrain the model on SHORT data, see backend/scripts/train_brain.py.

Design contract:
  - PURE FUNCTION compute_direction_multipliers — takes a list of
    (direction, was_correct, pnl_pct) tuples, returns a multiplier dict.
    Easy to unit-test in isolation, no DB.
  - ASYNC helper load_recent_validations() does the DB query and feeds
    compute_direction_multipliers. Caller injects a session_factory.
  - DEFAULT to multiplier=1.0 when the sample size is below MIN_N
    (default 20) — too noisy to trust.
  - The aggregator's existing signature is unchanged; the new
    ``direction_calibration`` kwarg is keyword-only and defaults to None
    (= no calibration, identical to prior behaviour). Backward
    compatible by design.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


log = logging.getLogger(__name__)

# Minimum validated rows per direction before we trust the empirical
# performance enough to apply a non-1.0 multiplier. Below this, the
# multiplier stays at 1.0 (no-op).
MIN_SAMPLE_PER_DIRECTION: int = 20

# Hard cap on how far the multiplier can drift from 1.0. Prevents a
# string of lucky / unlucky trades from runaway-amplifying one side.
# 0.7 / 1.3 means at most ±30% adjustment.
MULTIPLIER_LOWER_BOUND: float = 0.7
MULTIPLIER_UPPER_BOUND: float = 1.3

# How far back to look. 50 validated trades is roughly 1-3 weeks
# at the bot's current 1h cadence; long enough to be stable, short
# enough to react to regime shifts.
DEFAULT_LOOKBACK_N: int = 50


@dataclass(frozen=True)
class ValidationSample:
    """One validated prediction outcome — the input format for calibration.

    direction is the predicted side ('LONG' / 'SHORT' / 'NEUTRAL').
    was_correct is True when the realized move agreed with the prediction.
    pnl_pct is the realized move in percent (positive = price moved in
    the predicted direction; for SHORT predictions the sign is flipped
    so positive = predicted correctly).
    """
    direction: str
    was_correct: bool
    pnl_pct: float


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def compute_direction_multipliers(
    samples: Iterable[ValidationSample],
    *,
    min_n: int = MIN_SAMPLE_PER_DIRECTION,
    lower: float = MULTIPLIER_LOWER_BOUND,
    upper: float = MULTIPLIER_UPPER_BOUND,
) -> dict[str, float]:
    """Compute per-direction aggregator multipliers from validated outcomes.

    Pure function — no DB, no side effects. Easy to test.

    Returns a dict keyed by direction string. Always returns 1.0 for
    NEUTRAL (no multiplier — neutral predictions never trigger trades).
    Returns 1.0 for any direction with fewer than ``min_n`` samples.

    Otherwise, the multiplier is a linear function of the average
    realized pnl_pct on that side:

        multiplier = 1.0 + tanh(avg_pnl_pct / 5.0) * 0.3

    The tanh keeps the multiplier bounded near ±0.3 for typical
    average-pnl values (a +5% avg lifts to +0.3 * tanh(1.0) ≈ +0.23;
    a -2% avg drops to -0.3 * tanh(0.4) ≈ -0.11). Capped by the
    lower/upper bound just in case.

    Rationale: avg_pnl_pct is a directly-useful signal — when SHORT
    predictions have averaged +1% realized correct-side move, the model
    is being too conservative on shorts and we should amplify by ~6%.
    When LONG predictions are averaging -0.5%, we should dampen by ~3%.
    """
    by_dir: dict[str, list[float]] = {}
    for s in samples:
        by_dir.setdefault(s.direction, []).append(s.pnl_pct)

    out: dict[str, float] = {"LONG": 1.0, "SHORT": 1.0, "NEUTRAL": 1.0}
    for direction, pnls in by_dir.items():
        if direction == "NEUTRAL":
            continue
        if len(pnls) < min_n:
            continue
        avg_pnl = sum(pnls) / len(pnls)
        # tanh-soft adjustment: ~±0.3 range for typical avg_pnl ±5%.
        raw_adj = math.tanh(avg_pnl / 5.0) * 0.3
        out[direction] = _clip(1.0 + raw_adj, lower, upper)
    return out


async def load_recent_validations(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    lookback_n: int = DEFAULT_LOOKBACK_N,
) -> list[ValidationSample]:
    """Pull the most recent N validated rows per direction.

    Best-effort: any DB error returns an empty list (which causes
    ``compute_direction_multipliers`` to return all-1.0 multipliers,
    i.e. the calibration falls back to a no-op cleanly).
    """
    samples: list[ValidationSample] = []
    try:
        async with session_factory() as session:
            for direction in ("LONG", "SHORT", "NEUTRAL"):
                rows = (
                    await session.execute(
                        sa.text(
                            "SELECT direction, was_correct, pnl_pct "
                            "FROM prediction_validations "
                            "WHERE validated_at IS NOT NULL AND direction = :d "
                            "ORDER BY validated_at DESC LIMIT :n"
                        ),
                        {"d": direction, "n": lookback_n},
                    )
                ).all()
                for r in rows:
                    samples.append(
                        ValidationSample(
                            direction=str(r.direction),
                            was_correct=bool(r.was_correct),
                            pnl_pct=float(r.pnl_pct) if r.pnl_pct is not None else 0.0,
                        )
                    )
    except Exception as e:  # noqa: BLE001 — calibration is best-effort
        log.warning("load_recent_validations failed: %s", e)
        return []
    return samples


async def compute_calibration_for_predictor(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    lookback_n: int = DEFAULT_LOOKBACK_N,
    min_n: int = MIN_SAMPLE_PER_DIRECTION,
) -> dict[str, float]:
    """Convenience wrapper: load + compute in one call.

    The predictor calls this once per tick (cheap — single query against
    a small indexed table) and passes the result to ``aggregate(...)``
    as ``direction_calibration``.

    Returns {'LONG': 1.0, 'SHORT': 1.0, 'NEUTRAL': 1.0} if no validated
    rows exist or the DB call fails — i.e. calibration is fully
    transparent until the validator has accumulated enough data.
    """
    samples = await load_recent_validations(
        session_factory, lookback_n=lookback_n,
    )
    return compute_direction_multipliers(samples, min_n=min_n)


__all__ = [
    "MIN_SAMPLE_PER_DIRECTION",
    "MULTIPLIER_LOWER_BOUND",
    "MULTIPLIER_UPPER_BOUND",
    "DEFAULT_LOOKBACK_N",
    "ValidationSample",
    "compute_direction_multipliers",
    "load_recent_validations",
    "compute_calibration_for_predictor",
]
