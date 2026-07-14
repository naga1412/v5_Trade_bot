"""Tests for double_bottom.py — Phase 2-C volume-divergence fix.

Volume divergence: second trough forms on lower volume than first trough.
Lower volume at t2 = less selling pressure = accumulation phase.
Confidence:  0.80 when vol diverges (vol_t2 < vol_t1 * 0.85)
             0.55 when vol does not diverge
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.patterns.chart.double_bottom import DoubleBottomPattern


LOOKBACK = DoubleBottomPattern.LOOKBACK  # 60
_PATTERN = DoubleBottomPattern()

# Indices within the 61-bar window
TROUGH1_IDX = 10
TROUGH2_IDX = 40
PEAK_IDX = 24
TROUGH_PRICE = 80.0
PEAK_PRICE = 97.0
CURRENT_CLOSE = 100.0   # > peak → neckline broken


def _make_bars(vol_t1: float = 1_500, vol_t2: float = 1_200) -> pd.DataFrame:
    """Build a 61-bar double-bottom with configurable trough volumes.

    Structure:
      bars  0- 9: approach from 95 → 80 (trough 1 forms at bar 10)
      bars 10-20: trough 1 (lows ≈ 80) then partial recovery
      bars 21-30: peak around 97 at bar 24
      bars 31-39: decline toward trough 2
      bars 40-50: trough 2 (lows ≈ 80) then recovery
      bars 51-60: break above neckline (close[-1]=100 > peak=97)
    """
    n = LOOKBACK + 1  # 61
    closes = np.full(n, 95.0)

    # Descent to trough 1
    for i in range(5, TROUGH1_IDX + 1):
        closes[i] = 95.0 - (95.0 - TROUGH_PRICE) * (i - 5) / (TROUGH1_IDX - 5)
    # Recovery from trough 1 to peak
    for i in range(TROUGH1_IDX, PEAK_IDX + 1):
        closes[i] = TROUGH_PRICE + (PEAK_PRICE - TROUGH_PRICE) * (i - TROUGH1_IDX) / (PEAK_IDX - TROUGH1_IDX)
    # Decline from peak to trough 2
    for i in range(PEAK_IDX, TROUGH2_IDX + 1):
        closes[i] = PEAK_PRICE - (PEAK_PRICE - TROUGH_PRICE) * (i - PEAK_IDX) / (TROUGH2_IDX - PEAK_IDX)
    # Recovery from trough 2 to neckline break
    for i in range(TROUGH2_IDX, n):
        closes[i] = TROUGH_PRICE + (CURRENT_CLOSE - TROUGH_PRICE) * (i - TROUGH2_IDX) / (n - 1 - TROUGH2_IDX)

    highs = closes + 2.0
    lows = closes - 2.0

    volumes = np.ones(n) * 1_000.0
    volumes[TROUGH1_IDX] = vol_t1
    volumes[TROUGH2_IDX] = vol_t2

    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=pd.date_range("2024-01-01", periods=n, freq="1h"),
    )


# --- pattern structure ---

def test_pattern_id() -> None:
    assert _PATTERN.pattern_id == "double_bottom"


def test_pattern_type_is_chart() -> None:
    assert _PATTERN.pattern_type == "chart"


# --- detection (both volume cases should produce a fire) ---

def test_fires_with_volume_divergence() -> None:
    # vol_t2=1200 < vol_t1=1500*0.85=1275 → divergence
    bars = _make_bars(vol_t1=1_500, vol_t2=1_200)
    result = _PATTERN.detect(bars, current_idx=LOOKBACK)
    assert result is not None
    assert result.direction == "LONG"
    assert result.pattern_id == "double_bottom"


def test_fires_without_volume_divergence() -> None:
    # vol_t2=1400 > vol_t1=1500*0.85=1275 → no divergence
    bars = _make_bars(vol_t1=1_500, vol_t2=1_400)
    result = _PATTERN.detect(bars, current_idx=LOOKBACK)
    assert result is not None
    assert result.direction == "LONG"


# --- confidence reflects volume divergence ---

def test_confidence_higher_with_volume_divergence() -> None:
    bars_div = _make_bars(vol_t1=1_500, vol_t2=1_200)   # diverges
    bars_no_div = _make_bars(vol_t1=1_500, vol_t2=1_400)  # does not
    r_div = _PATTERN.detect(bars_div, current_idx=LOOKBACK)
    r_no_div = _PATTERN.detect(bars_no_div, current_idx=LOOKBACK)
    assert r_div is not None and r_no_div is not None
    assert r_div.confidence > r_no_div.confidence, (
        f"divergence confidence ({r_div.confidence}) should exceed "
        f"no-divergence confidence ({r_no_div.confidence})"
    )


def test_volume_divergence_confidence_value() -> None:
    bars = _make_bars(vol_t1=1_500, vol_t2=1_200)
    result = _PATTERN.detect(bars, current_idx=LOOKBACK)
    assert result is not None
    assert result.confidence == pytest.approx(0.80, abs=0.01)


def test_no_volume_divergence_confidence_value() -> None:
    bars = _make_bars(vol_t1=1_500, vol_t2=1_400)
    result = _PATTERN.detect(bars, current_idx=LOOKBACK)
    assert result is not None
    assert result.confidence == pytest.approx(0.55, abs=0.01)


# --- evidence carries volume data ---

def test_evidence_contains_volume_fields() -> None:
    bars = _make_bars(vol_t1=1_500, vol_t2=1_200)
    result = _PATTERN.detect(bars, current_idx=LOOKBACK)
    assert result is not None
    assert "vol_t1" in result.evidence
    assert "vol_t2" in result.evidence
    assert "vol_divergence" in result.evidence
    assert result.evidence["vol_divergence"] is True


def test_evidence_divergence_flag_false_when_no_divergence() -> None:
    bars = _make_bars(vol_t1=1_500, vol_t2=1_400)
    result = _PATTERN.detect(bars, current_idx=LOOKBACK)
    assert result is not None
    assert result.evidence["vol_divergence"] is False


# --- does not fire before neckline break ---

def test_does_not_fire_before_neckline_break() -> None:
    """Pattern must require close > neckline peak."""
    bars = _make_bars()
    # Override the current close to be below the peak
    bars_below = bars.copy()
    closes = bars_below["close"].to_numpy().copy()
    closes[-1] = PEAK_PRICE - 1  # 96 < peak=97
    bars_below = bars_below.copy()
    bars_below["close"] = closes
    bars_below["high"] = closes + 2.0
    bars_below["low"] = closes - 2.0
    result = _PATTERN.detect(bars_below, current_idx=LOOKBACK)
    assert result is None, "pattern must not fire until close exceeds neckline peak"


# --- not enough history ---

def test_returns_none_below_lookback() -> None:
    bars = _make_bars()
    result = _PATTERN.detect(bars, current_idx=LOOKBACK - 10)
    assert result is None
