"""PR3 Phase 3: per-TF persistence helpers.

Drives the SQLite-in-memory engine with the post-PR3 schema (timeframe
columns + composite PK / UNIQUE) and verifies the helpers:

  - set_cooldown(timeframe=...) and load_cooldowns() preserve TF state
  - persist_open_position + delete_open_position respect (symbol, tf)
  - persist_closed_trade threads pos.timeframe through to shadow_trades.timeframe
  - persist_closed_trade threads pos.hold_scaling_factor + .hold_timeout_bars
    through to the corresponding shadow_trades columns (G1, NULL when off)

Schema mirrors what alembic 0021_pr3_shadow_per_tf produces on Postgres
plus the live_trades columns since the shadow_trades insert participates
in the chain. SQLite has no JSONB / SMALLINT, but text/INTEGER stand-ins
match assertion semantics here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.shadow.engine import Direction, ShadowPosition
from app.shadow.exit_monitor import ExitReason
from app.shadow.persistence import (
    delete_open_position,
    list_open_positions,
    load_cooldowns_per_tf,
    persist_closed_trade,
    persist_open_position,
    set_cooldown,
)


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


async def _mk_engine() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # Post-PR3 schema: shadow_cooldowns PK = (user_id, symbol, timeframe)
        await conn.execute(sa.text(
            "CREATE TABLE shadow_cooldowns ("
            "user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL DEFAULT '1h', "
            "cooldown_until TEXT NOT NULL, "
            "PRIMARY KEY (user_id, symbol, timeframe))"
        ))
        # Post-PR3: UNIQUE = (symbol, timeframe). user_id is just a column.
        await conn.execute(sa.text(
            "CREATE TABLE shadow_open_positions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL DEFAULT '1h', "
            "direction TEXT NOT NULL, "
            "entry_price REAL, stop_loss REAL, take_profit REAL, "
            "position_size_usdt REAL, "
            "entry_score REAL, entry_confidence REAL, entry_atr REAL, "
            "bars_held INTEGER, opened_at TEXT, last_check_at TEXT, "
            "signal_id TEXT, "
            # PR-PLUMBING-1 alembic 0025: 7 PR1 analytics columns. NULLs
            # for pre-fix restart-survivor rows; populated by shadow_worker
            # for new same-session opens via persist_open_position.
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
            "mtf_directions_json TEXT, p_win REAL, effective_score REAL, "
            "realized_vol_20d REAL, funding_directional_adj REAL, "
            # Migration 0040 (2026-08-20): 5 columns Fix 3 missed.
            "layer_scores TEXT, mtf_adx_by_tf_json TEXT, "
            "symbol_source TEXT NOT NULL DEFAULT 'established_top20', "
            "hold_scaling_factor REAL, hold_timeout_bars INTEGER, "
            "UNIQUE (symbol, timeframe))"
        ))
        # shadow_trades with PR1 + PR3 G1 columns + audit chain
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, symbol TEXT, timeframe TEXT NOT NULL DEFAULT '1h', "
            "direction TEXT, entry_price REAL, stop_loss REAL, take_profit REAL, "
            "position_size_usdt REAL, entry_score REAL, entry_confidence REAL, "
            "layer_scores TEXT, entry_atr REAL, "
            "exit_price REAL, exit_reason TEXT, pnl_pct REAL, pnl_usdt REAL, "
            "bars_held INTEGER, opened_at TEXT, closed_at TEXT, "
            "inputs_hash TEXT, model_version TEXT, signal_id TEXT, "
            # PR1 analytics columns
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, mtf_directions_json TEXT, "
            "p_win REAL, effective_score REAL, realized_vol_20d REAL, "
            "funding_directional_adj REAL, "
            # Item 4 (alembic 0034)
            "mtf_adx_by_tf_json TEXT, "
            # Migration 0038 (2026-08-17): cohort tag
            "symbol_source TEXT NOT NULL DEFAULT 'established_top20', "
            # PR3 G1
            "hold_scaling_factor REAL, hold_timeout_bars INTEGER, "
            # audit chain
            "prev_hash TEXT, row_hash TEXT)"
        ))
    return engine


def _make_position(
    *, symbol: str = "BTCUSDT", timeframe: str = "1h",
    direction: Direction = Direction.LONG,
    hold_scaling_factor: float | None = None,
    hold_timeout_bars: int | None = None,
) -> ShadowPosition:
    return ShadowPosition(
        symbol=symbol,
        direction=direction,
        entry_price=100.0, stop_loss=98.0, take_profit=104.0,
        position_size_usdt=10.0,
        entry_score=0.4, entry_confidence=0.6, entry_atr=1.5,
        layer_scores={"L1": 0.85},
        bars_held=0,
        opened_at=_NOW,
        last_check_at=_NOW,
        signal_id="sig_abc",
        timeframe=timeframe,
        hold_scaling_factor=hold_scaling_factor,
        hold_timeout_bars=hold_timeout_bars,
    )


# --- Cooldowns -------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_cooldown_persists_timeframe_column() -> None:
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        await set_cooldown(
            s, user_id=1, symbol="BTCUSDT", timeframe="15m",
            until=_NOW + timedelta(minutes=30),
        )
        await s.commit()
        row = (await s.execute(sa.text(
            "SELECT symbol, timeframe FROM shadow_cooldowns WHERE user_id=1"
        ))).first()
    assert row.symbol == "BTCUSDT"
    assert row.timeframe == "15m"


@pytest.mark.asyncio
async def test_set_cooldown_independent_per_tf() -> None:
    """A 1h cooldown does not collide with a 15m cooldown on the same symbol."""
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        await set_cooldown(
            s, user_id=1, symbol="BTCUSDT", timeframe="1h",
            until=_NOW + timedelta(hours=1),
        )
        await set_cooldown(
            s, user_id=1, symbol="BTCUSDT", timeframe="15m",
            until=_NOW + timedelta(minutes=15),
        )
        await s.commit()
        rows = (await s.execute(sa.text(
            "SELECT timeframe FROM shadow_cooldowns WHERE user_id=1 ORDER BY timeframe"
        ))).all()
    assert [r.timeframe for r in rows] == ["15m", "1h"]


@pytest.mark.asyncio
async def test_set_cooldown_upsert_on_conflict_targets_3_columns() -> None:
    """Inserting same (user_id, symbol, timeframe) twice updates the row,
    doesn't fail with PK violation."""
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        t1 = _NOW + timedelta(minutes=10)
        t2 = _NOW + timedelta(minutes=30)
        await set_cooldown(s, user_id=1, symbol="BTCUSDT", timeframe="1h", until=t1)
        await set_cooldown(s, user_id=1, symbol="BTCUSDT", timeframe="1h", until=t2)
        await s.commit()
        rows = (await s.execute(sa.text(
            "SELECT count(*) AS n FROM shadow_cooldowns"
        ))).scalar()
    assert rows == 1


@pytest.mark.asyncio
async def test_load_cooldowns_per_tf_returns_tuple_keyed_dict() -> None:
    """load_cooldowns_per_tf should return dict[(symbol, tf), datetime].

    The legacy `load_cooldowns` returns 1h-only `dict[str, datetime]` for
    backward-compat with the existing shadow_worker; Phase 4 swaps the
    worker to consume this per-tf variant.
    """
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        await set_cooldown(
            s, user_id=1, symbol="BTCUSDT", timeframe="1h",
            until=_NOW + timedelta(hours=1),
        )
        await set_cooldown(
            s, user_id=1, symbol="ETHUSDT", timeframe="15m",
            until=_NOW + timedelta(minutes=30),
        )
        await s.commit()
        cooldowns = await load_cooldowns_per_tf(s, user_id=1)
    assert ("BTCUSDT", "1h") in cooldowns
    assert ("ETHUSDT", "15m") in cooldowns
    assert len(cooldowns) == 2


# --- Open positions --------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_open_position_writes_timeframe() -> None:
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        await persist_open_position(s, _make_position(timeframe="15m"), user_id=1)
        await s.commit()
        row = (await s.execute(sa.text(
            "SELECT symbol, timeframe FROM shadow_open_positions"
        ))).first()
    assert row.timeframe == "15m"


@pytest.mark.asyncio
async def test_persist_open_position_allows_same_symbol_different_tf() -> None:
    """The motivating scenario for PR3: 1h BTC position must NOT block 15m BTC."""
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        await persist_open_position(
            s, _make_position(symbol="BTCUSDT", timeframe="1h"), user_id=1,
        )
        await persist_open_position(
            s, _make_position(symbol="BTCUSDT", timeframe="15m"), user_id=1,
        )
        await s.commit()
        rows = (await s.execute(sa.text(
            "SELECT timeframe FROM shadow_open_positions ORDER BY timeframe"
        ))).all()
    assert [r.timeframe for r in rows] == ["15m", "1h"]


@pytest.mark.asyncio
async def test_delete_open_position_targets_symbol_and_tf() -> None:
    """Deleting the 1h position must NOT delete the 15m position on same symbol."""
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        await persist_open_position(
            s, _make_position(symbol="BTCUSDT", timeframe="1h"), user_id=1,
        )
        await persist_open_position(
            s, _make_position(symbol="BTCUSDT", timeframe="15m"), user_id=1,
        )
        await s.commit()
        await delete_open_position(s, user_id=1, symbol="BTCUSDT", timeframe="1h")
        await s.commit()
        rows = (await s.execute(sa.text(
            "SELECT timeframe FROM shadow_open_positions"
        ))).all()
    assert [r.timeframe for r in rows] == ["15m"]


@pytest.mark.asyncio
async def test_list_open_positions_returns_position_with_timeframe() -> None:
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        await persist_open_position(
            s, _make_position(symbol="ETHUSDT", timeframe="15m"), user_id=1,
        )
        await s.commit()
        positions = await list_open_positions(s, user_id=1)
    assert len(positions) == 1
    assert positions[0].timeframe == "15m"


# --- persist_closed_trade --------------------------------------------------


@pytest.mark.asyncio
async def test_persist_closed_trade_threads_timeframe_to_shadow_trades() -> None:
    """pos.timeframe='15m' → shadow_trades row has timeframe='15m'."""
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        pos = _make_position(timeframe="15m")
        await persist_closed_trade(
            s, pos, user_id=1,
            exit_price=104.0, exit_reason=ExitReason.TAKE_PROFIT,
            closed_at=_NOW + timedelta(hours=1),
            bars_held=4, inputs_hash="abc",
        )
        await s.commit()
        row = (await s.execute(sa.text(
            "SELECT timeframe FROM shadow_trades"
        ))).first()
    assert row.timeframe == "15m"


@pytest.mark.asyncio
async def test_persist_closed_trade_threads_g1_scaling_columns() -> None:
    """pos.hold_scaling_factor=1.5 + pos.hold_timeout_bars=96 → shadow_trades
    row has those values. NULL by default."""
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        pos = _make_position(
            timeframe="1h",
            hold_scaling_factor=1.5,
            hold_timeout_bars=96,
        )
        await persist_closed_trade(
            s, pos, user_id=1,
            exit_price=106.0, exit_reason=ExitReason.TAKE_PROFIT,
            closed_at=_NOW + timedelta(hours=4),
            bars_held=4, inputs_hash="abc",
        )
        await s.commit()
        row = (await s.execute(sa.text(
            "SELECT hold_scaling_factor, hold_timeout_bars FROM shadow_trades"
        ))).first()
    assert row.hold_scaling_factor == 1.5
    assert row.hold_timeout_bars == 96


@pytest.mark.asyncio
async def test_persist_closed_trade_g1_defaults_null_when_pos_fields_none() -> None:
    """Pre-G1 callers (or flag-OFF) → NULL on both columns."""
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        pos = _make_position()  # hold_* default None
        await persist_closed_trade(
            s, pos, user_id=1,
            exit_price=104.0, exit_reason=ExitReason.TAKE_PROFIT,
            closed_at=_NOW + timedelta(hours=1),
            bars_held=2, inputs_hash="abc",
        )
        await s.commit()
        row = (await s.execute(sa.text(
            "SELECT hold_scaling_factor, hold_timeout_bars FROM shadow_trades"
        ))).first()
    assert row.hold_scaling_factor is None
    assert row.hold_timeout_bars is None
