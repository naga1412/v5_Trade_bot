"""Verify the query guard fires in dev and is silent on user_id-bearing queries.

Spec §7.3 — the guard is wired into FastAPI's lifespan with dev_mode toggled
by Settings.env. These tests bypass the lifespan and attach the guard to the
in-memory test engine directly so we can assert dev-mode behaviour without
flipping process-wide environment.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa

from app.auth.query_guard import (
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
