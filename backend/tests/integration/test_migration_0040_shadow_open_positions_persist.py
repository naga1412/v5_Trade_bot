"""Migration 0040 (2026-08-20): persist + load the 5 shadow_trades columns
that PR-PLUMBING-1 Fix 3 (migration 0025) missed on `shadow_open_positions`.

Fix 3 covered 7 PR1 analytics columns (see
test_pr_plumbing_1_pr1_persist.py). It did NOT cover:
    layer_scores, mtf_adx_by_tf_json, symbol_source,
    hold_scaling_factor, hold_timeout_bars

either because they predate Fix 3 (layer_scores, hold_scaling_factor,
hold_timeout_bars) or were added to shadow_trades after Fix 3 shipped
with no shadow_open_positions follow-up (mtf_adx_by_tf_json: migration
0034; symbol_source: migration 0038). A restart between open + close
lost all 5 the same way the original 7 were lost pre-Fix-3: the closed
trade's layer_scores was serialized as {} (empty dict, not a JSON
null -- shadow_trades.layer_scores is NOT NULL), mtf_adx_by_tf_json /
hold_scaling_factor / hold_timeout_bars went NULL, and symbol_source
silently reverted to the "established_top20" dataclass default
regardless of the position's real cohort.

Tests mirror test_pr_plumbing_1_pr1_persist.py's structure exactly:
  - persist + raw-select round-trip writes the 5 fields.
  - list_open_positions reads them back onto ShadowPosition.
  - Full round-trip: persist_open -> (simulated restart via
    list_open_positions) -> persist_closed_trade -> assert all 5
    (plus the original 7, as a regression guard) land correctly on
    the resulting shadow_trades row.
"""
from __future__ import annotations

import json
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

# Full shadow_open_positions schema as of migration 0040: the 7
# PR-PLUMBING-1 columns (Fix 3) plus the 5 this migration adds.
_OPEN_POS_DDL = (
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
    "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
    "mtf_directions_json TEXT, p_win REAL, effective_score REAL, "
    "realized_vol_20d REAL, funding_directional_adj REAL, "
    # Migration 0040: the 5 columns Fix 3 missed.
    "layer_scores TEXT, mtf_adx_by_tf_json TEXT, "
    "symbol_source TEXT NOT NULL DEFAULT 'established_top20', "
    "hold_scaling_factor REAL, hold_timeout_bars INTEGER, "
    "UNIQUE (symbol, timeframe))"
)

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
    "mtf_adx_by_tf_json TEXT, "
    "symbol_source TEXT NOT NULL DEFAULT 'established_top20', "
    "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
)


def _make_signal() -> ShadowSignal:
    return ShadowSignal(
        symbol="ETHUSDT", direction=Direction.LONG, score=0.58,
        confidence=0.70, entry_price=3200.0, stop_loss=3150.0,
        take_profit=3300.0, atr=42.0,
        layer_scores={"1": 0.60, "3": 0.55, "5": 0.42},
        ts=datetime(2026, 8, 20, 10, tzinfo=timezone.utc),
        signal_id="migration-0040-test",
    )


def _populate_all_analytics_fields(pos: ShadowPosition) -> None:
    # The 7 PR-PLUMBING-1 fields (regression guard -- must keep working).
    pos.mtf_agreement = 3
    pos.mtf_dominant_tf = "4h"
    pos.mtf_directions_json = '{"1h": 1, "4h": 1}'
    pos.p_win = 0.55
    pos.effective_score = 0.38
    pos.realized_vol_20d = 0.021
    pos.funding_directional_adj = -0.002
    # The 5 migration-0040 fields.
    pos.mtf_adx_by_tf_json = '{"1h": 24.5, "4h": 31.2}'
    pos.symbol_source = "futures_poll"
    pos.hold_scaling_factor = 0.75
    pos.hold_timeout_bars = 48
    # layer_scores is already set by from_signal(); overwrite with a
    # value distinct from the default {} an unpopulated field would
    # produce, so a bug that drops it back to {} is caught.
    pos.layer_scores = {"1": 0.60, "3": 0.55, "5": 0.42, "8": 0.31}


@pytest.mark.asyncio
async def test_persist_open_position_writes_migration_0040_fields() -> None:
    """persist_open_position writes all 5 new columns. Raw SELECT round-trip."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_OPEN_POS_DDL))

    pos = ShadowPosition.from_signal(_make_signal(), position_size_usdt=25.0)
    _populate_all_analytics_fields(pos)

    async with AsyncSession(engine) as session:
        await persist_open_position(session, pos, user_id=1)
        await session.commit()
        row = (await session.execute(sa.text(
            "SELECT layer_scores, mtf_adx_by_tf_json, symbol_source, "
            "hold_scaling_factor, hold_timeout_bars FROM shadow_open_positions"
        ))).first()

    assert row is not None
    assert json.loads(row.layer_scores) == {
        "1": 0.60, "3": 0.55, "5": 0.42, "8": 0.31,
    }
    assert row.mtf_adx_by_tf_json == '{"1h": 24.5, "4h": 31.2}'
    assert row.symbol_source == "futures_poll"
    assert row.hold_scaling_factor == 0.75
    assert row.hold_timeout_bars == 48


@pytest.mark.asyncio
async def test_load_open_positions_reads_migration_0040_fields_back() -> None:
    """list_open_positions reconstructs a ShadowPosition carrying the 5
    new fields -- restart no longer loses them."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_OPEN_POS_DDL))

    pos = ShadowPosition.from_signal(_make_signal(), position_size_usdt=25.0)
    _populate_all_analytics_fields(pos)

    async with AsyncSession(engine) as session:
        await persist_open_position(session, pos, user_id=1)
        await session.commit()
        loaded = await list_open_positions(session, user_id=1)

    assert len(loaded) == 1
    rp = loaded[0]
    assert rp.layer_scores == {"1": 0.60, "3": 0.55, "5": 0.42, "8": 0.31}
    assert rp.mtf_adx_by_tf_json == '{"1h": 24.5, "4h": 31.2}'
    assert rp.symbol_source == "futures_poll"
    assert rp.hold_scaling_factor == 0.75
    assert rp.hold_timeout_bars == 48


@pytest.mark.asyncio
async def test_open_position_without_analytics_defaults_sanely() -> None:
    """A position with no analytics populated writes/reads {} for
    layer_scores (not NULL -- shadow_trades.layer_scores is NOT NULL)
    and the real dataclass default for symbol_source."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_OPEN_POS_DDL))

    pos = ShadowPosition.from_signal(_make_signal(), position_size_usdt=25.0)
    pos.layer_scores = {}  # from_signal() would normally set real scores

    async with AsyncSession(engine) as session:
        await persist_open_position(session, pos, user_id=1)
        await session.commit()
        loaded = await list_open_positions(session, user_id=1)

    assert len(loaded) == 1
    rp = loaded[0]
    assert rp.layer_scores == {}
    assert rp.mtf_adx_by_tf_json is None
    assert rp.symbol_source == "established_top20"
    assert rp.hold_scaling_factor is None
    assert rp.hold_timeout_bars is None


@pytest.mark.asyncio
@pytest.mark.parametrize("real_cohort", ["futures_poll", "liquidity_added_spot"])
async def test_phase4_cohort_position_retains_true_symbol_source_through_restart(
    real_cohort: str,
) -> None:
    """Explicit Phase 4 regression guard, not just generic coverage: a
    position opened under either real non-default cohort literal
    (`app.shadow.live_fleet_universe.Cohort` -- shadow doesn't assign
    these yet, but migration 0040 must protect them BEFORE that Task 9
    dependency lands, per the Phase 4 plan's Global Constraints note)
    must reload with its TRUE cohort after a restart, not silently
    fall back to "established_top20". Pre-0040, list_open_positions
    always reconstructed the dataclass default regardless of what was
    actually persisted -- this test would have failed against that code."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_OPEN_POS_DDL))
        await conn.execute(sa.text(_TRADES_DDL))

    pos = ShadowPosition.from_signal(_make_signal(), position_size_usdt=25.0)
    pos.symbol_source = real_cohort

    async with AsyncSession(engine) as session:
        await persist_open_position(session, pos, user_id=1)
        await session.commit()

        # Simulate restart: reload from DB, original in-memory `pos` is gone.
        loaded = await list_open_positions(session, user_id=1)
    assert len(loaded) == 1
    reloaded = loaded[0]
    assert reloaded.symbol_source == real_cohort  # NOT "established_top20"

    closed_at = datetime(2026, 8, 20, 15, tzinfo=timezone.utc)
    async with AsyncSession(engine) as session:
        await persist_closed_trade(
            session, reloaded, user_id=1,
            exit_price=3300.0, exit_reason=ExitReason.TAKE_PROFIT,
            closed_at=closed_at, bars_held=3, inputs_hash="feedface",
        )
        await session.commit()
        row = (await session.execute(sa.text(
            "SELECT symbol_source FROM shadow_trades"
        ))).first()

    assert row is not None
    assert row.symbol_source == real_cohort


@pytest.mark.asyncio
async def test_full_round_trip_persists_all_analytics_through_restart() -> None:
    """Full lifecycle with a simulated restart between open + close.
    Round-trips ALL 12 analytics columns (7 PR-PLUMBING-1 + 5 new) --
    the exact test the operator asked for: open a position, simulate a
    restart via list_open_positions(), close it, and assert every
    analytics column round-trips with its original value."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_OPEN_POS_DDL))
        await conn.execute(sa.text(_TRADES_DDL))

    pos = ShadowPosition.from_signal(_make_signal(), position_size_usdt=25.0)
    _populate_all_analytics_fields(pos)

    async with AsyncSession(engine) as session:
        # Step 1: open.
        await persist_open_position(session, pos, user_id=1)
        await session.commit()

        # Step 2: simulate restart -- reload from DB, in-memory pos is gone.
        loaded = await list_open_positions(session, user_id=1)
    assert len(loaded) == 1
    reloaded = loaded[0]

    # Sanity: survived the restart round-trip in-memory.
    assert reloaded.layer_scores == {"1": 0.60, "3": 0.55, "5": 0.42, "8": 0.31}
    assert reloaded.mtf_adx_by_tf_json == '{"1h": 24.5, "4h": 31.2}'
    assert reloaded.symbol_source == "futures_poll"
    assert reloaded.hold_scaling_factor == 0.75
    assert reloaded.hold_timeout_bars == 48

    # Step 3: close using the reloaded (post-restart) position.
    closed_at = datetime(2026, 8, 20, 14, tzinfo=timezone.utc)
    async with AsyncSession(engine) as session:
        await persist_closed_trade(
            session, reloaded, user_id=1,
            exit_price=3300.0, exit_reason=ExitReason.TAKE_PROFIT,
            closed_at=closed_at, bars_held=6, inputs_hash="cafef00d",
        )
        await session.commit()
        row = (await session.execute(sa.text(
            "SELECT layer_scores, mtf_adx_by_tf_json, symbol_source, "
            "hold_scaling_factor, hold_timeout_bars, "
            "mtf_agreement, mtf_dominant_tf, mtf_directions_json, "
            "p_win, effective_score, realized_vol_20d, "
            "funding_directional_adj FROM shadow_trades"
        ))).first()

    # Step 4: every analytics column survived the restart onto the closed row.
    assert row is not None
    assert json.loads(row.layer_scores) == {
        "1": 0.60, "3": 0.55, "5": 0.42, "8": 0.31,
    }
    assert row.mtf_adx_by_tf_json == '{"1h": 24.5, "4h": 31.2}'
    assert row.symbol_source == "futures_poll"
    assert row.hold_scaling_factor == 0.75
    assert row.hold_timeout_bars == 48
    # Regression guard: the original 7 PR-PLUMBING-1 fields still work
    # end-to-end through this same restart-then-close path.
    assert row.mtf_agreement == 3
    assert row.mtf_dominant_tf == "4h"
    assert row.mtf_directions_json == '{"1h": 1, "4h": 1}'
    assert row.p_win == 0.55
    assert row.effective_score == 0.38
    assert row.realized_vol_20d == 0.021
    assert row.funding_directional_adj == -0.002
