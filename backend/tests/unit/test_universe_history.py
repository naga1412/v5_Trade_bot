"""Tests for SP-3 DB-backed is_tradable() (replaces SP-0 hardcoded shortcut)."""
from datetime import datetime, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.data.universe import is_tradable


async def _seed_table_and_session() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE universe_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "exchange TEXT NOT NULL, symbol TEXT NOT NULL, "
            "asset_class TEXT NOT NULL, "
            "listed_at TIMESTAMP NOT NULL, "
            "delisted_at TIMESTAMP, "
            "last_synced_at TIMESTAMP NOT NULL, "
            "metadata TEXT, "
            "UNIQUE (exchange, symbol))"
        ))
        await conn.execute(sa.text(
            "INSERT INTO universe_history "
            "(exchange, symbol, asset_class, listed_at, last_synced_at) "
            "VALUES "
            "('binance', 'BTC/USDT', 'crypto', :btc_listed, :now), "
            "('binance', 'LUNA/USDT', 'crypto', :luna_listed, :now)"
        ), {
            "btc_listed": datetime(2017, 8, 17, tzinfo=timezone.utc),
            "luna_listed": datetime(2020, 8, 1, tzinfo=timezone.utc),
            "now": datetime(2026, 5, 1, tzinfo=timezone.utc),
        })
        await conn.execute(sa.text(
            "UPDATE universe_history SET delisted_at = :d "
            "WHERE symbol='LUNA/USDT'"
        ), {"d": datetime(2022, 5, 12, tzinfo=timezone.utc)})
    return engine


@pytest.mark.asyncio
async def test_btc_listed_today_is_tradable() -> None:
    engine = await _seed_table_and_session()
    async with AsyncSession(engine) as session:
        assert (await is_tradable(
            session, "BTC/USDT", datetime(2026, 1, 1, tzinfo=timezone.utc),
        )) is True


@pytest.mark.asyncio
async def test_luna_before_listing_is_not_tradable() -> None:
    engine = await _seed_table_and_session()
    async with AsyncSession(engine) as session:
        assert (await is_tradable(
            session, "LUNA/USDT", datetime(2018, 1, 1, tzinfo=timezone.utc),
        )) is False


@pytest.mark.asyncio
async def test_luna_during_listing_window_is_tradable() -> None:
    engine = await _seed_table_and_session()
    async with AsyncSession(engine) as session:
        assert (await is_tradable(
            session, "LUNA/USDT", datetime(2022, 4, 1, tzinfo=timezone.utc),
        )) is True


@pytest.mark.asyncio
async def test_luna_after_delisting_is_not_tradable() -> None:
    """Spec §11 acceptance: is_tradable('LUNA/USDT', '2024-01-01') is False."""
    engine = await _seed_table_and_session()
    async with AsyncSession(engine) as session:
        assert (await is_tradable(
            session, "LUNA/USDT", datetime(2024, 1, 1, tzinfo=timezone.utc),
        )) is False


@pytest.mark.asyncio
async def test_unknown_symbol_is_not_tradable() -> None:
    engine = await _seed_table_and_session()
    async with AsyncSession(engine) as session:
        assert (await is_tradable(
            session, "DOES/NOTEXIST",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )) is False


@pytest.mark.asyncio
async def test_any_exchange_listing_is_sufficient() -> None:
    """Spec §2 #10: tradable if ANY exchange has a matching row."""
    engine = await _seed_table_and_session()
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO universe_history "
            "(exchange, symbol, asset_class, listed_at, last_synced_at) "
            "VALUES ('yahoo', 'AAPL', 'stock', :l, :l)"
        ), {"l": datetime(2000, 1, 1, tzinfo=timezone.utc)})
    async with AsyncSession(engine) as session:
        assert (await is_tradable(
            session, "AAPL", datetime(2026, 1, 1, tzinfo=timezone.utc),
        )) is True
