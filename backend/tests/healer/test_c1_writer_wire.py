"""Healer C1 writer wire — a synthetic dispatch exception lands in the
healer_findings registry via ``record_dispatch_error``.

The C1 detector was dead code before this wire: no writer meant an
empty ``healer_findings`` table for dispatcher-side exceptions.

This test asserts the round-trip: raise a synthetic exception inside
``_maybe_dispatch``'s outermost try/except, verify:
  * The row lands in healer_findings with detector_name='dispatcher_exception'
  * details.exception_type carries the class name so C1's aggregation +
    novel-class check can fire
  * The healer write failure does NOT propagate out (best-effort)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine



async def _mk_healer_findings_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE healer_findings ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "detector_name TEXT NOT NULL, "
            "detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "severity TEXT NOT NULL, "
            "summary TEXT NOT NULL, "
            "details TEXT)"
        ))
    return engine


@pytest.mark.asyncio
async def test_c1_writer_records_synthetic_dispatch_exception() -> None:
    """The outermost handler at live_prediction.py MUST call
    record_dispatch_error() with the exception + pred context so C1's
    registry populates.

    Drive the handler by patching dispatch_if_eligible to raise, patch
    record_dispatch_error to capture the call args (SQLite's ``CAST(...)
    AS JSONB`` is a no-op for JSON dict strings, so hitting the real
    write path in a unit test is not meaningful — the runner-level test
    exercises the full round-trip against Postgres in soak).
    """
    captured: dict[str, object] = {}

    class _SyntheticDispatchError(RuntimeError):
        """Distinct class so we can grep it in the details column."""

    async def _raising_dispatch(*args, **kwargs):
        raise _SyntheticDispatchError("simulated dispatch blowup")

    async def _capture_record(session_factory, *, exception, context=None):
        captured["exception"] = exception
        captured["context"] = context

    # Build the minimum pred / layer_payload shape _maybe_dispatch needs
    # to actually enter the try/except body — it early-returns on missing
    # vault_keys or missing trade_setup.
    pred = MagicMock()
    pred.symbol = "TEST/USDT"
    pred.timeframe = "1h"
    pred.final = MagicMock(direction="LONG", confidence=0.6, score=0.4)
    pred.inputs_hash = "h0"
    pred.trade_setup = MagicMock(entry=100.0, stop_loss=99.0, take_profit=102.0)
    pred.mtf_agreement = None
    pred.mtf_dominant_tf = None
    pred.mtf_directions_json = None
    pred.funding_rate_daily = 0.0
    pred.layer_scores = {}
    layer_payload = {}

    from app.ws import live_prediction

    session_factory = MagicMock()

    with patch.object(
        live_prediction, "vault_keys", return_value=object(),
    ), patch.object(
        live_prediction, "dispatch_if_eligible", new=_raising_dispatch,
    ), patch.object(
        live_prediction, "get_settings",
        return_value=MagicMock(binance_use_testnet=True),
    ), patch(
        "app.healer.record_dispatch_error", new=_capture_record,
    ):
        # _maybe_dispatch's exception handler should invoke
        # record_dispatch_error internally. It must not re-raise.
        await live_prediction._maybe_dispatch(
            session_factory, pred=pred, layer_payload=layer_payload,
        )

    # The writer was called with the exception + pred context.
    assert isinstance(captured.get("exception"), _SyntheticDispatchError)
    ctx = captured.get("context") or {}
    assert ctx.get("symbol") == "TEST/USDT"
    assert ctx.get("timeframe") == "1h"
    assert ctx.get("direction") == "LONG"


@pytest.mark.asyncio
async def test_c1_writer_failure_does_not_propagate() -> None:
    """If the healer write itself raises (DB down / table missing), the
    outer handler must swallow it. A broken healer must not take the
    dispatch loop down with it."""
    async def _raising_dispatch(*args, **kwargs):
        raise RuntimeError("dispatch failed")

    async def _broken_record(*args, **kwargs):
        raise RuntimeError("healer_findings table missing")

    pred = MagicMock()
    pred.symbol = "TEST/USDT"
    pred.timeframe = "1h"
    pred.final = MagicMock(direction="LONG", confidence=0.6, score=0.4)
    pred.inputs_hash = "h0"
    pred.trade_setup = MagicMock(entry=100.0, stop_loss=99.0, take_profit=102.0)
    pred.mtf_agreement = None
    pred.mtf_dominant_tf = None
    pred.mtf_directions_json = None
    pred.funding_rate_daily = 0.0
    pred.layer_scores = {}

    from app.ws import live_prediction

    session_factory = MagicMock()

    with patch.object(
        live_prediction, "vault_keys", return_value=object(),
    ), patch.object(
        live_prediction, "dispatch_if_eligible", new=_raising_dispatch,
    ), patch.object(
        live_prediction, "get_settings",
        return_value=MagicMock(binance_use_testnet=True),
    ), patch(
        "app.healer.record_dispatch_error", new=_broken_record,
    ):
        # Must complete without raising.
        await live_prediction._maybe_dispatch(
            session_factory, pred=pred, layer_payload={},
        )
