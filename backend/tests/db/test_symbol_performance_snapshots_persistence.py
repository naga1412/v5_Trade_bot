"""PR10 persistence — insert via audit chain + load latest per symbol."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.symbol_performance_snapshots import (
    insert_snapshot_row,
    load_latest_snapshots_per_symbol,
)


_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


async def _mk_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE symbol_performance_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "window_start TEXT NOT NULL, "
            "window_end TEXT NOT NULL, "
            "trades_count INTEGER NOT NULL, "
            "win_rate REAL, sharpe REAL, "
            "allowed INTEGER NOT NULL, "
            "computed_at TEXT NOT NULL, "
            "prev_hash TEXT NOT NULL, "
            "row_hash TEXT NOT NULL UNIQUE, "
            "inputs_hash TEXT)"
        ))
    return engine


@pytest.mark.asyncio
async def test_insert_and_load_back() -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await insert_snapshot_row(
            s, symbol="BTCUSDT",
            window_start=_NOW - timedelta(days=30), window_end=_NOW,
            trades_count=42, win_rate=0.45, sharpe=1.2,
            allowed=True, computed_at=_NOW,
        )
        await s.commit()
    async with factory() as s:
        rows = await load_latest_snapshots_per_symbol(s)
    assert "BTCUSDT" in rows
    assert rows["BTCUSDT"].trades_count == 42
    assert abs(rows["BTCUSDT"].sharpe - 1.2) < 1e-9
    assert rows["BTCUSDT"].allowed is True


@pytest.mark.asyncio
async def test_load_returns_latest_only() -> None:
    """Two snapshots for same symbol -> load returns the newer one."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    older = _NOW - timedelta(days=1)
    async with factory() as s:
        await insert_snapshot_row(
            s, symbol="BTCUSDT",
            window_start=older - timedelta(days=30), window_end=older,
            trades_count=10, win_rate=0.30, sharpe=-1.0,
            allowed=False, computed_at=older,
        )
        await insert_snapshot_row(
            s, symbol="BTCUSDT",
            window_start=_NOW - timedelta(days=30), window_end=_NOW,
            trades_count=20, win_rate=0.55, sharpe=1.5,
            allowed=True, computed_at=_NOW,
        )
        await s.commit()
    async with factory() as s:
        rows = await load_latest_snapshots_per_symbol(s)
    assert rows["BTCUSDT"].trades_count == 20  # newer one


@pytest.mark.asyncio
async def test_empty_table_returns_empty_dict() -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        rows = await load_latest_snapshots_per_symbol(s)
    assert rows == {}


@pytest.mark.asyncio
async def test_multi_symbol_returns_per_symbol_latest() -> None:
    """Two symbols × two snapshots each → load returns the newer per symbol."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    older = _NOW - timedelta(days=1)
    async with factory() as s:
        # BTCUSDT: older=10 trades, newer=20 trades
        await insert_snapshot_row(
            s, symbol="BTCUSDT",
            window_start=older - timedelta(days=30), window_end=older,
            trades_count=10, win_rate=0.30, sharpe=-1.0,
            allowed=False, computed_at=older,
        )
        await insert_snapshot_row(
            s, symbol="BTCUSDT",
            window_start=_NOW - timedelta(days=30), window_end=_NOW,
            trades_count=20, win_rate=0.55, sharpe=1.5,
            allowed=True, computed_at=_NOW,
        )
        # ETHUSDT: older=5 trades, newer=15 trades
        await insert_snapshot_row(
            s, symbol="ETHUSDT",
            window_start=older - timedelta(days=30), window_end=older,
            trades_count=5, win_rate=0.40, sharpe=0.1,
            allowed=True, computed_at=older,
        )
        await insert_snapshot_row(
            s, symbol="ETHUSDT",
            window_start=_NOW - timedelta(days=30), window_end=_NOW,
            trades_count=15, win_rate=0.60, sharpe=2.0,
            allowed=True, computed_at=_NOW,
        )
        await s.commit()
    async with factory() as s:
        rows = await load_latest_snapshots_per_symbol(s)
    assert set(rows.keys()) == {"BTCUSDT", "ETHUSDT"}
    assert rows["BTCUSDT"].trades_count == 20
    assert rows["ETHUSDT"].trades_count == 15
    assert abs(rows["ETHUSDT"].sharpe - 2.0) < 1e-9
