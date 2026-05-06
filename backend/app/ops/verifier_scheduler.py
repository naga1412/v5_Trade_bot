"""Nightly audit chain verifier loop.

Implementation lands in Phase D2-D3. Wakes at 03:00 UTC, calls
verify_chain() on each chained table, alerts the admin and writes an
audit_violations row on any break.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def run_audit_verifier_loop(  # pragma: no cover — stub
    session_factory: "async_sessionmaker[AsyncSession]",
) -> None:
    """Phase D2 deliverable.

    TODO(SP-7 Phase D2): sleep until next 03:00 UTC, then loop:
    for table in CHAINED_TABLES: verify_chain(session, table); on break,
    alert_admin(...) + insert audit_violations row.
    """
    raise NotImplementedError("run_audit_verifier_loop: Phase D2 deliverable")


def start_audit_verifier_task(  # pragma: no cover — stub
    session_factory: "async_sessionmaker[AsyncSession]",
) -> "asyncio.Task[None]":
    """Phase D3 deliverable — wired into app.main lifespan.

    TODO(SP-7 Phase D3): asyncio.create_task(run_audit_verifier_loop(...))
    + register the task on the app state for graceful shutdown.
    """
    raise NotImplementedError("start_audit_verifier_task: Phase D3 deliverable")
