"""Postgres-only regression tests for PR-FIX-PR264.

Verifies migration 0027_extend_telegram_signals_response_check actually
accepts the 3 new response outcomes ('stale_price', 'auto_skipped',
'approve_lost_race') that PR #264 introduced in app code but failed to
add to the inline CHECK constraint. Bug history:

  - 2026-05-25 22:27 UTC: PR #264 lands on main with new response values
  - 2026-05-25 22:00-onwards: every drift-rejected approval and every
    expired auto-skip attempt fails with CheckViolationError; rows stuck
    at 'approved' with resulted_in_trade_id NULL; auto_skip worker logs
    "DB error (failing open)" every 30s for 6+ hours
  - 2026-05-26 03:18 UTC: operator surfaces the failure mode + scope
  - 2026-05-26: this migration + the session-merge restructure in
    `_route_callback` ships as the bundled fix

These tests are CI-only (run against the Postgres service in the backend
job after `alembic upgrade head`). On SQLite they skip via the
``postgres_engine`` fixture — SQLite tests can't exercise this because
the SQLite test schema in test_telegram_polling.py uses raw
``CREATE TABLE`` with no CHECK constraint at all.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


def _get_database_url() -> str | None:
    """Return DATABASE_URL if it points at Postgres, else None."""
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("postgresql") or url.startswith("postgres"):
        return url
    return None


@pytest.fixture
async def postgres_engine():
    """Async engine bound to the CI Postgres test DB.

    Skips if DATABASE_URL is not a Postgres URL. The CI backend job runs
    ``alembic upgrade head`` against the postgres service before pytest,
    so migration 0027 is applied when these tests run.
    """
    url = _get_database_url()
    if url is None:
        pytest.skip(
            "Postgres DATABASE_URL not set — these tests verify a Postgres "
            "CHECK constraint and are CI-only. Set "
            "DATABASE_URL=postgresql+asyncpg://... to run locally."
        )
    engine = create_async_engine(url, echo=False)
    yield engine
    await engine.dispose()


def _new_signal_id() -> str:
    """Generate a unique signal_id so concurrent test runs don't collide."""
    return f"pr264-test-{uuid.uuid4().hex[:12]}"


async def _insert_signal_with_response(
    session: AsyncSession,
    *,
    signal_id: str,
    response_value: str,
    sent_at: datetime,
) -> None:
    """INSERT one telegram_signals row with the given response value.

    Raises asyncpg.exceptions.CheckViolationError if the CHECK constraint
    rejects ``response_value``. Caller asserts behavior accordingly.
    """
    await session.execute(
        sa.text(
            "INSERT INTO telegram_signals "
            "(id, user_id, symbol, direction, sent_at, payload, "
            " response, response_at) "
            "VALUES (:id, 1, 'BTC/USDT', 'LONG', :sent_at, '{}'::jsonb, "
            "        :resp, :resp_at)"
        ),
        {
            "id": signal_id,
            "sent_at": sent_at,
            "resp": response_value,
            "resp_at": sent_at + timedelta(seconds=1),
        },
    )


# ---- CHECK constraint accepts every new response outcome ----------------


@pytest.mark.asyncio
@pytest.mark.parametrize("new_response", [
    "stale_price",
    "auto_skipped",
    "approve_lost_race",
])
async def test_check_constraint_accepts_new_response_values(
    postgres_engine, new_response: str,
) -> None:
    """After migration 0027, each of the 3 new response values must be
    accepted by the telegram_signals_response_check CHECK constraint.
    Pre-migration, any of these raised CheckViolationError."""
    now = datetime.now(timezone.utc)
    sig_id = _new_signal_id()
    async with AsyncSession(postgres_engine) as s:
        await _insert_signal_with_response(
            s, signal_id=sig_id, response_value=new_response, sent_at=now,
        )
        await s.commit()
        # Read back to confirm the row is durable.
        row = (await s.execute(
            sa.text(
                "SELECT response FROM telegram_signals WHERE id=:id"
            ),
            {"id": sig_id},
        )).first()
        assert row is not None
        assert row.response == new_response
        # Cleanup so the row doesn't pollute later test runs.
        await s.execute(
            sa.text("DELETE FROM telegram_signals WHERE id=:id"),
            {"id": sig_id},
        )
        await s.commit()


# ---- CHECK constraint still rejects obviously invalid values ------------


@pytest.mark.asyncio
async def test_check_constraint_still_rejects_unknown_values(
    postgres_engine,
) -> None:
    """The migration widens the allowed ARRAY but does NOT remove the
    constraint. Garbage values must still raise CheckViolationError so a
    future typo in app code can't silently land in the column."""
    from sqlalchemy.exc import IntegrityError

    now = datetime.now(timezone.utc)
    sig_id = _new_signal_id()
    async with AsyncSession(postgres_engine) as s:
        with pytest.raises(IntegrityError):
            await _insert_signal_with_response(
                s,
                signal_id=sig_id,
                response_value="totally-bogus-state",
                sent_at=now,
            )
            await s.commit()
        await s.rollback()


# ---- Pre-existing values still accepted ---------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_response", [
    "approved",
    "skipped",
    "timeout",
    "error",
])
async def test_check_constraint_still_accepts_original_values(
    postgres_engine, existing_response: str,
) -> None:
    """The 4 original allowed values must still be accepted — the
    migration is purely additive."""
    now = datetime.now(timezone.utc)
    sig_id = _new_signal_id()
    async with AsyncSession(postgres_engine) as s:
        await _insert_signal_with_response(
            s, signal_id=sig_id, response_value=existing_response, sent_at=now,
        )
        await s.commit()
        await s.execute(
            sa.text("DELETE FROM telegram_signals WHERE id=:id"),
            {"id": sig_id},
        )
        await s.commit()


# ---- auto_skip_expired_signals real-Postgres regression -----------------


@pytest.mark.asyncio
async def test_auto_skip_writes_to_postgres_without_check_violation(
    postgres_engine,
) -> None:
    """The auto-skip worker UPDATE writes response='auto_skipped'. Before
    migration 0027 this raised CheckViolationError; after the migration
    the UPDATE succeeds. Also confirms the datetime-binding fix — passing
    plain datetime objects (no .isoformat()) works against asyncpg's
    TIMESTAMPTZ binding."""
    from app.ops.telegram_polling import _auto_skip_expired_signals
    from sqlalchemy.ext.asyncio import async_sessionmaker

    now = datetime.now(timezone.utc)
    sent_at = now - timedelta(seconds=900)  # 15 min ago → past default 600s
    sig_id = _new_signal_id()
    async with AsyncSession(postgres_engine) as s:
        await s.execute(
            sa.text(
                "INSERT INTO telegram_signals "
                "(id, user_id, symbol, direction, sent_at, payload) "
                "VALUES (:id, 1, 'BTC/USDT', 'LONG', :sent_at, '{}'::jsonb)"
            ),
            {"id": sig_id, "sent_at": sent_at},
        )
        await s.commit()

    factory = async_sessionmaker(postgres_engine, expire_on_commit=False)
    n_marked = await _auto_skip_expired_signals(
        factory,  # type: ignore[arg-type]
        timeout_seconds=600,
        now=now,
    )

    async with AsyncSession(postgres_engine) as s:
        row = (await s.execute(
            sa.text(
                "SELECT response FROM telegram_signals WHERE id=:id"
            ),
            {"id": sig_id},
        )).first()
        assert row is not None
        assert row.response == "auto_skipped", (
            f"expected response='auto_skipped', got {row.response!r}"
        )
        # n_marked counts only this test's row if no other concurrent
        # tests are bumping the same window — assert >=1 to be safe.
        assert n_marked >= 1
        # Cleanup.
        await s.execute(
            sa.text("DELETE FROM telegram_signals WHERE id=:id"),
            {"id": sig_id},
        )
        await s.commit()
