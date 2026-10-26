"""The head-of-line block, its fix, and the alarm that would have caught it.

The load-bearing test is
``test_a_poisoned_head_BLOCKS_without_the_deadletter_and_CLEARS_with_it``.
It runs the pre-fix selector beside the post-fix one over the same rows
and shows the pre-fix one never reaches the validatable row -- the same
shape as #612's GROUP-BY test, for the same reason: a fix whose
discriminating power is not demonstrated is a fix nobody can check.

Measured context (migration 0045's docstring has the full account): six
futures-only symbols entered at 2026-09-02 17:00Z, were validated in
real time ZERO times, and their 129 rows exceeded batch_limit=100.
Validation stopped 2026-09-03 and stayed stopped 53 days.
"""
from __future__ import annotations

import ast
import datetime as _dt
import importlib.util
import pathlib

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ml import validator
from app.ops import check_events as ce


T0 = _dt.datetime(2026, 9, 3, 12, 0, 0, tzinfo=_dt.timezone.utc)

_DDL = (
    "CREATE TABLE prediction_validations ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "prediction_id INTEGER, user_id INTEGER NOT NULL, "
    "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
    "direction TEXT NOT NULL, score REAL NOT NULL, confidence REAL NOT NULL, "
    "anchor_ts TIMESTAMP NOT NULL, anchor_close REAL NOT NULL, "
    "target_ts TIMESTAMP NOT NULL, target_close REAL, "
    "was_correct BOOLEAN, pnl_pct REAL, validated_at TIMESTAMP, "
    "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "validation_attempts INTEGER NOT NULL DEFAULT 0, "
    "last_attempt_at TIMESTAMP, unvalidatable_reason TEXT)"
)

CE_MIG = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_10_26_0044_check_events.py"
)


def _apply_check_events(sync_conn) -> None:
    import alembic
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    ops = Operations(MigrationContext.configure(sync_conn))
    saved = getattr(alembic, "op", None)
    alembic.op = ops
    try:
        spec = importlib.util.spec_from_file_location("_mig0044b", CE_MIG)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.upgrade()
    finally:
        alembic.op = saved


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_DDL))
        await conn.run_sync(_apply_check_events)
    ce._reset_for_tests()
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        ce._reset_for_tests()
        await engine.dispose()


async def _insert(factory, symbol: str, target_ts: _dt.datetime) -> int:
    async with factory() as s:
        r = await s.execute(
            sa.text(
                "INSERT INTO prediction_validations ("
                "prediction_id, user_id, symbol, timeframe, direction, score,"
                " confidence, anchor_ts, anchor_close, target_ts) "
                "VALUES (1, 1, :sym, '1h', 'LONG', 0.4, 0.5, :a, 100.0, :t)"
            ),
            {"sym": symbol, "a": target_ts - _dt.timedelta(hours=1),
             "t": target_ts},
        )
        await s.commit()
        return int(r.lastrowid)


def _fetch_stub(bad_symbols: set[str]):
    """Return bars for every symbol EXCEPT the named ones.

    Mirrors the real failure: a futures-only symbol asked of SPOT klines
    returns nothing, and `_fetch_close_at` turns that into None.
    """
    async def _stub(http, symbol, timeframe, ts):
        if symbol in bad_symbols:
            return None
        return 102.0
    return _stub


# ---------------------------------------------------------------------------
# THE LOAD-BEARING TEST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_poisoned_head_BLOCKS_without_the_deadletter_and_CLEARS_with_it(
    factory, monkeypatch,
) -> None:
    """3 un-validatable rows ahead of 1 good row, batch_limit=3.

    Hand-derived. The poison count (3) equals batch_limit (3), so the
    batch is entirely poison and the good row -- which is FOURTH by
    target_ts -- is never selected. That is the real defect at
    1/33rd scale: in prod it was 129 poison rows against a batch of 100.

    PART A runs the PRE-FIX selector (no unvalidatable_reason filter) in
    a loop and asserts the good row is NEVER reached, no matter how many
    passes run. PART B runs the real post-fix code and asserts it IS
    reached. Without Part A, Part B would also pass for an
    implementation that simply had a bigger batch, which is exactly the
    non-fix the order rules out.
    """
    bad = {"1000PEPE/USDT", "1000SHIB/USDT", "USELESS/USDT"}
    for i, sym in enumerate(sorted(bad)):
        await _insert(factory, sym, T0 - _dt.timedelta(hours=10 - i))
    good_id = await _insert(factory, "BTC/USDT", T0 - _dt.timedelta(hours=1))

    monkeypatch.setattr(validator, "_fetch_close_at", _fetch_stub(bad))

    # ---- PART A: the pre-fix selector, 20 passes ----
    pre_fix_sql = (
        "SELECT id FROM prediction_validations "
        "WHERE validated_at IS NULL AND target_ts <= :now "
        "ORDER BY target_ts ASC LIMIT 3"
    )
    reached_pre_fix: set[int] = set()
    for _ in range(20):
        async with factory() as s:
            ids = {r.id for r in (await s.execute(
                sa.text(pre_fix_sql), {"now": T0})).all()}
        reached_pre_fix |= ids
    assert good_id not in reached_pre_fix, (
        "the PRE-FIX selector must never reach the good row -- if it "
        "does, this test is not reproducing the head-of-line block and "
        "proves nothing about the fix"
    )
    assert len(reached_pre_fix) == 3, reached_pre_fix

    # ---- PART B: the real code ----
    advanced_total = 0
    for _ in range(20):
        res = await validator.validate_pending_detailed(
            factory, http=object(), now=T0, batch_limit=3,
        )
        advanced_total += res.advanced

    assert advanced_total == 1, (
        f"exactly the one validatable row should ever advance, got "
        f"{advanced_total}"
    )
    async with factory() as s:
        row = (await s.execute(sa.text(
            "SELECT validated_at, target_close FROM prediction_validations "
            "WHERE id = :i"), {"i": good_id})).first()
    assert row.validated_at is not None, (
        "the good row BEHIND the poison must get validated once the "
        "poison is dead-lettered -- this is the entire fix"
    )
    assert row.target_close == pytest.approx(102.0)

    async with factory() as s:
        dead = (await s.execute(sa.text(
            "SELECT symbol, unvalidatable_reason, validation_attempts "
            "FROM prediction_validations "
            "WHERE unvalidatable_reason IS NOT NULL ORDER BY symbol"))).all()
    assert {d.symbol for d in dead} == bad
    assert all(d.validation_attempts == validator.MAX_VALIDATION_ATTEMPTS
               for d in dead), [(d.symbol, d.validation_attempts) for d in dead]


# ---------------------------------------------------------------------------
# Dead-letter mechanics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attempts_increment_once_per_pass_and_dead_letter_at_the_threshold(
    factory, monkeypatch,
) -> None:
    """Hand-derived: MAX_VALIDATION_ATTEMPTS is 6, so passes 1..5 leave the
    row eligible with attempts == pass number, and pass 6 writes the
    terminal reason. A row must NOT be written off early (that discards
    recoverable transient failures) nor late (that is the stall)."""
    rid = await _insert(factory, "XXX/USDT", T0 - _dt.timedelta(hours=2))
    monkeypatch.setattr(validator, "_fetch_close_at",
                        _fetch_stub({"XXX/USDT"}))

    seen: list[tuple[int, str | None]] = []
    for _ in range(validator.MAX_VALIDATION_ATTEMPTS):
        await validator.validate_pending_detailed(
            factory, http=object(), now=T0, batch_limit=10)
        async with factory() as s:
            r = (await s.execute(sa.text(
                "SELECT validation_attempts, unvalidatable_reason "
                "FROM prediction_validations WHERE id = :i"), {"i": rid})
            ).first()
        seen.append((r.validation_attempts, r.unvalidatable_reason))

    assert [a for a, _ in seen] == list(
        range(1, validator.MAX_VALIDATION_ATTEMPTS + 1))
    assert all(reason is None for _, reason in seen[:-1]), seen
    assert seen[-1][1] is not None
    assert "close_unavailable" in seen[-1][1]
    assert str(validator.MAX_VALIDATION_ATTEMPTS) in seen[-1][1]


@pytest.mark.asyncio
async def test_a_dead_lettered_row_is_EXCLUDED_from_selection(
    factory, monkeypatch,
) -> None:
    """The exclusion is the mechanism. Counting attempts without removing
    the row from the eligible set would leave the block in place with
    better logging."""
    rid = await _insert(factory, "XXX/USDT", T0 - _dt.timedelta(hours=2))
    async with factory() as s:
        await s.execute(sa.text(
            "UPDATE prediction_validations "
            "SET unvalidatable_reason = 'manual' WHERE id = :i"), {"i": rid})
        await s.commit()
    monkeypatch.setattr(validator, "_fetch_close_at", _fetch_stub(set()))

    res = await validator.validate_pending_detailed(
        factory, http=object(), now=T0, batch_limit=10)
    assert res.selected == 0, (
        "a dead-lettered row must not be selected even though it is "
        "still unvalidated and still due"
    )


@pytest.mark.asyncio
async def test_a_validatable_row_is_never_dead_lettered(
    factory, monkeypatch,
) -> None:
    """THE POSITIVE CONTROL. A dead-letter path that writes off healthy
    rows would satisfy every test above while silently discarding good
    data -- worse than the stall it replaces."""
    rid = await _insert(factory, "BTC/USDT", T0 - _dt.timedelta(hours=2))
    monkeypatch.setattr(validator, "_fetch_close_at", _fetch_stub(set()))

    for _ in range(validator.MAX_VALIDATION_ATTEMPTS + 3):
        await validator.validate_pending_detailed(
            factory, http=object(), now=T0, batch_limit=10)

    async with factory() as s:
        r = (await s.execute(sa.text(
            "SELECT validated_at, unvalidatable_reason, validation_attempts "
            "FROM prediction_validations WHERE id = :i"), {"i": rid})).first()
    assert r.validated_at is not None
    assert r.unvalidatable_reason is None
    assert r.validation_attempts == 0, (
        "a row that validated on its first attempt must not carry an "
        "attempt count"
    )


# ---------------------------------------------------------------------------
# (selected, advanced) -- and LIVENESS != PROGRESS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_LIVENESS_only_check_cannot_see_a_stuck_worker(
    factory, monkeypatch,
) -> None:
    """The gap #612 does not close, demonstrated rather than asserted.

    #612's find_silent_checks detects a check that FAILED TO EMIT. This
    defect emitted faithfully every 60s for 53 days. So the test runs
    the LIVENESS view beside the PROGRESS view over the same stalled
    passes and shows liveness reads healthy throughout.

    It also shows why `advanced == 0` alone is not the alarm: the final
    pass has advanced == 0 AND selected == 0 -- a genuinely idle queue --
    and must NOT be flagged.
    """
    bad = {"1000PEPE/USDT"}
    await _insert(factory, "1000PEPE/USDT", T0 - _dt.timedelta(hours=2))
    monkeypatch.setattr(validator, "_fetch_close_at", _fetch_stub(bad))

    # STRICTLY MORE passes than STALL_ALARM_PASSES. At exactly the
    # threshold, `streak == STALL_ALARM_PASSES` and
    # `streak >= STALL_ALARM_PASSES` behave identically, so a test that
    # stops at 3 cannot tell a once-per-episode onset row from one
    # written on every stalled pass. A mutant proved that: it SURVIVED
    # against a 3-pass version of this test. The count has to exceed the
    # threshold for the assertion below to discriminate.
    n_passes = ce.STALL_ALARM_PASSES + 2
    assert n_passes > ce.STALL_ALARM_PASSES
    # MAX_VALIDATION_ATTEMPTS must exceed n_passes, or the row would be
    # dead-lettered mid-test and later passes would read selected=0
    # (idle, not stalled) -- which would make this test pass for the
    # wrong reason.
    assert validator.MAX_VALIDATION_ATTEMPTS > n_passes, (
        "test design: the row must stay eligible for every pass here"
    )
    stalled_passes = 0
    for i in range(n_passes):
        res = await validator.validate_pending_detailed(
            factory, http=object(), now=T0, batch_limit=10)
        assert res.selected == 1, i
        assert res.advanced == 0, i
        assert res.stalled is True, i
        stalled_passes += 1
        async with factory() as s:
            await ce.record_queue_pass(
                s, "prediction_validator_task",
                selected=res.selected, advanced=res.advanced,
                now=T0 + _dt.timedelta(minutes=i),
            )
            await s.commit()
    assert stalled_passes == n_passes

    # (a) THE LIVENESS VIEW: did the worker emit recently? Yes, always.
    async with factory() as s:
        liveness = (await s.execute(sa.text(
            "SELECT MAX(last_seen_at) AS seen FROM check_events "
            "WHERE check_name = :n"),
            {"n": "queue_progress:prediction_validator_task"})).first()
    assert liveness.seen is not None, (
        "the worker DID emit -- if this is None the test is not "
        "reproducing an alive-but-stuck worker"
    )
    silent = await _silent_names(
        factory, T0 + _dt.timedelta(minutes=n_passes))
    assert "queue_progress:prediction_validator_task" not in silent, (
        "a liveness/silence check must read this worker as HEALTHY -- "
        "that blindness is the point being demonstrated"
    )

    # (b) THE PROGRESS VIEW: stalled passes == total passes.
    async with factory() as s:
        rows = (await s.execute(sa.text(
            "SELECT subject, observed_count FROM check_events "
            "WHERE check_name = :n AND granularity = 'daily'"),
            {"n": "queue_progress:prediction_validator_task"})).all()
    by_subject = {r.subject: r.observed_count for r in rows}
    assert by_subject[""] == n_passes, by_subject
    assert by_subject["stalled"] == n_passes, by_subject

    # (c) AND THE ONSET is a single sparse row, not one per pass.
    async with factory() as s:
        onset = (await s.execute(sa.text(
            "SELECT COUNT(*) FROM check_events "
            "WHERE granularity = 'event' AND subject = 'stall_onset'"))
        ).scalar_one()
    assert onset == 1, (
        f"the onset must be written exactly once per episode, got {onset}"
    )

    # (d) AN IDLE QUEUE IS NOT A STALL.
    async with factory() as s:
        await s.execute(sa.text(
            "UPDATE prediction_validations SET unvalidatable_reason = 'x'"))
        await s.commit()
    idle = await validator.validate_pending_detailed(
        factory, http=object(), now=T0, batch_limit=10)
    assert idle.selected == 0 and idle.advanced == 0
    assert idle.stalled is False, (
        "selected=0, advanced=0 is a healthy idle queue and must never "
        "alarm -- conflating it with a stall is why advanced alone was "
        "useless for 53 days"
    )


async def _silent_names(factory, now):
    async with factory() as s:
        return {x.name for x in await ce.find_silent_checks(s, now=now)}


@pytest.mark.asyncio
async def test_a_recovered_pass_resets_the_streak(factory, monkeypatch) -> None:
    """Positive control for the streak: an alarm that latches forever
    cannot tell a fixed worker from a broken one."""
    async with factory() as s:
        await ce.record_queue_pass(s, "w", selected=5, advanced=0, now=T0)
        st2 = await ce.record_queue_pass(
            s, "w", selected=5, advanced=0,
            now=T0 + _dt.timedelta(minutes=1))
        st3 = await ce.record_queue_pass(
            s, "w", selected=5, advanced=2,
            now=T0 + _dt.timedelta(minutes=2))
        await s.commit()
    assert st2 == 2
    assert st3 == 0, "a pass that advanced must clear the streak"


# ---------------------------------------------------------------------------
# Contract + drift guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_thin_wrapper_preserves_the_int_contract(
    factory, monkeypatch,
) -> None:
    """validate_pending must still return a plain int.

    Widening it in place is the pitfall that broke three integration
    unpack sites when PR #266 bumped a tuple from 8 to 9, so the
    detailed result went into a NEW function and this asserts the old
    one did not change shape.
    """
    await _insert(factory, "BTC/USDT", T0 - _dt.timedelta(hours=2))
    monkeypatch.setattr(validator, "_fetch_close_at", _fetch_stub(set()))
    n = await validator.validate_pending(
        factory, http=object(), now=T0, batch_limit=10)
    assert isinstance(n, int) and not isinstance(n, bool)
    assert n == 1


def test_the_selector_filters_dead_letters_and_still_orders_oldest_first() -> None:
    """Structural: the fix is a WHERE clause, so assert the WHERE clause.

    Oldest-first is deliberately KEPT -- validating in due order is
    correct behaviour, and the bug was never the ordering. Replacing
    ASC with DESC or random would 'fix' the symptom by abandoning
    chronological validation, so this pins both halves: the exclusion is
    present AND the ordering is unchanged.
    """
    src = pathlib.Path(validator.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef)
        and n.name == "validate_pending_detailed"
    )
    sql = " ".join(
        " ".join(c.value.split())
        for c in ast.walk(fn)
        if isinstance(c, ast.Constant) and isinstance(c.value, str)
        and "prediction_validations" in c.value
    )
    assert "unvalidatable_reason IS NULL" in sql, sql
    assert "validated_at IS NULL" in sql, sql
    assert "ORDER BY target_ts ASC" in sql, sql
    assert "LIMIT :lim" in sql, sql


def test_the_test_fixture_matches_migration_0045() -> None:
    """Fixture/migration drift guard.

    The validator tests build prediction_validations by hand, so a
    column that exists only in the fixture (or only in the migration)
    would let every test pass while prod rejected the write. Runs the
    REAL 0045 against a real table and compares the column sets.
    """
    import alembic
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = (
        pathlib.Path(__file__).resolve().parents[1]
        / ".." / "alembic" / "versions"
        / "2026_10_26_0045_validation_attempts_deadletter.py"
    ).resolve()
    assert mig.is_file(), f"migration not found at {mig}"

    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        # Pre-0045 shape: the fixture DDL minus the three new columns.
        base = _DDL.replace(
            ", validation_attempts INTEGER NOT NULL DEFAULT 0, "
            "last_attempt_at TIMESTAMP, unvalidatable_reason TEXT", ""
        )
        assert "validation_attempts" not in base, base
        conn.exec_driver_sql(base)

        ops = Operations(MigrationContext.configure(conn))
        saved = getattr(alembic, "op", None)
        alembic.op = ops
        try:
            spec = importlib.util.spec_from_file_location("_mig0045", mig)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.upgrade()
            assert mod.down_revision == "0044_check_events"
            migrated = {
                r[1] for r in conn.exec_driver_sql(
                    "PRAGMA table_info(prediction_validations)")
            }
            mod.downgrade()
            after = {
                r[1] for r in conn.exec_driver_sql(
                    "PRAGMA table_info(prediction_validations)")
            }
        finally:
            alembic.op = saved

    fixture_engine = sa.create_engine("sqlite://")
    with fixture_engine.begin() as c2:
        c2.exec_driver_sql(_DDL)
        fixture_cols = {
            r[1] for r in c2.exec_driver_sql(
                "PRAGMA table_info(prediction_validations)")
        }

    assert "validation_attempts" in migrated
    assert fixture_cols == migrated, (
        f"fixture/migration drift -- only in migration: "
        f"{sorted(migrated - fixture_cols)}; only in fixture: "
        f"{sorted(fixture_cols - migrated)}"
    )
    assert "validation_attempts" not in after, (
        "downgrade must drop the columns it added"
    )
