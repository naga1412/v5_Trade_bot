"""Task 8: `futures_poll_task` supervisor.

Mirrors tests/unit/test_ws_keepalive.py's (post-Task-5b) structure -- same
fleet pattern, same isolation guarantees, same open-position override,
different candle source (REST poll instead of spot WS).

REDRAFTED 2026-08-17: the plan doc's original draft asserted the OPPOSITE
of what's tested here -- "unconditional cancel, retention isn't being
built" -- for the drop-out case. The liquidity-floor-selector addendum
made the hard open-position override a requirement on BOTH fleets, so
this file's drop-out tests now assert: unconditional cancel is the base
case (no session_factory / no open position), retention is the override
(open position present).

One deviation from the plan doc's own Step 2 test snippet, verified before
writing this file:

`test_retains_dropped_child_with_open_position` -- the plan doc's own
illustrative snippet passes `session_factory=object()`. `_refresh_futures_
children` mirrors keepalive.py's `_refresh_children` exactly, which means
the drop-out branch calls `async with session_factory() as session:` when
`session_factory is not None` -- i.e. `session_factory` must be an
invocable async-context-manager factory, not merely a non-None sentinel.
A bare `object()` instance has no `__call__`, so `object()()` raises
`TypeError: 'object' object is not callable` (confirmed directly against
CPython before writing this test) the moment the second `_refresh_futures_
children` call reaches the cancellation branch -- the plan's own snippet
would never reach its `assert ... in children` line. This test uses a real
in-memory sqlite `async_sessionmaker`, exactly mirroring
`test_ws_keepalive.py::test_refresh_children_retains_dropped_symbol_with_
open_position`'s proven-working pattern for the identical scenario on the
spot-WS fleet, rather than the plan's uncallable placeholder.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ws.futures_poll import _refresh_futures_children, run_futures_poll


@pytest.mark.asyncio
async def test_spawns_a_child_per_desired_symbol() -> None:
    spawned: list[tuple[str, str]] = []

    async def fake_runner(symbol_pair: str, timeframe: str) -> None:
        spawned.append((symbol_pair, timeframe))
        await asyncio.sleep(3600)  # never returns on its own

    children: dict[tuple[str, str], asyncio.Task[None]] = {}
    try:
        await _refresh_futures_children(
            children, [("FOO/USDT", "1h"), ("BAR/USDT", "1h")], runner=fake_runner,
        )
        await asyncio.sleep(0)  # let the spawned tasks start
        assert set(children.keys()) == {("FOO/USDT", "1h"), ("BAR/USDT", "1h")}
        assert set(spawned) == {("FOO/USDT", "1h"), ("BAR/USDT", "1h")}
    finally:
        for task in children.values():
            task.cancel()


@pytest.mark.asyncio
async def test_cancels_child_when_symbol_drops_out_and_no_open_position() -> None:
    """REPLACES the original draft's 'unconditional cancel, retention
    isn't being built' test -- retention IS being built now (addendum
    (a), hard requirement). Base case unchanged: no open position (here,
    no session_factory at all) means the drop-out still cancels exactly
    as before."""
    async def fake_runner(symbol_pair: str, timeframe: str) -> None:
        await asyncio.sleep(3600)

    children: dict[tuple[str, str], asyncio.Task[None]] = {}
    await _refresh_futures_children(children, [("FOO/USDT", "1h")], runner=fake_runner)
    await asyncio.sleep(0)
    assert ("FOO/USDT", "1h") in children

    await _refresh_futures_children(children, [], runner=fake_runner)  # FOO drops out
    assert ("FOO/USDT", "1h") not in children


@pytest.mark.asyncio
async def test_retains_dropped_child_with_open_position(monkeypatch: Any) -> None:
    """NEW -- addendum (a)'s hard open-position override, futures-poll side.

    See this file's module docstring for why a real in-memory sqlite
    session_factory is used here instead of the plan doc's uncallable
    `object()` placeholder."""
    from app.ws import futures_poll as fmod

    async def fake_has_open_position(session: Any, symbol_pair: str) -> bool:
        return symbol_pair == "FOO/USDT"

    monkeypatch.setattr(fmod, "has_open_position", fake_has_open_position)

    async def fake_runner(symbol_pair: str, timeframe: str) -> None:
        await asyncio.sleep(3600)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    children: dict[tuple[str, str], asyncio.Task[None]] = {}
    try:
        await _refresh_futures_children(
            children, [("FOO/USDT", "1h")], runner=fake_runner, session_factory=session_factory,
        )
        await asyncio.sleep(0)
        await _refresh_futures_children(
            children, [], runner=fake_runner, session_factory=session_factory,
        )
        assert ("FOO/USDT", "1h") in children  # retained
        assert not children[("FOO/USDT", "1h")].done()
    finally:
        for task in children.values():
            task.cancel()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancels_dropped_child_without_open_position_even_with_session_factory(
    monkeypatch: Any,
) -> None:
    """Companion to the retention test above: proves the override is
    conditional on has_open_position's answer, not a blanket "passing
    session_factory means never cancel" -- a dropped symbol WITHOUT an
    open position is still cancelled even when session_factory is
    supplied. Not one of the plan doc's 4 listed cases, but the doc's own
    Step 4 implementation sketch has this exact branch (the `if await
    has_open_position(...)` check only `continue`s on True), and
    test_ws_keepalive.py carries the identical companion test for
    keepalive.py's twin code path -- added here for the same coverage
    parity."""

    from app.ws import futures_poll as fmod

    async def fake_has_open_position(_session: Any, _symbol_pair: str) -> bool:
        return False

    monkeypatch.setattr(fmod, "has_open_position", fake_has_open_position)

    async def fake_runner(symbol_pair: str, timeframe: str) -> None:
        await asyncio.sleep(3600)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    children: dict[tuple[str, str], asyncio.Task[None]] = {}
    try:
        await _refresh_futures_children(
            children, [("BAR/USDT", "1h")], runner=fake_runner, session_factory=session_factory,
        )
        bar_task = children[("BAR/USDT", "1h")]
        await _refresh_futures_children(
            children, [], runner=fake_runner, session_factory=session_factory,
        )
        assert ("BAR/USDT", "1h") not in children
        assert bar_task.cancelled() or bar_task.done()
    finally:
        for task in children.values():
            task.cancel()
        await engine.dispose()


@pytest.mark.asyncio
async def test_child_crash_does_not_take_down_siblings() -> None:
    from app.ws.futures_poll import _run_futures_child_with_restart

    calls: dict[str, int] = {"FOO/USDT": 0, "BAR/USDT": 0}

    async def flaky_runner(symbol_pair: str, timeframe: str) -> None:
        calls[symbol_pair] += 1
        if symbol_pair == "FOO/USDT" and calls[symbol_pair] == 1:
            raise RuntimeError("simulated crash")
        await asyncio.sleep(3600)

    task_foo = asyncio.create_task(
        _run_futures_child_with_restart(flaky_runner, "FOO/USDT", "1h", backoff_base_s=0.01),
    )
    task_bar = asyncio.create_task(
        _run_futures_child_with_restart(flaky_runner, "BAR/USDT", "1h", backoff_base_s=0.01),
    )
    try:
        await asyncio.sleep(0.1)
        assert calls["FOO/USDT"] >= 2  # crashed once, restarted
        assert calls["BAR/USDT"] >= 1  # never affected by FOO's crash
    finally:
        task_foo.cancel()
        task_bar.cancel()
        for t in (task_foo, task_bar):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# run_futures_poll -- supervisor smoke test with all I/O stubbed. Not one of
# the plan doc's 4 listed cases, but mirrors test_ws_keepalive.py's own
# `test_run_keepalive_initial_load_and_clean_shutdown` smoke test for
# run_keepalive -- added for the same reason: exercise the outer loop's
# initial-population + clean-shutdown path (deterministic, no long-running
# tick), leaving the internal reconciliation logic to the unit tests above.


@pytest.mark.asyncio
async def test_run_futures_poll_initial_load_and_clean_shutdown(monkeypatch: Any) -> None:
    from app.ws import futures_poll as fmod

    async def fake_load_desired(session_factory: Any, *, timeframe: str) -> list[tuple[str, str]]:
        return [("FOO/USDT", timeframe), ("BAR/USDT", timeframe)]

    monkeypatch.setattr(fmod, "_load_desired_futures_symbols", fake_load_desired)

    async def fake_record_heartbeat(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(fmod, "record_heartbeat", fake_record_heartbeat)

    spawned: list[tuple[str, str]] = []

    async def _track_runner(symbol_pair: str, timeframe: str) -> None:
        spawned.append((symbol_pair, timeframe))
        await asyncio.Event().wait()  # hold the slot open

    task = asyncio.create_task(run_futures_poll(
        object(),  # never touched directly -- _load_desired_futures_symbols is stubbed
        timeframe="1h",
        refresh_seconds=60,
        heartbeat_seconds=60,
        runner=_track_runner,
    ))

    for _ in range(50):
        await asyncio.sleep(0.005)
        if len(spawned) >= 2:
            break

    try:
        assert set(spawned) == {("FOO/USDT", "1h"), ("BAR/USDT", "1h")}
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# Phase 4 Task 5f — root-cause regression: last_refresh must be a genuine
# captured loop.time() reading, not a hardcoded 0.0. Mirrors
# tests/unit/test_ws_keepalive.py's
# test_run_keepalive_reconciliation_requires_genuine_elapsed_loop_time
# exactly -- run_futures_poll's outer loop is structurally identical to
# run_keepalive's (see docs/superpowers/decisions/2026-08-19-live-fleet-
# universe-never-scheduled-incident.md's second "Implementation note").
#
# Summary: `now - last_refresh >= refresh_seconds` used `now = loop.time()`,
# backed by CLOCK_MONOTONIC -- a clock Docker containers share with their
# host kernel (no per-container reset), so its absolute value reflects
# host-wide elapsed time, not process/container start time. On a
# long-lived host, `loop.time()` was already far larger than any
# refresh_seconds, so `last_refresh = 0.0` made the reconciliation gate
# trivially true on the very first in-loop check -- it "worked" only by
# accident. A host reboot near a container restart would reset the clock
# and make the identical code genuinely wait the full nominal interval --
# an environment-dependent divergence between a long-running host and a
# freshly-rebooted one.


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
    directly against this repo's CPython/Windows ProactorEventLoop while
    building this test's twin in test_ws_keepalive.py: monkeypatching the
    REAL loop instance's ``.time`` attribute in place corrupts that
    internal scheduling (the loop's own deadline math sees the same
    wildly-jumping fake values our test feeds it), producing garbled,
    non-deterministic sleep behavior. Swapping what
    ``asyncio.get_event_loop()`` *returns* to callers, instead, leaves the
    real loop's own internal `self` reference (and thus its scheduling)
    completely untouched -- only code that explicitly calls
    ``asyncio.get_event_loop().time()``, which is exactly what
    ``run_futures_poll`` does, observes the fake clock.
    """

    def __init__(self, real_loop: asyncio.AbstractEventLoop, fake_clock: _FakeClock) -> None:
        self._real_loop = real_loop
        self._fake_clock = fake_clock

    def time(self) -> float:
        return self._fake_clock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real_loop, name)


@pytest.mark.asyncio
async def test_run_futures_poll_reconciliation_requires_genuine_elapsed_loop_time(
    monkeypatch: Any,
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
    from app.ws import futures_poll as fmod

    refresh_calls = 0

    async def fake_load_desired(session_factory: Any, *, timeframe: str) -> list[tuple[str, str]]:
        nonlocal refresh_calls
        refresh_calls += 1
        return [("FOO/USDT", timeframe)]

    monkeypatch.setattr(fmod, "_load_desired_futures_symbols", fake_load_desired)

    async def fake_record_heartbeat(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(fmod, "record_heartbeat", fake_record_heartbeat)

    # start >> refresh_seconds below, exactly the long-uptime-host scenario
    # that let the old bug hide undetected on staging.
    real_loop = asyncio.get_event_loop()
    fake_clock = _FakeClock(start=100_000.0, step=25.0)
    proxy_loop = _LoopTimeProxy(real_loop, fake_clock)
    monkeypatch.setattr(fmod.asyncio, "get_event_loop", lambda: proxy_loop)

    async def _noop_runner(_symbol_pair: str, _timeframe: str) -> None:
        await asyncio.Event().wait()

    # heartbeat_seconds is real wall-clock time (kept tiny so the test is
    # fast); refresh_seconds is compared against the fully-decoupled
    # SIMULATED clock above.
    task = asyncio.create_task(run_futures_poll(
        object(),  # never touched directly -- _load_desired_futures_symbols is stubbed
        timeframe="1h",
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
        # Initial population's own _load_desired_futures_symbols call (runs
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
