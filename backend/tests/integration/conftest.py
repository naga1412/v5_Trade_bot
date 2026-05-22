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
            "symbol TEXT NOT NULL, "
            # PR3 alembic 0021: timeframe + (symbol, timeframe) UNIQUE
            "timeframe TEXT NOT NULL DEFAULT '1h', "
            "direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL, stop_loss REAL NOT NULL, "
            "take_profit REAL NOT NULL, position_size_usdt REAL NOT NULL, "
            "entry_score REAL NOT NULL, entry_confidence REAL NOT NULL, "
            "entry_atr REAL NOT NULL, bars_held INTEGER NOT NULL DEFAULT 0, "
            "opened_at TEXT NOT NULL, last_check_at TEXT NOT NULL, "
            "signal_id TEXT NOT NULL UNIQUE, "
            # PR-PLUMBING-1 alembic 0025: 7 PR1 analytics columns. NULLs
            # for pre-fix restart-survivor rows; populated by shadow_worker
            # for new same-session opens via persist_open_position.
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
            "mtf_directions_json TEXT, p_win REAL, effective_score REAL, "
            "realized_vol_20d REAL, funding_directional_adj REAL, "
            "UNIQUE (symbol, timeframe))"
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
            # PR3 G1: recording-only scaling columns (NULL when scaling OFF)
            "hold_scaling_factor REAL, hold_timeout_bars INTEGER, "
            # PR-strategy-1: PR1 analytics columns now populated.
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
            "mtf_directions_json TEXT, p_win REAL, effective_score REAL, "
            "realized_vol_20d REAL, funding_directional_adj REAL, "
            "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE shadow_cooldowns ("
            "user_id INTEGER NOT NULL DEFAULT 1, "
            "symbol TEXT NOT NULL, "
            # PR3 alembic 0021: timeframe in composite PK
            "timeframe TEXT NOT NULL DEFAULT '1h', "
            "cooldown_until TEXT NOT NULL, "
            "PRIMARY KEY (user_id, symbol, timeframe))"
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
        # SP-6 Phase A2: predictions table mirror so scanner radar tests can
        # seed per-symbol prediction rows. Schema mirrors the post-SP-5
        # production layout: id, user_id (NOT NULL), symbol, timeframe, ts,
        # price, layer_scores (TEXT/JSON), inputs_hash. Hash-chain columns
        # omitted (this fixture exercises read-only endpoints).
        await conn.execute(sa.text(
            "CREATE TABLE predictions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, "
            "ts TEXT NOT NULL, "
            "price REAL, "
            "layer_scores TEXT NOT NULL, "
            # Mirrors the prod migration 0002 columns. Required so the
            # scanner radar endpoint can read direction/final_score/
            # confidence directly from the row instead of digging into
            # layer_scores JSONB (the latter is unsafe when the 'final'
            # key is a scalar float — see scanner.py:_build_card).
            "direction TEXT, "
            "final_score REAL, "
            "confidence REAL, "
            "inputs_hash TEXT NOT NULL DEFAULT '')"
        ))
        # SP-7 Phase B4: backtests table. SQLite-friendly mirror of
        # migration 0012 (BIGSERIAL -> INTEGER AUTOINCREMENT,
        # JSONB -> TEXT, TIMESTAMPTZ -> TEXT). Used by the persistence
        # helper test in tests/integration/test_backtests_persisted.py
        # and the admin REST endpoint tests.
        await conn.execute(sa.text(
            "CREATE TABLE backtests ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "triggered_by INTEGER, "
            "triggered_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, "
            "start_ts TEXT NOT NULL, "
            "end_ts TEXT NOT NULL, "
            "layer_weights TEXT, "
            "enabled_layers TEXT, "
            "enabled_traps TEXT, "
            "initial_balance REAL NOT NULL, "
            "n_trades INTEGER NOT NULL DEFAULT 0, "
            "win_rate REAL, "
            "profit_factor REAL, "
            "sharpe REAL, "
            "max_drawdown REAL, "
            "equity_curve_uri TEXT, "
            "trade_log_uri TEXT, "
            "params_hash TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'completed', "
            "error_message TEXT)"
        ))
        # SP-7 Phase C3: hyperopt_studies table. SQLite-friendly mirror of
        # migration 0012 (BIGSERIAL -> INTEGER AUTOINCREMENT, TSTZRANGE
        # -> TEXT, JSONB -> TEXT, TIMESTAMPTZ -> TEXT). The status CHECK
        # constraint is preserved verbatim (SQLite honours it).
        await conn.execute(sa.text(
            "CREATE TABLE hyperopt_studies ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "triggered_by INTEGER, "
            "triggered_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "completed_at TEXT, "
            "n_trials INTEGER NOT NULL, "
            "train_window TEXT NOT NULL, "
            "val_window TEXT NOT NULL, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, "
            "best_weights TEXT, "
            "best_sharpe REAL, "
            "mlflow_run_id TEXT, "
            "status TEXT NOT NULL "
            "  CHECK (status IN ('running','completed','failed')), "
            "error_message TEXT)"
        ))
        # SP-9 Phase E3: news_items SQLite mirror so integration tests that
        # exercise build_prediction(..., session=...) can seed news rows the
        # L9 layer reads. Mirror of migration 0013's SQLite branch:
        # affected_assets is a JSON-encoded TEXT column (no PG array).
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS news_items ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT NOT NULL, "
            "url TEXT UNIQUE NOT NULL, "
            "title TEXT NOT NULL, "
            "body TEXT, "
            "published_at TIMESTAMP NOT NULL, "
            "fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "sentiment_score REAL, "
            "sentiment_label TEXT, "
            "sentiment_confidence REAL, "
            "impact_score REAL, "
            "category TEXT, "
            "affected_assets TEXT)"
        ))
        # SP-3.5: intermarket_snapshots SQLite mirror so integration tests
        # that exercise the predictor's _build_trap_context(..., session=...)
        # path can seed funding + OI rows. Mirror of migration 0014.
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS intermarket_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "captured_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "funding_rate REAL, "
            "mark_price REAL, "
            "open_interest REAL, "
            "source TEXT NOT NULL "
            "  CHECK (source IN ('binance_futures', 'bybit')))"
        ))
        # PR8: live_cooldowns SQLite mirror so /bot-status/cooldowns
        # integration tests work without alembic. Mirror of migration 0022.
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS live_cooldowns ("
            "user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, "
            "cooldown_until TEXT NOT NULL, "
            "last_exit_reason TEXT NOT NULL, "
            "last_mtf_agreement INTEGER, "
            "updated_at TEXT NOT NULL, "
            "PRIMARY KEY (user_id, symbol))"
        ))
        # FU-33: symbol_halt_state SQLite mirror so shadow_worker tests
        # that exercise _maybe_open_position / _maybe_close_position work
        # without alembic. Mirror of alembic 0026_symbol_halt_state.
        # No rows seeded — guard is default-OFF; check_slippage's flag
        # gate keeps the table read-only when SLIPPAGE_GUARD_ENABLED=False.
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS symbol_halt_state ("
            "symbol TEXT PRIMARY KEY, "
            "halted_until TEXT NOT NULL, "
            "reason TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        ))
        # PR10: symbol_performance_snapshots SQLite mirror so the
        # /bot-status/symbol-allowlist endpoint's integration tests can
        # seed snapshot rows. Mirror of the PR10 Phase 5 migration.
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS symbol_performance_snapshots ("
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
        # SP-4 §6.4: rl_checkpoints registry. SQLite-friendly mirror of
        # migration 0015 (no partial unique index on the SQLite branch).
        # Used by the admin_rl REST tests in test_api_admin_rl_checkpoints.py.
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS rl_checkpoints ("
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
        # SP-5 Phase F2: trap_enabled. SQLite-friendly mirror of migration
        # 0011 (BIGSERIAL → INTEGER AUTOINCREMENT, BOOLEAN → INTEGER,
        # TIMESTAMPTZ → TEXT).
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS trap_enabled ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "trap_id TEXT NOT NULL, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, "
            "enabled INTEGER NOT NULL DEFAULT 1, "
            "disabled_reason TEXT, "
            "updated_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "updated_by INTEGER, "
            "UNIQUE (trap_id, symbol, timeframe))"
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
        # SP-8 Phase J: shadow_trades + mode_change_log. Needed by the
        # PATCH /me/trading-mode tests (compute_gates_from_db reads
        # shadow_trades, set_mode appends to mode_change_log via the
        # audit hash chain).
        # DEFAULT-fill the NOT NULL columns so existing tests that
        # INSERT with only a subset of columns (test_api_admin_audit_trail
        # in particular) keep working unchanged.
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 1, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL DEFAULT 0, "
            "stop_loss REAL NOT NULL DEFAULT 0, "
            "take_profit REAL NOT NULL DEFAULT 0, "
            "position_size_usdt REAL NOT NULL DEFAULT 0, "
            "entry_score REAL NOT NULL DEFAULT 0, "
            "entry_confidence REAL NOT NULL DEFAULT 0, "
            "layer_scores TEXT NOT NULL DEFAULT '{}', "
            "entry_atr REAL NOT NULL DEFAULT 0, "
            "exit_price REAL, exit_reason TEXT, pnl_pct REAL, pnl_usdt REAL, "
            "bars_held INTEGER, opened_at TEXT NOT NULL, closed_at TEXT, "
            "inputs_hash TEXT NOT NULL DEFAULT '', "
            "model_version TEXT NOT NULL DEFAULT 'sp-0', "
            "signal_id TEXT NOT NULL UNIQUE, "
            "prev_hash TEXT NOT NULL DEFAULT '', "
            "row_hash TEXT NOT NULL UNIQUE)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS mode_change_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "old_mode TEXT NOT NULL, new_mode TEXT NOT NULL, "
            "triggered_by TEXT NOT NULL, reason TEXT, "
            "gate_snapshot TEXT, changed_at TEXT NOT NULL, "
            "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS kill_switch_state ("
            "user_id INTEGER NOT NULL, switch_name TEXT NOT NULL, "
            "enabled INTEGER NOT NULL DEFAULT 1, "
            "threshold_value REAL, "
            "is_tripped INTEGER NOT NULL DEFAULT 0, "
            "tripped_at TEXT, tripped_reason TEXT, "
            "updated_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "PRIMARY KEY (user_id, switch_name))"
        ))
        # SP-8 §8: tax_events. SQLite-friendly mirror of migration 0016
        # (BIGSERIAL → INTEGER AUTOINCREMENT, BIGINT → INTEGER,
        # TIMESTAMPTZ → TEXT, no FK enforcement).
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS tax_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "trade_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, direction TEXT NOT NULL, "
            "quantity REAL NOT NULL, "
            "entry_price REAL NOT NULL, exit_price REAL NOT NULL, "
            "entry_value_inr REAL NOT NULL, exit_value_inr REAL NOT NULL, "
            "realized_pnl_inr REAL NOT NULL, tds_owed_inr REAL NOT NULL, "
            "fee_paid_inr REAL NOT NULL DEFAULT 0, "
            "leverage INTEGER NOT NULL, "
            "exchange TEXT NOT NULL DEFAULT 'binance', "
            "fy_year TEXT NOT NULL, "
            "closed_at TEXT NOT NULL, fifo_match_id INTEGER, "
            "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
        ))
        # SP-7 Phase B4/B5: backtests table. SQLite-friendly mirror of
        # migration 0012 (BIGSERIAL -> INTEGER AUTOINCREMENT,
        # JSONB -> TEXT, TIMESTAMPTZ -> TEXT). Used by the admin REST
        # endpoint tests in test_api_admin_backtest.py.
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS backtests ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "triggered_by INTEGER, "
            "triggered_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, "
            "start_ts TEXT NOT NULL, "
            "end_ts TEXT NOT NULL, "
            "layer_weights TEXT, "
            "enabled_layers TEXT, "
            "enabled_traps TEXT, "
            "initial_balance REAL NOT NULL, "
            "n_trades INTEGER NOT NULL DEFAULT 0, "
            "win_rate REAL, "
            "profit_factor REAL, "
            "sharpe REAL, "
            "max_drawdown REAL, "
            "equity_curve_uri TEXT, "
            "trade_log_uri TEXT, "
            "params_hash TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'completed', "
            "error_message TEXT)"
        ))
        # SP-7 Phase C3/C4: hyperopt_studies table. SQLite-friendly mirror
        # of migration 0012 (BIGSERIAL -> INTEGER AUTOINCREMENT, TSTZRANGE
        # -> TEXT, JSONB -> TEXT, TIMESTAMPTZ -> TEXT). Used by the admin
        # REST endpoint tests in test_api_admin_hyperopt.py.
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS hyperopt_studies ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "triggered_by INTEGER, "
            "triggered_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "completed_at TEXT, "
            "n_trials INTEGER NOT NULL, "
            "train_window TEXT NOT NULL, "
            "val_window TEXT NOT NULL, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, "
            "best_weights TEXT, "
            "best_sharpe REAL, "
            "mlflow_run_id TEXT, "
            "status TEXT NOT NULL "
            "  CHECK (status IN ('running','completed','failed')), "
            "error_message TEXT)"
        ))
        # SP-9 Phase F5: news_items SQLite mirror so admin_news REST tests
        # can seed/list rows via the admin_client. Mirror of migration 0013's
        # SQLite branch — affected_assets is a JSON-encoded TEXT column.
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS news_items ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT NOT NULL, "
            "url TEXT UNIQUE NOT NULL, "
            "title TEXT NOT NULL, "
            "body TEXT, "
            "published_at TIMESTAMP NOT NULL, "
            "fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "sentiment_score REAL, "
            "sentiment_label TEXT, "
            "sentiment_confidence REAL, "
            "impact_score REAL, "
            "category TEXT, "
            "affected_assets TEXT)"
        ))
        # SP-3.5 Phase E2: intermarket_snapshots SQLite mirror for the
        # GET /api/v1/intermarket/{symbol} REST tests via admin_client.
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS intermarket_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "captured_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "funding_rate REAL, "
            "mark_price REAL, "
            "open_interest REAL, "
            "source TEXT NOT NULL "
            "  CHECK (source IN ('binance_futures', 'bybit')))"
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
