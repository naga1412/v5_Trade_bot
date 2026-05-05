"""L6 micro-pattern aggregator (SP-5 Phase B2)."""
from __future__ import annotations

import pandas as pd

from app.core.scoring.layer6_micro import MICRO_PATTERN_IDS, score
from app.core.scoring.types import Direction


def make_bars(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
        "open": closes,
        "high": [c * 1.005 for c in closes],
        "low":  [c * 0.995 for c in closes],
        "close": closes,
        "volume": [1000.0] * n,
    }).set_index("ts")


def test_micro_pattern_ids_non_empty() -> None:
    assert len(MICRO_PATTERN_IDS) >= 10


def test_micro_pattern_ids_resolve_to_registered_patterns() -> None:
    """Every id in MICRO_PATTERN_IDS must exist in ALL_PATTERNS."""
    from app.core.patterns import ALL_PATTERNS
    registered = {p.pattern_id for p in ALL_PATTERNS}
    missing = MICRO_PATTERN_IDS - registered
    assert not missing, f"micro ids not in registry: {missing}"


def test_returns_layer_score_on_normal_bars() -> None:
    bars = make_bars([100.0 + i * 0.1 for i in range(60)])
    s = score(bars)
    assert s is not None
    assert 0.0 <= s.strength <= 1.0
    assert 0.0 <= s.confidence <= 1.0


def test_returns_none_with_zero_bars() -> None:
    bars = make_bars([])
    assert score(bars) is None


def test_returns_neutral_on_flat_bars() -> None:
    """A very flat series should not produce a strong signal."""
    bars = make_bars([100.0] * 60)
    s = score(bars)
    assert s is not None
    assert s.strength <= 0.5  # weak or neutral


def test_explicit_bullish_engulfing_pushes_long() -> None:
    """A clean bullish-engulfing setup at the last bar should not vote SHORT."""
    closes = [100.0] * 50 + [99.0, 102.0]  # red bar followed by larger green
    bars = make_bars(closes)
    # Hand-craft last two bars so engulfing fires bullish.
    # Bar i-1: bearish (open 100, close 99)
    bars.iloc[-2, bars.columns.get_loc("open")] = 100.0
    bars.iloc[-2, bars.columns.get_loc("close")] = 99.0
    bars.iloc[-2, bars.columns.get_loc("high")] = 100.2
    bars.iloc[-2, bars.columns.get_loc("low")] = 98.8
    # Bar i: bullish, body engulfs prior body (open 98.5, close 100.5)
    bars.iloc[-1, bars.columns.get_loc("open")] = 98.5
    bars.iloc[-1, bars.columns.get_loc("close")] = 100.5
    bars.iloc[-1, bars.columns.get_loc("high")] = 100.7
    bars.iloc[-1, bars.columns.get_loc("low")] = 98.4
    s = score(bars)
    assert s is not None
    assert s.direction is not Direction.SHORT
