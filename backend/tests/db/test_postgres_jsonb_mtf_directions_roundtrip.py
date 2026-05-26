"""PR-MTF-DIRECTIONS-FOLLOWUP-2 (2026-05-26): Postgres-only round-trip
integration tests for `shadow_open_positions.mtf_directions_json`.

WHY THIS EXISTS
================
The original PR-MTF-DIRECTIONS-JSON-SERIALIZATION-FIX (#248) addressed a
prod crash:

    asyncpg.exceptions.DataError: 'dict' object has no attribute 'encode'

raised when `persist_closed_trade` re-serialised a `ShadowPosition` that
had been round-tripped through `shadow_open_positions.mtf_directions_json`.
The column is JSONB on Postgres, asyncpg auto-decodes JSONB → Python
dict on SELECT, and the subsequent INSERT path expected a `str`.

The bug shipped through CI because the unit-test suite runs on SQLite,
where `mtf_directions_json` is just TEXT. SQLite round-trips strings
faithfully — the asyncpg dict-decode behaviour that triggers the bug
NEVER FIRES under SQLite. The reviewer of #248 flagged this gap as
non-blocking but worth closing; these tests close it.

WHAT THIS TESTS
================
1. After `persist_open_position(...)` writes a canonical JSON STRING
   into the JSONB column, `list_open_positions(...)` returns a
   ShadowPosition whose `mtf_directions_json` is a STRING, NOT a dict.
   (Pre-fix this would have been a dict.)
2. The string is byte-identical to what was written (the
   `_normalize_mtf_directions_json` helper at the read boundary
   re-serialises in the canonical form, matching the writer).
3. Passing that round-tripped ShadowPosition to `persist_closed_trade`
   does NOT raise the `'dict' object has no attribute 'encode'`
   DataError. End-to-end the bug class is fixed.
4. Inserting a row with `mtf_directions_json=NULL` round-trips as
   `None` (regression guard for the "absent value gets stringified
   as 'null'" failure mode).

POSTGRES-ONLY
=============
Skipped on SQLite / in-memory. CI's `backend` job runs
`alembic upgrade head` against a Postgres test DB before pytest, so the
JSONB column is in place when these tests run. Local development can
opt in by setting:

    DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost/trading_radar
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.shadow.engine import Direction, ShadowPosition
from app.shadow.exit_monitor import ExitReason
from app.shadow.persistence import (
    list_open_positions,
    persist_closed_trade,
    persist_open_position,
)


_DSN = os.environ.get("DATABASE_URL", "")
_IS_PG = _DSN.startswith("postgresql") or _DSN.startswith("postgres")

pytestmark = pytest.mark.skipif(
    not _IS_PG,
    reason="Postgres-only — set DATABASE_URL=postgresql+asyncpg://... to run.",
)


# Sentinel keys so this test's rows don't collide with any other fixture.
# DELETE-clause WHERE on these tokens guarantees teardown reaches only us.
_TEST_USER_ID: int = 9_999_911
_TEST_SYMBOL: str = "__pr_mtf_jsonb_roundtrip_test__"
_TEST_SIGNAL_ID: str = "__pr_mtf_jsonb_roundtrip_signal__"

# Canonical JSON form the writer (`_normalize_mtf_directions_json`)
# produces: sort_keys=True + tight separators. The whole point of this
# test is that the bytes survive the JSONB round-trip.
_CANONICAL_MTF_JSON: str = '{"1d":-1,"1h":0,"1w":-1,"4h":-1}'


@pytest.fixture
async def _engine():
    """Async engine bound to the CI Postgres instance. Cleans up any
    pre-existing test rows before yielding so a failed prior run can't
    poison the unique constraint."""
    engine = create_async_engine(_DSN)
    async with engine.begin() as conn:
        # Pre-clean: also wipe the shadow_trades audit-chain rows the
        # test writes, so a re-run is idempotent.
        await conn.execute(sa.text(
            "DELETE FROM shadow_open_positions "
            "WHERE user_id = :uid AND symbol = :sym"
        ), {"uid": _TEST_USER_ID, "sym": _TEST_SYMBOL})
        await conn.execute(sa.text(
            "DELETE FROM shadow_trades "
            "WHERE user_id = :uid AND symbol = :sym"
        ), {"uid": _TEST_USER_ID, "sym": _TEST_SYMBOL})
    yield engine
    # Post-clean: tear down anything the test inserted, regardless of
    # success/failure.
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "DELETE FROM shadow_open_positions "
            "WHERE user_id = :uid AND symbol = :sym"
        ), {"uid": _TEST_USER_ID, "sym": _TEST_SYMBOL})
        await conn.execute(sa.text(
            "DELETE FROM shadow_trades "
            "WHERE user_id = :uid AND symbol = :sym"
        ), {"uid": _TEST_USER_ID, "sym": _TEST_SYMBOL})
    await engine.dispose()


def _make_position(
    *, mtf_json: str | None = _CANONICAL_MTF_JSON,
) -> ShadowPosition:
    """Build a minimal ShadowPosition with PR1 analytics + the
    canonical MTF JSON string the writer would produce."""
    n = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
    return ShadowPosition(
        symbol=_TEST_SYMBOL,
        direction=Direction.LONG,
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        position_size_usdt=30.0,
        entry_score=0.5, entry_confidence=0.7, entry_atr=1.0,
        layer_scores={}, bars_held=0,
        opened_at=n, last_check_at=n,
        signal_id=_TEST_SIGNAL_ID,
        timeframe="1h",
        mtf_agreement=3, mtf_dominant_tf="1d",
        mtf_directions_json=mtf_json,
        p_win=0.55, effective_score=0.42,
        realized_vol_20d=0.018, funding_directional_adj=1.02,
    )


@pytest.mark.asyncio
async def test_jsonb_string_round_trips_as_string_not_dict(_engine) -> None:
    """Bug class: asyncpg decodes JSONB → Python dict on SELECT, but the
    `ShadowPosition.mtf_directions_json` contract is `str | None`. The
    helper `_normalize_mtf_directions_json` at the read boundary must
    re-serialise the dict back to a canonical str.

    Pre-fix: this test would observe a dict on read and assert-fail at
    `isinstance(pos.mtf_directions_json, str)`. Post-fix: str round-trip.
    """
    async with AsyncSession(_engine) as s:
        await persist_open_position(
            s, _make_position(), user_id=_TEST_USER_ID,
        )
        await s.commit()

    async with AsyncSession(_engine) as s:
        positions = await list_open_positions(s, user_id=_TEST_USER_ID)

    test_pos = next(
        (p for p in positions if p.signal_id == _TEST_SIGNAL_ID), None,
    )
    assert test_pos is not None, (
        "round-tripped position not found — INSERT may have silently failed"
    )
    # The actual bug-class assertion: type must be str, NOT dict.
    assert isinstance(test_pos.mtf_directions_json, str), (
        f"mtf_directions_json round-tripped as "
        f"{type(test_pos.mtf_directions_json).__name__}; expected str. "
        "asyncpg JSONB decode is leaking past _normalize_mtf_directions_json "
        "at the read boundary (app/shadow/persistence.py:list_open_positions)."
    )
    # Bytes-stable round-trip: read-back must equal the canonical form
    # the writer encodes. If this fails, either the writer drifted from
    # the canonical form OR the read-side normalizer is producing a
    # different shape than the writer.
    assert test_pos.mtf_directions_json == _CANONICAL_MTF_JSON, (
        f"round-tripped {test_pos.mtf_directions_json!r} != "
        f"canonical {_CANONICAL_MTF_JSON!r}"
    )


@pytest.mark.asyncio
async def test_persist_closed_trade_on_jsonb_roundtripped_position(
    _engine,
) -> None:
    """End-to-end regression: open → round-trip → close MUST NOT raise
    `asyncpg.exceptions.DataError: 'dict' object has no attribute
    'encode'`. This is the exact error the production logs showed
    pre-PR-MTF-DIRECTIONS-JSON-SERIALIZATION-FIX.

    The test inserts an open position, fetches it back through the
    real Postgres JSONB pipeline (where asyncpg WOULD decode to dict
    if the read-side normaliser weren't there), then passes the
    round-tripped ShadowPosition straight into `persist_closed_trade`.
    Pre-fix this raised DataError; post-fix the closed row writes
    cleanly with the canonical JSON string.
    """
    async with AsyncSession(_engine) as s:
        await persist_open_position(
            s, _make_position(), user_id=_TEST_USER_ID,
        )
        await s.commit()

    async with AsyncSession(_engine) as s:
        positions = await list_open_positions(s, user_id=_TEST_USER_ID)
    test_pos = next(p for p in positions if p.signal_id == _TEST_SIGNAL_ID)

    closed_at = datetime(2026, 5, 26, 13, 0, 0, tzinfo=timezone.utc)
    async with AsyncSession(_engine) as s:
        # If asyncpg sees a dict in test_pos.mtf_directions_json this
        # raises DataError at parameter binding. We exercise the FULL
        # writer + audit-chain path to catch the bug class end-to-end.
        row_hash = await persist_closed_trade(
            s, test_pos,
            user_id=_TEST_USER_ID,
            exit_price=102.0,
            exit_reason=ExitReason.TAKE_PROFIT,
            closed_at=closed_at,
            bars_held=4,
            inputs_hash="pr-mtf-jsonb-roundtrip",
        )
        await s.commit()
    # Audit-chain row_hash is a 64-char hex string when insert_with_chain
    # succeeds; assertion is loose because the test doesn't care about
    # the chain math, only that no exception was raised.
    assert isinstance(row_hash, str) and len(row_hash) == 64

    # Belt-and-braces: read back the persisted shadow_trades row and
    # confirm mtf_directions_json is the canonical str on the closed
    # trade too.
    async with AsyncSession(_engine) as s:
        r = (await s.execute(sa.text(
            "SELECT mtf_directions_json FROM shadow_trades "
            "WHERE user_id = :uid AND symbol = :sym"
        ), {"uid": _TEST_USER_ID, "sym": _TEST_SYMBOL})).first()
    assert r is not None
    # shadow_trades.mtf_directions_json is also JSONB on Postgres, so
    # asyncpg may again return a dict here. The CONTRACT is that the
    # canonical-form bytes were what hit the wire on INSERT; what we
    # read back may be the decoded dict. So allow either, but if it's
    # a dict, confirm it round-trips to the canonical form via
    # json.dumps with the same params.
    import json
    if isinstance(r.mtf_directions_json, str):
        assert r.mtf_directions_json == _CANONICAL_MTF_JSON
    else:
        assert isinstance(r.mtf_directions_json, dict)
        assert json.dumps(
            r.mtf_directions_json, sort_keys=True, separators=(",", ":"),
        ) == _CANONICAL_MTF_JSON


@pytest.mark.asyncio
async def test_jsonb_null_round_trips_as_none(_engine) -> None:
    """A position written with mtf_directions_json=None reads back as
    None (NOT the string 'null'). Regression guard against an over-
    eager normaliser that JSON-encodes None → 'null'."""
    async with AsyncSession(_engine) as s:
        await persist_open_position(
            s, _make_position(mtf_json=None), user_id=_TEST_USER_ID,
        )
        await s.commit()

    async with AsyncSession(_engine) as s:
        positions = await list_open_positions(s, user_id=_TEST_USER_ID)
    test_pos = next(p for p in positions if p.signal_id == _TEST_SIGNAL_ID)
    assert test_pos.mtf_directions_json is None
