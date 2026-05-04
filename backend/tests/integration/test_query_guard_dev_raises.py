"""Verify the query guard fires in dev and is silent on user_id-bearing queries.

Spec §7.3 — the guard is wired into FastAPI's lifespan with dev_mode toggled
by Settings.env. These tests bypass the lifespan and attach the guard to the
in-memory test engine directly so we can assert dev-mode behaviour without
flipping process-wide environment.

Phase L4 verification: the parametric tests below probe every table in
`PER_USER_TABLES` (predictions, paper_trades, shadow_trades,
shadow_open_positions, shadow_cooldowns) so a future addition to that
frozenset has guaranteed regression coverage.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa

from app.auth.query_guard import (
    PER_USER_TABLES,
    MissingUserIdFilterError,
    attach_query_guard,
)


@pytest.mark.asyncio
async def test_dev_mode_raises_on_naive_query(
    bot_status_engine: Any, bot_status_factory: Any,
) -> None:
    attach_query_guard(bot_status_engine.sync_engine, dev_mode=True)

    async with bot_status_factory() as session:
        with pytest.raises(MissingUserIdFilterError):
            await session.execute(sa.text("SELECT * FROM shadow_trades"))


@pytest.mark.asyncio
async def test_dev_mode_passes_when_user_id_present(
    bot_status_engine: Any, bot_status_factory: Any,
) -> None:
    attach_query_guard(bot_status_engine.sync_engine, dev_mode=True)

    async with bot_status_factory() as session:
        # No rows yet; the guard only checks the predicate, not the result.
        await session.execute(
            sa.text("SELECT * FROM shadow_trades WHERE user_id = :uid"),
            {"uid": 1},
        )


@pytest.mark.asyncio
async def test_dev_mode_ignores_shared_table(
    bot_status_engine: Any, bot_status_factory: Any,
) -> None:
    """asset_universe is shared across users; the guard must not interfere."""
    attach_query_guard(bot_status_engine.sync_engine, dev_mode=True)

    async with bot_status_factory() as session:
        await session.execute(sa.text("SELECT * FROM asset_universe"))


# --- L4: cover every per-user table ---------------------------------------


# Frozensets are unordered; sort for deterministic test discovery / reporting.
_ALL_PER_USER_TABLES = sorted(PER_USER_TABLES)


@pytest.mark.parametrize("table", _ALL_PER_USER_TABLES)
@pytest.mark.asyncio
async def test_dev_mode_raises_for_every_per_user_table(
    bot_status_engine: Any, bot_status_factory: Any, table: str,
) -> None:
    """Every entry in PER_USER_TABLES must trip the guard on a naive SELECT.

    The guard runs in `before_cursor_execute`, so the raise fires before the
    SQL is dispatched to SQLite — we don't need the table to physically
    exist in the test fixture for this assertion to be meaningful.
    """
    attach_query_guard(bot_status_engine.sync_engine, dev_mode=True)

    async with bot_status_factory() as session:
        with pytest.raises(MissingUserIdFilterError):
            await session.execute(sa.text(f"SELECT * FROM {table}"))


@pytest.mark.parametrize("table", _ALL_PER_USER_TABLES)
@pytest.mark.asyncio
async def test_dev_mode_silent_with_user_id_for_every_per_user_table(
    bot_status_engine: Any, bot_status_factory: Any, table: str,
) -> None:
    """Symmetric: every per-user table is silent when user_id predicate present.

    For tables that don't exist in the in-memory fixture we still assert
    that the *guard* did not raise; the underlying SQLite OperationalError
    (if any) is expected and proves the guard handed the statement off
    instead of short-circuiting.
    """
    attach_query_guard(bot_status_engine.sync_engine, dev_mode=True)

    async with bot_status_factory() as session:
        try:
            await session.execute(
                sa.text(f"SELECT * FROM {table} WHERE user_id = :uid"),
                {"uid": 1},
            )
        except MissingUserIdFilterError:
            raise
        except Exception:  # noqa: BLE001 — DB-level errors are acceptable here
            # Table may not exist in the fixture; the guard handed the SQL
            # through to SQLite. That is exactly the behaviour we want.
            pass


def test_per_user_tables_set_is_complete() -> None:
    """Pin the contract: PER_USER_TABLES must list all 5 spec §7.1 tables."""
    expected = {
        "predictions",
        "paper_trades",
        "shadow_trades",
        "shadow_open_positions",
        "shadow_cooldowns",
    }
    assert set(PER_USER_TABLES) == expected, (
        f"PER_USER_TABLES drifted from spec §7.1: {set(PER_USER_TABLES)!r}"
    )
