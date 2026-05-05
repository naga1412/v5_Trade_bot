"""Backward-compatible tests for app.data.universe (SP-3 Phase F).

The SP-0 hardcoded shortcut became ``async`` and DB-backed in SP-3. The
constant ``BTC_USDT`` still exports the canonical string for callers that
just need the symbol literal. Richer behaviour is exercised in
``test_universe_history.py`` against an in-memory universe_history table.
"""
from datetime import datetime, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.data.universe import BTC_USDT, is_tradable


def test_btc_usdt_constant_is_canonical_string() -> None:
    assert BTC_USDT == "BTC/USDT"


async def _seed_btc_only() -> Any:
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
            "VALUES ('binance', 'BTC/USDT', 'crypto', :l, :l)"
        ), {"l": datetime(2017, 8, 17, tzinfo=timezone.utc)})
    return engine


@pytest.mark.asyncio
async def test_btc_usdt_is_tradable_today() -> None:
    engine = await _seed_btc_only()
    now = datetime.now(timezone.utc)
    async with AsyncSession(engine) as session:
        assert (await is_tradable(session, BTC_USDT, now)) is True


@pytest.mark.asyncio
async def test_unknown_symbol_returns_false() -> None:
    engine = await _seed_btc_only()
    now = datetime.now(timezone.utc)
    async with AsyncSession(engine) as session:
        assert (await is_tradable(session, "XXX/YYY", now)) is False
