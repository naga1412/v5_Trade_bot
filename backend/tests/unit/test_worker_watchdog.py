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

    async def _capture(message: str, *, level: str = "warning") -> bool:
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

    async def _capture(message: str, *, level: str = "warning") -> bool:
        sent.append((level, message))
        return True

    with patch.object(worker_watchdog, "alert_admin", _capture):
        await worker_watchdog._alert_if_dead(statuses)

    assert len(sent) == 1
    level, body = sent[0]
    assert level == "critical"
    assert "stale_one" in body
    assert "ok_one" not in body
    assert "absent_one" not in body


@pytest.mark.asyncio
async def test_no_alert_when_all_workers_ok() -> None:
    statuses = [{"name": "w", "state": "ok"}]
    sent: list[tuple[str, str]] = []

    async def _capture(message: str, *, level: str = "warning") -> bool:
        sent.append((level, message))
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

    async def _capture(message: str, *, level: str = "warning") -> bool:
        sent.append(message)
        return True

    with patch.object(worker_watchdog, "alert_admin", _capture):
        await worker_watchdog._alert_if_dead(statuses)

    assert "ALERT-ONLY (stateful)" in sent[0]
    assert "auto_restart_candidate" not in sent[0]


# ---------------------------------------------------------------------------
# Auto-restart wiring (self-healing).


@pytest.mark.asyncio
async def test_supervised_nonstateful_worker_is_restarted_and_severity_downgrades() -> None:
    """A stale non-stateful worker that is registered with the supervisor
    must be auto-restarted; if every dead worker self-heals, the admin
    alert severity drops from 'critical' to 'warning' so the pager stays
    quiet."""
    from app.ops import worker_supervisor as ws

    ws._reset_for_tests()

    restart_calls: list[str] = []

    async def _fake_restart(name: str) -> bool:
        restart_calls.append(name)
        return True

    statuses = [
        {"name": "scanner_batch_task", "state": "stale", "stateful": False,
         "staleness_seconds": 1200, "max_staleness_seconds": 600},
    ]
    sent: list[tuple[str, str]] = []

    async def _capture(message: str, *, level: str = "warning") -> bool:
        sent.append((level, message))
        return True

    with (
        patch.object(ws, "is_registered", lambda n: True),
        patch.object(ws, "restart", _fake_restart),
        patch.object(worker_watchdog, "alert_admin", _capture),
    ):
        await worker_watchdog._alert_if_dead(statuses)

    assert restart_calls == ["scanner_batch_task"]
    assert len(sent) == 1
    level, body = sent[0]
    # Self-heal succeeded → severity downgraded.
    assert level == "warning"
    assert "action=restarted" in body


@pytest.mark.asyncio
async def test_unsupervised_nonstateful_worker_alerts_critical() -> None:
    """A non-stateful worker that's NOT registered with the supervisor
    (legacy / not-yet-migrated) still gets the critical alert path —
    we can't restart what we don't know about."""
    from app.ops import worker_supervisor as ws

    ws._reset_for_tests()

    statuses = [
        {"name": "legacy_worker", "state": "stale", "stateful": False,
         "staleness_seconds": 1200, "max_staleness_seconds": 600},
    ]
    sent: list[tuple[str, str]] = []

    async def _capture(message: str, *, level: str = "warning") -> bool:
        sent.append((level, message))
        return True

    with patch.object(worker_watchdog, "alert_admin", _capture):
        await worker_watchdog._alert_if_dead(statuses)

    assert len(sent) == 1
    level, body = sent[0]
    assert level == "critical"
    assert "action=alert (not_supervised)" in body


@pytest.mark.asyncio
async def test_failed_restart_keeps_severity_critical() -> None:
    """If supervisor.restart() returns False (factory raised), we keep
    severity=critical so the operator gets paged — self-heal failed."""
    from app.ops import worker_supervisor as ws

    ws._reset_for_tests()

    async def _failing_restart(name: str) -> bool:
        return False

    statuses = [
        {"name": "scanner_batch_task", "state": "stale", "stateful": False,
         "staleness_seconds": 1200, "max_staleness_seconds": 600},
    ]
    sent: list[tuple[str, str]] = []

    async def _capture(message: str, *, level: str = "warning") -> bool:
        sent.append((level, message))
        return True

    with (
        patch.object(ws, "is_registered", lambda n: True),
        patch.object(ws, "restart", _failing_restart),
        patch.object(worker_watchdog, "alert_admin", _capture),
    ):
        await worker_watchdog._alert_if_dead(statuses)

    level, body = sent[0]
    assert level == "critical"
    assert "restart_FAILED" in body


@pytest.mark.asyncio
async def test_mixed_dead_workers_severity_critical_unless_all_healed() -> None:
    """Three dead workers: one stateful (alert-only), one supervised
    (will heal), one unsupervised (cannot heal). Severity must stay
    critical because at least one cannot self-heal."""
    from app.ops import worker_supervisor as ws

    ws._reset_for_tests()

    async def _ok_restart(name: str) -> bool:
        return True

    statuses = [
        {"name": "live_worker", "state": "stale", "stateful": True,
         "staleness_seconds": 1200, "max_staleness_seconds": 600},
        {"name": "scanner_batch_task", "state": "stale", "stateful": False,
         "staleness_seconds": 1200, "max_staleness_seconds": 600},
        {"name": "legacy", "state": "stale", "stateful": False,
         "staleness_seconds": 1200, "max_staleness_seconds": 600},
    ]
    sent: list[tuple[str, str]] = []

    async def _capture(message: str, *, level: str = "warning") -> bool:
        sent.append((level, message))
        return True

    def _fake_is_registered(name: str) -> bool:
        return name == "scanner_batch_task"

    with (
        patch.object(ws, "is_registered", _fake_is_registered),
        patch.object(ws, "restart", _ok_restart),
        patch.object(worker_watchdog, "alert_admin", _capture),
    ):
        await worker_watchdog._alert_if_dead(statuses)

    level, body = sent[0]
    assert level == "critical"
    # All three actions are correctly classified in the body.
    assert "ALERT-ONLY (stateful)" in body
    assert "action=restarted" in body
    assert "not_supervised" in body
