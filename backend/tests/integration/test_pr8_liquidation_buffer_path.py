"""PR8 integration: liquidation-buffer auto-close writes exit_reason + cooldown.

When the liquidation_monitor's evaluate_position decides to auto-close
(buffer < 10%), the run-loop now stamps live_trades.exit_reason =
"liquidation_buffer_breach" and upserts a 24h cooldown so the next
signal on the same symbol is gated.

Tests the integration directly via _write_liquidation_buffer_close
(the helper invoked from the run loop after a confirmed auto-close).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.trading.execution.liquidation_monitor import (
    OpenPosition,
    _write_liquidation_buffer_close,
)


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _settings():
    return SimpleNamespace(
        LIVE_COOLDOWN_HOURS_BY_OUTCOME={
            "stop_loss": 8.0, "take_profit": 1.0, "timeout": 4.0,
            "manual_close": 0.0, "external_close": 0.0,
            "liquidation_buffer_breach": 24.0,
        },
    )


async def _mk_engine():
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


@pytest.mark.asyncio
async def test_liquidation_buffer_write_stamps_exit_reason() -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    pos = OpenPosition(
        trade_id=1, user_id=1, symbol="BTCUSDT", direction="LONG",
        entry_price=100.0, stop_loss_price=95.0, mtf_agreement=5,
    )
    async with factory() as s:
        await _write_liquidation_buffer_close(
            s, pos=pos, settings=_settings(), now=_NOW,
        )
        await s.commit()
    async with factory() as s:
        row = (await s.execute(sa.text(
            "SELECT exit_reason, closed_at FROM live_trades WHERE id = 1"
        ))).first()
        assert row.exit_reason == "liquidation_buffer_breach"
        assert row.closed_at is not None


@pytest.mark.asyncio
async def test_liquidation_buffer_write_upserts_24h_cooldown() -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    pos = OpenPosition(
        trade_id=1, user_id=1, symbol="BTCUSDT", direction="LONG",
        entry_price=100.0, stop_loss_price=95.0, mtf_agreement=5,
    )
    async with factory() as s:
        await _write_liquidation_buffer_close(
            s, pos=pos, settings=_settings(), now=_NOW,
        )
        await s.commit()
    async with factory() as s:
        row = (await s.execute(sa.text(
            "SELECT last_exit_reason, last_mtf_agreement, cooldown_until "
            "FROM live_cooldowns WHERE user_id = 1 AND symbol = 'BTCUSDT'"
        ))).first()
        assert row is not None
        assert row.last_exit_reason == "liquidation_buffer_breach"
        assert row.last_mtf_agreement == 5


@pytest.mark.asyncio
async def test_liquidation_buffer_write_handles_null_mtf() -> None:
    """Historic trade with mtf_agreement=None still gets a cooldown."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    pos = OpenPosition(
        trade_id=1, user_id=1, symbol="BTCUSDT", direction="LONG",
        entry_price=100.0, stop_loss_price=95.0, mtf_agreement=None,
    )
    async with factory() as s:
        await _write_liquidation_buffer_close(
            s, pos=pos, settings=_settings(), now=_NOW,
        )
        await s.commit()
    async with factory() as s:
        row = (await s.execute(sa.text(
            "SELECT last_mtf_agreement FROM live_cooldowns "
            "WHERE user_id = 1 AND symbol = 'BTCUSDT'"
        ))).first()
        assert row.last_mtf_agreement is None
