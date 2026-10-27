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

# ---------------------------------------------------------------------------
# GRANDFATHERED NETWORK ACCESS -- PRE-EXISTING DEBT, NOT APPROVAL.
# ---------------------------------------------------------------------------
# This module reaches the real internet. It was already doing so before the
# suite-wide egress block landed (2026-10-26); the block did not create this
# dependency, it REVEALED it. 51 tests across 10 files were found this way,
# all routing through the real build_prediction path, which fetches live
# market data.
#
# WHY MARKED RATHER THAN FIXED HERE: mocking the transport would change what
# each test actually verifies, across ten files, and that is its own piece
# of work with its own review. Marking makes the dependency EXPLICIT AND
# ENUMERATED instead of invisible, and keeps the guard live for every other
# test -- which is the point: a NEW test that reaches the network now fails
# immediately instead of waiting to be noticed.
#
# PARTITIONED 2026-10-27, BEFORE anyone pays this down: 51 STRUCTURE,
# 0 VALUE. Every one of the 51 asserts on fixture-injected values (seeded
# shadow_trades rows, monkeypatched ghost candles, seeded PatternStatsLookup,
# seeded news with a FROZEN clock) or on a quantity's definitional range.
# Live data is an INPUT to these tests but cannot change any verdict, so
# there is NO false-green risk from market movement -- which is the thing
# that would have made this a project rather than a chore.
#
# Cross-checked against CI history: ZERO flakes among the 51 across the last
# 80 runs (4 failures total -- 2 from the commit that introduced the egress
# guard, 2 from TimescaleDB service-container startup, none a test in this
# set).
#
# WHAT IT STILL COSTS, so nobody rediscovers it: a fetch failure fails the
# test, so these cannot run in a sandboxed or offline runner, and CI runners
# reach Binance from SHARED GitHub IP RANGES -- meaning the suite depends on
# an external service's tolerance of OTHER TENANTS' traffic. The failure mode
# is not "our calls are too frequent", it is a 418/429 earned by somebody
# else. That is why the debt is real rather than cosmetic, even at 0 VALUE.
#
# Low risk to pay down: swap the fetch for a fixture and the assertions are
# unchanged. Removing a file from this list is the goal. Adding one is not.
# NOTE: marks are PER-TEST below, not module-level. A blanket
# pytestmark here granted network to 99 tests when 51 need it --
# the grant must be no broader than the evidence.


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


@pytest.mark.allow_network
async def test_brain_layer_scores_include_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Layer scores passed to brain = signed_strength * confidence (not just signed_strength).

    8 values (L1..L6, L8, L9) — L7 (permanent XGBoost stub) is dropped
    from the RL observation's layer-score tuple, per PR-RL-DROP-L7.
    """
    captured = await _capture(monkeypatch, _bars())
    layer_scores = captured["layer_scores"]
    assert len(layer_scores) == 8
    for score in layer_scores:
        assert -1.0 <= score <= 1.0


@pytest.mark.allow_network
async def test_brain_atr_pct_is_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    """atr_pct passed to brain is computed from bars (not hardcoded 0.0)."""
    captured = await _capture(monkeypatch, _bars())
    assert captured["market"].atr_pct > 0.0


@pytest.mark.allow_network
async def test_brain_weekend_true_on_saturday(monkeypatch: pytest.MonkeyPatch) -> None:
    """weekend=True when last bar is on a Saturday (UTC weekday=5). 2026-01-03 = Sat."""
    bars = _bars(end="2026-01-03 12:00")
    captured = await _capture(monkeypatch, bars)
    assert captured["macro"].weekend is True


@pytest.mark.allow_network
async def test_brain_weekend_false_on_tuesday(monkeypatch: pytest.MonkeyPatch) -> None:
    """weekend=False on a weekday (Tuesday 2026-01-06)."""
    bars = _bars(end="2026-01-06 12:00")
    captured = await _capture(monkeypatch, bars)
    assert captured["macro"].weekend is False


@pytest.mark.allow_network
async def test_brain_asia_open_true_at_utc_03(monkeypatch: pytest.MonkeyPatch) -> None:
    """asia_open=True when candle hour is in 00-07 UTC (matching shadow/observation.py)."""
    bars = _bars(end="2026-01-05 03:00")
    captured = await _capture(monkeypatch, bars)
    assert captured["macro"].asia_open is True


@pytest.mark.allow_network
async def test_brain_asia_open_false_at_utc_14(monkeypatch: pytest.MonkeyPatch) -> None:
    """asia_open=False when candle hour is >= 8 UTC."""
    bars = _bars(end="2026-01-05 14:00")
    captured = await _capture(monkeypatch, bars)
    assert captured["macro"].asia_open is False


@pytest.mark.allow_network
async def test_brain_adjust_propagates_to_prediction_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """brain_hook.brain_adjust must appear in prediction_extras, not hardcoded 1.0.

    Regression for: _build_extras was called with brain_adjust=1.0 even when
    compute_brain_adjust_and_persist returned a different value.
    """
    async def fake_hook(**kwargs):
        return BrainHookResult(brain_adjust=1.5, decision=None)

    monkeypatch.setattr(
        "app.core.predictor.compute_brain_adjust_and_persist",
        fake_hook,
    )
    from app.core.predictor import build_prediction
    result = await build_prediction(symbol="BTCUSDT", timeframe="1h", bars=_bars())
    assert result is not None
    assert result.prediction_extras["brain_adjust"] == pytest.approx(1.5)


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
