"""Unit tests for app/scanner/fast_scan.py (Feature 4 — fast scanner).

The module is pure — feed it a DataFrame, get back a FastScanResult. Tests
construct synthetic OHLCV with known characteristics (trending up,
trending down, sideways, compressed range) and assert the scan
classifies them correctly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.scanner.fast_scan import (
    MIN_BARS_REQUIRED,
    FastScanResult,
    fast_scan,
)


def _build_bars(closes: np.ndarray, *, base_volume: float = 1000.0) -> pd.DataFrame:
    """Build an OHLCV DataFrame from a 1-D close series."""
    n = len(closes)
    return pd.DataFrame({
        "open": closes * 0.999,
        "high": closes * 1.003,
        "low": closes * 0.997,
        "close": closes,
        "volume": np.full(n, base_volume),
    }, index=pd.date_range("2026-05-12", periods=n, freq="1h", tz="UTC"))


def test_fast_scan_returns_none_when_too_few_bars() -> None:
    bars = _build_bars(np.full(MIN_BARS_REQUIRED - 1, 100.0))
    assert fast_scan(bars, symbol="BTC/USDT", timeframe="1h") is None


def test_fast_scan_returns_none_when_required_columns_missing() -> None:
    bars = pd.DataFrame({
        "open": [1.0] * 100, "close": [1.0] * 100,
        # missing high/low/volume
    })
    assert fast_scan(bars, symbol="BTC/USDT", timeframe="1h") is None


def test_fast_scan_strong_uptrend_produces_long_signal() -> None:
    # 100 bars climbing from 100 → 130 with mild noise.
    rng = np.random.default_rng(42)
    closes = np.linspace(100.0, 130.0, 100) + rng.normal(0, 0.5, 100)
    res = fast_scan(_build_bars(closes), symbol="BTC/USDT", timeframe="1h")
    assert res is not None
    assert isinstance(res, FastScanResult)
    assert res.direction == "LONG"
    assert res.final_score > 0
    assert res.confidence >= 0.6


def test_fast_scan_strong_downtrend_produces_short_signal() -> None:
    rng = np.random.default_rng(43)
    closes = np.linspace(130.0, 100.0, 100) + rng.normal(0, 0.5, 100)
    res = fast_scan(_build_bars(closes), symbol="ETH/USDT", timeframe="1h")
    assert res is not None
    assert res.direction == "SHORT"
    assert res.final_score < 0


def test_fast_scan_sideways_market_produces_neutral_or_weak() -> None:
    # Pure random walk around 100 with small steps.
    rng = np.random.default_rng(44)
    closes = 100.0 + np.cumsum(rng.normal(0, 0.2, 100))
    res = fast_scan(_build_bars(closes), symbol="SOL/USDT", timeframe="1h")
    assert res is not None
    # In a clean sideways market the tier should not be 'confirmed'.
    assert res.tier in ("weak", "neutral", "probable", "diverging")
    # The aggregate magnitude should be modest.
    assert abs(res.final_score) < 50


def test_fast_scan_uptrend_classified_markup() -> None:
    rng = np.random.default_rng(45)
    closes = np.linspace(100.0, 140.0, 100) + rng.normal(0, 0.5, 100)
    res = fast_scan(_build_bars(closes), symbol="BTC/USDT", timeframe="1h")
    assert res is not None
    assert res.phase == "markup"


def test_fast_scan_downtrend_classified_markdown() -> None:
    rng = np.random.default_rng(46)
    closes = np.linspace(140.0, 100.0, 100) + rng.normal(0, 0.5, 100)
    res = fast_scan(_build_bars(closes), symbol="BTC/USDT", timeframe="1h")
    assert res is not None
    assert res.phase == "markdown"


def test_fast_scan_returns_five_module_scores() -> None:
    rng = np.random.default_rng(47)
    closes = 100.0 + np.cumsum(rng.normal(0, 0.5, 100))
    res = fast_scan(_build_bars(closes), symbol="X/USDT", timeframe="1h")
    assert res is not None
    names = [m.name for m in res.modules]
    assert set(names) == {"trend", "momentum", "volatility", "volume", "structure"}
    # Weights sum to 1.0 (rounded for floating point).
    assert sum(m.weight for m in res.modules) == pytest.approx(1.0)


def test_fast_scan_includes_expected_move_for_directional_signals() -> None:
    rng = np.random.default_rng(48)
    closes = np.linspace(100.0, 130.0, 100) + rng.normal(0, 0.5, 100)
    res = fast_scan(_build_bars(closes), symbol="BTC/USDT", timeframe="1h")
    assert res is not None
    if res.direction == "LONG":
        assert res.expected_move_pct is not None
        assert res.expected_move_pct > 0
    assert res.rr_estimate is not None
    assert res.rr_estimate == 2.0


def test_fast_scan_neutral_market_has_zero_expected_move() -> None:
    """When direction is NEUTRAL, expected_move_pct should be 0 (no projected move)."""
    rng = np.random.default_rng(49)
    closes = 100.0 + np.cumsum(rng.normal(0, 0.05, 100))  # very tight range
    res = fast_scan(_build_bars(closes), symbol="USDC/USDT", timeframe="1h")
    assert res is not None
    if res.direction == "NEUTRAL":
        assert res.expected_move_pct == 0.0 or res.expected_move_pct is None
