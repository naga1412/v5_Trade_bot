"""Unit tests for ``app.ws.keepalive`` — server-side WS supervisor.

These cover the four pieces of the supervisor in isolation:

1. ``_to_pair`` — Binance no-slash ↔ slash-form normalization.
2. ``_load_keepalive_symbols`` — top-N + exclude filtering against a
   real (sqlite) asset_universe table; graceful empty on DB failure.
3. ``_refresh_children`` — reconciliation diff: add new, cancel removed,
   leave existing untouched.
4. ``_run_child_with_restart`` — per-symbol crash isolation: throwing
   runner restarts with backoff; CancelledError propagates cleanly.

We deliberately do NOT test the long-running ``run_keepalive`` outer
loop end-to-end here — its tick interval makes a deterministic test
brittle. The pieces above plus the integration test cover the surface.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ws import keepalive


# ---------------------------------------------------------------------------
# _to_pair — pure helper.


def test_to_pair_strips_usdt_suffix() -> None:
    assert keepalive._to_pair("BTCUSDT") == "BTC/USDT"
    assert keepalive._to_pair("ETHUSDT") == "ETH/USDT"
    assert keepalive._to_pair("1000PEPEUSDT") == "1000PEPE/USDT"


def test_to_pair_leaves_non_usdt_alone() -> None:
    """Defensive: if the table ever holds a non-USDT symbol we don't mangle it."""
    assert keepalive._to_pair("BTCBUSD") == "BTCBUSD"


# ---------------------------------------------------------------------------
# _load_keepalive_symbols — DB-backed, sqlite mirror of asset_universe.


@pytest.fixture
async def universe_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE asset_universe ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "quote_volume_usd_24h REAL NOT NULL, "
            "rank INTEGER NOT NULL, "
            "snapshot_at TIMESTAMP NOT NULL)"
        ))
    yield factory
    await engine.dispose()


async def _seed_universe(
    factory: async_sessionmaker, *, symbols: list[str],
) -> None:
    async with factory() as session:
        for i, sym in enumerate(symbols):
            await session.execute(
                sa.text(
                    "INSERT INTO asset_universe "
                    "(symbol, quote_volume_usd_24h, rank, snapshot_at) "
                    "VALUES (:s, :v, :r, :ts)"
                ),
                {
                    "s": sym, "v": 1e9 - i * 1e6,
                    "r": i + 1, "ts": "2026-05-15T00:00:00",
                },
            )
        await session.commit()


@pytest.mark.asyncio
async def test_load_symbols_applies_top_n(universe_factory: Any) -> None:
    await _seed_universe(
        universe_factory,
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"],
    )
    result = await keepalive._load_keepalive_symbols(
        universe_factory, top_n=3, exclude=frozenset(), timeframe="1h",
    )
    # First 3 from rank order, all normalized to slash form.
    assert result == [
        ("BTC/USDT", "1h"),
        ("ETH/USDT", "1h"),
        ("SOL/USDT", "1h"),
    ]


@pytest.mark.asyncio
async def test_load_symbols_filters_excludes(universe_factory: Any) -> None:
    """Default exclude {(BTC/USDT, 1h)} must drop the singleton-owned pair."""
    await _seed_universe(
        universe_factory, symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    )
    result = await keepalive._load_keepalive_symbols(
        universe_factory, top_n=3,
        exclude=frozenset({("BTC/USDT", "1h")}), timeframe="1h",
    )
    assert result == [("ETH/USDT", "1h"), ("SOL/USDT", "1h")]
    # Same universe but on a different timeframe should NOT be excluded —
    # the exclude key is (pair, timeframe), not just pair.
    result_5m = await keepalive._load_keepalive_symbols(
        universe_factory, top_n=3,
        exclude=frozenset({("BTC/USDT", "1h")}), timeframe="5m",
    )
    assert ("BTC/USDT", "5m") in result_5m


@pytest.mark.asyncio
async def test_load_symbols_returns_empty_on_db_failure(monkeypatch: Any) -> None:
    """Any exception in load_current_universe → empty list, no raise."""

    class _Boom:
        def __call__(self) -> Any:
            class _Ctx:
                async def __aenter__(self) -> Any:
                    raise RuntimeError("DB unreachable")

                async def __aexit__(self, *a: Any) -> None:
                    return None

            return _Ctx()

    result = await keepalive._load_keepalive_symbols(
        _Boom(), top_n=5, exclude=frozenset(), timeframe="1h",  # type: ignore[arg-type]
    )
    assert result == []


# ---------------------------------------------------------------------------
# _refresh_children — reconciliation diff.


async def _idle_runner(_symbol: str, _tf: str) -> None:
    """Hangs forever — stands in for the real WS loop. Tests cancel it."""
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_refresh_children_spawns_new() -> None:
    children: dict[tuple[str, str], asyncio.Task] = {}
    desired = [("BTC/USDT", "1h"), ("ETH/USDT", "1h")]
    await keepalive._refresh_children(children, desired, runner=_idle_runner)
    try:
        assert set(children) == set(desired)
        for task in children.values():
            assert not task.done()
    finally:
        for task in children.values():
            task.cancel()


@pytest.mark.asyncio
async def test_refresh_children_cancels_dropped() -> None:
    children: dict[tuple[str, str], asyncio.Task] = {}
    await keepalive._refresh_children(
        children,
        [("BTC/USDT", "1h"), ("ETH/USDT", "1h"), ("SOL/USDT", "1h")],
        runner=_idle_runner,
    )
    sol_task = children[("SOL/USDT", "1h")]

    # SOL leaves the universe; ETH stays; ADA joins.
    await keepalive._refresh_children(
        children,
        [("BTC/USDT", "1h"), ("ETH/USDT", "1h"), ("ADA/USDT", "1h")],
        runner=_idle_runner,
    )
    try:
        assert ("SOL/USDT", "1h") not in children
        assert sol_task.cancelled() or sol_task.done()
        assert ("ADA/USDT", "1h") in children
        # BTC + ETH tasks must NOT be churned (identity preserved).
    finally:
        for task in children.values():
            task.cancel()


@pytest.mark.asyncio
async def test_refresh_children_no_churn_for_unchanged() -> None:
    """A repeat call with the same desired set must NOT recreate tasks."""
    children: dict[tuple[str, str], asyncio.Task] = {}
    desired = [("BTC/USDT", "1h"), ("ETH/USDT", "1h")]
    await keepalive._refresh_children(children, desired, runner=_idle_runner)
    snapshot = dict(children)
    await keepalive._refresh_children(children, desired, runner=_idle_runner)
    try:
        for key, task in snapshot.items():
            assert children[key] is task, (
                f"task identity for {key} changed across refresh — "
                "this would reset Binance WS connection rate limits"
            )
    finally:
        for task in children.values():
            task.cancel()


# ---------------------------------------------------------------------------
# _run_child_with_restart — crash-isolation supervisor.


@pytest.mark.asyncio
async def test_child_restart_on_exception(monkeypatch: Any) -> None:
    """A throwing runner must restart; supervisor must not propagate exc."""
    # Collapse the backoff to make the test fast.
    monkeypatch.setattr(keepalive, "KEEPALIVE_CHILD_BACKOFF_BASE_S", 0.001)
    monkeypatch.setattr(keepalive, "KEEPALIVE_CHILD_BACKOFF_MAX_S", 0.001)

    attempts = {"n": 0}

    async def _flaky(_sym: str, _tf: str) -> None:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("Binance hiccup")
        # After 3 attempts settle into the "happy path" — hang forever
        # to simulate a healthy WS stream.
        await asyncio.Event().wait()

    task = asyncio.create_task(
        keepalive._run_child_with_restart(_flaky, "ETH/USDT", "1h"),
    )
    # Give the supervisor enough loop turns to hit the failure and restart.
    for _ in range(50):
        await asyncio.sleep(0.005)
        if attempts["n"] >= 3:
            break

    try:
        assert attempts["n"] >= 3, (
            "supervisor should have restarted past the failing attempts"
        )
        assert not task.done(), "supervisor must still be alive after restarts"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_child_cancellation_propagates() -> None:
    """CancelledError must escape the restart loop — clean shutdown path."""

    task = asyncio.create_task(
        keepalive._run_child_with_restart(_idle_runner, "BTC/USDT", "1h"),
    )
    await asyncio.sleep(0)  # let it enter the runner
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# run_keepalive — supervisor smoke test with all I/O stubbed.


@pytest.mark.asyncio
async def test_run_keepalive_initial_load_and_clean_shutdown(
    monkeypatch: Any, universe_factory: Any,
) -> None:
    """Boot the supervisor with a seeded universe, let it spawn children
    via a stub runner, then cancel and verify clean shutdown."""
    await _seed_universe(
        universe_factory, symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    )

    spawned: list[tuple[str, str]] = []

    async def _track_runner(symbol_pair: str, timeframe: str) -> None:
        spawned.append((symbol_pair, timeframe))
        await asyncio.Event().wait()  # hold the slot open

    # Tight intervals so the initial heartbeat + child spawn happen
    # in a single event-loop turn before we cancel.
    task = asyncio.create_task(keepalive.run_keepalive(
        universe_factory,
        top_n=10,
        timeframe="1h",
        exclude=frozenset({("BTC/USDT", "1h")}),
        refresh_seconds=60,
        heartbeat_seconds=60,
        runner=_track_runner,
    ))

    # Let the supervisor finish its initial population.
    for _ in range(50):
        await asyncio.sleep(0.005)
        if len(spawned) >= 2:
            break

    try:
        assert set(spawned) == {("ETH/USDT", "1h"), ("SOL/USDT", "1h")}, (
            f"unexpected spawned set: {spawned}"
        )
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
