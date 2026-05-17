"""FU-15: watchdog state machine handles single-shot workers correctly.

A `single_shot=True` worker fires once at startup and exits clean. Without
FU-15, the watchdog classified `mtf_cache_prewarm_task` as `no_signal`
(alarming) because its `liveness_query=None` returned `None` for staleness.
FU-15 introduces:

  - new `WorkerSpec.single_shot: bool = False` field
  - new watchdog states:
      `single_shot_completed` (non-alarming) — task heartbeated with
        status='single_shot_completed'
      `starting` (non-alarming) — no heartbeat yet, container within
        the startup grace period
      `single_shot_never_completed` (ALARMING) — no heartbeat past grace
      `single_shot_failed` (ALARMING) — heartbeat with unexpected status
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ops import worker_watchdog
from app.ops.worker_registry import HEARTBEAT, WorkerSpec


@pytest.fixture
async def heartbeat_factory():
    """In-memory SQLite session_factory with a worker_heartbeats table."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE worker_heartbeats ("
            "worker_name TEXT PRIMARY KEY, beat_at TIMESTAMP NOT NULL, "
            "last_status TEXT, details TEXT)",
        ))
    yield factory
    await engine.dispose()


def _single_shot_spec(name: str = "test_single_shot") -> WorkerSpec:
    return WorkerSpec(
        name=name,
        description=f"single-shot: {name}",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=10 * 60,
        stateful=False,
        single_shot=True,
    )


@pytest.mark.asyncio
async def test_single_shot_with_completed_heartbeat_is_non_alarming(
    heartbeat_factory,
) -> None:
    """Heartbeat row with last_status='single_shot_completed' → state=single_shot_completed."""
    factory = heartbeat_factory
    async with factory() as session:
        await session.execute(sa.text(
            "INSERT INTO worker_heartbeats (worker_name, beat_at, last_status) "
            "VALUES ('test_single_shot', :t, 'single_shot_completed')",
        ), {"t": datetime.now(timezone.utc)})
        await session.commit()

    spec = _single_shot_spec()
    with patch.object(worker_watchdog, "WORKER_REGISTRY", (spec,)):
        statuses = await worker_watchdog.check_all_workers(factory)

    assert statuses[0]["state"] == "single_shot_completed"
    # Non-alarming → not in BAD_STATES
    assert "single_shot_completed" not in worker_watchdog.BAD_STATES


@pytest.mark.asyncio
async def test_single_shot_no_heartbeat_within_grace_is_starting(
    heartbeat_factory,
) -> None:
    """No heartbeat row + container uptime < grace → state=starting."""
    factory = heartbeat_factory
    spec = _single_shot_spec()
    with patch.object(worker_watchdog, "WORKER_REGISTRY", (spec,)), \
         patch.object(worker_watchdog, "_container_uptime_seconds", return_value=30.0):
        statuses = await worker_watchdog.check_all_workers(factory)

    assert statuses[0]["state"] == "starting"
    assert "starting" not in worker_watchdog.BAD_STATES


@pytest.mark.asyncio
async def test_single_shot_no_heartbeat_past_grace_is_never_completed(
    heartbeat_factory,
) -> None:
    """No heartbeat row + container uptime > grace → state=single_shot_never_completed (ALARM)."""
    factory = heartbeat_factory
    spec = _single_shot_spec()
    # 600s = 10 min, well past the 5-min default grace
    with patch.object(worker_watchdog, "WORKER_REGISTRY", (spec,)), \
         patch.object(worker_watchdog, "_container_uptime_seconds", return_value=600.0):
        statuses = await worker_watchdog.check_all_workers(factory)

    assert statuses[0]["state"] == "single_shot_never_completed"
    assert "single_shot_never_completed" in worker_watchdog.BAD_STATES


@pytest.mark.asyncio
async def test_single_shot_heartbeat_with_other_status_is_failed(
    heartbeat_factory,
) -> None:
    """Heartbeat row with last_status='error' (or anything not single_shot_completed)
    → state=single_shot_failed (ALARM). Shouldn't happen in practice but guards
    against accidental status='ok' from a misconfigured worker."""
    factory = heartbeat_factory
    async with factory() as session:
        await session.execute(sa.text(
            "INSERT INTO worker_heartbeats (worker_name, beat_at, last_status) "
            "VALUES ('test_single_shot', :t, 'error')",
        ), {"t": datetime.now(timezone.utc)})
        await session.commit()

    spec = _single_shot_spec()
    with patch.object(worker_watchdog, "WORKER_REGISTRY", (spec,)):
        statuses = await worker_watchdog.check_all_workers(factory)

    assert statuses[0]["state"] == "single_shot_failed"
    assert "single_shot_failed" in worker_watchdog.BAD_STATES


@pytest.mark.asyncio
async def test_non_single_shot_worker_unaffected_by_state_machine_change(
    heartbeat_factory,
) -> None:
    """Regression guard: a normal worker (single_shot=False) keeps its
    existing state classification — 'ok' / 'stale' / 'never_heartbeated'."""
    factory = heartbeat_factory
    async with factory() as session:
        await session.execute(sa.text(
            "INSERT INTO worker_heartbeats (worker_name, beat_at, last_status) "
            "VALUES ('normal', :t, 'ok')",
        ), {"t": datetime.now(timezone.utc)})
        await session.commit()

    spec = WorkerSpec(
        name="normal",
        description="not single-shot",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=300,
        stateful=False,
    )
    with patch.object(worker_watchdog, "WORKER_REGISTRY", (spec,)):
        statuses = await worker_watchdog.check_all_workers(factory)

    assert statuses[0]["state"] == "ok"
