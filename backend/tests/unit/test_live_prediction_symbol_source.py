"""Phase 4 Task 9 — symbol_source cohort tag threaded through persist +
dispatch + shadow.

Covers (per the 2026-08-14 plan doc's Task 9 section, redrafted
2026-08-17):
  1. ``build_predictions_payload`` — defaults to "established_top20",
     threads an explicit cohort through unchanged.
  2. ``_maybe_dispatch`` — threads ``symbol_source`` into
     ``proposal_kwargs`` (the dict unpacked into
     ``proposal_from_prediction`` inside ``dispatch_if_eligible``).
  3. Step 6 (shadow_trades write path) — ``persist_closed_trade`` /
     ``build_shadow_trade_payload`` thread a closed ``ShadowPosition``'s
     ``symbol_source`` into the row payload. The real write site is
     ``app.shadow.persistence.persist_closed_trade`` (confirmed via
     ``grep -rn "INSERT INTO shadow_trades|class ShadowTrade\\b" backend/app``
     — ``shadow_trades`` rows are written via
     ``insert_with_chain(session, "shadow_trades", payload)`` where
     ``payload`` comes from ``build_shadow_trade_payload`` in
     ``app/db/payload_builders.py``; there is no standalone
     ``class ShadowTrade`` model).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.payload_builders import build_predictions_payload, build_shadow_trade_payload
from app.shadow.engine import Direction, ShadowPosition
from app.shadow.exit_monitor import ExitReason
from app.shadow.persistence import persist_closed_trade
from app.trading.execution import glue
from app.ws import live_prediction
from app.ws.live_prediction import _maybe_dispatch


class _StubFinal:
    score = 0.5
    direction = "LONG"
    confidence = 70.0


class _StubPred:
    symbol = "FOO/USDT"
    timeframe = "1h"
    ts = None
    final = _StubFinal()
    inputs_hash = "a" * 64
    cold_start = False


# ---------------------------------------------------------------------------
# Step 1: build_predictions_payload


def test_build_predictions_payload_includes_symbol_source_default() -> None:
    payload = build_predictions_payload(
        _StubPred(), user_id=1, layer_payload={},
    )
    assert payload["symbol_source"] == "established_top20"


def test_build_predictions_payload_threads_futures_poll() -> None:
    payload = build_predictions_payload(
        _StubPred(), user_id=1, layer_payload={}, symbol_source="futures_poll",
    )
    assert payload["symbol_source"] == "futures_poll"


# ---------------------------------------------------------------------------
# Step 1 (dispatch-side): _maybe_dispatch -> proposal_kwargs. This is the
# test that proves telegram_signals/live_trades end up tagged, since those
# are written from inside _maybe_dispatch -> dispatch_if_eligible, not
# from the predictions-persist path above.


class _StubTradeSetup:
    entry = 100.0
    stop_loss = 95.0
    take_profit = 110.0


class _StubDispatchPred:
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


# NOTE on deviating from the plan doc's own Step 1 snippet: the doc's
# illustrative test passes `AsyncMock()` as `_maybe_dispatch`'s
# `session_factory` positional arg. `_maybe_dispatch`'s real body does
# `async with session_factory() as dispatch_session:` -- calling an
# `AsyncMock()` instance returns a coroutine, not an async context
# manager, so `async with` raises TypeError before `dispatch_if_eligible`
# is ever reached, and the mocked `dispatch_if_eligible` is never called
# (`mock_dispatch.call_args` stays None). Verified directly by running the
# doc's snippet against the real function before writing this. Using the
# same `_FakeSession`/plain-callable-factory pattern
# tests/unit/test_live_prediction_dispatch_hook.py already proves works
# against the real `_maybe_dispatch` body instead.
class _FakeSession:
    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def execute(self, *_: Any, **__: Any) -> None:
        return None


def _session_factory() -> Any:
    return lambda: _FakeSession()


@pytest.fixture(autouse=True)
def _reset_vault_for_dispatch_tests():
    glue.reset_vault_cache_for_tests()
    yield
    glue.reset_vault_cache_for_tests()


def _vault_loaded() -> None:
    glue._vault_keys.update(  # noqa: SLF001 — intentional test poke
        {"binance_api_key": "k", "binance_api_secret": "s"},
    )


# Task 10 correction (found while re-running this file's regression suite
# against the new liquidity re-check, not part of the plan doc's original
# text): _maybe_dispatch now runs a real liquidity re-check for any
# symbol_source != "established_top20" -- this test's "futures_poll" now
# exercises that new dependency too, so check_liquidity/get_intermarket_
# adapter need mocking to reach dispatch_if_eligible at all, same as
# Task 10's own test_dispatch_liquidity_recheck.py already does.
@pytest.mark.asyncio
async def test_maybe_dispatch_threads_symbol_source_into_proposal_kwargs() -> None:
    _vault_loaded()
    captured: dict[str, Any] = {}

    async def _fake_dispatch(session, **kwargs):
        captured.update(kwargs)
        return None

    from app.data.futures_liquidity import LiquidityCheck

    with patch.object(live_prediction, "dispatch_if_eligible", _fake_dispatch), \
         patch.object(
             live_prediction, "check_liquidity", new_callable=AsyncMock,
             return_value=LiquidityCheck(
                 passed=True, qvol_24h=25_000_000.0, spread_bps=2.0,
                 depth_0_5pct_usdt=100_000.0,
             ),
         ), \
         patch.object(
             live_prediction, "get_intermarket_adapter",
             return_value=MagicMock(rate_client=MagicMock()),
         ):
        await _maybe_dispatch(
            _session_factory(), pred=_StubDispatchPred(), layer_payload={},
            symbol_source="futures_poll",
        )
    assert captured["proposal_kwargs"]["symbol_source"] == "futures_poll"


@pytest.mark.asyncio
async def test_maybe_dispatch_defaults_symbol_source_to_established_top20() -> None:
    _vault_loaded()
    captured: dict[str, Any] = {}

    async def _fake_dispatch(session, **kwargs):
        captured.update(kwargs)
        return None

    with patch.object(live_prediction, "dispatch_if_eligible", _fake_dispatch):
        await _maybe_dispatch(
            _session_factory(), pred=_StubDispatchPred(), layer_payload={},
        )
    assert captured["proposal_kwargs"]["symbol_source"] == "established_top20"


# ---------------------------------------------------------------------------
# Step 4 correction (not in the plan doc's original text, found while
# verifying against the real call graph): app.trading.execution.glue's
# proposal_from_prediction has an explicit keyword-only parameter list
# (no **kwargs catch-all) -- dispatch_if_eligible calls
# `proposal_from_prediction(**proposal_kwargs)`. Adding "symbol_source" to
# proposal_kwargs without also accepting it in proposal_from_prediction
# would raise TypeError on every real (non-mocked) dispatch attempt. This
# guard proves the two ends actually match, not just that _maybe_dispatch
# builds a dict with the right key in isolation (the plan doc's own test
# above mocks dispatch_if_eligible entirely, so it can't catch this).


def test_proposal_from_prediction_accepts_symbol_source_kwarg() -> None:
    from app.trading.execution.glue import proposal_from_prediction

    proposal = proposal_from_prediction(
        symbol="FOO/USDT", timeframe="1h",
        pred_direction="LONG", pred_confidence=0.7,
        layer_summary={}, inputs_hash="a" * 64,
        entry_price=100.0, stop_loss_price=95.0, take_profit_price=110.0,
        symbol_source="futures_poll",
    )
    assert proposal is not None
    assert proposal.symbol_source == "futures_poll"


def test_proposal_from_prediction_defaults_symbol_source() -> None:
    from app.trading.execution.glue import proposal_from_prediction

    proposal = proposal_from_prediction(
        symbol="FOO/USDT", timeframe="1h",
        pred_direction="LONG", pred_confidence=0.7,
        layer_summary={}, inputs_hash="a" * 64,
        entry_price=100.0, stop_loss_price=95.0, take_profit_price=110.0,
    )
    assert proposal is not None
    assert proposal.symbol_source == "established_top20"


# ---------------------------------------------------------------------------
# Step 6: shadow_trades write path.
#
# The real close-write site is app.shadow.persistence.persist_closed_trade
# (confirmed via grep -- no standalone `class ShadowTrade` exists; rows go
# through insert_with_chain(session, "shadow_trades", payload) with
# payload built by build_shadow_trade_payload).


def _make_closed_position(*, symbol_source: str | None) -> ShadowPosition:
    opened_at = datetime(2026, 8, 17, 10, tzinfo=timezone.utc)
    kwargs = dict(
        symbol="FOO/USDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        position_size_usdt=30.0, entry_score=0.4, entry_confidence=0.7,
        entry_atr=2.0, layer_scores={}, bars_held=3,
        opened_at=opened_at, last_check_at=opened_at, signal_id="sig-tag-1",
    )
    if symbol_source is not None:
        kwargs["symbol_source"] = symbol_source
    return ShadowPosition(**kwargs)


def test_build_shadow_trade_payload_threads_symbol_source() -> None:
    payload = build_shadow_trade_payload(
        _make_closed_position(symbol_source="futures_poll"),
        user_id=1, exit_price=110.0, exit_reason=ExitReason.TAKE_PROFIT,
        closed_at=datetime(2026, 8, 17, 12, tzinfo=timezone.utc),
        bars_held=3, inputs_hash="c" * 64,
        symbol_source="futures_poll",
    )
    assert payload["symbol_source"] == "futures_poll"


def test_build_shadow_trade_payload_defaults_symbol_source() -> None:
    payload = build_shadow_trade_payload(
        _make_closed_position(symbol_source=None),
        user_id=1, exit_price=110.0, exit_reason=ExitReason.TAKE_PROFIT,
        closed_at=datetime(2026, 8, 17, 12, tzinfo=timezone.utc),
        bars_held=3, inputs_hash="c" * 64,
    )
    assert payload["symbol_source"] == "established_top20"


@pytest.mark.asyncio
async def test_persist_closed_trade_threads_position_symbol_source() -> None:
    """persist_closed_trade must read symbol_source off the ShadowPosition
    (set at open time -- shadow_worker assigns it directly onto the
    in-memory position between from_signal() and persist_open_position(),
    mirroring the existing mtf_agreement/p_win/etc. propagation pattern)
    and thread it into the persisted row, not silently drop back to the
    default regardless of what the position actually carries."""
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, symbol TEXT, timeframe TEXT, direction TEXT, "
            "entry_price REAL, stop_loss REAL, take_profit REAL, "
            "position_size_usdt REAL, entry_score REAL, entry_confidence REAL, "
            "layer_scores TEXT, entry_atr REAL, exit_price REAL, exit_reason TEXT, "
            "pnl_pct REAL, pnl_usdt REAL, bars_held INTEGER, opened_at TEXT, "
            "closed_at TEXT, inputs_hash TEXT, model_version TEXT, signal_id TEXT, "
            "hold_scaling_factor REAL, hold_timeout_bars INTEGER, "
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, mtf_directions_json TEXT, "
            "mtf_adx_by_tf_json TEXT, p_win REAL, effective_score REAL, "
            "realized_vol_20d REAL, funding_directional_adj REAL, "
            "symbol_source TEXT NOT NULL DEFAULT 'established_top20', "
            "prev_hash TEXT, row_hash TEXT)"
        ))

    pos = _make_closed_position(symbol_source="liquidity_added_spot")
    async with factory() as session:
        await persist_closed_trade(
            session, pos, user_id=1, exit_price=110.0,
            exit_reason=ExitReason.TAKE_PROFIT,
            closed_at=datetime(2026, 8, 17, 12, tzinfo=timezone.utc),
            bars_held=3, inputs_hash="d" * 64,
        )
        await session.commit()

    async with factory() as session:
        row = (await session.execute(
            sa.text("SELECT symbol_source FROM shadow_trades WHERE signal_id = :s"),
            {"s": "sig-tag-1"},
        )).first()
    await engine.dispose()
    assert row is not None
    assert row.symbol_source == "liquidity_added_spot"
