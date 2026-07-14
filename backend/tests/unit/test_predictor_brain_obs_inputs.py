"""Regression tests: build_prediction brain hook receives aligned obs inputs.

Three train/serve mismatches were found and fixed (Phase 3A):

1. Layer obs scores: training (shadow/worker.py) uses sign*strength*confidence;
   inference used signed_strength only (no confidence factor).
2. atr_pct: training used real ATR from bars; inference always passed 0.0.
3. weekend/asia_open: training derived from candle timestamp; inference was False.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.rl.predictor_glue import BrainHookResult


def _bars(n: int = 250, end: str = "2026-01-05 10:00", tz: str = "UTC") -> pd.DataFrame:
    closes = list(np.linspace(100.0, 200.0, n))
    index = pd.date_range(end=pd.Timestamp(end, tz=tz), periods=n, freq="1h")
    return pd.DataFrame({
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low":  [c * 0.99 for c in closes],
        "close": closes,
        "volume": [1000.0] * n,
    }, index=index)


async def _capture(monkeypatch: pytest.MonkeyPatch, bars: pd.DataFrame) -> dict:
    """Call build_prediction, intercept compute_brain_adjust_and_persist, return kwargs."""
    captured: dict = {}

    async def fake_hook(**kwargs):
        captured.update(kwargs)
        return BrainHookResult(brain_adjust=1.0, decision=None)

    monkeypatch.setattr(
        "app.core.predictor.compute_brain_adjust_and_persist",
        fake_hook,
    )
    from app.core.predictor import build_prediction
    await build_prediction(symbol="BTCUSDT", timeframe="1h", bars=bars)
    return captured


async def test_brain_layer_scores_include_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Layer scores passed to brain = signed_strength * confidence (not just signed_strength)."""
    captured = await _capture(monkeypatch, _bars())
    layer_scores = captured["layer_scores"]
    assert len(layer_scores) == 9
    for score in layer_scores:
        assert -1.0 <= score <= 1.0


async def test_brain_atr_pct_is_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    """atr_pct passed to brain is computed from bars (not hardcoded 0.0)."""
    captured = await _capture(monkeypatch, _bars())
    assert captured["market"].atr_pct > 0.0


async def test_brain_weekend_true_on_saturday(monkeypatch: pytest.MonkeyPatch) -> None:
    """weekend=True when last bar is on a Saturday (UTC weekday=5). 2026-01-03 = Sat."""
    bars = _bars(end="2026-01-03 12:00")
    captured = await _capture(monkeypatch, bars)
    assert captured["macro"].weekend is True


async def test_brain_weekend_false_on_tuesday(monkeypatch: pytest.MonkeyPatch) -> None:
    """weekend=False on a weekday (Tuesday 2026-01-06)."""
    bars = _bars(end="2026-01-06 12:00")
    captured = await _capture(monkeypatch, bars)
    assert captured["macro"].weekend is False


async def test_brain_asia_open_true_at_utc_03(monkeypatch: pytest.MonkeyPatch) -> None:
    """asia_open=True when candle hour is in 00-07 UTC (matching shadow/observation.py)."""
    bars = _bars(end="2026-01-05 03:00")
    captured = await _capture(monkeypatch, bars)
    assert captured["macro"].asia_open is True


async def test_brain_asia_open_false_at_utc_14(monkeypatch: pytest.MonkeyPatch) -> None:
    """asia_open=False when candle hour is >= 8 UTC."""
    bars = _bars(end="2026-01-05 14:00")
    captured = await _capture(monkeypatch, bars)
    assert captured["macro"].asia_open is False


async def test_brain_regime_maps_bull_to_bull_breakout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regime classifier 'bull' must map to 'bull_breakout' in MarketFeatures."""
    async def _bull():
        return "bull"
    monkeypatch.setattr("app.core.predictor.get_cached_market_regime", _bull)
    captured = await _capture(monkeypatch, _bars())
    assert captured["market"].regime == "bull_breakout"


async def test_brain_regime_maps_bear_to_bear_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regime classifier 'bear' must map to 'bear_crash' in MarketFeatures."""
    async def _bear():
        return "bear"
    monkeypatch.setattr("app.core.predictor.get_cached_market_regime", _bear)
    captured = await _capture(monkeypatch, _bars())
    assert captured["market"].regime == "bear_crash"


async def test_brain_regime_none_falls_back_to_sideways(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regime fetch failure (None) must fall back to 'sideways_grind'."""
    async def _fail():
        return None
    monkeypatch.setattr("app.core.predictor.get_cached_market_regime", _fail)
    captured = await _capture(monkeypatch, _bars())
    assert captured["market"].regime == "sideways_grind"
