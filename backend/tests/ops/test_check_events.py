"""check_events: the retrieval path, and the guard on its own worst bug.

The load-bearing test here is
``test_a_check_that_NEVER_ran_is_reported_and_a_GROUP_BY_cannot_see_it``.
Everything else is mechanics.
"""
from __future__ import annotations

import ast
import datetime as _dt
import importlib.util
import pathlib

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ops import check_events as ce


MIG = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_10_26_0044_check_events.py"
)


def _apply_migration(sync_conn) -> None:
    """Run the REAL migration, so these tests cannot pass against a
    hand-written fixture that has drifted from what prod will have."""
    import alembic
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    assert MIG.is_file(), f"migration not found at {MIG}"
    ops = Operations(MigrationContext.configure(sync_conn))
    saved = getattr(alembic, "op", None)
    alembic.op = ops
    try:
        spec = importlib.util.spec_from_file_location("_mig0044", MIG)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.upgrade()
    finally:
        alembic.op = saved


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_apply_migration)
    ce._reset_for_tests()
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        ce._reset_for_tests()
        await engine.dispose()


T0 = _dt.datetime(2026, 10, 26, 12, 0, 0, tzinfo=_dt.timezone.utc)


async def _rows(session: AsyncSession, name: str | None = None):
    sql = (
        "SELECT check_name, check_kind, status, granularity, bucket_date, "
        "subject, observed_count, detail, first_seen_at, last_seen_at "
        "FROM check_events"
    )
    params = {}
    if name is not None:
        sql += " WHERE check_name = :n"
        params["n"] = name
    sql += " ORDER BY id"
    return (await session.execute(sa.text(sql), params)).all()


# ---------------------------------------------------------------------------
# Rule 2 -- SILENCE IS NOT SUCCESS. This is the whole point of the table.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_check_that_NEVER_ran_is_reported_and_a_GROUP_BY_cannot_see_it(
    factory, monkeypatch,
) -> None:
    """A registry entry with ZERO rows must be reported as silent.

    This is `phase4-universe-discrepancy-check`'s exact failure mode: a
    dead script, invisible for months, because every run exited 0 and
    nothing recorded the non-run.

    The test does not merely assert that find_silent_checks works -- it
    RUNS THE OBVIOUS WRONG IMPLEMENTATION beside it and shows that the
    wrong one is blind. Without that half, a future edit could replace
    the registry iteration with a GROUP BY and this test would still
    pass as long as some other check happened to be stale.
    """
    monkeypatch.setitem(
        ce.EXPECTED_CHECKS, "never_wired_probe",
        ce.ExpectedCheck(
            name="never_wired_probe", kind="probe",
            max_silence=_dt.timedelta(hours=1),
            why="a probe that was never wired up",
        ),
    )
    async with factory() as session:
        # Another check HAS reported, so the table is not empty -- an
        # empty table would make a GROUP BY trivially return nothing and
        # the comparison below would prove nothing.
        await ce.record_event(
            session, "some_other_check", kind="guard", status="ok", now=T0,
        )
        await session.commit()

        # (a) THE WRONG IMPLEMENTATION: group over the table.
        grouped = (await session.execute(sa.text(
            "SELECT check_name, MAX(last_seen_at) FROM check_events "
            "GROUP BY check_name"
        ))).all()
        grouped_names = {r[0] for r in grouped}
        assert grouped_names == {"some_other_check"}
        assert "never_wired_probe" not in grouped_names, (
            "a GROUP BY over check_events cannot name a check that never "
            "wrote a row -- that is the bug, demonstrated here so the "
            "assertion below has something to beat"
        )

        # (b) THE REAL IMPLEMENTATION: iterate the registry.
        silent = await ce.find_silent_checks(session, now=T0)
        by_name = {s.name: s for s in silent}
        assert "never_wired_probe" in by_name, (
            "find_silent_checks must report a registry entry with no rows"
        )
        found = by_name["never_wired_probe"]
        assert found.never_reported is True
        assert found.last_seen_at is None
        assert found.age is None


@pytest.mark.asyncio
async def test_never_reported_is_a_DISTINCT_state_from_merely_stale(
    factory, monkeypatch,
) -> None:
    """Stale and never-run must not render identically.

    #610 was the collapse of two different facts onto one string. A
    never-run check has no evidence it ever executed; a stale one has a
    last-known-good timestamp. Conflating them (e.g. by reporting
    age=infinity for both) throws away the only datum that distinguishes
    "broken recently" from "never worked".
    """
    monkeypatch.setitem(
        ce.EXPECTED_CHECKS, "never_wired_probe",
        ce.ExpectedCheck("never_wired_probe", "probe",
                         _dt.timedelta(hours=1), "never wired"),
    )
    monkeypatch.setitem(
        ce.EXPECTED_CHECKS, "stale_probe",
        ce.ExpectedCheck("stale_probe", "probe",
                         _dt.timedelta(hours=1), "ran, then stopped"),
    )
    async with factory() as session:
        await ce.record_event(
            session, "stale_probe", kind="probe", status="ok",
            now=T0 - _dt.timedelta(hours=5),
        )
        await session.commit()
        silent = {s.name: s for s in await ce.find_silent_checks(session, now=T0)}

    assert set(silent) >= {"never_wired_probe", "stale_probe"}
    assert silent["never_wired_probe"].never_reported is True
    assert silent["stale_probe"].never_reported is False
    assert silent["stale_probe"].last_seen_at == T0 - _dt.timedelta(hours=5)
    assert silent["stale_probe"].age == _dt.timedelta(hours=5)


@pytest.mark.asyncio
async def test_a_fresh_check_is_NOT_reported(factory, monkeypatch) -> None:
    """The positive control: the detector must be able to stay quiet.

    Without this, ``return [everything]`` would pass every test above --
    a detector that reports all checks as silent at all times is useless
    in precisely the way that gets an alarm channel muted.
    """
    monkeypatch.setitem(
        ce.EXPECTED_CHECKS, "fresh_probe",
        ce.ExpectedCheck("fresh_probe", "probe",
                         _dt.timedelta(hours=1), "healthy"),
    )
    async with factory() as session:
        await ce.record_event(
            session, "fresh_probe", kind="probe", status="ok",
            now=T0 - _dt.timedelta(minutes=5),
        )
        await session.commit()
        silent = {s.name for s in await ce.find_silent_checks(session, now=T0)}
    assert "fresh_probe" not in silent


@pytest.mark.asyncio
async def test_an_ok_row_counts_as_reporting(factory, monkeypatch) -> None:
    """status='ok' must satisfy the silence check.

    If only violations counted as evidence of life, a healthy check would
    look dead -- which inverts rule 2 instead of implementing it.
    """
    monkeypatch.setitem(
        ce.EXPECTED_CHECKS, "ok_only_probe",
        ce.ExpectedCheck("ok_only_probe", "probe",
                         _dt.timedelta(hours=1), "only ever passes"),
    )
    async with factory() as session:
        await ce.record_daily(
            session, "ok_only_probe", kind="probe", status="ok",
            increment=0, now=T0 - _dt.timedelta(minutes=1),
        )
        await session.commit()
        silent = {s.name for s in await ce.find_silent_checks(session, now=T0)}
    assert "ok_only_probe" not in silent


# ---------------------------------------------------------------------------
# Rule 3 -- GRANULARITY MATCHED TO THE ALARM.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_granularity_writes_one_row_per_call(factory) -> None:
    """Sparse mode: three firings, three rows, bucket_date NULL on each.

    The NULL is load-bearing: the unique index covers
    (check_name, subject, bucket_date) unconditionally, and these rows
    only coexist because NULLs compare as DISTINCT in a unique index.
    If that assumption were wrong, the second insert would raise.
    """
    async with factory() as session:
        for i in range(3):
            await ce.record_event(
                session, "sparse_alarm", kind="guard", status="violation",
                subject="BTCUSDT", detail={"i": i},
                now=T0 + _dt.timedelta(seconds=i),
            )
        await session.commit()
        rows = await _rows(session, "sparse_alarm")

    assert len(rows) == 3, "event granularity must not collapse"
    assert all(r.granularity == "event" for r in rows)
    assert all(r.bucket_date is None for r in rows)
    assert all(r.observed_count == 1 for r in rows)
    assert all(r.subject == "BTCUSDT" for r in rows)


@pytest.mark.asyncio
async def test_daily_granularity_collapses_and_accumulates(factory) -> None:
    """Continuous mode: 1 + 5 + 4 firings on one day -> ONE row, count 10.

    Hand-derived: increments 1, 5, 4 sum to 10 and the row count is 1.
    """
    async with factory() as session:
        for inc, mins in ((1, 0), (5, 30), (4, 90)):
            await ce.record_daily(
                session, "continuous_alarm", kind="counter",
                status="violation", subject="XPLUSDT", increment=inc,
                now=T0 + _dt.timedelta(minutes=mins),
            )
        await session.commit()
        rows = await _rows(session, "continuous_alarm")

    assert len(rows) == 1, f"daily granularity must collapse, got {len(rows)}"
    row = rows[0]
    assert row.observed_count == 10
    assert row.granularity == "daily"
    # first_seen_at is the "when did this start today" datum and must NOT
    # be overwritten by later increments; last_seen_at must advance.
    assert row.first_seen_at == T0.isoformat(sep=" ")
    assert row.last_seen_at == (T0 + _dt.timedelta(minutes=90)).isoformat(sep=" ")


@pytest.mark.asyncio
async def test_daily_rows_separate_by_subject_and_by_day(factory) -> None:
    """One row per (check, subject, day) -- not one row per check."""
    async with factory() as session:
        await ce.record_daily(session, "c", kind="counter", status="violation",
                              subject="AAAUSDT", increment=2, now=T0)
        await ce.record_daily(session, "c", kind="counter", status="violation",
                              subject="BBBUSDT", increment=3, now=T0)
        await ce.record_daily(session, "c", kind="counter", status="violation",
                              subject="AAAUSDT", increment=7,
                              now=T0 + _dt.timedelta(days=1))
        await session.commit()
        rows = await _rows(session, "c")

    assert len(rows) == 3
    got = {(r.subject, r.bucket_date, r.observed_count) for r in rows}
    assert got == {
        ("AAAUSDT", "2026-10-26", 2),
        ("BBBUSDT", "2026-10-26", 3),
        ("AAAUSDT", "2026-10-27", 7),
    }


# ---------------------------------------------------------------------------
# The first customer: #610's counter.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flush_writes_DELTAS_not_the_cumulative_total(factory) -> None:
    """The counter is cumulative; the table stores increments.

    Hand-derived. Flush 1 sees BTC=3. Flush 2 sees BTC=5 (two more
    failures since). The correct stored total for the day is 5, NOT
    3 + 5 = 8. Writing the absolute value each time is the obvious bug
    and it inflates by the number of flushes.
    """
    async with factory() as session:
        n1 = await ce.flush_mtf_fetch_failures(
            session, now=T0, counts={"BTCUSDT": 3})
        n2 = await ce.flush_mtf_fetch_failures(
            session, now=T0 + _dt.timedelta(minutes=5), counts={"BTCUSDT": 5})
        await session.commit()
        rows = await _rows(session, "mtf_kline_fetch_failed")

    assert (n1, n2) == (1, 1)
    per_symbol = [r for r in rows if r.subject == "BTCUSDT"]
    assert len(per_symbol) == 1
    assert per_symbol[0].observed_count == 5, (
        "stored total must equal the cumulative counter, not the sum of "
        "the per-flush readings"
    )


@pytest.mark.asyncio
async def test_flush_with_no_change_adds_nothing_but_still_heartbeats(
    factory,
) -> None:
    """An unchanged counter must not re-add, yet must still prove life."""
    async with factory() as session:
        await ce.flush_mtf_fetch_failures(
            session, now=T0, counts={"BTCUSDT": 4})
        n2 = await ce.flush_mtf_fetch_failures(
            session, now=T0 + _dt.timedelta(minutes=5),
            counts={"BTCUSDT": 4})
        await session.commit()
        rows = await _rows(session, "mtf_kline_fetch_failed")

    assert n2 == 0
    sym = [r for r in rows if r.subject == "BTCUSDT"]
    assert len(sym) == 1 and sym[0].observed_count == 4
    beat = [r for r in rows if r.subject == ""]
    assert len(beat) == 1, "the heartbeat row must exist"
    assert beat[0].last_seen_at == (
        T0 + _dt.timedelta(minutes=5)).isoformat(sep=" "), (
        "the heartbeat must advance on a no-change flush -- that advance "
        "is the only thing separating a healthy flusher from a dead one"
    )


@pytest.mark.asyncio
async def test_flush_with_zero_failures_writes_an_ok_heartbeat(factory) -> None:
    """Zero failures is a RESULT, not an absence of one."""
    async with factory() as session:
        n = await ce.flush_mtf_fetch_failures(session, now=T0, counts={})
        await session.commit()
        rows = await _rows(session, "mtf_kline_fetch_failed")

    assert n == 0
    assert len(rows) == 1
    assert rows[0].subject == ""
    assert rows[0].status == "ok"
    assert rows[0].observed_count == 0
    # And that row is enough to keep the registry entry out of the silent list.
    async with factory() as s2:
        pass
    assert "mtf_kline_fetch_failed" in ce.EXPECTED_CHECKS, (
        "the first customer must be registered, or its silence is unwatched"
    )


@pytest.mark.asyncio
async def test_counter_going_backwards_resyncs_instead_of_double_counting(
    factory,
) -> None:
    """A process restart resets the in-memory counter to a LOWER value.

    Treating that as a delta would write a negative increment; ignoring
    the resync would make every subsequent flush re-add from the old
    high-water mark. Hand-derived: after 9 then 2, the stored total stays
    9; a further rise to 4 adds 2 (4 - 2), giving 11.
    """
    async with factory() as session:
        await ce.flush_mtf_fetch_failures(session, now=T0, counts={"X": 9})
        await ce.flush_mtf_fetch_failures(
            session, now=T0 + _dt.timedelta(minutes=5), counts={"X": 2})
        await ce.flush_mtf_fetch_failures(
            session, now=T0 + _dt.timedelta(minutes=10), counts={"X": 4})
        await session.commit()
        rows = await _rows(session, "mtf_kline_fetch_failed")

    sym = [r for r in rows if r.subject == "X"]
    assert len(sym) == 1
    assert sym[0].observed_count == 11


# ---------------------------------------------------------------------------
# NO AUTO-REMEDIATION -- enforced structurally, not by reviewer vigilance.
# ---------------------------------------------------------------------------


def test_this_module_can_only_ever_write_to_check_events() -> None:
    """The no-remediation rule, as a property of the code rather than a
    promise in a docstring.

    Every SQL string literal in the module must name `check_events` and
    no other table, and none may DELETE or DROP. A remediation hook would
    have to UPDATE or DELETE some OTHER table -- deactivate a checkpoint,
    clear a cooldown, flip a flag -- so a module that provably touches
    only its own append-only table cannot heal anything, whatever a
    future docstring claims.

    MUTATIONS RUN: adding `UPDATE rl_checkpoints SET is_active=FALSE` ->
    red here; adding `DELETE FROM check_events` -> red here; renaming a
    column inside an existing check_events statement -> green here and
    red in the mechanics tests above, which is the correct split.
    """
    src = pathlib.Path(ce.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Known table names in this schema that a healer would reach for.
    forbidden_tables = (
        "rl_checkpoints", "ml_checkpoints", "predictions", "shadow_trades",
        "live_trades", "live_cooldowns", "users", "asset_universe",
        "prediction_validations", "worker_heartbeats", "auth_violations",
    )
    sql_like = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            up = node.value.upper()
            if any(verb in up for verb in
                   ("INSERT ", "UPDATE ", "DELETE ", "SELECT ", "DROP ")):
                sql_like.append(node.value)

    assert sql_like, "found no SQL in the module -- the scan is not working"

    for frag in sql_like:
        up = frag.upper()
        assert "DELETE " not in up, f"DELETE is not detection: {frag!r}"
        assert "DROP " not in up, f"DROP is not detection: {frag!r}"
        for table in forbidden_tables:
            assert table not in frag, (
                f"this module must not reference {table!r}: {frag!r}"
            )

    joined = " ".join(sql_like).upper()
    assert "CHECK_EVENTS" in joined


# Action-shaped verbs. Matched on IDENTIFIER COMPONENTS, never as raw
# substrings -- see _looks_like_an_action.
_BANNED_VERBS = (
    "repair", "heal", "fix", "retry", "activate", "deactivate",
    "disable", "enable", "restart", "promote", "remediate",
)


def _looks_like_an_action(name: str) -> bool:
    """True if any underscore-delimited component IS a banned verb.

    COMPONENT MATCHING, NOT SUBSTRING MATCHING, and the distinction is
    not cosmetic: the first version of this guard used
    ``word not in name.lower()`` and went red on
    ``QUEUE_PROGRESS_PREFIX`` because "fix" is inside "pre-FIX". A
    substring assertion that fires on an unrelated word is the
    substring-assertion weakness in the cannot-fail family, and the
    repair is to make the predicate precise rather than to rename the
    constant to dodge it.

    Scope stated honestly: this catches the realistic additions, which in
    this codebase's naming are underscore-delimited (``heal_x``,
    ``retry_x``, ``fix_x``). A run-together spelling like ``autofix``
    would pass, so this guard is a tripwire on the obvious case and not
    a proof of absence. The proof of absence is
    ``test_this_module_can_only_ever_write_to_check_events``, which
    constrains what the code can DO rather than what it is called.
    """
    parts = [p for p in name.lower().split("_") if p]
    return any(p in _BANNED_VERBS for p in parts)


def test_the_action_name_guard_is_itself_calibrated() -> None:
    """Positive AND negative control on the predicate above.

    Without the negative half, ``return True`` passes the real guard's
    intent inverted and flags everything; without the positive half,
    ``return False`` makes the guard decorative. Both halves are the
    reason the substring bug was caught at all.
    """
    # MUST flag
    for bad in ("heal_silent_checks", "retry_failed", "repair_queue",
                "activate_checkpoint", "HEAL", "promote_now"):
        assert _looks_like_an_action(bad), bad
    # MUST NOT flag
    for ok in ("QUEUE_PROGRESS_PREFIX", "record_queue_pass",
               "find_silent_checks", "STALL_ALARM_PASSES", "record_daily"):
        assert not _looks_like_an_action(ok), ok


def test_no_remediation_entrypoint_is_exported() -> None:
    """__all__ must expose only recording and detection.

    A name like `repair`, `heal`, `retry` or `activate` appearing here
    would be the first sign the module had grown an action.
    """
    for name in ce.__all__:
        assert not _looks_like_an_action(name), (
            f"{name!r} in __all__ looks like an action, not a detection"
        )


def test_every_registry_entry_has_a_positive_max_silence() -> None:
    """A zero or negative window would report its check silent forever;
    an absurdly long one would never report it at all. Both are ways of
    having a registry that cannot do its job."""
    assert ce.EXPECTED_CHECKS, "an empty registry watches nothing"
    for name, exp in ce.EXPECTED_CHECKS.items():
        assert exp.name == name, f"registry key {name!r} != entry {exp.name!r}"
        assert exp.max_silence > _dt.timedelta(0), name
        assert exp.max_silence <= _dt.timedelta(days=2), (
            f"{name}: a window over 2 days is not a watchdog"
        )
        assert exp.why.strip(), f"{name}: no reason recorded"
