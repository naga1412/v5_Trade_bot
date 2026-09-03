# backend/tests/unit/test_dispatch_liquidity_recheck.py
#
# DEVIATION from the plan doc's literal Step 1 text: the plan passes a bare
# `AsyncMock()` as the positional `session_factory` argument to
# `_maybe_dispatch`. That doesn't work against this repo's actual
# `_maybe_dispatch` body, which does `async with session_factory() as
# dispatch_session:` — calling a plain `AsyncMock()` returns a *coroutine*
# (AsyncMock.__call__ is itself a coroutine function), and a coroutine does
# not support the `async with` protocol. The two "dispatch succeeds" tests
# failed with `'coroutine' object does not support the asynchronous context
# manager protocol` when run against the plan's literal fixture. The existing
# `backend/tests/unit/test_live_prediction_dispatch_hook.py` already
# establishes this repo's convention for mocking `_maybe_dispatch`'s
# `session_factory` (a `_FakeSession` class implementing `__aenter__` /
# `__aexit__` / `commit`, plus a zero-arg factory function returning it) —
# `_FakeSession` / `_session_factory` below mirror that convention.
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.ws.live_prediction import _maybe_dispatch


class _FakeSession:
    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def commit(self) -> None:
        return None


def _session_factory() -> _FakeSession:
    return _FakeSession()


class _StubTradeSetup:
    entry = 100.0
    stop_loss = 95.0
    take_profit = 110.0


class _StubFinal:
    direction = "LONG"
    score = 0.5
    confidence = 70.0


class _StubPred:
    symbol = "FOO/USDT"
    timeframe = "1h"
    trade_setup = _StubTradeSetup()
    final = _StubFinal()
    inputs_hash = "a" * 64
    mtf_agreement = None
    mtf_dominant_tf = None
    mtf_directions_json = None
    funding_rate_daily = 0.0
    mtf_adx_by_tf_json = None


@pytest.mark.asyncio
async def test_established_top20_signals_skip_liquidity_recheck_entirely() -> None:
    with patch("app.ws.live_prediction.vault_keys", return_value="fake-key"), \
         patch("app.ws.live_prediction.check_liquidity", new_callable=AsyncMock) as mock_check, \
         patch("app.ws.live_prediction.dispatch_if_eligible", new_callable=AsyncMock, return_value=None), \
         patch("app.ws.live_prediction.get_settings"):
        await _maybe_dispatch(
            _session_factory, pred=_StubPred(), layer_payload={}, symbol_source="established_top20",
        )
    mock_check.assert_not_called()


@pytest.mark.asyncio
async def test_liquidity_added_spot_signal_dispatches_when_liquidity_passes() -> None:
    from app.data.futures_liquidity import LiquidityCheck

    with patch("app.ws.live_prediction.vault_keys", return_value="fake-key"), \
         patch("app.ws.live_prediction.check_liquidity", new_callable=AsyncMock) as mock_check, \
         patch("app.ws.live_prediction.dispatch_if_eligible", new_callable=AsyncMock, return_value=None) as mock_dispatch, \
         patch("app.ws.live_prediction.get_settings"):
        mock_check.return_value = LiquidityCheck(
            passed=True, qvol_24h=25_000_000.0, spread_bps=2.0, depth_0_5pct_usdt=100_000.0,
        )
        await _maybe_dispatch(
            _session_factory, pred=_StubPred(), layer_payload={}, symbol_source="liquidity_added_spot",
        )
    mock_check.assert_awaited_once()
    mock_dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_liquidity_added_spot_signal_suppressed_when_liquidity_fails(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="app.ws.live_prediction")
    from app.data.futures_liquidity import LiquidityCheck

    with patch("app.ws.live_prediction.vault_keys", return_value="fake-key"), \
         patch("app.ws.live_prediction.check_liquidity", new_callable=AsyncMock) as mock_check, \
         patch("app.ws.live_prediction.dispatch_if_eligible", new_callable=AsyncMock) as mock_dispatch, \
         patch("app.ws.live_prediction.get_settings"):
        mock_check.return_value = LiquidityCheck(
            passed=False, qvol_24h=1_000_000.0, spread_bps=10.0, depth_0_5pct_usdt=5_000.0,
        )
        await _maybe_dispatch(
            _session_factory, pred=_StubPred(), layer_payload={}, symbol_source="liquidity_added_spot",
        )
    mock_dispatch.assert_not_called()
    assert any("liquidity" in r.getMessage().lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_futures_poll_signal_dispatches_when_liquidity_passes() -> None:
    from app.data.futures_liquidity import LiquidityCheck

    with patch("app.ws.live_prediction.vault_keys", return_value="fake-key"), \
         patch("app.ws.live_prediction.check_liquidity", new_callable=AsyncMock) as mock_check, \
         patch("app.ws.live_prediction.dispatch_if_eligible", new_callable=AsyncMock, return_value=None) as mock_dispatch, \
         patch("app.ws.live_prediction.get_settings"):
        mock_check.return_value = LiquidityCheck(
            passed=True, qvol_24h=25_000_000.0, spread_bps=2.0, depth_0_5pct_usdt=100_000.0,
        )
        await _maybe_dispatch(
            _session_factory, pred=_StubPred(), layer_payload={}, symbol_source="futures_poll",
        )
    mock_dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_futures_poll_signal_suppressed_when_liquidity_fails(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="app.ws.live_prediction")
    from app.data.futures_liquidity import LiquidityCheck

    with patch("app.ws.live_prediction.vault_keys", return_value="fake-key"), \
         patch("app.ws.live_prediction.check_liquidity", new_callable=AsyncMock) as mock_check, \
         patch("app.ws.live_prediction.dispatch_if_eligible", new_callable=AsyncMock) as mock_dispatch, \
         patch("app.ws.live_prediction.get_settings"):
        mock_check.return_value = LiquidityCheck(
            passed=False, qvol_24h=1_000_000.0, spread_bps=10.0, depth_0_5pct_usdt=5_000.0,
        )
        await _maybe_dispatch(
            _session_factory, pred=_StubPred(), layer_payload={}, symbol_source="futures_poll",
        )
    mock_dispatch.assert_not_called()
    assert any("liquidity" in r.getMessage().lower() for r in caplog.records)
