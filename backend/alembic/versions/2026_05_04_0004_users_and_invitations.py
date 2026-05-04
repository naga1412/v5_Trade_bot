"""users + pending_invitations + auth_violations + impersonation_events + impersonation_state

Revision ID: 0004_users_and_invitations
Revises: 0003_shadow_trading
Create Date: 2026-05-04
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0004_users_and_invitations"
down_revision: str | None = "0003_shadow_trading"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE users (
            id BIGSERIAL PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            is_admin BOOLEAN NOT NULL DEFAULT FALSE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,

            trading_mode TEXT NOT NULL DEFAULT 'manual'
                CHECK (trading_mode IN ('manual', 'telegram-approve', 'fully-auto')),
            position_sizing_mode TEXT NOT NULL DEFAULT 'fixed'
                CHECK (position_sizing_mode IN ('fixed', 'percentage')),
            fixed_size_min_usdt DOUBLE PRECISION DEFAULT 20.0,
            fixed_size_max_usdt DOUBLE PRECISION DEFAULT 50.0,
            max_concurrent_positions INTEGER DEFAULT 5,
            max_leverage_cap INTEGER DEFAULT 10,

            binance_api_key_encrypted TEXT,
            binance_api_secret_encrypted TEXT,
            telegram_bot_token_encrypted TEXT,
            telegram_chat_id TEXT,
            totp_secret_encrypted TEXT,
            totp_backup_codes_encrypted TEXT,

            quiet_hours_start TIME DEFAULT '23:00',
            quiet_hours_end TIME DEFAULT '07:00',
            quiet_hours_enabled BOOLEAN NOT NULL DEFAULT TRUE,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_login TIMESTAMPTZ,
            invited_by BIGINT REFERENCES users(id),
            notes TEXT
        );
        """
    )
    op.execute("CREATE INDEX users_email_idx ON users (email);")
    op.execute(
        "CREATE INDEX users_is_active_idx ON users (is_active) WHERE is_active = TRUE;"
    )

    op.execute(
        """
        CREATE TABLE pending_invitations (
            id BIGSERIAL PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT,
            invited_by BIGINT NOT NULL REFERENCES users(id),
            is_admin BOOLEAN NOT NULL DEFAULT FALSE,
            invited_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            accepted_at TIMESTAMPTZ,
            cf_access_added BOOLEAN NOT NULL DEFAULT FALSE,
            notes TEXT
        );
        """
    )
    op.execute(
        "CREATE INDEX pending_invitations_email_idx ON pending_invitations (email);"
    )

    op.execute(
        """
        CREATE TABLE auth_violations (
            id BIGSERIAL PRIMARY KEY,
            attempted_email TEXT NOT NULL,
            attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reason TEXT NOT NULL,
            jwt_sub TEXT,
            request_path TEXT
        );
        """
    )
    op.execute(
        "CREATE INDEX auth_violations_email_at_idx "
        "ON auth_violations (attempted_email, attempted_at DESC);"
    )

    op.execute(
        """
        CREATE TABLE impersonation_events (
            id BIGSERIAL PRIMARY KEY,
            admin_user_id BIGINT NOT NULL REFERENCES users(id),
            target_user_id BIGINT NOT NULL REFERENCES users(id),
            action TEXT NOT NULL CHECK (action IN ('start', 'stop', 'page_view')),
            request_path TEXT,
            ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        "CREATE INDEX impersonation_events_admin_ts_idx "
        "ON impersonation_events (admin_user_id, ts DESC);"
    )

    # Server-state store for active impersonation (one row per admin).
    # Spec §9.2 — admin clicks "View as user" → upsert here. Cleared on Exit.
    op.execute(
        """
        CREATE TABLE impersonation_state (
            admin_user_id BIGINT PRIMARY KEY REFERENCES users(id),
            target_user_id BIGINT NOT NULL REFERENCES users(id),
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS impersonation_state;")
    op.execute("DROP TABLE IF EXISTS impersonation_events;")
    op.execute("DROP TABLE IF EXISTS auth_violations;")
    op.execute("DROP TABLE IF EXISTS pending_invitations;")
    op.execute("DROP TABLE IF EXISTS users;")
