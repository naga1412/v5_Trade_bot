"""SP-8 trading mode infrastructure: live_trades, tax_events, mode_change_log,
hardware_confirms, kill_switch_state, telegram_signals, fx_rates.

Revision ID: 0016_sp8_trading
Revises: 0015_rl_brain
Create Date: 2026-05-09

Spec: docs/superpowers/specs/2026-05-03-autonomous-trading-design.md §14.

Hash-chained tables (live_trades, tax_events, mode_change_log,
hardware_confirms) get the same prev_hash/row_hash columns as
predictions/paper_trades/brain_decisions so SP-7's verify_chain
worker picks them up automatically.

NOTE on users columns: SP-8 spec §14 calls for trading_mode,
totp_secret_encrypted, and telegram_chat_id on users. Migration 0004
already added all three (plus several more SP-8-shaped columns:
position_sizing_mode, fixed_size_min/max_usdt, max_concurrent_positions,
max_leverage_cap, binance_api_key/secret_encrypted,
telegram_bot_token_encrypted, totp_backup_codes_encrypted, quiet_hours_*).
This migration only adds the new TABLES; the users columns belong to 0004.

Cross-cutting compliance: same dialect-aware DDL pattern as 0015.
SQLite path is intentionally minimal — full schema is Postgres-only;
SQLite tests only need the tables to exist with the columns the
tests actually exercise.
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0016_sp8_trading"
down_revision: str | None = "0015_rl_brain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # users columns (trading_mode, totp_secret_encrypted, telegram_chat_id)
        # are owned by migration 0004 — skipping the ALTER TABLE here so
        # this migration is idempotent against the existing prod schema.

        # ----- mode_change_log (hash-chained audit) -----
        op.execute(
            """
            CREATE TABLE mode_change_log (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                old_mode TEXT NOT NULL,
                new_mode TEXT NOT NULL,
                triggered_by TEXT NOT NULL
                    CHECK (triggered_by IN ('user', 'auto-demote', 'admin')),
                reason TEXT,
                gate_snapshot JSONB,
                changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL UNIQUE
            );
            """
        )
        op.execute(
            "CREATE INDEX mode_change_log_user_idx "
            "ON mode_change_log (user_id, changed_at DESC);"
        )

        # ----- 3. live_trades (real-money trades; mirrors paper_trades + extras) -----
        op.execute(
            """
            CREATE TABLE live_trades (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
                margin_usdt DOUBLE PRECISION NOT NULL,
                leverage INTEGER NOT NULL CHECK (leverage BETWEEN 1 AND 125),
                position_value_usdt DOUBLE PRECISION NOT NULL,
                entry_price DOUBLE PRECISION NOT NULL,
                exit_price DOUBLE PRECISION,
                stop_loss DOUBLE PRECISION NOT NULL,
                take_profit DOUBLE PRECISION NOT NULL,
                liquidation_price DOUBLE PRECISION,
                binance_order_id TEXT NOT NULL UNIQUE,
                binance_position_id TEXT,
                opened_at TIMESTAMPTZ NOT NULL,
                closed_at TIMESTAMPTZ,
                pnl_usdt DOUBLE PRECISION,
                pnl_pct DOUBLE PRECISION,
                fees_paid_usdt DOUBLE PRECISION DEFAULT 0,
                funding_paid_usdt DOUBLE PRECISION DEFAULT 0,
                exit_reason TEXT,
                mode_at_open TEXT NOT NULL,
                approved_via TEXT
                    CHECK (approved_via IN ('telegram-button', 'auto', 'manual-button')),
                reasoning JSONB,
                inputs_hash TEXT,
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL UNIQUE
            );
            """
        )
        op.execute(
            "CREATE INDEX live_trades_user_opened_idx "
            "ON live_trades (user_id, opened_at DESC);"
        )
        op.execute(
            "CREATE INDEX live_trades_open_positions_idx "
            "ON live_trades (user_id, symbol) WHERE closed_at IS NULL;"
        )

        # ----- 4. telegram_signals (audit of every approval message) -----
        op.execute(
            """
            CREATE TABLE telegram_signals (
                id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
                sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                payload JSONB NOT NULL,
                response TEXT
                    CHECK (response IN ('approved', 'skipped', 'timeout', 'error')),
                response_at TIMESTAMPTZ,
                response_leverage INTEGER,
                resulted_in_trade_id BIGINT REFERENCES live_trades(id)
            );
            """
        )
        op.execute(
            "CREATE INDEX telegram_signals_user_sent_idx "
            "ON telegram_signals (user_id, sent_at DESC);"
        )

        # ----- 5. hardware_confirms -----
        op.execute(
            """
            CREATE TABLE hardware_confirms (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                action TEXT NOT NULL,
                method TEXT NOT NULL CHECK (method IN ('totp', 'telegram')),
                success BOOLEAN NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        op.execute(
            "CREATE INDEX hardware_confirms_user_action_idx "
            "ON hardware_confirms (user_id, action, attempted_at DESC);"
        )

        # ----- 6. kill_switch_state -----
        op.execute(
            """
            CREATE TABLE kill_switch_state (
                user_id BIGINT NOT NULL,
                switch_name TEXT NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                threshold_value DOUBLE PRECISION,
                is_tripped BOOLEAN NOT NULL DEFAULT FALSE,
                tripped_at TIMESTAMPTZ,
                tripped_reason TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (user_id, switch_name)
            );
            """
        )

        # ----- 7. tax_events (hash-chained, FIFO-matched) -----
        op.execute(
            """
            CREATE TABLE tax_events (
                id BIGSERIAL PRIMARY KEY,
                trade_id BIGINT NOT NULL REFERENCES live_trades(id),
                user_id BIGINT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
                quantity DOUBLE PRECISION NOT NULL,
                entry_price DOUBLE PRECISION NOT NULL,
                exit_price DOUBLE PRECISION NOT NULL,
                entry_value_inr DOUBLE PRECISION NOT NULL,
                exit_value_inr DOUBLE PRECISION NOT NULL,
                realized_pnl_inr DOUBLE PRECISION NOT NULL,
                tds_owed_inr DOUBLE PRECISION NOT NULL,
                fee_paid_inr DOUBLE PRECISION NOT NULL DEFAULT 0,
                leverage INTEGER NOT NULL,
                exchange TEXT NOT NULL DEFAULT 'binance',
                fy_year TEXT NOT NULL,
                closed_at TIMESTAMPTZ NOT NULL,
                fifo_match_id BIGINT,
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL UNIQUE
            );
            """
        )
        op.execute(
            "CREATE INDEX tax_events_user_fy_idx ON tax_events (user_id, fy_year);"
        )
        op.execute(
            "CREATE INDEX tax_events_closed_at_idx ON tax_events (closed_at);"
        )

        # ----- 8. fx_rates (USD/INR cache for tax conversion) -----
        op.execute(
            """
            CREATE TABLE fx_rates (
                pair TEXT NOT NULL,
                ts TIMESTAMPTZ NOT NULL,
                rate DOUBLE PRECISION NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (pair, ts)
            );
            """
        )

    else:
        # SQLite path — minimal schema for tests that exercise these tables.
        # Production never runs on SQLite; tests that need richer constraints
        # construct their own tables in-place. The users column ALTERs that
        # spec §14 calls for are skipped: migration 0004 already added them.

        for ddl in (
            (
                "CREATE TABLE mode_change_log ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "user_id INTEGER NOT NULL, "
                "old_mode TEXT NOT NULL, new_mode TEXT NOT NULL, "
                "triggered_by TEXT NOT NULL, reason TEXT, "
                "gate_snapshot TEXT, "
                "changed_at TEXT NOT NULL DEFAULT (datetime('now')), "
                "prev_hash TEXT NOT NULL, "
                "row_hash TEXT NOT NULL UNIQUE)"
            ),
            (
                "CREATE TABLE live_trades ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "user_id INTEGER NOT NULL, symbol TEXT NOT NULL, "
                "direction TEXT NOT NULL, margin_usdt REAL NOT NULL, "
                "leverage INTEGER NOT NULL, "
                "position_value_usdt REAL NOT NULL, "
                "entry_price REAL NOT NULL, exit_price REAL, "
                "stop_loss REAL NOT NULL, take_profit REAL NOT NULL, "
                "liquidation_price REAL, "
                "binance_order_id TEXT NOT NULL UNIQUE, "
                "binance_position_id TEXT, "
                "opened_at TEXT NOT NULL, closed_at TEXT, "
                "pnl_usdt REAL, pnl_pct REAL, "
                "fees_paid_usdt REAL DEFAULT 0, "
                "funding_paid_usdt REAL DEFAULT 0, "
                "exit_reason TEXT, mode_at_open TEXT NOT NULL, "
                "approved_via TEXT, reasoning TEXT, inputs_hash TEXT, "
                "prev_hash TEXT NOT NULL, "
                "row_hash TEXT NOT NULL UNIQUE)"
            ),
            (
                "CREATE TABLE telegram_signals ("
                "id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, "
                "symbol TEXT NOT NULL, direction TEXT NOT NULL, "
                "sent_at TEXT NOT NULL DEFAULT (datetime('now')), "
                "payload TEXT NOT NULL, response TEXT, "
                "response_at TEXT, response_leverage INTEGER, "
                "resulted_in_trade_id INTEGER)"
            ),
            (
                "CREATE TABLE hardware_confirms ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "user_id INTEGER NOT NULL, action TEXT NOT NULL, "
                "method TEXT NOT NULL, success INTEGER NOT NULL, "
                "ip_address TEXT, user_agent TEXT, "
                "attempted_at TEXT NOT NULL DEFAULT (datetime('now')))"
            ),
            (
                "CREATE TABLE kill_switch_state ("
                "user_id INTEGER NOT NULL, switch_name TEXT NOT NULL, "
                "enabled INTEGER NOT NULL DEFAULT 1, "
                "threshold_value REAL, "
                "is_tripped INTEGER NOT NULL DEFAULT 0, "
                "tripped_at TEXT, tripped_reason TEXT, "
                "updated_at TEXT NOT NULL DEFAULT (datetime('now')), "
                "PRIMARY KEY (user_id, switch_name))"
            ),
            (
                "CREATE TABLE tax_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "trade_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
                "symbol TEXT NOT NULL, direction TEXT NOT NULL, "
                "quantity REAL NOT NULL, entry_price REAL NOT NULL, "
                "exit_price REAL NOT NULL, "
                "entry_value_inr REAL NOT NULL, "
                "exit_value_inr REAL NOT NULL, "
                "realized_pnl_inr REAL NOT NULL, "
                "tds_owed_inr REAL NOT NULL, "
                "fee_paid_inr REAL NOT NULL DEFAULT 0, "
                "leverage INTEGER NOT NULL, "
                "exchange TEXT NOT NULL DEFAULT 'binance', "
                "fy_year TEXT NOT NULL, closed_at TEXT NOT NULL, "
                "fifo_match_id INTEGER, "
                "prev_hash TEXT NOT NULL, "
                "row_hash TEXT NOT NULL UNIQUE)"
            ),
            (
                "CREATE TABLE fx_rates ("
                "pair TEXT NOT NULL, ts TEXT NOT NULL, "
                "rate REAL NOT NULL, source TEXT NOT NULL, "
                "PRIMARY KEY (pair, ts))"
            ),
        ):
            op.execute(ddl)


def downgrade() -> None:
    # Explicit drops (not a loop) so the source-scan test in
    # test_migration_0016_sp8_trading.py can verify each table by name.
    op.execute("DROP TABLE IF EXISTS fx_rates")
    op.execute("DROP TABLE IF EXISTS tax_events")
    op.execute("DROP TABLE IF EXISTS kill_switch_state")
    op.execute("DROP TABLE IF EXISTS hardware_confirms")
    op.execute("DROP TABLE IF EXISTS telegram_signals")
    op.execute("DROP TABLE IF EXISTS live_trades")
    op.execute("DROP TABLE IF EXISTS mode_change_log")
    # No ALTER TABLE on users — those columns are owned by migration 0004.
