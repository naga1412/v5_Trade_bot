"""Shared fixtures for the integration test suite.

Provides:
- `bot_status_engine`: an in-memory SQLite async engine with the shadow tables
  + the SP-0.7 `users` / `impersonation_state` tables created (matches the
  schema used in test_shadow_worker.py).
- `bot_status_factory`: a session factory bound to that engine.
- `bot_status_client`: an httpx.AsyncClient pointed at the FastAPI app, with
  `get_session`, `require_cf_user`, `require_user`, and
  `current_user_or_impersonated` overridden so endpoints under
  /api/v1/bot-status and /api/v1/predict can be exercised end-to-end without
  a real database or Cloudflare Access.
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
        # SP-0.7: users + impersonation_state must exist before any handler
        # that depends on require_user is exercised. Seed user_id=1 as the
        # bootstrap admin used by all legacy single-user fixtures.
        await conn.execute(sa.text(
            "CREATE TABLE users ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "email TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL, "
            "is_admin INTEGER NOT NULL DEFAULT 0, "
            "is_active INTEGER NOT NULL DEFAULT 1, "
            "trading_mode TEXT NOT NULL DEFAULT 'manual', "
            "position_sizing_mode TEXT NOT NULL DEFAULT 'fixed', "
            "fixed_size_min_usdt REAL, fixed_size_max_usdt REAL, "
            "max_concurrent_positions INTEGER, max_leverage_cap INTEGER, "
            "binance_api_key_encrypted TEXT, binance_api_secret_encrypted TEXT, "
            "telegram_bot_token_encrypted TEXT, telegram_chat_id TEXT, "
            "totp_secret_encrypted TEXT, totp_backup_codes_encrypted TEXT, "
            "quiet_hours_start TEXT, quiet_hours_end TEXT, "
            "quiet_hours_enabled INTEGER NOT NULL DEFAULT 1, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "last_login TEXT, invited_by INTEGER, notes TEXT)"
        ))
        await conn.execute(sa.text(
            "INSERT INTO users (id, email, display_name, is_admin) "
            "VALUES (1, 'test@local', 'Test', 1)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE impersonation_state ("
            "admin_user_id INTEGER PRIMARY KEY, "
            "target_user_id INTEGER NOT NULL, "
            "started_at TEXT NOT NULL DEFAULT (datetime('now')))"
        ))
        # SP-0.7 Phase E: per-user tables include `user_id NOT NULL`. We default
        # to 1 (bootstrap admin) so legacy fixture rows that omit user_id still
        # satisfy the constraint.
        await conn.execute(sa.text(
            "CREATE TABLE shadow_open_positions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 1, "
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
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 1, "
            "symbol TEXT NOT NULL, "
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
            "user_id INTEGER NOT NULL DEFAULT 1, "
            "symbol TEXT NOT NULL, cooldown_until TEXT NOT NULL, "
            "PRIMARY KEY (user_id, symbol))"
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
        # SP-3 Phase F: universe_history is the point-in-time tradable
        # universe queried by app.data.universe.is_tradable. Seed BTC/USDT
        # so the legacy SP-0 predict-route tests (which just expect "BTC/USDT
        # is always tradable") keep passing without new fixture changes.
        await conn.execute(sa.text(
            "CREATE TABLE universe_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "exchange TEXT NOT NULL, symbol TEXT NOT NULL, "
            "asset_class TEXT NOT NULL, "
            "listed_at TEXT NOT NULL, "
            "delisted_at TEXT, "
            "last_synced_at TEXT NOT NULL, "
            "metadata TEXT, "
            "UNIQUE (exchange, symbol))"
        ))
        await conn.execute(sa.text(
            "INSERT INTO universe_history "
            "(exchange, symbol, asset_class, listed_at, last_synced_at) "
            "VALUES ('binance', 'BTC/USDT', 'crypto', "
            "'2017-08-17T00:00:00+00:00', '2026-05-01T00:00:00+00:00')"
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
    """An ASGI client with all auth deps overridden to act as user_id=1."""
    from app.auth.deps import current_user_or_impersonated, require_user
    from app.auth.models import User
    from app.db.session import get_session
    from app.deps import CFAccessUser, require_cf_user
    from app.main import app

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with bot_status_factory() as session:
            yield session

    async def _override_cf_user() -> CFAccessUser:
        return CFAccessUser(email="test@local", sub="test", raw={})

    async def _override_user() -> User:
        # Build a detached User instance matching the seed in _create_shadow_tables.
        return User(
            id=1,
            email="test@local",
            display_name="Test",
            is_admin=True,
            is_active=True,
        )

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[require_cf_user] = _override_cf_user
    app.dependency_overrides[require_user] = _override_user
    app.dependency_overrides[current_user_or_impersonated] = _override_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(require_cf_user, None)
        app.dependency_overrides.pop(require_user, None)
        app.dependency_overrides.pop(current_user_or_impersonated, None)


@pytest.fixture
def fixed_now_iso() -> str:
    """A deterministic 'now' for fixture rows."""
    return "2026-05-03T00:00:00+00:00"


# --- SP-0.7 Phase G/H shared admin/me fixtures ----------------------------


async def _create_auth_tables(engine: Any) -> None:
    """Create the SP-0.7 auth tables (users, pending_invitations, ...) and
    seed three users: admin@x.com (id=1, admin), friend@x.com (id=2),
    deact@x.com (id=3, is_active=False).
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
        # SP-1 §6.4: ml_checkpoints registry. SQLite-friendly mirror of
        # migration 0007 (no GENERATED accuracy / partial unique index —
        # both are tested in integration suites that don't need them).
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS ml_checkpoints ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "model_name TEXT NOT NULL, version TEXT NOT NULL, "
            "checkpoint_uri TEXT NOT NULL, sha256 TEXT NOT NULL, "
            "trained_at TEXT NOT NULL, "
            "train_data_window TEXT NOT NULL, "
            "eval_results TEXT NOT NULL, "
            "is_active INTEGER NOT NULL DEFAULT 0, "
            "activated_at TEXT, deactivated_at TEXT, notes TEXT, "
            "UNIQUE (model_name, version))"
        ))
        # SP-2 Phase E E5: pattern_enabled. SQLite-friendly mirror of
        # migration 0009 (BIGSERIAL → INTEGER AUTOINCREMENT, BOOLEAN → INTEGER).
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS pattern_enabled ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "pattern_id TEXT NOT NULL, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, "
            "enabled INTEGER NOT NULL DEFAULT 1, "
            "disabled_reason TEXT, "
            "updated_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "updated_by INTEGER, "
            "UNIQUE (pattern_id, symbol, timeframe))"
        ))
        # SP-3 Phase F: universe_history + adapter_health. SQLite-friendly
        # mirrors of migration 0010 (TIMESTAMPTZ → TEXT, JSONB → TEXT).
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS universe_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "exchange TEXT NOT NULL, symbol TEXT NOT NULL, "
            "asset_class TEXT NOT NULL, "
            "listed_at TEXT NOT NULL, "
            "delisted_at TEXT, "
            "last_synced_at TEXT NOT NULL, "
            "metadata TEXT, "
            "UNIQUE (exchange, symbol))"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS adapter_health ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "exchange TEXT NOT NULL, "
            "checked_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "is_healthy INTEGER NOT NULL, "
            "latency_ms INTEGER, error_message TEXT, "
            "quota_used_pct REAL)"
        ))
        # Seed via raw SQL so created_at ordering is deterministic.
        await conn.execute(sa.text(
            "INSERT INTO users (id, email, display_name, is_admin, is_active, "
            "trading_mode, position_sizing_mode, quiet_hours_enabled, "
            "created_at) VALUES "
            "(1, 'admin@x.com', 'Admin', 1, 1, 'manual', 'fixed', 1, "
            " '2026-01-01T00:00:00+00:00'), "
            "(2, 'friend@x.com', 'Friend', 0, 1, 'manual', 'fixed', 1, "
            " '2026-01-02T00:00:00+00:00'), "
            "(3, 'deact@x.com', 'Deact', 0, 0, 'manual', 'fixed', 1, "
            " '2026-01-03T00:00:00+00:00')"
        ))


@pytest_asyncio.fixture
async def auth_engine() -> AsyncIterator[Any]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _create_auth_tables(engine)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def auth_factory(auth_engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(auth_engine, expire_on_commit=False)


def _override_session_factory(
    factory: async_sessionmaker[AsyncSession],
) -> Any:
    async def _gen() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s
    return _gen


def _detached_user(
    uid: int, email: str, *, is_admin: bool, is_active: bool = True,
) -> Any:
    from datetime import datetime, timezone

    from app.auth.models import User
    return User(
        id=uid, email=email, display_name=email.split("@")[0],
        is_admin=is_admin, is_active=is_active,
        trading_mode="manual", position_sizing_mode="fixed",
        quiet_hours_enabled=True,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest_asyncio.fixture
async def admin_client(
    auth_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client acting as user_id=1 (admin)."""
    from app.auth.deps import (
        current_user_or_impersonated,
        require_admin,
        require_user,
    )
    from app.db.session import get_session
    from app.deps import CFAccessUser, require_cf_user
    from app.main import app

    user = _detached_user(1, "admin@x.com", is_admin=True)

    async def _cf() -> CFAccessUser:
        return CFAccessUser(email="admin@x.com", sub="admin", raw={})

    async def _u() -> Any:
        return user

    app.dependency_overrides[get_session] = _override_session_factory(auth_factory)
    app.dependency_overrides[require_cf_user] = _cf
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[require_admin] = _u
    app.dependency_overrides[current_user_or_impersonated] = _u
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        for d in (
            get_session, require_cf_user, require_user,
            require_admin, current_user_or_impersonated,
        ):
            app.dependency_overrides.pop(d, None)


@pytest_asyncio.fixture
async def friend_client(
    auth_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client acting as user_id=2 (non-admin friend).

    Real require_admin is left in place so admin-only routes return 403.
    """
    from app.auth.deps import current_user_or_impersonated, require_user
    from app.db.session import get_session
    from app.deps import CFAccessUser, require_cf_user
    from app.main import app

    user = _detached_user(2, "friend@x.com", is_admin=False)

    async def _cf() -> CFAccessUser:
        return CFAccessUser(email="friend@x.com", sub="friend", raw={})

    async def _u() -> Any:
        return user

    app.dependency_overrides[get_session] = _override_session_factory(auth_factory)
    app.dependency_overrides[require_cf_user] = _cf
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[current_user_or_impersonated] = _u
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        for d in (
            get_session, require_cf_user, require_user,
            current_user_or_impersonated,
        ):
            app.dependency_overrides.pop(d, None)
