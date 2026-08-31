"""Unit tests for ``app.ws.keepalive`` — server-side WS supervisor.

These cover the four pieces of the supervisor in isolation:

1. ``to_pair`` — Binance no-slash ↔ slash-form normalization.
2. ``_load_keepalive_symbols`` — cohort read (``established_top20`` +
   ``liquidity_added_spot``) + exclude filtering against a real (sqlite)
   ``live_fleet_universe`` table; graceful empty on DB failure. No
   ``top_n`` slice — Phase 4 Task 5b switched the selection source from
   ``asset_universe``/``load_current_universe`` (rank-based top-N) to
   ``live_fleet_universe`` (liquidity-floor pass/fail, no rank cutoff on
   top of it). This is the intentional, pre-authorized source swap the
   Phase 4 plan doc's Task 5b calls out — the fixture, seeding helper,
   and the tests that read through them change accordingly below.
3. ``_refresh_children`` — reconciliation diff: add new, cancel removed
   (unless an open position retains it — Task 5b's new hard
   open-position override), leave existing untouched.
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
# to_pair — pure helper.


def test_to_pair_strips_usdt_suffix() -> None:
    assert keepalive.to_pair("BTCUSDT") == "BTC/USDT"
    assert keepalive.to_pair("ETHUSDT") == "ETH/USDT"
    assert keepalive.to_pair("1000PEPEUSDT") == "1000PEPE/USDT"


def test_to_pair_leaves_non_usdt_alone() -> None:
    """Defensive: if the table ever holds a non-USDT symbol we don't mangle it."""
    assert keepalive.to_pair("BTCBUSD") == "BTCBUSD"


# ---------------------------------------------------------------------------
# _load_keepalive_symbols — DB-backed, sqlite mirror of live_fleet_universe.


@pytest.fixture
async def fleet_factory():
    """In-memory sqlite standing in for live_fleet_universe. Schema mirrors
    the real Postgres migration (backend/alembic/versions/2026_08_17_0039_
    live_fleet_universe.py) with sqlite-equivalent column types — same
    shape used by tests/unit/test_live_fleet_universe.py's own fixture."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE live_fleet_universe ("
            "symbol TEXT NOT NULL, cohort TEXT NOT NULL, "
            "qvol_24h REAL NOT NULL, spread_bps REAL NOT NULL, "
            "depth_0_5pct_usdt REAL NOT NULL, "
            "snapshot_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (symbol, snapshot_at))"
        ))
    yield factory
    await engine.dispose()


async def _seed_fleet(
    factory: async_sessionmaker, *, entries: list[tuple[str, str]],
) -> None:
    """``entries``: list of ``(symbol_no_slash, cohort)`` tuples, all
    sharing one ``snapshot_at`` so they all count as "the latest
    snapshot" (``load_live_fleet_universe`` selects ``MAX(snapshot_at)``).
    Liquidity numbers are fixed dummy pass values — irrelevant to these
    tests, which only exercise cohort/exclude filtering, not the
    liquidity floor itself (that's covered by test_live_fleet_universe.py
    and test_futures_liquidity.py)."""
    async with factory() as session:
        for sym, cohort in entries:
            await session.execute(
                sa.text(
                    "INSERT INTO live_fleet_universe "
                    "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
                    "VALUES (:s, :c, 25000000, 2.0, 60000, '2026-08-17T00:00:00')"
                ),
                {"s": sym, "c": cohort},
            )
        await session.commit()


@pytest.mark.asyncio
async def test_load_symbols_reads_established_and_liquidity_added_no_top_n(
    fleet_factory: Any,
) -> None:
    """Both spot-backed cohorts are read in full — no top_n slice, since
    the liquidity floor itself is now the only membership criterion."""
    await _seed_fleet(
        fleet_factory,
        entries=[
            ("BTCUSDT", "established_top20"),
            ("ETHUSDT", "established_top20"),
            ("SOLUSDT", "liquidity_added_spot"),
            ("BNBUSDT", "liquidity_added_spot"),
            ("XRPUSDT", "liquidity_added_spot"),
        ],
    )
    result = await keepalive._load_keepalive_symbols(
        fleet_factory, exclude=frozenset(), timeframe="1h",
    )
    # All 5 rows across both cohorts come back — nothing sliced off.
    assert set(result) == {
        ("BTC/USDT", "1h"), ("ETH/USDT", "1h"), ("SOL/USDT", "1h"),
        ("BNB/USDT", "1h"), ("XRP/USDT", "1h"),
    }


@pytest.mark.asyncio
async def test_load_symbols_excludes_futures_poll_cohort(fleet_factory: Any) -> None:
    """``futures_poll`` rows belong to the separate futures REST-poll
    supervisor (Phase 4 Task 8) — _load_keepalive_symbols must never
    surface them, even though they live in the same table."""
    await _seed_fleet(
        fleet_factory,
        entries=[
            ("BTCUSDT", "established_top20"),
            ("NEWCOINUSDT", "futures_poll"),
        ],
    )
    result = await keepalive._load_keepalive_symbols(
        fleet_factory, exclude=frozenset(), timeframe="1h",
    )
    assert result == [("BTC/USDT", "1h")]


@pytest.mark.asyncio
async def test_load_symbols_filters_excludes(fleet_factory: Any) -> None:
    """Default exclude {(BTC/USDT, 1h)} must drop the singleton-owned pair."""
    await _seed_fleet(
        fleet_factory,
        entries=[
            ("BTCUSDT", "established_top20"),
            ("ETHUSDT", "established_top20"),
            ("SOLUSDT", "liquidity_added_spot"),
        ],
    )
    result = await keepalive._load_keepalive_symbols(
        fleet_factory, exclude=frozenset({("BTC/USDT", "1h")}), timeframe="1h",
    )
    assert set(result) == {("ETH/USDT", "1h"), ("SOL/USDT", "1h")}
    # Same fleet but on a different timeframe should NOT be excluded —
    # the exclude key is (pair, timeframe), not just pair.
    result_5m = await keepalive._load_keepalive_symbols(
        fleet_factory, exclude=frozenset({("BTC/USDT", "1h")}), timeframe="5m",
    )
    assert ("BTC/USDT", "5m") in result_5m


@pytest.mark.asyncio
async def test_load_symbols_returns_empty_on_db_failure(monkeypatch: Any) -> None:
    """Any exception in load_live_fleet_universe → empty list, no raise."""

    class _Boom:
        def __call__(self) -> Any:
            class _Ctx:
                async def __aenter__(self) -> Any:
                    raise RuntimeError("DB unreachable")

                async def __aexit__(self, *a: Any) -> None:
                    return None

            return _Ctx()

    result = await keepalive._load_keepalive_symbols(
        _Boom(), exclude=frozenset(), timeframe="1h",  # type: ignore[arg-type]
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

    # SOL leaves the fleet; ETH stays; ADA joins.
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
# _refresh_children — Phase 4 Task 5b hard open-position override (new).
#
# Deviates from the plan doc's own illustrative test by explicitly passing
# session_factory to _refresh_children: the override is gated on
# ``session_factory is not None`` (see _refresh_children's docstring), so
# a call site that omits it — as the plan doc's illustrative snippet did —
# would never invoke has_open_position at all, regardless of what it's
# monkeypatched to return. Verified directly against the real
# _refresh_children implementation in this file, not assumed from the plan.


@pytest.mark.asyncio
async def test_refresh_children_retains_dropped_symbol_with_open_position(
    monkeypatch: Any,
) -> None:
    """A symbol no longer in the desired set, but with an open position,
    is NOT cancelled — liquidity-floor-selector addendum (a)'s hard
    open-position override."""

    async def fake_has_open_position(_session: Any, symbol_pair: str) -> bool:
        return symbol_pair == "FOO/USDT"

    monkeypatch.setattr(keepalive, "has_open_position", fake_has_open_position)

    async def fake_runner(_symbol_pair: str, _timeframe: str) -> None:
        await asyncio.sleep(3600)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    children: dict[tuple[str, str], asyncio.Task] = {}
    try:
        await keepalive._refresh_children(
            children, [("FOO/USDT", "1h")], runner=fake_runner,
            session_factory=session_factory,
        )
        await asyncio.sleep(0)
        await keepalive._refresh_children(
            children, [], runner=fake_runner, session_factory=session_factory,
        )  # FOO drops out of the desired set
        assert ("FOO/USDT", "1h") in children  # retained — open position
        assert not children[("FOO/USDT", "1h")].done()
    finally:
        for task in children.values():
            task.cancel()
        await engine.dispose()


@pytest.mark.asyncio
async def test_refresh_children_cancels_dropped_without_open_position_even_with_session_factory(
    monkeypatch: Any,
) -> None:
    """Companion to the retention test above: proves the override is
    conditional on has_open_position's answer, not a blanket "passing
    session_factory means never cancel" — a dropped symbol WITHOUT an
    open position is still cancelled even when session_factory is
    supplied."""

    async def fake_has_open_position(_session: Any, _symbol_pair: str) -> bool:
        return False

    monkeypatch.setattr(keepalive, "has_open_position", fake_has_open_position)

    async def fake_runner(_symbol_pair: str, _timeframe: str) -> None:
        await asyncio.sleep(3600)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    children: dict[tuple[str, str], asyncio.Task] = {}
    try:
        await keepalive._refresh_children(
            children, [("BAR/USDT", "1h")], runner=fake_runner,
            session_factory=session_factory,
        )
        bar_task = children[("BAR/USDT", "1h")]
        await keepalive._refresh_children(
            children, [], runner=fake_runner, session_factory=session_factory,
        )
        assert ("BAR/USDT", "1h") not in children
        assert bar_task.cancelled() or bar_task.done()
    finally:
        for task in children.values():
            task.cancel()
        await engine.dispose()


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
    monkeypatch: Any, fleet_factory: Any,
) -> None:
    """Boot the supervisor with a seeded live_fleet_universe, let it spawn
    children via a stub runner, then cancel and verify clean shutdown.

    top_n is no longer a run_keepalive parameter (Phase 4 Task 5b removed
    it along with the rank-cutoff selection it fed — the liquidity floor
    is now the only membership criterion), so this test's seeding source
    and call site both change from the pre-Task-5b version accordingly.
    The initial-population path never reaches the open-position-override
    branch (children starts empty, so nothing is up for cancellation),
    so no live_trades/shadow_open_positions table is needed in the
    fixture for this smoke test."""
    await _seed_fleet(
        fleet_factory,
        entries=[
            ("BTCUSDT", "established_top20"),
            ("ETHUSDT", "established_top20"),
            ("SOLUSDT", "liquidity_added_spot"),
        ],
    )

    spawned: list[tuple[str, str]] = []

    async def _track_runner(symbol_pair: str, timeframe: str) -> None:
        spawned.append((symbol_pair, timeframe))
        await asyncio.Event().wait()  # hold the slot open

    # Tight intervals so the initial heartbeat + child spawn happen
    # in a single event-loop turn before we cancel.
    task = asyncio.create_task(keepalive.run_keepalive(
        fleet_factory,
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


# ---------------------------------------------------------------------------
# Phase 4 Task 5f — root-cause regression: last_refresh must be a genuine
# captured loop.time() reading, not a hardcoded 0.0.
#
# See docs/superpowers/decisions/2026-08-19-live-fleet-universe-never-
# scheduled-incident.md's second "Implementation note" for the full
# incident. Summary: `now - last_refresh >= refresh_seconds` used
# `now = loop.time()`, backed by CLOCK_MONOTONIC -- a clock Docker
# containers share with their host kernel (no per-container reset), so its
# absolute value reflects host-wide elapsed time, not process/container
# start time. On a long-lived host, `loop.time()` was already far larger
# than any refresh_seconds, so `last_refresh = 0.0` made the reconciliation
# gate trivially true on the very first in-loop check -- it "worked" only
# by accident. A host reboot near a container restart would reset the
# clock and make the identical code genuinely wait the full nominal
# interval -- an environment-dependent divergence between a long-running
# host and a freshly-rebooted one.


class _FakeClock:
    """Deterministic stand-in for ``loop.time()``, decoupled from real
    wall-clock time.

    Starts at ``start`` (a value far larger than any ``refresh_seconds``
    under test -- simulating a long-lived host's CLOCK_MONOTONIC reading)
    and advances by ``step`` SIMULATED seconds on every call. This lets
    the test prove reconciliation only fires once ``step``-accumulated
    SIMULATED elapsed time genuinely reaches ``refresh_seconds`` since the
    last capture -- not merely because the absolute reading is large,
    which is exactly what the old ``last_refresh = 0.0`` bug got wrong.
    """

    def __init__(self, start: float, step: float) -> None:
        self._value = start
        self._step = step
        self.call_count = 0

    def __call__(self) -> float:
        self.call_count += 1
        current = self._value
        self._value += self._step
        return current


class _LoopTimeProxy:
    """Wraps the REAL running loop, forwarding every attribute unchanged
    EXCEPT ``.time()``.

    ``BaseEventLoop._run_once`` calls ``self.time()`` directly on its own
    instance (not via ``asyncio.get_event_loop()``) to compute callback
    deadlines and ``select()`` timeouts on every iteration -- confirmed
    directly against this repo's CPython/Windows ProactorEventLoop before
    settling on this design: monkeypatching the REAL loop instance's
    ``.time`` attribute in place corrupts that internal scheduling (the
    loop's own deadline math sees the same wildly-jumping fake values our
    test feeds it), producing garbled, non-deterministic sleep behavior --
    exactly the kind of flaky test this rollout's own lesson
    ([[test_suite_scanner_universe_flakiness]]) warns about. Swapping what
    ``asyncio.get_event_loop()`` *returns* to callers, instead, leaves the
    real loop's own internal `self` reference (and thus its scheduling)
    completely untouched -- only code that explicitly calls
    ``asyncio.get_event_loop().time()``, which is exactly what
    ``run_keepalive`` does, observes the fake clock.
    """

    def __init__(self, real_loop: asyncio.AbstractEventLoop, fake_clock: _FakeClock) -> None:
        self._real_loop = real_loop
        self._fake_clock = fake_clock

    def time(self) -> float:
        return self._fake_clock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real_loop, name)


@pytest.mark.asyncio
async def test_run_keepalive_reconciliation_requires_genuine_elapsed_loop_time(
    monkeypatch: Any, fleet_factory: Any,
) -> None:
    """Phase 4 Task 5f: reconciliation must NOT fire just because
    ``loop.time()``'s absolute value happens to exceed ``refresh_seconds``
    (a long-uptime host) -- only after a genuine ``refresh_seconds`` has
    elapsed, in SIMULATED loop-time terms, since the initial population.

    This is the test that would have caught the actual staging bug: under
    the old ``last_refresh = 0.0`` code, a single call to the fake clock
    returning ~100_025 (far bigger than ``refresh_seconds=60``) would
    already satisfy ``now - 0.0 >= refresh_seconds`` and reconcile on the
    very first in-loop tick. Under the fix (``last_refresh = loop.time()``,
    captured after the initial population), the diff computed on that same
    first tick is only ``25`` (one ``step``), so it correctly does NOT
    fire — reconciliation only fires on the third tick, once 75 SIMULATED
    seconds have genuinely accrued since the capture.
    """
    await _seed_fleet(fleet_factory, entries=[("BTCUSDT", "established_top20")])

    # start >> refresh_seconds below, exactly the long-uptime-host scenario
    # that let the old bug hide undetected on staging.
    real_loop = asyncio.get_event_loop()
    fake_clock = _FakeClock(start=100_000.0, step=25.0)
    proxy_loop = _LoopTimeProxy(real_loop, fake_clock)
    monkeypatch.setattr(keepalive.asyncio, "get_event_loop", lambda: proxy_loop)

    refresh_calls = 0
    real_load = keepalive._load_keepalive_symbols

    async def _counting_load(*args: Any, **kwargs: Any) -> list[tuple[str, str, str]]:
        nonlocal refresh_calls
        refresh_calls += 1
        return await real_load(*args, **kwargs)

    monkeypatch.setattr(keepalive, "_load_keepalive_symbols", _counting_load)

    async def _noop_runner(_symbol_pair: str, _timeframe: str, _cohort: str) -> None:
        await asyncio.Event().wait()

    # heartbeat_seconds is real wall-clock time (kept tiny so the test is
    # fast); refresh_seconds is compared against the fully-decoupled
    # SIMULATED clock above.
    task = asyncio.create_task(keepalive.run_keepalive(
        fleet_factory,
        timeframe="1h",
        exclude=frozenset(),
        refresh_seconds=60,
        heartbeat_seconds=0.01,
        runner=_noop_runner,
    ))

    async def _wait_until(predicate: Any, *, budget_s: float = 5.0) -> None:
        elapsed = 0.0
        step_s = 0.005
        while not predicate():
            await asyncio.sleep(step_s)
            elapsed += step_s
            if elapsed >= budget_s:
                raise AssertionError("timed out waiting for condition")

    try:
        # Initial population's own _load_keepalive_symbols call (runs
        # before last_refresh is even captured) -- not part of the
        # reconciliation logic under test.
        await _wait_until(lambda: refresh_calls >= 1)
        assert refresh_calls == 1

        # Two in-loop ticks have now happened (fake_clock.call_count counts
        # the last_refresh capture itself plus one call per tick, so
        # call_count >= 3 means 2 ticks completed): cumulative SIMULATED
        # elapsed time is 50s (2 * step), still short of refresh_seconds=60.
        # Reconciliation must NOT have fired yet.
        await _wait_until(lambda: fake_clock.call_count >= 3)
        assert refresh_calls == 1, (
            "reconciliation fired before refresh_seconds of genuine "
            "SIMULATED elapsed time had accrued since the initial "
            "population -- this is the last_refresh=0.0 root-cause bug "
            "(see docs/superpowers/decisions/2026-08-19-live-fleet-"
            "universe-never-scheduled-incident.md)"
        )

        # Tick 3 pushes cumulative SIMULATED elapsed time to 75s (>= 60) --
        # reconciliation must fire now.
        await _wait_until(lambda: refresh_calls >= 2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

