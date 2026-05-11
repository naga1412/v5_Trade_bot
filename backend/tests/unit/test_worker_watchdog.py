"""Unit tests for the worker watchdog.

Covers the four states check_all_workers can produce:
  - ok           → recent heartbeat
  - stale        → heartbeat older than max_staleness_seconds
  - never_heartbeated → liveness query returns NULL
  - expected_absent → required_env not set

Plus _alert_if_dead's grouping behavior.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ops import worker_watchdog
from app.ops.worker_registry import HEARTBEAT, WorkerSpec


# Use an in-memory SQLite db with a worker_heartbeats-shaped table so the
# HEARTBEAT query (parameterised :n) actually executes.
@pytest.fixture
async def heartbeat_factory():
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


def _spec(name: str, max_stale: int = 60, stateful: bool = False) -> WorkerSpec:
    return WorkerSpec(
        name=name,
        description=f"test-{name}",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=max_stale,
        stateful=stateful,
    )


@pytest.mark.asyncio
async def test_recent_heartbeat_is_ok(heartbeat_factory) -> None:
    factory = heartbeat_factory
    async with factory() as session:
        await session.execute(sa.text(
            "INSERT INTO worker_heartbeats (worker_name, beat_at, last_status) "
            "VALUES ('w1', :t, 'ok')",
        ), {"t": datetime.now(timezone.utc)})
        await session.commit()

    spec = _spec("w1", max_stale=300)
    with patch.object(worker_watchdog, "WORKER_REGISTRY", (spec,)):
        statuses = await worker_watchdog.check_all_workers(factory)

    assert len(statuses) == 1
    assert statuses[0]["state"] == "ok"


@pytest.mark.asyncio
async def test_old_heartbeat_is_stale(heartbeat_factory) -> None:
    factory = heartbeat_factory
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    async with factory() as session:
        await session.execute(sa.text(
            "INSERT INTO worker_heartbeats (worker_name, beat_at, last_status) "
            "VALUES ('w_stale', :t, 'ok')",
        ), {"t": old})
        await session.commit()

    spec = _spec("w_stale", max_stale=60)
    with patch.object(worker_watchdog, "WORKER_REGISTRY", (spec,)):
        statuses = await worker_watchdog.check_all_workers(factory)

    assert statuses[0]["state"] == "stale"
    assert statuses[0]["staleness_seconds"] > 60


@pytest.mark.asyncio
async def test_no_heartbeat_row_is_never_heartbeated(heartbeat_factory) -> None:
    factory = heartbeat_factory
    spec = _spec("w_missing")
    with patch.object(worker_watchdog, "WORKER_REGISTRY", (spec,)):
        statuses = await worker_watchdog.check_all_workers(factory)
    assert statuses[0]["state"] == "never_heartbeated"


@pytest.mark.asyncio
async def test_no_heartbeat_row_with_pending_flag_is_pending_heartbeat(heartbeat_factory) -> None:
    factory = heartbeat_factory
    spec = WorkerSpec(
        name="w_pending", description="not yet instrumented",
        liveness_query=HEARTBEAT, max_staleness_seconds=60, stateful=False,
        pending_heartbeat=True,
    )
    with patch.object(worker_watchdog, "WORKER_REGISTRY", (spec,)):
        statuses = await worker_watchdog.check_all_workers(factory)
    assert statuses[0]["state"] == "pending_heartbeat"


@pytest.mark.asyncio
async def test_pending_heartbeat_does_not_trigger_alert() -> None:
    statuses = [
        {"name": "w_pending", "state": "pending_heartbeat"},
        {"name": "w_real_dead", "state": "stale", "stateful": False,
         "staleness_seconds": 7200, "max_staleness_seconds": 3600},
    ]
    sent: list[str] = []

    async def _capture(message: str, *, severity: str = "warning") -> bool:
        sent.append(message)
        return True

    with patch.object(worker_watchdog, "alert_admin", _capture):
        await worker_watchdog._alert_if_dead(statuses)

    assert len(sent) == 1
    assert "w_real_dead" in sent[0]
    assert "w_pending" not in sent[0]


@pytest.mark.asyncio
async def test_required_env_unset_is_expected_absent(heartbeat_factory, monkeypatch) -> None:
    factory = heartbeat_factory
    monkeypatch.delenv("AUTONOMOUS_TRADING_ENABLED", raising=False)
    spec = WorkerSpec(
        name="w_gated", description="gated", liveness_query=HEARTBEAT,
        max_staleness_seconds=60, stateful=True,
        required_env=("AUTONOMOUS_TRADING_ENABLED",),
    )
    with patch.object(worker_watchdog, "WORKER_REGISTRY", (spec,)):
        statuses = await worker_watchdog.check_all_workers(factory)
    assert statuses[0]["state"] == "expected_absent"


@pytest.mark.asyncio
async def test_required_env_falsey_value_is_expected_absent(heartbeat_factory, monkeypatch) -> None:
    factory = heartbeat_factory
    monkeypatch.setenv("AUTONOMOUS_TRADING_ENABLED", "false")
    spec = WorkerSpec(
        name="w_gated2", description="gated2", liveness_query=HEARTBEAT,
        max_staleness_seconds=60, stateful=True,
        required_env=("AUTONOMOUS_TRADING_ENABLED",),
    )
    with patch.object(worker_watchdog, "WORKER_REGISTRY", (spec,)):
        statuses = await worker_watchdog.check_all_workers(factory)
    assert statuses[0]["state"] == "expected_absent"


@pytest.mark.asyncio
async def test_alert_only_emitted_for_dead_workers() -> None:
    statuses = [
        {"name": "ok_one", "state": "ok"},
        {"name": "stale_one", "state": "stale", "stateful": False,
         "staleness_seconds": 7200, "max_staleness_seconds": 3600},
        {"name": "absent_one", "state": "expected_absent"},
    ]
    sent: list[tuple[str, str]] = []

    async def _capture(message: str, *, severity: str = "warning") -> bool:
        sent.append((severity, message))
        return True

    with patch.object(worker_watchdog, "alert_admin", _capture):
        await worker_watchdog._alert_if_dead(statuses)

    assert len(sent) == 1
    severity, body = sent[0]
    assert severity == "critical"
    assert "stale_one" in body
    assert "ok_one" not in body
    assert "absent_one" not in body


@pytest.mark.asyncio
async def test_no_alert_when_all_workers_ok() -> None:
    statuses = [{"name": "w", "state": "ok"}]
    sent: list[tuple[str, str]] = []

    async def _capture(message: str, *, severity: str = "warning") -> bool:
        sent.append((severity, message))
        return True

    with patch.object(worker_watchdog, "alert_admin", _capture):
        await worker_watchdog._alert_if_dead(statuses)
    assert sent == []


@pytest.mark.asyncio
async def test_stateful_workers_marked_alert_only_in_message() -> None:
    statuses = [
        {"name": "stateful_dead", "state": "stale", "stateful": True,
         "staleness_seconds": 600, "max_staleness_seconds": 300},
    ]
    sent: list[str] = []

    async def _capture(message: str, *, severity: str = "warning") -> bool:
        sent.append(message)
        return True

    with patch.object(worker_watchdog, "alert_admin", _capture):
        await worker_watchdog._alert_if_dead(statuses)

    assert "ALERT-ONLY (stateful)" in sent[0]
    assert "auto_restart_candidate" not in sent[0]
