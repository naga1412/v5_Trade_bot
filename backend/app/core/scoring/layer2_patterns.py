"""Layer-2 pattern scoring aggregator (SP-2 spec §3.3).

Iterates ``ALL_PATTERNS`` at a given bar index, weights each fire by
``strength * confidence * historical_accuracy``, separates by direction,
tanh-squashes the long-minus-short raw into ``[-1, 1]``, and emits a
``LayerScore``.

Historical accuracy is loaded from the ``pattern_stats`` table once per
``(symbol, timeframe)`` at worker startup (see Phase E task E4) and refreshed
after the SP-1 nightly job.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.patterns import ALL_PATTERNS
from app.core.patterns.base import PatternFire
from app.core.scoring.types import Direction, LayerScore

# Built once at import time — avoids per-call type checks on the hot path.
# Pattern.pattern_type is "chart" | "candle" per base.PatternType.
_CHART_PATTERN_IDS: frozenset[str] = frozenset(
    p.pattern_id for p in ALL_PATTERNS if p.pattern_type == "chart"
)

PRIOR_ACCURACY: float = 0.5
"""Default accuracy when pattern_stats has no warm row for a pattern."""

COLD_START_THRESHOLD: int = 50
"""spec §2 decision 6 — fewer samples than this are too noisy to trust."""

TANH_DIVISOR: float = 3.0
"""spec §3.3 — squashing scale; tunable risk fallback in §10."""

NEUTRAL_BAND: float = 0.05
"""|squashed| < 0.05 → NEUTRAL; spec §3.3."""

NOTES_MAX_CHARS: int = 500
"""spec §12 Q4 — keep ``LayerScore.notes`` short for JSONB storage."""


@dataclass(frozen=True)
class PatternStatsLookup:
    """Bulk-loaded accuracies for one ``(symbol, timeframe)``.

    Keeps the L2 aggregator out of the DB on the per-bar path.
    """
    by_pattern: dict[str, float]

    def get(self, pattern_id: str) -> float:
        """Return historical accuracy for ``pattern_id``, or ``PRIOR_ACCURACY``."""
        return self.by_pattern.get(pattern_id, PRIOR_ACCURACY)


async def load_pattern_stats(
    session: AsyncSession, *, symbol: str, timeframe: str,
) -> PatternStatsLookup:
    """Read all rows for ``(symbol, timeframe)`` from ``pattern_stats``.

    Cold-start gating (``n_samples < COLD_START_THRESHOLD``) excludes the row
    so ``PatternStatsLookup.get`` returns the prior — noisy early data must
    not bias the L2 score.
    """
    rows = (await session.execute(
        sa.text(
            "SELECT pattern_id, n_samples, n_correct "
            "FROM pattern_stats WHERE symbol = :sym AND timeframe = :tf"
        ),
        {"sym": symbol, "tf": timeframe},
    )).all()

    by_pattern: dict[str, float] = {}
    for r in rows:
        n_samples = int(r.n_samples)
        n_correct = int(r.n_correct)
        if n_samples >= COLD_START_THRESHOLD:
            by_pattern[r.pattern_id] = n_correct / n_samples
        # Below threshold → leave absent so .get() falls back to PRIOR_ACCURACY.
    return PatternStatsLookup(by_pattern=by_pattern)


def score(
    bars: pd.DataFrame,
    *,
    current_idx: int,
    stats: PatternStatsLookup,
    enabled_patterns: set[str] | None = None,
) -> LayerScore:
    """Aggregate every pattern fire at ``current_idx`` into a single ``LayerScore``.

    Args:
        bars: OHLCV DataFrame indexed by ascending DatetimeIndex.
        current_idx: positional index of the bar to score.
        stats: ``PatternStatsLookup`` loaded once per (symbol, timeframe).
        enabled_patterns: if not ``None``, only fires whose ``pattern_id`` is
            in this set count. ``None`` means "all patterns enabled".

    Returns:
        A ``LayerScore`` with direction, strength, and confidence. The
        ``notes`` field carries a compact JSON of the firing patterns and
        their evidence (capped at ``NOTES_MAX_CHARS`` per spec §12 Q4).

    Notes:
        Per-pattern detection is wrapped in ``try/except`` so a single broken
        detector cannot brick the whole layer.
    """
    fires: list[PatternFire] = []
    for pat in ALL_PATTERNS:
        if enabled_patterns is not None and pat.pattern_id not in enabled_patterns:
            continue
        try:
            fire = pat.detect(bars, current_idx)
        except Exception:  # noqa: BLE001 — pattern bug must not brick layer
            continue
        if fire is not None:
            fires.append(fire)

    long_score = sum(
        f.strength * f.confidence * stats.get(f.pattern_id)
        for f in fires if f.direction == "LONG"
    )
    short_score = sum(
        f.strength * f.confidence * stats.get(f.pattern_id)
        for f in fires if f.direction == "SHORT"
    )
    raw = long_score - short_score
    squashed = math.tanh(raw / TANH_DIVISOR)

    if abs(squashed) < NEUTRAL_BAND:
        direction = Direction.NEUTRAL
        strength = 0.0
    elif squashed > 0:
        direction = Direction.LONG
        strength = squashed
    else:
        direction = Direction.SHORT
        strength = -squashed

    confidence = _compute_layer_confidence(fires)
    notes = _build_notes(fires)

    return LayerScore(
        direction=direction,
        strength=strength,
        confidence=confidence,
        notes=notes,
    )


def _compute_layer_confidence(fires: list[PatternFire]) -> float:
    """How confident are we that L2's direction call is reliable?

    Chart patterns (multi-bar structural setups) earn meaningful confidence
    from a single fire because their detection already requires a sustained
    formation.  Candle patterns are single-bar and need a plurality to be
    reliable — the original len/10 formula is kept for them.

    Chart: conf = avg(strength × per-fire-confidence) + count_bonus
      1 fire at quality 0.72 → 0.72; 2 fires → 0.87; 3+ fires → 0.95
    Candle: conf = min(0.80, n / 10) — unchanged semantics, max capped at 0.80
    Layer confidence = max(chart_conf, candle_conf)
    """
    if not fires:
        return 0.0

    chart_fires = [f for f in fires if f.pattern_id in _CHART_PATTERN_IDS]
    candle_fires = [f for f in fires if f.pattern_id not in _CHART_PATTERN_IDS]

    chart_conf = 0.0
    if chart_fires:
        avg_quality = (
            sum(f.strength * f.confidence for f in chart_fires) / len(chart_fires)
        )
        # Each additional chart fire adds 0.15, saturating at +0.30 for 3+.
        count_bonus = min(0.30, (len(chart_fires) - 1) * 0.15)
        chart_conf = min(0.95, avg_quality + count_bonus)

    candle_conf = min(0.80, len(candle_fires) / 10.0) if candle_fires else 0.0

    return max(chart_conf, candle_conf)


def _build_notes(fires: list[PatternFire]) -> str:
    """Compact JSON-style summary capped at ``NOTES_MAX_CHARS`` (spec §12 Q4)."""
    if not fires:
        return "0 patterns fired"
    payload = {
        "n": len(fires),
        "patterns": [
            {
                "id": f.pattern_id, "dir": f.direction,
                "s": round(f.strength, 3), "c": round(f.confidence, 3),
            }
            for f in fires
        ],
    }
    return json.dumps(payload, separators=(",", ":"))[:NOTES_MAX_CHARS]
