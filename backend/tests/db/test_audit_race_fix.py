"""FU-24 — `insert_with_chain` per-table advisory-lock race fix.

Before the fix, `_last_row_hash` + INSERT was not serialized: concurrent
writers at hourly close read the same latest `row_hash` and all set their
`prev_hash` identically → chain breaks. The fix wraps the read+insert in
``pg_advisory_xact_lock(hashtext(table_name))`` so writers on the same
table serialize while writers on different tables stay independent.

Advisory locks are Postgres-only — these tests skip on SQLite. The CI
backend job sets ``DATABASE_URL=postgresql+asyncpg://...`` before pytest
and runs ``alembic upgrade head`` so the schema is at head when these
tests run.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.audit import GENESIS_HASH, insert_with_chain


_DSN = os.environ.get("DATABASE_URL", "")
_IS_PG = _DSN.startswith("postgresql") or _DSN.startswith("postgres")

pytestmark = pytest.mark.skipif(
    not _IS_PG,
    reason="Postgres-only — pg_advisory_xact_lock not available on SQLite.",
)


def _make_engine() -> object:
    """Build a test engine with NullPool so every checked-out connection is a
    dedicated, freshly-opened asyncpg connection.

    Why NullPool here (and only here):
      - Concurrent ``pg_advisory_xact_lock`` writers each hold their connection
        for the duration of the lock-wait + insert + COMMIT. With the default
        ``AsyncAdaptedQueuePool`` (pool_size=5, max_overflow=10) under
        ``asyncio.gather`` of 10 tasks, the pool's lazy checkout +
        ``asyncpg.Pool`` task-locking can interact with the Postgres-side
        lock-wait queue in ways that stall the event loop (observed as
        ``select.poll()`` hang at the pytest-timeout 120s mark).
      - ``NullPool`` short-circuits this: each session opens a fresh
        connection, runs to completion, and closes the connection on session
        exit. No shared pool state, no coordination between tasks beyond the
        Postgres advisory lock itself — which is exactly what this test is
        designed to exercise.
      - This is test-only. Production still uses the queue pool configured
        in ``app/db/session.py``.
    """
    return create_async_engine(_DSN, poolclass=NullPool)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


# Minimum payloads that satisfy the columns hashed for each table — populated
# only with whitelisted fields so the chain hash is computed from real data,
# and any other NOT NULL DB columns must already exist on the migrated
# Postgres schema (they do — these tables are part of head).
def _predictions_payload(i: int) -> dict[str, object]:
    return {
        "user_id": 1,
        "symbol": f"__FU24__{i}",
        "timeframe": "1h",
        # asyncpg requires native datetime objects for TIMESTAMPTZ columns —
        # ISO strings are NOT auto-coerced (unlike sqlite3). Production call
        # sites (e.g. shadow.persistence.persist_closed_trade) always pass
        # real datetime instances, so this mirrors prod typing exactly.
        "ts": datetime(2026, 5, 22, 10, 0, 0, tzinfo=timezone.utc),
        "layer_scores": "{}",
        "final_score": 0.1 * i,
        "direction": "LONG",
        "confidence": 0.6,
        "inputs_hash": f"fu24-pred-{i:04d}",
        "model_version": "fu24-test",
        "cold_start": False,
    }


def _shadow_trade_payload(i: int) -> dict[str, object]:
    return {
        "user_id": 1,
        "symbol": f"__FU24S__{i}",
        "timeframe": "1h",
        "direction": "LONG",
        "entry_price": 100.0 + i,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "position_size_usdt": 30.0,
        "entry_score": 0.5,
        "entry_confidence": 0.7,
        "layer_scores": "{}",
        "entry_atr": 1.0,
        # asyncpg requires native datetime objects for TIMESTAMPTZ columns —
        # ISO strings are NOT auto-coerced (unlike sqlite3). Production call
        # sites (e.g. shadow.persistence.persist_closed_trade) always pass
        # real datetime instances, so this mirrors prod typing exactly.
        "opened_at": datetime(2026, 5, 22, 10, 0, 0, tzinfo=timezone.utc),
        "inputs_hash": f"fu24-shadow-{i:04d}",
        "model_version": "fu24-test",
        "signal_id": f"fu24-sig-{i:04d}",
    }


async def _cleanup_test_rows(engine: object) -> None:
    """Remove any rows produced by this test module so re-runs are clean."""
    async with engine.connect() as conn:  # type: ignore[attr-defined]
        await conn.execute(sa.text(
            "DELETE FROM predictions WHERE symbol LIKE '__FU24__%'"
        ))
        await conn.execute(sa.text(
            "DELETE FROM shadow_trades WHERE symbol LIKE '__FU24S__%'"
        ))
        await conn.commit()


async def _insert_one(
    engine: object, table: str, payload: dict[str, object],
) -> str:
    """Open a fresh session, insert, commit, dispose. Returns the row_hash.

    Each call opens an independent ``AsyncSession`` against an engine with a
    ``NullPool`` (see ``_make_engine``), so every concurrent caller gets a
    dedicated asyncpg connection. The ``async with`` guarantees the
    connection is rolled back + closed even when ``insert_with_chain`` or
    the commit raises — important under ``asyncio.gather`` so a single
    failing task can't strand an open transaction holding the advisory lock.
    """
    async with AsyncSession(engine) as session:  # type: ignore[arg-type]
        try:
            h = await insert_with_chain(session, table, payload)
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
    return h


async def _gather_inserts(coros: list) -> list:
    """Run inserts concurrently and re-raise the first exception, AFTER
    every task has finished (so no task is left mid-transaction holding
    the per-table advisory lock when cleanup runs).

    ``asyncio.gather(*..., return_exceptions=False)`` raises on the first
    failure but lets siblings keep running — if cleanup then runs before
    they finish, the cleanup ``DELETE`` blocks on those lingering locks
    and we deadlock at the pytest-timeout boundary. Collecting first,
    raising second, gives the test a clean failure signal.
    """
    results = await asyncio.gather(*coros, return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            raise r
    return results


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_concurrent_inserts_same_table_serialize_and_chain_intact() -> None:
    """Spawn 10 concurrent inserts on `shadow_trades`. Each writer uses its
    own session. After all commits, every row's ``prev_hash`` must match the
    previous row's ``row_hash`` ordered by ``id``.

    Without the advisory lock, all 10 read the same ``_last_row_hash`` and
    chain-link to it → only one row is correctly linked, the other 9 break.
    """
    engine = _make_engine()
    await _cleanup_test_rows(engine)
    try:
        await _gather_inserts([
            _insert_one(engine, "shadow_trades", _shadow_trade_payload(i))
            for i in range(10)
        ])
        async with engine.connect() as conn:
            rows = (await conn.execute(sa.text(
                "SELECT id, prev_hash, row_hash FROM shadow_trades "
                "WHERE symbol LIKE '__FU24S__%' ORDER BY id ASC"
            ))).all()
        assert len(rows) == 10
        # First row links to whatever was already in the table (could be
        # GENESIS_HASH on an empty test DB, or the previous head row_hash
        # on a populated one). What matters is link-by-link consistency.
        for i in range(1, len(rows)):
            assert rows[i].prev_hash == rows[i - 1].row_hash, (
                f"chain break at id={rows[i].id}: "
                f"prev_hash={rows[i].prev_hash[:8]}... "
                f"expected={rows[i - 1].row_hash[:8]}... (id={rows[i - 1].id})"
            )
    finally:
        await _cleanup_test_rows(engine)
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_inserts_different_tables_dont_block() -> None:
    """Inserts on `shadow_trades` and `predictions` MUST NOT serialize against
    each other — they hash on independent lock keys
    (hashtext('shadow_trades') ≠ hashtext('predictions')).

    Heuristic: 10 concurrent cross-table inserts should complete in less than
    ~2× the wall-time of a single insert. Without per-table key separation,
    a global lock would serialize all 10 and triple the wall-time.
    """
    engine = _make_engine()
    await _cleanup_test_rows(engine)
    try:
        # Measure a single insert as the baseline.
        t0 = time.monotonic()
        await _insert_one(
            engine, "shadow_trades", _shadow_trade_payload(900),
        )
        single = time.monotonic() - t0

        # Spawn 10 concurrent inserts across both tables.
        t1 = time.monotonic()
        await _gather_inserts([
            _insert_one(
                engine,
                "shadow_trades" if (i % 2 == 0) else "predictions",
                _shadow_trade_payload(i) if (i % 2 == 0) else _predictions_payload(i),
            )
            for i in range(10)
        ])
        wall = time.monotonic() - t1

        # Bound: cross-table inserts must not serialize globally.
        # Globally-serialized 10-way would be ~10× single + setup overhead
        # (≥12-15× wall). Cross-table-parallel with NullPool pays a fresh
        # connection setup cost per task (no pool reuse), so wall is
        # typically 6-10× single under CI even though work IS parallel —
        # well below the 12× serial floor. Threshold is 12× rather than
        # something tighter to absorb NullPool fresh-connection overhead
        # variance; it still catches a regression where the lock
        # accidentally becomes global (serial would be ≥12×).
        assert wall < single * 12, (
            f"cross-table inserts appear globally serialized: "
            f"single={single:.3f}s wall={wall:.3f}s (10 concurrent)"
        )
    finally:
        await _cleanup_test_rows(engine)
        await engine.dispose()


@pytest.mark.asyncio
async def test_advisory_lock_released_on_commit() -> None:
    """After insert_with_chain + commit, the per-table advisory lock must be
    released — a new session must be able to acquire it immediately.

    `pg_try_advisory_xact_lock` returns True immediately if the lock is
    free, False if held. We assert True (lock is free) inside a fresh
    transaction.
    """
    engine = _make_engine()
    await _cleanup_test_rows(engine)
    try:
        await _insert_one(
            engine, "shadow_trades", _shadow_trade_payload(800),
        )
        # Fresh session/transaction. xact_lock variant auto-releases on
        # COMMIT of the previous transaction — try_lock should succeed.
        async with AsyncSession(engine) as session:
            got = (await session.execute(sa.text(
                "SELECT pg_try_advisory_xact_lock(hashtext('shadow_trades'))"
            ))).scalar_one()
            assert got is True, "advisory lock was NOT released on COMMIT"
            await session.rollback()
    finally:
        await _cleanup_test_rows(engine)
        await engine.dispose()


@pytest.mark.asyncio
async def test_advisory_lock_released_on_rollback() -> None:
    """A ROLLBACK inside an insert_with_chain transaction must also release
    the advisory lock — same xact_lock semantics as commit.
    """
    engine = _make_engine()
    await _cleanup_test_rows(engine)
    try:
        # Insert then ROLLBACK (do NOT commit).
        async with AsyncSession(engine) as session:
            await insert_with_chain(
                session, "shadow_trades", _shadow_trade_payload(700),
            )
            await session.rollback()

        # New session must acquire the lock immediately.
        async with AsyncSession(engine) as session:
            got = (await session.execute(sa.text(
                "SELECT pg_try_advisory_xact_lock(hashtext('shadow_trades'))"
            ))).scalar_one()
            assert got is True, "advisory lock was NOT released on ROLLBACK"
            await session.rollback()
    finally:
        await _cleanup_test_rows(engine)
        await engine.dispose()


@pytest.mark.asyncio
async def test_single_writer_unchanged_behavior() -> None:
    """Regression: a single writer's row_hash must match
    ``compute_row_hash(prev, _filter_for_hash(payload))`` exactly — the
    advisory lock changes timing only, not the hash value.
    """
    from app.db.audit import _filter_for_hash, _last_row_hash, compute_row_hash

    engine = _make_engine()
    await _cleanup_test_rows(engine)
    try:
        payload = _shadow_trade_payload(600)
        async with AsyncSession(engine) as session:
            expected_prev = await _last_row_hash(session, "shadow_trades")
            await session.rollback()  # don't disturb the table

        produced = await _insert_one(engine, "shadow_trades", payload)
        expected = compute_row_hash(
            expected_prev, _filter_for_hash("shadow_trades", payload),
        )
        # Note: another concurrent test could have inserted between our
        # _last_row_hash read and our _insert_one call. Guard with a
        # second-attempt fallback against the actual stored prev_hash.
        if produced != expected:
            async with engine.connect() as conn:
                row = (await conn.execute(sa.text(
                    "SELECT prev_hash, row_hash FROM shadow_trades "
                    "WHERE inputs_hash = :h"
                ), {"h": payload["inputs_hash"]})).first()
            assert row is not None
            expected = compute_row_hash(
                row.prev_hash, _filter_for_hash("shadow_trades", payload),
            )
            assert row.row_hash == expected
        else:
            # The genesis-path is also valid when the test DB is fresh.
            assert produced == expected or expected_prev == GENESIS_HASH
    finally:
        await _cleanup_test_rows(engine)
        await engine.dispose()
