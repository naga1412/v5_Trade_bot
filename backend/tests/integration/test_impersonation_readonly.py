"""Phase L3: impersonation is strictly read-only.

Spec §9.2 — when an admin starts an impersonation session against user B:
  * Read endpoints (gated by `current_user_or_impersonated`) return B's data.
  * Write endpoints (gated by `require_user`) reject with HTTP 403 because
    the /me handlers explicitly call `_reject_during_impersonation`.

This test seeds shadow_trades for both admin (id=1) and friend (id=2),
starts impersonation, then asserts:
  1. POST /me/binance-keys returns 403 (mutation rejected).
  2. user B's `binance_api_key_encrypted` remains NULL (no leakage either way).
  3. GET /bot-status/recent-trades returns *only* B's trades (lens applied).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.models import User
from app.db.audit import insert_with_chain


# --- Engine: auth tables + shadow tables in one in-memory SQLite ----------


async def _create_combined_tables(engine: Any) -> None:
    """Create auth + impersonation + shadow_trades in one engine.

    Mirrors what the production Alembic chain produces, but in-process so
    the impersonation lensing test can exercise both auth deps and the
    bot-status read path against a single session factory.
    """
    from app.auth.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS impersonation_state ("
            "admin_user_id INTEGER PRIMARY KEY, "
            "target_user_id INTEGER NOT NULL, "
            "started_at TEXT NOT NULL DEFAULT (datetime('now')))"
        ))
        await conn.execute(sa.text(
            "INSERT INTO users (id, email, display_name, is_admin, is_active, "
            "trading_mode, position_sizing_mode, quiet_hours_enabled, "
            "created_at) VALUES "
            "(1, 'admin@x.com', 'Admin', 1, 1, 'manual', 'fixed', 1, "
            " '2026-01-01T00:00:00+00:00'), "
            "(2, 'friend@x.com', 'Friend', 0, 1, 'manual', 'fixed', 1, "
            " '2026-01-02T00:00:00+00:00')"
        ))
        # Hash-chained shadow_trades — schema mirrors the bot_status_engine
        # fixture in conftest.py.
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, direction TEXT NOT NULL, "
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


@pytest_asyncio.fixture
async def combined_engine() -> AsyncIterator[Any]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _create_combined_tables(engine)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def combined_factory(
    combined_engine: Any,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(combined_engine, expire_on_commit=False)


# --- Seed helpers ----------------------------------------------------------


def _trade(*, user_id: int, signal_id: str, closed_at: datetime) -> dict[str, Any]:
    opened_at = closed_at - timedelta(hours=4)
    return {
        "user_id": user_id,
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "direction": "LONG",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "position_size_usdt": 30.0,
        "entry_score": 0.6,
        "entry_confidence": 0.7,
        "layer_scores": json.dumps({}),
        "entry_atr": 1.0,
        "exit_price": 110.0,
        "exit_reason": "TAKE_PROFIT",
        "pnl_pct": 10.0,
        "pnl_usdt": 3.0,
        "bars_held": 4,
        "opened_at": opened_at.isoformat(),
        "closed_at": closed_at.isoformat(),
        "inputs_hash": "h" * 64,
        "model_version": "sp-0.7",
        "signal_id": signal_id,
    }


async def _seed_trade_pair(factory: async_sessionmaker[AsyncSession]) -> None:
    """One trade for admin (id=1), one for friend (id=2)."""
    now = datetime.now(UTC)
    async with factory() as s:
        await insert_with_chain(s, "shadow_trades", _trade(
            user_id=1, signal_id="admin-trade", closed_at=now - timedelta(hours=2),
        ))
        await insert_with_chain(s, "shadow_trades", _trade(
            user_id=2, signal_id="friend-trade", closed_at=now - timedelta(hours=1),
        ))
        await s.commit()


# --- Test ------------------------------------------------------------------


def _admin_user_obj() -> User:
    return User(
        id=1, email="admin@x.com", display_name="Admin",
        is_admin=True, is_active=True,
        trading_mode="manual", position_sizing_mode="fixed",
        quiet_hours_enabled=True,
    )


def _install_admin_actual_overrides(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Override require_cf_user + require_user to act as admin.

    Critically does NOT override `current_user_or_impersonated`, so the real
    dep runs — which consults `impersonation_state` and returns the target
    user when impersonation is active.
    """
    from app.auth.deps import current_user_or_impersonated, require_admin
    from app.auth.deps import require_user
    from app.db.session import get_session
    from app.deps import CFAccessUser, require_cf_user
    from app.main import app

    async def _gen_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async def _cf() -> CFAccessUser:
        return CFAccessUser(email="admin@x.com", sub="admin", raw={})

    admin = _admin_user_obj()

    async def _u() -> User:
        return admin

    # Drop any prior current_user_or_impersonated override so the REAL dep runs.
    app.dependency_overrides.pop(current_user_or_impersonated, None)

    app.dependency_overrides[get_session] = _gen_session
    app.dependency_overrides[require_cf_user] = _cf
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[require_admin] = _u


def _clear_overrides() -> None:
    from app.auth.deps import (
        current_user_or_impersonated,
        require_admin,
        require_user,
    )
    from app.db.session import get_session
    from app.deps import require_cf_user
    from app.main import app

    for d in (
        get_session, require_cf_user, require_user,
        require_admin, current_user_or_impersonated,
    ):
        app.dependency_overrides.pop(d, None)


@pytest.mark.asyncio
async def test_impersonation_is_readonly_and_lenses_data(
    combined_factory: async_sessionmaker[AsyncSession],
) -> None:
    """End-to-end: start impersonation, write blocked, reads see B's lens."""
    from app.main import app

    await _seed_trade_pair(combined_factory)
    _install_admin_actual_overrides(combined_factory)

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            # --- Step 1: admin starts impersonation of user 2. ---
            r = await c.post("/api/v1/admin/impersonate/2")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["admin_user_id"] == 1
            assert body["target_user_id"] == 2

            # --- Step 2: write attempt during impersonation -> 403. ---
            r2 = await c.post(
                "/api/v1/me/binance-keys",
                json={"api_key": "leaked-key", "api_secret": "leaked-secret"},
            )
            assert r2.status_code == 403, r2.text

            # --- Step 3: friend's binance keys remain NULL. ---
            async with combined_factory() as s:
                friend = await s.get(User, 2)
                assert friend is not None
                assert friend.binance_api_key_encrypted is None
                assert friend.binance_api_secret_encrypted is None
                # Defensive: admin's keys also remain NULL because the write
                # was rejected before reaching the persistence layer.
                admin = await s.get(User, 1)
                assert admin is not None
                assert admin.binance_api_key_encrypted is None

            # --- Step 4: read endpoint returns B's data lens. ---
            r3 = await c.get("/api/v1/bot-status/recent-trades")
            assert r3.status_code == 200, r3.text
            trades = r3.json()
            assert len(trades) == 1, (
                f"impersonating B should see exactly B's 1 trade, got {trades!r}"
            )
            assert trades[0]["signal_id"] == "friend-trade"
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_no_impersonation_returns_admin_lens(
    combined_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Sanity: with no impersonation set, admin sees only admin's trades."""
    from app.main import app

    await _seed_trade_pair(combined_factory)
    _install_admin_actual_overrides(combined_factory)

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/v1/bot-status/recent-trades")
            assert r.status_code == 200, r.text
            trades = r.json()
            assert len(trades) == 1
            assert trades[0]["signal_id"] == "admin-trade"
    finally:
        _clear_overrides()
