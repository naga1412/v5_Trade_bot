"""Healer Phase 0 — watchdog additions (Tasks B1, B2, B3).

Motivating incident: pre-#344 the allowlist worker was silent → watchdog
staleness alarm fired (worked). Post-#344 the worker beat daily with
last_status='error' for one cycle — fresh beat_at made staleness
detection permanently blind. A worker failing every cycle while
heartbeating was invisible. B1 closes that class.

Adjacencies covered by the same test file:
  * B2 — daily-cadence staleness tune for symbol_allowlist_refresh
  * B3 — expected_dormant classification for auto_promote_task
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ops import worker_watchdog
from app.ops.worker_registry import HEARTBEAT, WORKER_REGISTRY, WorkerSpec, by_name


# ---------- fixture ------------------------------------------------------


@pytest.fixture
async def heartbeat_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE worker_heartbeats ("
            "worker_name TEXT PRIMARY KEY, beat_at TIMESTAMP NOT NULL, "
            "last_status TEXT, details TEXT)"
        ))
    yield factory
    await engine.dispose()


def _spec(
    name: str = "test_worker",
    max_staleness_seconds: int = 15 * 60,
    optional_gate_env: tuple[str, ...] = (),
) -> WorkerSpec:
    return WorkerSpec(
        name=name,
        description=f"test: {name}",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=max_staleness_seconds,
        stateful=False,
        optional_gate_env=optional_gate_env,
    )


async def _insert_beat(
    factory: async_sessionmaker,
    *,
    worker: str,
    beat_at: datetime,
    last_status: str,
    details: str | None = None,
) -> None:
    async with factory() as session:
        await session.execute(sa.text(
            "INSERT OR REPLACE INTO worker_heartbeats "
            "(worker_name, beat_at, last_status, details) "
            "VALUES (:n, :b, :s, :d)"
        ), {"n": worker, "b": beat_at, "s": last_status, "d": details})
        await session.commit()


@pytest.fixture(autouse=True)
def _reset_error_streaks():
    """Wipe the in-memory streak dicts between tests so cross-test state
    can't cause a phantom N=2 threshold trip in a test that only inserts
    one error beat."""
    worker_watchdog._ERROR_STREAKS.clear()
    worker_watchdog._LAST_SEEN_BEAT_AT.clear()
    yield
    worker_watchdog._ERROR_STREAKS.clear()
    worker_watchdog._LAST_SEEN_BEAT_AT.clear()


# ---------- B1: error-status heartbeat detection -------------------------


@pytest.mark.asyncio
async def test_b1_single_error_beat_below_default_threshold_stays_ok(
    heartbeat_factory,
) -> None:
    """N=2 default: one error beat is tolerated (worker may be flaky)."""
    now = datetime.now(timezone.utc)
    await _insert_beat(
        heartbeat_factory,
        worker="test_worker",
        beat_at=now - timedelta(seconds=30),
        last_status="error",
        details='{"error": "flake"}',
    )
    with patch.object(
        worker_watchdog, "WORKER_REGISTRY", (_spec(),),
    ):
        statuses = await worker_watchdog.check_all_workers(heartbeat_factory)
    assert statuses[0]["state"] == "ok"
    assert statuses[0]["error_streak"] == 1


@pytest.mark.asyncio
async def test_b1_two_consecutive_error_beats_alarm_at_default_threshold(
    heartbeat_factory,
) -> None:
    """N=2 default: the second consecutive error beat trips heartbeat_error."""
    now = datetime.now(timezone.utc)
    with patch.object(
        worker_watchdog, "WORKER_REGISTRY", (_spec(),),
    ):
        # Beat 1: error
        await _insert_beat(
            heartbeat_factory,
            worker="test_worker",
            beat_at=now - timedelta(seconds=60),
            last_status="error",
            details='{"error": "first"}',
        )
        first_pass = await worker_watchdog.check_all_workers(heartbeat_factory)
        assert first_pass[0]["state"] == "ok"
        # Beat 2: error again on a FRESH beat_at
        await _insert_beat(
            heartbeat_factory,
            worker="test_worker",
            beat_at=now - timedelta(seconds=30),
            last_status="error",
            details='{"error": "second-still-broken"}',
        )
        second_pass = await worker_watchdog.check_all_workers(heartbeat_factory)
    assert second_pass[0]["state"] == "heartbeat_error"
    assert second_pass[0]["error_streak"] == 2
    # Details excerpt must surface in the entry so _alert_if_dead can
    # include it in the alarm body.
    assert "second-still-broken" in str(second_pass[0]["details_excerpt"])


@pytest.mark.asyncio
async def test_b1_daily_cadence_alarms_on_first_error_beat(
    heartbeat_factory,
) -> None:
    """N=1 for daily-cadence workers (max_staleness >= 12h). The
    2026-07-22 symbol_allowlist_refresh incident regression test: even
    one status='error' heartbeat means ~24h of blindness."""
    now = datetime.now(timezone.utc)
    await _insert_beat(
        heartbeat_factory,
        worker="test_worker",
        beat_at=now - timedelta(seconds=30),
        last_status="error",
        details=(
            '{"error": "asyncpg.exceptions.DataError: invalid input for '
            "query argument $1: '2026-06-23T09:29:56.988842+00:00' "
            '(expected a datetime.date or datetime.dat"}'
        ),
    )
    daily_spec = _spec(max_staleness_seconds=26 * 60 * 60)
    with patch.object(
        worker_watchdog, "WORKER_REGISTRY", (daily_spec,),
    ):
        statuses = await worker_watchdog.check_all_workers(heartbeat_factory)
    assert statuses[0]["state"] == "heartbeat_error"
    assert statuses[0]["error_streak"] == 1
    assert "asyncpg" in str(statuses[0]["details_excerpt"])


@pytest.mark.asyncio
async def test_b1_error_streak_resets_on_ok_beat(heartbeat_factory) -> None:
    """A single healthy beat clears the streak — the worker recovered."""
    now = datetime.now(timezone.utc)
    with patch.object(
        worker_watchdog, "WORKER_REGISTRY", (_spec(),),
    ):
        await _insert_beat(
            heartbeat_factory,
            worker="test_worker",
            beat_at=now - timedelta(seconds=90),
            last_status="error",
        )
        await worker_watchdog.check_all_workers(heartbeat_factory)
        # Recovery beat
        await _insert_beat(
            heartbeat_factory,
            worker="test_worker",
            beat_at=now - timedelta(seconds=60),
            last_status="ok",
        )
        await worker_watchdog.check_all_workers(heartbeat_factory)
        # A NEW error beat should start a fresh streak from 1, not 2.
        await _insert_beat(
            heartbeat_factory,
            worker="test_worker",
            beat_at=now - timedelta(seconds=30),
            last_status="error",
        )
        third_pass = await worker_watchdog.check_all_workers(heartbeat_factory)
    assert third_pass[0]["state"] == "ok"
    assert third_pass[0]["error_streak"] == 1


@pytest.mark.asyncio
async def test_b1_same_beat_at_does_not_double_advance_streak(
    heartbeat_factory,
) -> None:
    """Two watchdog passes on the SAME beat_at must count as one error,
    not two — otherwise a 5-min watchdog cadence would double-alarm a
    daily worker with one bad beat."""
    now = datetime.now(timezone.utc)
    await _insert_beat(
        heartbeat_factory,
        worker="test_worker",
        beat_at=now - timedelta(seconds=30),
        last_status="error",
    )
    with patch.object(
        worker_watchdog, "WORKER_REGISTRY", (_spec(),),
    ):
        first = await worker_watchdog.check_all_workers(heartbeat_factory)
        second = await worker_watchdog.check_all_workers(heartbeat_factory)
    assert first[0]["error_streak"] == 1
    assert second[0]["error_streak"] == 1


@pytest.mark.asyncio
async def test_b1_heartbeat_error_is_in_bad_states() -> None:
    """`heartbeat_error` is in BAD_STATES so _alert_if_dead treats it
    as alarm-worthy (same code path as `stale`)."""
    assert "heartbeat_error" in worker_watchdog.BAD_STATES


# ---------- B2: daily-cadence staleness tune -----------------------------


def test_b2_symbol_allowlist_refresh_uses_daily_treatment() -> None:
    """Registry regression: the allowlist worker's budget must match
    the cadence+10% treatment, not the older 48h "1 missed run allowed"
    knob that would delay the alarm ~24h after a genuine failure."""
    spec = by_name("symbol_allowlist_refresh")
    assert spec is not None
    # Cadence + ~10% grace, matching live_worker at 3700s (1h + 100s).
    # 24h cadence → 26h budget = 93600s.
    assert spec.max_staleness_seconds == 26 * 60 * 60


# ---------- B3: expected_dormant classification --------------------------


@pytest.mark.asyncio
async def test_b3_auto_promote_intentionally_idle_is_expected_dormant(
    heartbeat_factory, monkeypatch,
) -> None:
    """auto_promote_task must class as expected_dormant (info) — NOT
    never_heartbeated (alarm) — when neither of its opt-in feature flags
    is truthy. The prior classification was training the operator to
    ignore the daily "auto_promote_task appear DEAD" watchdog log."""
    monkeypatch.setenv("AUTONOMOUS_TRADING_ENABLED", "true")
    monkeypatch.delenv("AUTO_PROMOTE_TO_TELEGRAM_ENABLED", raising=False)
    monkeypatch.delenv("AUTO_PROMOTE_TO_FULLYAUTO_ENABLED", raising=False)
    spec = _spec(
        name="auto_promote_task",
        max_staleness_seconds=26 * 60 * 60,
        optional_gate_env=(
            "AUTO_PROMOTE_TO_TELEGRAM_ENABLED",
            "AUTO_PROMOTE_TO_FULLYAUTO_ENABLED",
        ),
    )
    with patch.object(
        worker_watchdog, "WORKER_REGISTRY", (spec,),
    ):
        statuses = await worker_watchdog.check_all_workers(heartbeat_factory)
    assert statuses[0]["state"] == "expected_dormant"
    assert "expected_dormant" not in worker_watchdog.BAD_STATES


@pytest.mark.asyncio
async def test_b3_optional_gate_env_active_flips_back_to_normal_classification(
    heartbeat_factory, monkeypatch,
) -> None:
    """When ANY optional_gate_env var is truthy, watchdog uses the normal
    staleness / error-status path — expected_dormant no longer applies."""
    monkeypatch.setenv("AUTO_PROMOTE_TO_TELEGRAM_ENABLED", "true")
    monkeypatch.delenv("AUTO_PROMOTE_TO_FULLYAUTO_ENABLED", raising=False)
    spec = _spec(
        name="auto_promote_task",
        max_staleness_seconds=26 * 60 * 60,
        optional_gate_env=(
            "AUTO_PROMOTE_TO_TELEGRAM_ENABLED",
            "AUTO_PROMOTE_TO_FULLYAUTO_ENABLED",
        ),
    )
    with patch.object(
        worker_watchdog, "WORKER_REGISTRY", (spec,),
    ):
        statuses = await worker_watchdog.check_all_workers(heartbeat_factory)
    # No heartbeat rows seeded → falls through to never_heartbeated,
    # not expected_dormant.
    assert statuses[0]["state"] == "never_heartbeated"


def test_b3_auto_promote_task_registry_declares_gate_env() -> None:
    """Registry-level regression: the actual auto_promote_task WorkerSpec
    must declare both AUTO_PROMOTE_* env vars in optional_gate_env."""
    spec = by_name("auto_promote_task")
    assert spec is not None
    assert set(spec.optional_gate_env) == {
        "AUTO_PROMOTE_TO_TELEGRAM_ENABLED",
        "AUTO_PROMOTE_TO_FULLYAUTO_ENABLED",
    }
