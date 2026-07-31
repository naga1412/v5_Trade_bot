"""Per-symbol open-position gate — unit tests for the helper.

The dispatcher-level integration lives in `test_trading_dispatcher.py`;
these tests exercise only `get_open_position_trade_id` in isolation.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.trading.execution.symbol_position_gate import (
    get_open_position_trade_id,
)


async def _mk_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE live_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, symbol TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'pending', "
            "closed_at TEXT)"
        ))
    return engine


async def _insert(session, *, user_id, symbol, status, closed_at=None):
    await session.execute(sa.text(
        "INSERT INTO live_trades (user_id, symbol, status, closed_at) "
        "VALUES (:u, :s, :st, :c)"
    ), {"u": user_id, "s": symbol, "st": status, "c": closed_at})
    await session.commit()


@pytest.mark.asyncio
async def test_returns_none_when_no_rows() -> None:
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        assert await get_open_position_trade_id(
            s, user_id=1, symbol="BTC/USDT",
        ) is None


@pytest.mark.asyncio
async def test_returns_trade_id_when_open_row_exists() -> None:
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        await _insert(s, user_id=1, symbol="BTC/USDT", status="open")
        got = await get_open_position_trade_id(
            s, user_id=1, symbol="BTC/USDT",
        )
    assert got == 1


@pytest.mark.asyncio
async def test_ignores_closed_row() -> None:
    """`status='closed'` must not gate a new entry."""
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        await _insert(
            s, user_id=1, symbol="BTC/USDT",
            status="closed", closed_at="2026-05-27T00:00:00Z",
        )
        assert await get_open_position_trade_id(
            s, user_id=1, symbol="BTC/USDT",
        ) is None


@pytest.mark.asyncio
async def test_ignores_pending_row() -> None:
    """`status='pending'` (pre-Binance-ack) also must not gate.

    Rationale: `pending` means we've inserted a placeholder row before
    submitting the market order. The dispatcher never reaches this
    check twice for the same in-flight signal (dispatch → phase1 is
    within a single session-scoped call), and the source-of-truth
    predicate matches build_user_context (status='open' only).
    """
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        await _insert(s, user_id=1, symbol="BTC/USDT", status="pending")
        assert await get_open_position_trade_id(
            s, user_id=1, symbol="BTC/USDT",
        ) is None


@pytest.mark.asyncio
async def test_ignores_different_symbol() -> None:
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        await _insert(s, user_id=1, symbol="ETH/USDT", status="open")
        assert await get_open_position_trade_id(
            s, user_id=1, symbol="BTC/USDT",
        ) is None


@pytest.mark.asyncio
async def test_ignores_different_user() -> None:
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        await _insert(s, user_id=2, symbol="BTC/USDT", status="open")
        assert await get_open_position_trade_id(
            s, user_id=1, symbol="BTC/USDT",
        ) is None


@pytest.mark.asyncio
async def test_fails_open_on_db_error() -> None:
    """Any DB exception returns None (max-concurrent gate is backstop)."""
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        # Drop the table AFTER the session is bound to force an error.
        await s.execute(sa.text("DROP TABLE live_trades"))
        await s.commit()
        # Should not raise, should return None.
        assert await get_open_position_trade_id(
            s, user_id=1, symbol="BTC/USDT",
        ) is None
