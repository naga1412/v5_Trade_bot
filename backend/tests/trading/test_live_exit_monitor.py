"""PR8 live_exit_monitor — classification + write-back tests.

Exercises `classify_exit` (pure function) + `_process_one` (integration
with SQLite + mocked Binance).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.live_exit_reasons import LiveExitReason
from app.trading.execution.live_exit_monitor import (
    OpenLiveTrade,
    _process_one,
    classify_exit,
)


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


# --- classify_exit pure-function tests ---------------------------------


def _long_pos(**overrides) -> OpenLiveTrade:
    defaults = dict(
        trade_id=1, user_id=1, symbol="BTCUSDT", direction="LONG",
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        opened_at=_NOW - timedelta(hours=2), mtf_agreement=5,
    )
    defaults.update(overrides)
    return OpenLiveTrade(**defaults)


def _short_pos(**overrides) -> OpenLiveTrade:
    defaults = dict(
        trade_id=2, user_id=1, symbol="ETHUSDT", direction="SHORT",
        entry_price=100.0, stop_loss=105.0, take_profit=90.0,
        opened_at=_NOW - timedelta(hours=2), mtf_agreement=4,
    )
    defaults.update(overrides)
    return OpenLiveTrade(**defaults)


def test_classify_long_take_profit_when_price_hits_tp() -> None:
    cls = classify_exit(pos=_long_pos(), current_price=111.0, now=_NOW)
    assert cls is not None
    assert cls.exit_reason is LiveExitReason.TAKE_PROFIT
    assert cls.exit_price == 111.0


def test_classify_long_stop_loss_when_price_at_sl() -> None:
    cls = classify_exit(pos=_long_pos(), current_price=95.0, now=_NOW)
    assert cls is not None
    assert cls.exit_reason is LiveExitReason.STOP_LOSS


def test_classify_long_still_open_when_price_inside_bracket() -> None:
    cls = classify_exit(pos=_long_pos(), current_price=102.0, now=_NOW)
    assert cls is None


def test_classify_short_take_profit_when_price_drops_to_tp() -> None:
    cls = classify_exit(pos=_short_pos(), current_price=89.0, now=_NOW)
    assert cls is not None
    assert cls.exit_reason is LiveExitReason.TAKE_PROFIT


def test_classify_short_stop_loss_when_price_rises_to_sl() -> None:
    cls = classify_exit(pos=_short_pos(), current_price=106.0, now=_NOW)
    assert cls is not None
    assert cls.exit_reason is LiveExitReason.STOP_LOSS


def test_classify_timeout_when_age_exceeds_baseline() -> None:
    old = _long_pos(opened_at=_NOW - timedelta(hours=25))
    cls = classify_exit(pos=old, current_price=100.0, now=_NOW)
    assert cls is not None
    assert cls.exit_reason is LiveExitReason.TIMEOUT


def test_classify_external_close_when_current_price_none() -> None:
    cls = classify_exit(pos=_long_pos(), current_price=None, now=_NOW)
    assert cls is not None
    assert cls.exit_reason is LiveExitReason.EXTERNAL_CLOSE
    assert cls.exit_price is None


def test_classify_tp_wins_when_both_boundaries_could_match_long() -> None:
    """Flash candle hitting TP first in a LONG — TP wins (price >= TP)."""
    # Long: TP=110, SL=95. Price 200 hit both intra-candle. classify_exit
    # sees current=200 and selects TP because the LONG branch checks TP
    # first.
    cls = classify_exit(pos=_long_pos(), current_price=200.0, now=_NOW)
    assert cls.exit_reason is LiveExitReason.TAKE_PROFIT


# --- _process_one integration tests ------------------------------------


async def _mk_engine_with_live_trades():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE live_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, "
            "direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL, "
            "exit_price REAL, "
            "stop_loss REAL NOT NULL, "
            "take_profit REAL NOT NULL, "
            "opened_at TEXT NOT NULL, "
            "closed_at TEXT, "
            "mtf_agreement INTEGER, "
            "exit_reason TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE live_cooldowns ("
            "user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, "
            "cooldown_until TEXT NOT NULL, "
            "last_exit_reason TEXT NOT NULL, "
            "last_mtf_agreement INTEGER, "
            "updated_at TEXT NOT NULL, "
            "PRIMARY KEY (user_id, symbol))"
        ))
        await conn.execute(sa.text(
            "INSERT INTO live_trades "
            "(id, user_id, symbol, direction, entry_price, stop_loss, "
            " take_profit, opened_at, mtf_agreement) "
            "VALUES (1, 1, 'BTCUSDT', 'LONG', 100.0, 95.0, 110.0, "
            "        '2026-05-18T10:00:00+00:00', 5)"
        ))
    return engine


def _settings():
    return SimpleNamespace(
        LIVE_COOLDOWN_HOURS_BY_OUTCOME={
            "stop_loss": 8.0, "take_profit": 1.0, "timeout": 4.0,
            "manual_close": 0.0, "external_close": 0.0,
            "liquidation_buffer_breach": 24.0,
        },
    )


@pytest.mark.asyncio
async def test_process_one_writes_exit_reason_and_cooldown_on_tp() -> None:
    engine = await _mk_engine_with_live_trades()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    pos = OpenLiveTrade(
        trade_id=1, user_id=1, symbol="BTCUSDT", direction="LONG",
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        opened_at=_NOW - timedelta(hours=2), mtf_agreement=5,
    )
    binance = MagicMock()
    binance.get_position = AsyncMock(return_value=SimpleNamespace(entry_price=111.0))
    binance.aclose = AsyncMock()

    async with factory() as session:
        cls = await _process_one(
            pos=pos, binance=binance, session=session,
            settings=_settings(), now_fn=lambda: _NOW,
        )
        await session.commit()
    assert cls is not None
    assert cls.exit_reason is LiveExitReason.TAKE_PROFIT

    # Read back: exit_reason persisted + 1h cooldown set
    async with factory() as session:
        row = (await session.execute(sa.text(
            "SELECT exit_reason, closed_at FROM live_trades WHERE id = 1"
        ))).first()
        assert row.exit_reason == "take_profit"
        assert row.closed_at is not None

        cooldown = (await session.execute(sa.text(
            "SELECT last_exit_reason, last_mtf_agreement, cooldown_until "
            "FROM live_cooldowns WHERE user_id = 1 AND symbol = 'BTCUSDT'"
        ))).first()
        assert cooldown is not None
        assert cooldown.last_exit_reason == "take_profit"
        assert cooldown.last_mtf_agreement == 5


@pytest.mark.asyncio
async def test_process_one_no_cooldown_row_when_zero_duration() -> None:
    """external_close has 0h duration → no cooldown row created."""
    engine = await _mk_engine_with_live_trades()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    pos = OpenLiveTrade(
        trade_id=1, user_id=1, symbol="BTCUSDT", direction="LONG",
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        opened_at=_NOW - timedelta(hours=2), mtf_agreement=5,
    )
    binance = MagicMock()
    # Binance reports position no longer open → EXTERNAL_CLOSE
    binance.get_position = AsyncMock(return_value=None)
    binance.aclose = AsyncMock()

    async with factory() as session:
        cls = await _process_one(
            pos=pos, binance=binance, session=session,
            settings=_settings(), now_fn=lambda: _NOW,
        )
        await session.commit()
    assert cls.exit_reason is LiveExitReason.EXTERNAL_CLOSE

    async with factory() as session:
        row = (await session.execute(sa.text(
            "SELECT exit_reason FROM live_trades WHERE id = 1"
        ))).first()
        assert row.exit_reason == "external_close"

        # No cooldown row should be created when duration == 0.
        cooldown = (await session.execute(sa.text(
            "SELECT 1 FROM live_cooldowns WHERE user_id = 1"
        ))).first()
        assert cooldown is None


@pytest.mark.asyncio
async def test_process_one_returns_none_when_still_open() -> None:
    engine = await _mk_engine_with_live_trades()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    pos = OpenLiveTrade(
        trade_id=1, user_id=1, symbol="BTCUSDT", direction="LONG",
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        opened_at=_NOW - timedelta(hours=2), mtf_agreement=5,
    )
    binance = MagicMock()
    # Price still inside bracket
    binance.get_position = AsyncMock(return_value=SimpleNamespace(entry_price=102.0))
    binance.aclose = AsyncMock()

    async with factory() as session:
        cls = await _process_one(
            pos=pos, binance=binance, session=session,
            settings=_settings(), now_fn=lambda: _NOW,
        )
        await session.commit()
    assert cls is None

    # Trade still open
    async with factory() as session:
        row = (await session.execute(sa.text(
            "SELECT closed_at FROM live_trades WHERE id = 1"
        ))).first()
        assert row.closed_at is None
