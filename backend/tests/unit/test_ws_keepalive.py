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

from app.db.payload_builders import build_predictions_payload
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
        ("BTC/USDT", "1h", "established_top20"),
        ("ETH/USDT", "1h", "established_top20"),
        ("SOL/USDT", "1h", "liquidity_added_spot"),
        ("BNB/USDT", "1h", "liquidity_added_spot"),
        ("XRP/USDT", "1h", "liquidity_added_spot"),
    }


@pytest.mark.asyncio
async def test_load_symbols_tags_each_symbol_with_its_own_cohort(
    fleet_factory: Any,
) -> None:
    """Phase 4 Task 9: the cohort element must reflect which
    live_fleet_universe query the symbol actually came from -- a
    liquidity_added_spot symbol must never be silently tagged
    established_top20 (or vice versa)."""
    await _seed_fleet(
        fleet_factory,
        entries=[
            ("BTCUSDT", "established_top20"),
            ("NEWCOINUSDT", "liquidity_added_spot"),
        ],
    )
    result = await keepalive._load_keepalive_symbols(
        fleet_factory, exclude=frozenset(), timeframe="1h",
    )
    by_pair = {pair: cohort for pair, _tf, cohort in result}
    assert by_pair["BTC/USDT"] == "established_top20"
    assert by_pair["NEWCOIN/USDT"] == "liquidity_added_spot"


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
    assert result == [("BTC/USDT", "1h", "established_top20")]


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
    assert set(result) == {
        ("ETH/USDT", "1h", "established_top20"),
        ("SOL/USDT", "1h", "liquidity_added_spot"),
    }
    # Same fleet but on a different timeframe should NOT be excluded —
    # the exclude key is (pair, timeframe), not just pair.
    result_5m = await keepalive._load_keepalive_symbols(
        fleet_factory, exclude=frozenset({("BTC/USDT", "1h")}), timeframe="5m",
    )
    assert ("BTC/USDT", "5m", "established_top20") in result_5m


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


async def _idle_runner(_symbol: str, _tf: str, _cohort: str) -> None:
    """Hangs forever — stands in for the real WS loop. Tests cancel it."""
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_refresh_children_spawns_new() -> None:
    children: dict[tuple[str, str], asyncio.Task] = {}
    desired = [
        ("BTC/USDT", "1h", "established_top20"),
        ("ETH/USDT", "1h", "established_top20"),
    ]
    await keepalive._refresh_children(children, desired, runner=_idle_runner)
    try:
        assert set(children) == {("BTC/USDT", "1h"), ("ETH/USDT", "1h")}
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
        [
            ("BTC/USDT", "1h", "established_top20"),
            ("ETH/USDT", "1h", "established_top20"),
            ("SOL/USDT", "1h", "liquidity_added_spot"),
        ],
        runner=_idle_runner,
    )
    sol_task = children[("SOL/USDT", "1h")]

    # SOL leaves the fleet; ETH stays; ADA joins.
    await keepalive._refresh_children(
        children,
        [
            ("BTC/USDT", "1h", "established_top20"),
            ("ETH/USDT", "1h", "established_top20"),
            ("ADA/USDT", "1h", "liquidity_added_spot"),
        ],
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
    desired = [
        ("BTC/USDT", "1h", "established_top20"),
        ("ETH/USDT", "1h", "established_top20"),
    ]
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


@pytest.mark.asyncio
async def test_refresh_children_threads_cohort_into_runner() -> None:
    """Phase 4 Task 9: the cohort element of a desired-set entry must
    reach the runner call verbatim -- this is the mechanism
    _default_runner relies on to thread symbol_source into
    run_live_prediction."""
    calls: list[tuple[str, str, str]] = []

    async def _capturing_runner(symbol: str, tf: str, cohort: str) -> None:
        calls.append((symbol, tf, cohort))
        await asyncio.Event().wait()

    children: dict[tuple[str, str], asyncio.Task] = {}
    try:
        await keepalive._refresh_children(
            children,
            [
                ("BTC/USDT", "1h", "established_top20"),
                ("NEWCOIN/USDT", "1h", "liquidity_added_spot"),
            ],
            runner=_capturing_runner,
        )
        await asyncio.sleep(0)  # let the spawned tasks reach the capture line
        assert ("BTC/USDT", "1h", "established_top20") in calls
        assert ("NEWCOIN/USDT", "1h", "liquidity_added_spot") in calls
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

    async def fake_runner(_symbol_pair: str, _timeframe: str, _cohort: str) -> None:
        await asyncio.sleep(3600)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    children: dict[tuple[str, str], asyncio.Task] = {}
    try:
        await keepalive._refresh_children(
            children, [("FOO/USDT", "1h", "established_top20")], runner=fake_runner,
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

    async def fake_runner(_symbol_pair: str, _timeframe: str, _cohort: str) -> None:
        await asyncio.sleep(3600)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    children: dict[tuple[str, str], asyncio.Task] = {}
    try:
        await keepalive._refresh_children(
            children, [("BAR/USDT", "1h", "established_top20")], runner=fake_runner,
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

    async def _flaky(_sym: str, _tf: str, _cohort: str) -> None:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("Binance hiccup")
        # After 3 attempts settle into the "happy path" — hang forever
        # to simulate a healthy WS stream.
        await asyncio.Event().wait()

    task = asyncio.create_task(
        keepalive._run_child_with_restart(_flaky, "ETH/USDT", "1h", "established_top20"),
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
        keepalive._run_child_with_restart(_idle_runner, "BTC/USDT", "1h", "established_top20"),
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

    async def _track_runner(symbol_pair: str, timeframe: str, _cohort: str) -> None:
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
# Phase 4 Task 9 — cohort tag reaches the predictions row (end-to-end,
# minus real network I/O).
#
# The 2026-08-17 redraft calls this out explicitly: "add a test asserting
# a liquidity_added_spot symbol's predictions row is actually tagged
# liquidity_added_spot, not silently defaulted to established_top20 --
# that silent-default failure mode is exactly the kind of thing this task
# exists to prevent." This test drives the real production call chain --
# live_fleet_universe (DB) -> _load_keepalive_symbols -> _refresh_children
# -> _default_runner -- with only run_live_prediction itself stubbed out
# (so no real Binance WS/REST calls happen), and has the stub build a
# real predictions payload via build_predictions_payload (the same
# function run_live_prediction's own body calls) so the assertion is
# against the literal payload dict that would become the predictions row,
# not an intermediate proxy value.


class _StubFinalForTag:
    score = 0.5
    direction = "LONG"
    confidence = 70.0


class _StubPredForTag:
    timeframe = "1h"
    ts = None
    final = _StubFinalForTag()
    inputs_hash = "b" * 64
    cold_start = False

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol


@pytest.mark.asyncio
async def test_liquidity_added_spot_symbol_tagged_through_to_predictions_row(
    monkeypatch: Any, fleet_factory: Any,
) -> None:
    """established_top20 and liquidity_added_spot symbols served by the
    SAME keepalive fleet must end up with DIFFERENT symbol_source values
    on the predictions payload their run_live_prediction call would
    persist -- proving the cohort survives live_fleet_universe ->
    _load_keepalive_symbols -> _refresh_children -> _default_runner ->
    run_live_prediction(symbol_source=...) -> build_predictions_payload."""
    await _seed_fleet(
        fleet_factory,
        entries=[
            ("BTCUSDT", "established_top20"),
            ("NEWCOINUSDT", "liquidity_added_spot"),
        ],
    )

    captured_payloads: dict[str, dict[str, Any]] = {}

    async def fake_run_live_prediction(
        symbol_pair: str, timeframe: str, *,
        candle_source: Any = None, symbol_source: str = "established_top20",
    ) -> None:
        pred = _StubPredForTag(symbol_pair)
        captured_payloads[symbol_pair] = build_predictions_payload(
            pred, user_id=1, layer_payload={}, symbol_source=symbol_source,
        )
        await asyncio.Event().wait()  # simulate the long-running WS loop

    # Patch the module-global run_live_prediction binding -- _default_runner
    # looks this name up at call time (not a def-time-bound default), so
    # this patch reaches it exactly like a real run_live_prediction bug
    # fix would.
    monkeypatch.setattr(keepalive, "run_live_prediction", fake_run_live_prediction)

    desired = await keepalive._load_keepalive_symbols(
        fleet_factory, exclude=frozenset(), timeframe="1h",
    )
    children: dict[tuple[str, str], asyncio.Task] = {}
    try:
        await keepalive._refresh_children(
            children, desired, runner=keepalive._default_runner,
        )
        await asyncio.sleep(0)  # let the spawned tasks reach the capture line

        # _load_keepalive_symbols normalizes to slash form (to_pair) before
        # _refresh_children ever sees the symbol, so the runner (and this
        # capture) receives "BTC/USDT" / "NEWCOIN/USDT", not the raw
        # Binance no-slash form seeded into live_fleet_universe.
        assert captured_payloads["BTC/USDT"]["symbol_source"] == "established_top20"
        assert captured_payloads["NEWCOIN/USDT"]["symbol_source"] == "liquidity_added_spot"
    finally:
        for task in children.values():
            task.cancel()
