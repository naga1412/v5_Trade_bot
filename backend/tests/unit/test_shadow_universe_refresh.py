"""Tests for the daily-at-00:00-UTC universe refresh background task."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from app.shadow.universe import AssetUniverseEntry
from app.shadow.universe_refresh import (
    run_universe_refresh_loop,
    seconds_until_next_utc,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# --- pure-function tests for seconds_until_next_utc ---

def test_seconds_until_same_day_before_hour() -> None:
    now = datetime(2026, 5, 3, 10, 0, 0, tzinfo=UTC)
    # Refresh at 18:00 today → 8 hours.
    assert seconds_until_next_utc(18, now) == 8 * 3600


def test_seconds_until_after_hour_rolls_to_next_day() -> None:
    now = datetime(2026, 5, 3, 13, 0, 0, tzinfo=UTC)
    # Refresh at 0:00 tomorrow → 11 hours.
    assert seconds_until_next_utc(0, now) == 11 * 3600


def test_seconds_until_exact_hour_rolls_to_next_day() -> None:
    """If it's exactly the refresh hour, the *next* run is 24h away."""
    now = datetime(2026, 5, 3, 0, 0, 0, tzinfo=UTC)
    assert seconds_until_next_utc(0, now) == 24 * 3600


def test_seconds_until_partial_hour() -> None:
    now = datetime(2026, 5, 3, 23, 30, 0, tzinfo=UTC)
    # 30 minutes until 00:00.
    assert seconds_until_next_utc(0, now) == 30 * 60


def test_seconds_until_within_minute_after_hour() -> None:
    """Just after 00:00:30 → wait ~24h-30s."""
    now = datetime(2026, 5, 3, 0, 0, 30, tzinfo=UTC)
    assert seconds_until_next_utc(0, now) == 24 * 3600 - 30


# --- async loop test ---

async def _create_universe_table(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE asset_universe ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "quote_volume_usd_24h REAL NOT NULL, "
            "rank INTEGER NOT NULL, "
            "snapshot_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "UNIQUE (symbol, snapshot_at))"
        ))


@pytest.mark.asyncio
async def test_loop_runs_one_round_signals_event() -> None:
    """One iteration: sleep → fetch → persist → set event → exit."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _create_universe_table(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    sleep_calls: list[float] = []

    fake_entries = [
        AssetUniverseEntry(symbol="BTCUSDT", quote_volume_usd_24h=1e9, rank=1),
        AssetUniverseEntry(symbol="ETHUSDT", quote_volume_usd_24h=8e8, rank=2),
    ]

    async def fake_fetch(http: Any, *, n: int = 30) -> list[AssetUniverseEntry]:  # noqa: ARG001
        return fake_entries

    refreshed = asyncio.Event()

    # Cancel after one iteration: stopper runs as the loop's _sleep, so the
    # second invocation (which would mean a second iteration is starting) cancels.
    iterations = {"n": 0}
    stop_after_iterations = 2

    async def stopper(_seconds: float) -> None:
        sleep_calls.append(_seconds)
        iterations["n"] += 1
        if iterations["n"] >= stop_after_iterations:
            raise asyncio.CancelledError

    task = asyncio.create_task(run_universe_refresh_loop(
        session_factory=factory,
        refreshed_event=refreshed,
        wake_at_utc_hour=0,
        _sleep=stopper,
        _fetch=fake_fetch,
        _now=lambda: datetime(2026, 5, 3, 23, 30, tzinfo=UTC),
    ))
    with pytest.raises(asyncio.CancelledError):
        await task

    # The first sleep should be 30 minutes (until 00:00 UTC).
    assert sleep_calls[0] == 30 * 60
    # The refresh event must have been set.
    assert refreshed.is_set()

    # And the snapshot row must be present in the DB.
    async with factory() as s:
        rows = (await s.execute(
            sa.text("SELECT symbol, rank FROM asset_universe ORDER BY rank")
        )).all()
    assert [(r.symbol, r.rank) for r in rows] == [("BTCUSDT", 1), ("ETHUSDT", 2)]


@pytest.mark.asyncio
async def test_loop_swallows_fetch_errors_and_continues() -> None:
    """A failing fetch must not kill the loop."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _create_universe_table(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    sleeps = {"n": 0}
    cancel_after_sleeps = 3

    async def fake_sleep(_seconds: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] >= cancel_after_sleeps:
            # one pre-iter1, one pre-iter2, third call cancels at the start of iter3
            raise asyncio.CancelledError

    async def failing_fetch(_http: Any, *, n: int = 30) -> list[AssetUniverseEntry]:  # noqa: ARG001
        raise RuntimeError("boom")

    refreshed = asyncio.Event()
    fixed_now = datetime(2026, 5, 3, 23, 30, tzinfo=UTC)

    task = asyncio.create_task(run_universe_refresh_loop(
        session_factory=factory,
        refreshed_event=refreshed,
        wake_at_utc_hour=0,
        _sleep=fake_sleep,
        _fetch=failing_fetch,
        _now=lambda: fixed_now,
    ))
    with pytest.raises(asyncio.CancelledError):
        await task

    # Two iterations attempted (sleeps.n == 3 means: 1 pre-iter, 1 pre-iter, 1 cancel).
    min_iterations_attempted = 2
    assert sleeps["n"] >= min_iterations_attempted
    # No rows persisted because fetch always failed.
    async with factory() as s:
        rows = (await s.execute(sa.text("SELECT * FROM asset_universe"))).all()
    assert rows == []


def test_seconds_until_handles_naive_now_as_utc() -> None:
    """Defensive: naive datetimes are treated as UTC."""
    now = datetime(2026, 5, 3, 22, 0, 0)  # naive
    assert seconds_until_next_utc(0, now) == 2 * 3600


def test_seconds_until_one_second_before_hour() -> None:
    now = datetime(2026, 5, 3, 23, 59, 59, tzinfo=UTC)
    assert seconds_until_next_utc(0, now) == 1
