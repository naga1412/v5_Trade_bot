"""Layer 6 - micro-pattern aggregator (SP-5 spec section 3 decision 4).

A subset of the 158-pattern library tuned for high-frequency timeframes
(1m / 5m). Same tanh-squashed long-minus-short shape as L2 but without
``pattern_stats`` weighting (micro patterns fire too often for stats to
stabilise; equal weight is honest).

The curated set is drawn from the candle subpackage and limited to
single-bar / two-bar / three-bar reaction patterns that are useful at
intraday cadence. Multi-bar chart patterns (head & shoulders, triangles,
harmonics) are deliberately excluded — they belong to L2.

NOTE on naming: the SP-5 plan listed conceptual ids like ``marubozu_bull``
and ``engulfing_bull`` that don't exist in the registry — the candle
patterns return signed direction inside ``PatternFire`` rather than
splitting bullish / bearish into separate ids. We use the actual ids
present in ``app.core.patterns`` (see registry verified by
``test_micro_pattern_ids_resolve_to_registered_patterns``).
"""
from __future__ import annotations

import math

import pandas as pd

from app.core.patterns import ALL_PATTERNS
from app.core.patterns.base import PatternFire, detect_safe
from app.core.scoring.types import Direction, LayerScore

# Curated set: single-bar + multi-bar reaction patterns valuable on 1m/5m.
# All ids verified to exist in ALL_PATTERNS by the corresponding unit test.
MICRO_PATTERN_IDS: frozenset[str] = frozenset({
    # Single-bar candle shapes
    "doji",
    "hammer",
    "hanging_man",
    "inverted_hammer",
    "shooting_star",
    "marubozu",
    "spinning_top",
    # Two-bar reactions
    "engulfing",
    "harami",
    "harami_cross",
    "pinbar_long",
    "pinbar_short",
    # Inside / outside bars
    "inside_bar_breakout_long",
    "inside_bar_breakout_short",
    "outside_bar_reversal_long",
    "outside_bar_reversal_short",
    # Three-bar
    "three_inside_up_down",
    # Key reversals
    "key_reversal_high",
    "key_reversal_low",
    "key_reversal_long",
    "key_reversal_short",
    # Wick rejections at S/R
    "rejection_wick_at_resistance",
    "rejection_wick_at_support",
})

NEUTRAL_BAND: float = 0.05
TANH_DIVISOR: float = 3.0


def score(bars: pd.DataFrame) -> LayerScore | None:
    if len(bars) == 0:
        return None
    micro = [p for p in ALL_PATTERNS if p.pattern_id in MICRO_PATTERN_IDS]
    if not micro:
        return None
    current_idx = len(bars) - 1
    fires: list[PatternFire] = []
    for pat in micro:
        f = detect_safe(pat, bars, current_idx)
        if f is not None:
            fires.append(f)

    long_score = sum(
        f.strength * f.confidence for f in fires if f.direction == "LONG"
    )
    short_score = sum(
        f.strength * f.confidence for f in fires if f.direction == "SHORT"
    )
    raw = long_score - short_score
    squashed = math.tanh(raw / TANH_DIVISOR)

    if abs(squashed) < NEUTRAL_BAND:
        direction = Direction.NEUTRAL
        strength = 0.0
    elif squashed > 0:
        direction = Direction.LONG
        strength = float(squashed)
    else:
        direction = Direction.SHORT
        strength = float(-squashed)
    confidence = min(1.0, len(fires) / 5.0)
    notes = f"{len(fires)} micro patterns fired"
    return LayerScore(
        direction=direction,
        strength=strength,
        confidence=confidence,
        notes=notes,
    )
