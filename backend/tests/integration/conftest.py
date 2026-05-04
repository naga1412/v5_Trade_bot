"""Shared fixtures for the integration test suite.

Provides:
- `bot_status_engine`: an in-memory SQLite async engine with the shadow tables
  created (matches the schema used in test_shadow_worker.py).
- `bot_status_factory`: a session factory bound to that engine.
- `bot_status_client`: an httpx.AsyncClient pointed at the FastAPI app, with
  `get_session` and `require_cf_user` overridden so endpoints under
  /api/v1/bot-status can be exercised end-to-end without a real database
  or Cloudflare Access.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


async def _create_shadow_tables(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_open_positions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL UNIQUE, direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL, stop_loss REAL NOT NULL, "
            "take_profit REAL NOT NULL, position_size_usdt REAL NOT NULL, "
            "entry_score REAL NOT NULL, entry_confidence REAL NOT NULL, "
            "entry_atr REAL NOT NULL, bars_held INTEGER NOT NULL DEFAULT 0, "
            "opened_at TEXT NOT NULL, last_check_at TEXT NOT NULL, "
            "signal_id TEXT NOT NULL UNIQUE)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL, stop_loss REAL NOT NULL, "
            "take_profit REAL NOT NULL, position_size_usdt REAL NOT NULL, "
            "entry_score REAL NOT NULL, entry_confidence REAL NOT NULL, "
            "layer_scores TEXT NOT NULL, entry_atr REAL NOT NULL, "
            "exit_price REAL, exit_reason TEXT, pnl_pct REAL, pnl_usdt REAL, "
            "bars_held INTEGER, opened_at TEXT NOT NULL, closed_at TEXT, "
            "inputs_hash TEXT NOT NULL, model_version TEXT NOT NULL, "
            "signal_id TEXT NOT NULL UNIQUE, "
            "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE shadow_cooldowns ("
            "symbol TEXT PRIMARY KEY, cooldown_until TEXT NOT NULL)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE asset_universe ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "quote_volume_usd_24h REAL NOT NULL, "
            "rank INTEGER NOT NULL, "
            "snapshot_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "UNIQUE (symbol, snapshot_at))"
        ))


@pytest_asyncio.fixture
async def bot_status_engine() -> AsyncIterator[Any]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _create_shadow_tables(engine)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def bot_status_factory(bot_status_engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bot_status_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def bot_status_client(
    bot_status_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    """An ASGI client with get_session + require_cf_user overridden."""
    from app.db.session import get_session
    from app.deps import CFAccessUser, require_cf_user
    from app.main import app

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with bot_status_factory() as session:
            yield session

    async def _override_user() -> CFAccessUser:
        return CFAccessUser(email="test@local", sub="test", raw={})

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[require_cf_user] = _override_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(require_cf_user, None)


@pytest.fixture
def fixed_now_iso() -> str:
    """A deterministic 'now' for fixture rows."""
    return "2026-05-03T00:00:00+00:00"
