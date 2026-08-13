"""Integration test — shadow_trade_variants persistence + FK contract.

Exercises the end-of-close path added by Amendment 3 (2026-07-31):

1. Base close via `persist_closed_trade` (bit-identical to today).
2. `lookup_shadow_trade_id_by_row_hash` — new helper for the variant
   lane to find the freshly-inserted base row's id.
3. `simulate_variant_exit` — pure function replayed at close time.
4. `insert_shadow_trade_variant` — new persistence helper.

Confirms:
- Base `shadow_trades` row is written unchanged.
- Both 0.40R and 0.50R variant rows land in `shadow_trade_variants`
  keyed by `base_shadow_trade_id`.
- Variant `pnl_pct` differs from base when the trigger arms.
- No `shadow_cooldowns` writes originate from the variant path
  (operator confirmation (a) — variant lane MUST NOT write
  cooldowns).
- ON CONFLICT (base, variant_name) UNIQUE prevents duplicate rows
  on retry.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.shadow.breakeven_variant import (
    BarSnapshot,
    simulate_variant_exit,
    variant_name_for,
)
from app.shadow.engine import Direction, ShadowPosition
from app.shadow.exit_monitor import ExitReason
from app.shadow.persistence import (
    insert_shadow_trade_variant,
    lookup_shadow_trade_id_by_row_hash,
    persist_closed_trade,
)


_T0 = datetime(2026, 7, 31, 0, 0, tzinfo=timezone.utc)


# Minimal DDLs to run on in-memory SQLite. Only columns the code paths
# under test read/write are included.
_SHADOW_TRADES_DDL = (
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
    # Item 4 (alembic 0034): per-TF ADX map, same NULL-when-absent treatment.
    "mtf_adx_by_tf_json TEXT, "
    "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
)

_VARIANTS_DDL = (
    "CREATE TABLE shadow_trade_variants ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "base_shadow_trade_id INTEGER NOT NULL REFERENCES shadow_trades(id) "
    "  ON DELETE CASCADE, "
    "variant_name TEXT NOT NULL, "
    "trigger_r REAL NOT NULL, "
    "armed INTEGER NOT NULL, "  # SQLite bool -> INTEGER
    "exit_price REAL NOT NULL, "
    "exit_reason TEXT NOT NULL "
    "  CHECK (exit_reason IN ('TAKE_PROFIT','STOP_LOSS','TIMEOUT','BREAKEVEN')), "
    "exit_ts TEXT NOT NULL, "
    "bars_held INTEGER NOT NULL, "
    "pnl_pct REAL NOT NULL, "
    "created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
    "UNIQUE (base_shadow_trade_id, variant_name))"
)

_COOLDOWNS_DDL = (
    "CREATE TABLE shadow_cooldowns ("
    "user_id INTEGER NOT NULL, "
    "symbol TEXT NOT NULL, "
    "timeframe TEXT NOT NULL DEFAULT '1h', "
    "cooldown_until TEXT NOT NULL, "
    "PRIMARY KEY (user_id, symbol, timeframe))"
)


def _make_pos() -> ShadowPosition:
    """Base LONG position at 100, R=2 (SL=98, TP=106). bar_history
    seeded with the same 2-bar sequence used in
    test_040R_arms_where_050R_does_not: 0.40R arms, 0.50R doesn't."""
    return ShadowPosition(
        symbol="BTC/USDT",
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=106.0,
        position_size_usdt=30.0,
        entry_score=0.55,
        entry_confidence=0.60,
        entry_atr=1.5,
        layer_scores={"L1": {"score": 0.8}},
        bars_held=2,
        opened_at=_T0,
        last_check_at=_T0 + timedelta(hours=2),
        signal_id="bev-int-test",
        timeframe="1h",
        bar_history=[
            BarSnapshot(ts=_T0 + timedelta(hours=1),
                        high=100.9, low=99.8, close=100.5),
            BarSnapshot(ts=_T0 + timedelta(hours=2),
                        high=100.0, low=97.5, close=97.8),
        ],
    )


async def _mk_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_SHADOW_TRADES_DDL))
        await conn.execute(sa.text(_VARIANTS_DDL))
        await conn.execute(sa.text(_COOLDOWNS_DDL))
    return engine


@pytest.mark.asyncio
async def test_end_to_end_base_plus_dual_variants() -> None:
    """Base close hits ORIGINAL SL (bar 2 low 97.5 ≤ 98). Variant 0.40R
    ARMS on bar 1 (peak 0.45R crosses trigger) → breakeven at bar 2
    (low 97.5 ≤ 100 armed stop). Variant 0.50R never arms → same as
    base → STOP_LOSS at 98. All rows persisted, FK intact."""
    engine = await _mk_engine()
    pos = _make_pos()

    async with AsyncSession(engine) as s:
        base_row_hash = await persist_closed_trade(
            s, pos,
            user_id=1,
            exit_price=98.0,
            exit_reason=ExitReason.STOP_LOSS,
            closed_at=_T0 + timedelta(hours=2),
            bars_held=2,
            inputs_hash="int-test-hash",
        )
        await s.commit()

    # Simulate + insert variants in a fresh session (mirrors worker path).
    async with AsyncSession(engine) as s:
        base_id = await lookup_shadow_trade_id_by_row_hash(
            s, row_hash=base_row_hash,
        )
        assert base_id is not None
        for trig in [0.40, 0.50]:
            outcome = simulate_variant_exit(
                direction=pos.direction.value,
                entry_price=pos.entry_price,
                initial_stop_loss=pos.stop_loss,
                take_profit=pos.take_profit,
                timeframe=pos.timeframe,
                trigger_r=trig,
                bar_history=pos.bar_history,
            )
            await insert_shadow_trade_variant(
                s,
                base_shadow_trade_id=base_id,
                variant_name=variant_name_for(trig),
                trigger_r=trig,
                armed=outcome.armed,
                exit_price=outcome.exit_price,
                exit_reason=outcome.exit_reason,
                exit_ts=outcome.exit_ts,
                bars_held=outcome.bars_held,
                pnl_pct=outcome.pnl_pct,
            )
        await s.commit()

    # ---- Assertions ---------------------------------------------------
    async with AsyncSession(engine) as s:
        base = (await s.execute(sa.text(
            "SELECT id, exit_price, exit_reason FROM shadow_trades"
        ))).all()
        assert len(base) == 1
        assert base[0].exit_reason == "STOP_LOSS"
        assert base[0].exit_price == 98.0

        variants = (await s.execute(sa.text(
            "SELECT variant_name, trigger_r, armed, exit_reason, "
            "       exit_price, pnl_pct, base_shadow_trade_id "
            "FROM shadow_trade_variants "
            "ORDER BY trigger_r"
        ))).all()
        assert len(variants) == 2

        v40, v50 = variants
        # 0.40R lane armed → BREAKEVEN at entry.
        assert v40.variant_name == "breakeven_0.40R"
        assert v40.trigger_r == 0.40
        assert bool(v40.armed) is True
        assert v40.exit_reason == "BREAKEVEN"
        assert v40.exit_price == 100.0
        assert v40.pnl_pct == 0.0
        assert v40.base_shadow_trade_id == base[0].id

        # 0.50R lane never armed → identical to base.
        assert v50.variant_name == "breakeven_0.50R"
        assert v50.trigger_r == 0.50
        assert bool(v50.armed) is False
        assert v50.exit_reason == "STOP_LOSS"
        assert v50.exit_price == 98.0
        # -2% loss on a 2R SL from entry 100.
        assert v50.pnl_pct == pytest.approx(-2.0)

        # OPERATOR CONFIRMATION (a) — no cooldown writes from the
        # variant lane. `set_cooldown` was never called on this session.
        cooldown_count = (await s.execute(sa.text(
            "SELECT count(*) FROM shadow_cooldowns"
        ))).scalar()
        assert cooldown_count == 0


@pytest.mark.asyncio
async def test_variant_insert_is_idempotent_via_unique_constraint() -> None:
    """A retried variant insert on the same (base_id, variant_name) tuple
    must ON CONFLICT DO NOTHING — no duplicate rows, no exception.
    Protects against the worker retrying persistence after a partial
    failure."""
    engine = await _mk_engine()
    pos = _make_pos()

    async with AsyncSession(engine) as s:
        base_row_hash = await persist_closed_trade(
            s, pos, user_id=1,
            exit_price=98.0, exit_reason=ExitReason.STOP_LOSS,
            closed_at=_T0 + timedelta(hours=2),
            bars_held=2, inputs_hash="idem-hash",
        )
        await s.commit()

    async with AsyncSession(engine) as s:
        base_id = await lookup_shadow_trade_id_by_row_hash(
            s, row_hash=base_row_hash,
        )
        outcome = simulate_variant_exit(
            direction="LONG",
            entry_price=100.0, initial_stop_loss=98.0, take_profit=106.0,
            timeframe="1h", trigger_r=0.40,
            bar_history=pos.bar_history,
        )
        # First insert.
        await insert_shadow_trade_variant(
            s, base_shadow_trade_id=base_id,
            variant_name=variant_name_for(0.40), trigger_r=0.40,
            armed=outcome.armed, exit_price=outcome.exit_price,
            exit_reason=outcome.exit_reason, exit_ts=outcome.exit_ts,
            bars_held=outcome.bars_held, pnl_pct=outcome.pnl_pct,
        )
        # Retry the same insert — must not raise, must not duplicate.
        await insert_shadow_trade_variant(
            s, base_shadow_trade_id=base_id,
            variant_name=variant_name_for(0.40), trigger_r=0.40,
            armed=outcome.armed, exit_price=outcome.exit_price,
            exit_reason=outcome.exit_reason, exit_ts=outcome.exit_ts,
            bars_held=outcome.bars_held, pnl_pct=outcome.pnl_pct,
        )
        await s.commit()

    async with AsyncSession(engine) as s:
        n = (await s.execute(sa.text(
            "SELECT count(*) FROM shadow_trade_variants"
        ))).scalar()
    assert n == 1


@pytest.mark.asyncio
async def test_lookup_returns_none_for_unknown_hash() -> None:
    """Miss case — caller should treat None as 'skip variant persistence',
    never fatal."""
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        got = await lookup_shadow_trade_id_by_row_hash(
            s, row_hash="does-not-exist",
        )
    assert got is None
