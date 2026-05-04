"""SQLAlchemy ORM models for the multi-user identity layer.

Spec §3 — `users` table holds per-user trading state and encrypted secrets.
Spec §4.3 — `pending_invitations` gates first-time-login.
Spec §4.2 case 3 — `auth_violations` is a defense-in-depth audit signal.
Spec §9.2 — `impersonation_events` is the append-only audit trail of admin
            "View as user" actions.
"""

from datetime import datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    Time,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# SQLite quirk: BIGINT PK is not auto-incrementing the way Postgres BIGSERIAL is.
# Use Integer for SQLite (test fixtures), BigInteger for Postgres (production).
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")
BigIntFK = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    trading_mode: Mapped[str] = mapped_column(
        Text, nullable=False, default="manual",
    )
    position_sizing_mode: Mapped[str] = mapped_column(
        Text, nullable=False, default="fixed",
    )
    fixed_size_min_usdt: Mapped[float | None] = mapped_column(Float, default=20.0)
    fixed_size_max_usdt: Mapped[float | None] = mapped_column(Float, default=50.0)
    max_concurrent_positions: Mapped[int | None] = mapped_column(Integer, default=5)
    max_leverage_cap: Mapped[int | None] = mapped_column(Integer, default=10)

    binance_api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    binance_api_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    telegram_bot_token_encrypted: Mapped[str | None] = mapped_column(Text)
    telegram_chat_id: Mapped[str | None] = mapped_column(Text)
    totp_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    totp_backup_codes_encrypted: Mapped[str | None] = mapped_column(Text)

    quiet_hours_start: Mapped[time | None] = mapped_column(Time, default=time(23, 0))
    quiet_hours_end: Mapped[time | None] = mapped_column(Time, default=time(7, 0))
    quiet_hours_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by: Mapped[int | None] = mapped_column(
        BigIntFK, ForeignKey("users.id"),
    )
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "trading_mode IN ('manual', 'telegram-approve', 'fully-auto')",
            name="ck_users_trading_mode",
        ),
        CheckConstraint(
            "position_sizing_mode IN ('fixed', 'percentage')",
            name="ck_users_position_sizing_mode",
        ),
    )


class PendingInvitation(Base):
    __tablename__ = "pending_invitations"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    invited_by: Mapped[int] = mapped_column(
        BigIntFK, ForeignKey("users.id"), nullable=False,
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cf_access_added: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    notes: Mapped[str | None] = mapped_column(Text)


class AuthViolation(Base):
    __tablename__ = "auth_violations"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    attempted_email: Mapped[str] = mapped_column(Text, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    jwt_sub: Mapped[str | None] = mapped_column(Text)
    request_path: Mapped[str | None] = mapped_column(Text)


class ImpersonationEvent(Base):
    __tablename__ = "impersonation_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    admin_user_id: Mapped[int] = mapped_column(
        BigIntFK, ForeignKey("users.id"), nullable=False,
    )
    target_user_id: Mapped[int] = mapped_column(
        BigIntFK, ForeignKey("users.id"), nullable=False,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    request_path: Mapped[str | None] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "action IN ('start', 'stop', 'page_view')",
            name="ck_impersonation_action",
        ),
    )
