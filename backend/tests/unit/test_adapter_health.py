"""Unit tests for the adapter_health pinger (SP-3 Phase F)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.data.adapter_health import (
    HealthProbe,
    HealthResult,
    DEFAULT_HEALTH_INTERVAL_SEC,
    probe_adapter,
    record_health,
    run_health_pinger_loop,
)


def _mk_engine() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    return engine


async def _create_table(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE adapter_health ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "exchange TEXT NOT NULL, "
            "checked_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "is_healthy INTEGER NOT NULL, "
            "latency_ms INTEGER, error_message TEXT, "
            "quota_used_pct REAL)"
        ))


class _AlwaysHealthy:
    name = "binance"

    async def health_check(self) -> HealthResult:
        return HealthResult(is_healthy=True, latency_ms=42)


class _AlwaysFailing:
    name = "bybit"

    async def health_check(self) -> HealthResult:
        return HealthResult(
            is_healthy=False,
            latency_ms=None,
            error_message="429 Too Many Requests",
        )


class _RaisingAdapter:
    name = "yahoo"

    async def health_check(self) -> HealthResult:
        raise RuntimeError("boom")


class _NoHealthCheck:
    name = "twelvedata"


@pytest.mark.asyncio
async def test_probe_adapter_with_explicit_health_check_returns_result() -> None:
    res = await probe_adapter(_AlwaysHealthy())
    assert res.is_healthy is True
    assert res.latency_ms == 42


@pytest.mark.asyncio
async def test_probe_adapter_with_failing_check_returns_unhealthy() -> None:
    res = await probe_adapter(_AlwaysFailing())
    assert res.is_healthy is False
    assert res.error_message and "429" in res.error_message


@pytest.mark.asyncio
async def test_probe_adapter_catches_unexpected_exception() -> None:
    res = await probe_adapter(_RaisingAdapter())
    assert res.is_healthy is False
    assert res.error_message and "boom" in res.error_message


@pytest.mark.asyncio
async def test_probe_adapter_without_health_check_falls_back_to_unknown() -> None:
    res = await probe_adapter(_NoHealthCheck())
    # Adapters that do not implement health_check are reported as healthy by
    # default with an explanatory note (no probe = no signal).
    assert res.is_healthy is True
    assert res.error_message and "no health_check" in res.error_message


@pytest.mark.asyncio
async def test_record_health_inserts_row() -> None:
    engine = _mk_engine()
    await _create_table(engine)
    res = HealthResult(
        is_healthy=True, latency_ms=10, quota_used_pct=0.05,
    )
    async with AsyncSession(engine) as session:
        await record_health(
            session, exchange="binance", result=res,
            checked_at=datetime(2026, 5, 5, 11, 0, tzinfo=UTC),
        )
        await session.commit()

    async with AsyncSession(engine) as session:
        row = (await session.execute(sa.text(
            "SELECT exchange, is_healthy, latency_ms, quota_used_pct "
            "FROM adapter_health"
        ))).first()
    assert row is not None
    assert row.exchange == "binance"
    assert bool(row.is_healthy) is True
    assert row.latency_ms == 10
    assert row.quota_used_pct == 0.05


@pytest.mark.asyncio
async def test_record_health_passes_real_bool_to_sql() -> None:
    """Regression: prod hit asyncpg DataError because is_healthy was bound
    as int 1 instead of bool True. SQLite tolerates either, so this test
    intercepts the bind dict directly and asserts the bool type — independent
    of DB backend strictness.
    """
    captured: dict[str, Any] = {}

    class _Spy:
        async def execute(self, _stmt: Any, params: dict[str, Any]) -> None:
            captured.update(params)

    await record_health(
        _Spy(), exchange="binance",  # type: ignore[arg-type]
        result=HealthResult(is_healthy=True),
    )
    assert "h" in captured
    assert captured["h"] is True
    assert type(captured["h"]) is bool

    captured.clear()
    await record_health(
        _Spy(), exchange="bybit",  # type: ignore[arg-type]
        result=HealthResult(is_healthy=False, error_message="down"),
    )
    assert captured["h"] is False
    assert type(captured["h"]) is bool


@pytest.mark.asyncio
async def test_run_health_pinger_loop_one_pass(monkeypatch: Any) -> None:
    """Loop wakes, probes each adapter once, records, then is cancelled."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    engine = _mk_engine()
    await _create_table(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    sleep_calls: list[float] = []

    async def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)
        # Sleep #1 returns normally so the inner for-loop runs once;
        # Sleep #2 cancels so we exit before another probe round.
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    # Force the registry to return only one fake adapter.
    from app.data import adapter_health as ahmod

    fake = _AlwaysHealthy()
    monkeypatch.setattr(ahmod, "list_registered", lambda: ["binance"])
    monkeypatch.setattr(ahmod, "get_adapter", lambda _: fake)

    with pytest.raises(asyncio.CancelledError):
        await run_health_pinger_loop(
            session_factory=factory,
            interval_sec=DEFAULT_HEALTH_INTERVAL_SEC,
            _sleep=_fake_sleep,
        )

    async with AsyncSession(engine) as session:
        rows = (await session.execute(sa.text(
            "SELECT exchange, is_healthy FROM adapter_health"
        ))).all()
    assert len(rows) == 1
    assert rows[0].exchange == "binance"
    assert bool(rows[0].is_healthy) is True


def test_health_probe_protocol_is_runtime_checkable() -> None:
    """Adapters can opt in by exposing async health_check; runtime check works."""
    assert isinstance(_AlwaysHealthy(), HealthProbe)
    assert not isinstance(_NoHealthCheck(), HealthProbe)
