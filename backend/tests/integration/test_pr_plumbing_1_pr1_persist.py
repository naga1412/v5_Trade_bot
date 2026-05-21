"""PR-PLUMBING-1 Fix 3: persist + load the 7 PR1 analytics fields on
`shadow_open_positions` so restart-survivor positions don't lose them.

Pre-fix, `shadow_open_positions` carried none of the 7 PR1 columns:
    mtf_agreement, mtf_dominant_tf, mtf_directions_json,
    p_win, effective_score, realized_vol_20d, funding_directional_adj

`shadow/worker.py::_maybe_open_position` set them on `ShadowPosition` AND
on close `persist_closed_trade` forwarded them into `shadow_trades`. But a
process restart between open + close = `list_open_positions` reconstructs
a position with these fields = None = the closed trade row had NULLs.

Tests:
  - persist + raw-select round-trip writes the 7 fields.
  - persist with None defaults writes NULLs.
  - list_open_positions reads the 7 columns back onto ShadowPosition.
  - Full round-trip: persist_open → list_open → persist_closed → assert
    the 7 cols flow through to shadow_trades on the closed row.
  - Legacy NULL-row load is backwards-compatible (no AttributeError).

Schema mirror: SQLite test fixtures must match the alembic migration
(0025_pr_plumbing_1_op_pr1_cols) — JSONB → TEXT, SMALLINT → INTEGER,
REAL → REAL.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.shadow.engine import Direction, ShadowPosition, ShadowSignal
from app.shadow.exit_monitor import ExitReason
from app.shadow.persistence import (
    list_open_positions,
    persist_closed_trade,
    persist_open_position,
)


# PR-PLUMBING-1: SQLite mirror of the alembic 0025 migration. The 7 PR1
# columns map JSONB → TEXT, SMALLINT → INTEGER, REAL → REAL per the PR1
# Phase 8 convention (see backend/tests/integration/conftest.py).
_OPEN_POS_DDL_PR_PLUMBING_1 = (
    "CREATE TABLE shadow_open_positions ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "user_id INTEGER NOT NULL, "
    "symbol TEXT NOT NULL, "
    "timeframe TEXT NOT NULL DEFAULT '1h', "
    "direction TEXT NOT NULL, "
    "entry_price REAL NOT NULL, stop_loss REAL NOT NULL, "
    "take_profit REAL NOT NULL, position_size_usdt REAL NOT NULL, "
    "entry_score REAL NOT NULL, entry_confidence REAL NOT NULL, "
    "entry_atr REAL NOT NULL, bars_held INTEGER NOT NULL DEFAULT 0, "
    "opened_at TEXT NOT NULL, last_check_at TEXT NOT NULL, "
    "signal_id TEXT NOT NULL UNIQUE, "
    # PR-PLUMBING-1 alembic 0025: 7 PR1 analytics columns. All nullable.
    "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
    "mtf_directions_json TEXT, p_win REAL, effective_score REAL, "
    "realized_vol_20d REAL, funding_directional_adj REAL, "
    "UNIQUE (symbol, timeframe))"
)

# Shadow trades DDL mirrors the prod schema after PR-strategy-1.
_TRADES_DDL = (
    "CREATE TABLE shadow_trades ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "user_id INTEGER NOT NULL, "
    "symbol TEXT NOT NULL, "
    "timeframe TEXT NOT NULL, direction TEXT NOT NULL, "
    "entry_price REAL NOT NULL, stop_loss REAL NOT NULL, "
    "take_profit REAL NOT NULL, position_size_usdt REAL NOT NULL, "
    "entry_score REAL NOT NULL, entry_confidence REAL NOT NULL, "
    "layer_scores TEXT NOT NULL, entry_atr REAL NOT NULL, "
    "exit_price REAL, exit_reason TEXT, pnl_pct REAL, pnl_usdt REAL, "
    "bars_held INTEGER, opened_at TEXT NOT NULL, closed_at TEXT, "
    "inputs_hash TEXT NOT NULL, model_version TEXT NOT NULL, "
    "signal_id TEXT NOT NULL UNIQUE, "
    "hold_scaling_factor REAL, hold_timeout_bars INTEGER, "
    "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
    "mtf_directions_json TEXT, p_win REAL, effective_score REAL, "
    "realized_vol_20d REAL, funding_directional_adj REAL, "
    "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
)


def _make_signal() -> ShadowSignal:
    return ShadowSignal(
        symbol="BTCUSDT", direction=Direction.LONG, score=0.65,
        confidence=0.72, entry_price=78250.0, stop_loss=77077.75,
        take_profit=80594.5, atr=781.5,
        layer_scores={"1": 0.85, "3": 0.72, "5": 0.40},
        ts=datetime(2026, 5, 21, 14, tzinfo=timezone.utc),
        signal_id="prplumbing-1-test",
    )


def _populate_pr1_fields(pos: ShadowPosition) -> None:
    pos.mtf_agreement = 4
    pos.mtf_dominant_tf = "1h"
    pos.mtf_directions_json = '{"5m": 1, "1h": 1, "4h": 1}'
    pos.p_win = 0.65
    pos.effective_score = 0.42
    pos.realized_vol_20d = 0.018
    pos.funding_directional_adj = 0.003


# ---------------------------------------------------------------------------
# persist_open_position writes the 7 PR1 fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_open_position_writes_pr1_fields() -> None:
    """When ShadowPosition carries the 7 PR1 fields, persist_open_position
    writes them as columns on shadow_open_positions. Raw SELECT round-trip."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_OPEN_POS_DDL_PR_PLUMBING_1))

    pos = ShadowPosition.from_signal(_make_signal(), position_size_usdt=30.0)
    _populate_pr1_fields(pos)

    async with AsyncSession(engine) as session:
        await persist_open_position(session, pos, user_id=1)
        await session.commit()
        row = (await session.execute(sa.text(
            "SELECT mtf_agreement, mtf_dominant_tf, mtf_directions_json, "
            "p_win, effective_score, realized_vol_20d, "
            "funding_directional_adj FROM shadow_open_positions"
        ))).first()
    assert row is not None
    assert row.mtf_agreement == 4
    assert row.mtf_dominant_tf == "1h"
    assert row.mtf_directions_json == '{"5m": 1, "1h": 1, "4h": 1}'
    assert row.p_win == 0.65
    assert row.effective_score == 0.42
    assert row.realized_vol_20d == 0.018
    assert row.funding_directional_adj == 0.003


@pytest.mark.asyncio
async def test_persist_open_position_writes_nulls_when_pr1_fields_unset() -> None:
    """When ShadowPosition's PR1 fields are at their default None,
    persist_open_position writes NULLs (not zero / empty string)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_OPEN_POS_DDL_PR_PLUMBING_1))

    pos = ShadowPosition.from_signal(_make_signal(), position_size_usdt=30.0)
    # PR1 fields NOT populated — they stay at their dataclass defaults of None.

    async with AsyncSession(engine) as session:
        await persist_open_position(session, pos, user_id=1)
        await session.commit()
        row = (await session.execute(sa.text(
            "SELECT mtf_agreement, mtf_dominant_tf, mtf_directions_json, "
            "p_win, effective_score, realized_vol_20d, "
            "funding_directional_adj FROM shadow_open_positions"
        ))).first()
    assert row is not None
    assert row.mtf_agreement is None
    assert row.mtf_dominant_tf is None
    assert row.mtf_directions_json is None
    assert row.p_win is None
    assert row.effective_score is None
    assert row.realized_vol_20d is None
    assert row.funding_directional_adj is None


# ---------------------------------------------------------------------------
# list_open_positions reads the 7 PR1 fields back onto ShadowPosition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_open_positions_reads_pr1_fields_back() -> None:
    """After persisting an open position with PR1 fields, list_open_positions
    must reconstruct a ShadowPosition carrying the same 7 values (restart
    survives without losing PR1 analytics)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_OPEN_POS_DDL_PR_PLUMBING_1))

    pos = ShadowPosition.from_signal(_make_signal(), position_size_usdt=30.0)
    _populate_pr1_fields(pos)

    async with AsyncSession(engine) as session:
        await persist_open_position(session, pos, user_id=1)
        await session.commit()
        loaded = await list_open_positions(session, user_id=1)

    assert len(loaded) == 1
    rp = loaded[0]
    assert rp.mtf_agreement == 4
    assert rp.mtf_dominant_tf == "1h"
    assert rp.mtf_directions_json == '{"5m": 1, "1h": 1, "4h": 1}'
    assert rp.p_win == 0.65
    assert rp.effective_score == 0.42
    assert rp.realized_vol_20d == 0.018
    assert rp.funding_directional_adj == 0.003


@pytest.mark.asyncio
async def test_legacy_open_positions_load_with_nulls() -> None:
    """A row INSERTed with NULL PR1 columns (e.g. pre-PR-PLUMBING-1 row
    that survived the migration) must reconstruct without AttributeError.
    All 7 fields on the resulting ShadowPosition must be None."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_OPEN_POS_DDL_PR_PLUMBING_1))

    async with AsyncSession(engine) as session:
        await session.execute(sa.text(
            "INSERT INTO shadow_open_positions ("
            "user_id, symbol, timeframe, direction, entry_price, stop_loss, "
            "take_profit, position_size_usdt, entry_score, entry_confidence, "
            "entry_atr, bars_held, opened_at, last_check_at, signal_id"
            ") VALUES ("
            "1, 'BTCUSDT', '1h', 'LONG', 78250.0, 77077.75, 80594.5, "
            "30.0, 0.65, 0.72, 781.5, 0, "
            "'2026-05-21T14:00:00+00:00', '2026-05-21T14:00:00+00:00', "
            "'legacy-null-row'"
            ")"
        ))
        await session.commit()
        loaded = await list_open_positions(session, user_id=1)

    assert len(loaded) == 1
    rp = loaded[0]
    # Backwards-compat: no AttributeError, defaults to None.
    assert rp.mtf_agreement is None
    assert rp.mtf_dominant_tf is None
    assert rp.mtf_directions_json is None
    assert rp.p_win is None
    assert rp.effective_score is None
    assert rp.realized_vol_20d is None
    assert rp.funding_directional_adj is None


# ---------------------------------------------------------------------------
# Full round-trip: open → restart → close → assert PR1 on shadow_trades
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_round_trip_persists_pr1_fields_through_restart() -> None:
    """The full lifecycle. Simulate a restart between open + close:
      1. persist_open_position with 7 PR1 fields populated
      2. (process restart) list_open_positions reconstructs ShadowPosition
      3. persist_closed_trade against the reconstructed position
      4. SELECT shadow_trades and assert the 7 columns flow through
    Pre-PR-PLUMBING-1 step (2) lost the 7 fields → step (4) saw NULLs."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_OPEN_POS_DDL_PR_PLUMBING_1))
        await conn.execute(sa.text(_TRADES_DDL))

    pos = ShadowPosition.from_signal(_make_signal(), position_size_usdt=30.0)
    _populate_pr1_fields(pos)

    async with AsyncSession(engine) as session:
        # Step 1: persist the open position with PR1 fields.
        await persist_open_position(session, pos, user_id=1)
        await session.commit()

        # Step 2: simulate restart — reload the position from DB.
        loaded = await list_open_positions(session, user_id=1)
    assert len(loaded) == 1
    reloaded = loaded[0]

    # Sanity: PR1 fields survived the round-trip.
    assert reloaded.mtf_agreement == 4
    assert reloaded.p_win == 0.65

    # Step 3: persist the close from the reloaded position.
    closed_at = datetime(2026, 5, 21, 18, tzinfo=timezone.utc)
    async with AsyncSession(engine) as session:
        await persist_closed_trade(
            session, reloaded, user_id=1,
            exit_price=80594.5, exit_reason=ExitReason.TAKE_PROFIT,
            closed_at=closed_at, bars_held=4, inputs_hash="deadbeef",
        )
        await session.commit()
        row = (await session.execute(sa.text(
            "SELECT mtf_agreement, mtf_dominant_tf, mtf_directions_json, "
            "p_win, effective_score, realized_vol_20d, "
            "funding_directional_adj FROM shadow_trades"
        ))).first()

    # Step 4: closed trade carries the 7 PR1 columns from the open record.
    assert row is not None
    assert row.mtf_agreement == 4
    assert row.mtf_dominant_tf == "1h"
    assert row.mtf_directions_json == '{"5m": 1, "1h": 1, "4h": 1}'
    assert row.p_win == 0.65
    assert row.effective_score == 0.42
    assert row.realized_vol_20d == 0.018
    assert row.funding_directional_adj == 0.003
