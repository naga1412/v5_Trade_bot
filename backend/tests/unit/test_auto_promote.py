"""SP-8 Phase J.2 — auto-promote worker tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.trading.auto_promote import (
    AutoPromoteConfig,
    _next_target,
    _seconds_until_next_tick,
    evaluate_user,
)


_NOW = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)


# ---- _next_target --------------------------------------------------------


def test_next_target_manual_to_telegram_when_enabled() -> None:
    cfg = AutoPromoteConfig(to_telegram_enabled=True, to_fullyauto_enabled=False)
    assert _next_target("manual", cfg) == "telegram-approve"


def test_next_target_telegram_to_fullyauto_when_enabled() -> None:
    cfg = AutoPromoteConfig(to_telegram_enabled=False, to_fullyauto_enabled=True)
    assert _next_target("telegram-approve", cfg) == "fully-auto"


def test_next_target_none_when_already_fully_auto() -> None:
    cfg = AutoPromoteConfig(to_telegram_enabled=True, to_fullyauto_enabled=True)
    assert _next_target("fully-auto", cfg) is None


def test_next_target_none_when_telegram_target_disabled() -> None:
    cfg = AutoPromoteConfig(to_telegram_enabled=False, to_fullyauto_enabled=True)
    assert _next_target("manual", cfg) is None  # can only go to telegram from manual


# ---- _seconds_until_next_tick -------------------------------------------


def test_tick_calc_after_target_hour_returns_next_day() -> None:
    """At 04:00 UTC, the next 03:30 tick is tomorrow → ~23.5 hours."""
    now = datetime(2026, 5, 9, 4, 0, 0, tzinfo=timezone.utc)
    s = _seconds_until_next_tick(now=now)
    assert 23 * 3600 < s < 24 * 3600


def test_tick_calc_before_target_hour_returns_today() -> None:
    """At 02:00 UTC, the next 03:30 tick is in 1.5 hours = 5400s."""
    now = datetime(2026, 5, 9, 2, 0, 0, tzinfo=timezone.utc)
    s = _seconds_until_next_tick(now=now)
    assert s == pytest.approx(5400, abs=2)


# ---- evaluate_user --------------------------------------------------------


async def _setup_user_and_trades(
    *, current_mode: str = "manual",
    n_trades_per_day: int = 20, n_days_back: int = 35,
    win_rate: float = 0.55,
    pnl_per_win: float = 2.0, pnl_per_loss: float = -1.5,
) -> Any:
    """In-memory SQLite with users + shadow_trades populated for tests.

    Generates synthetic shadow_trades distributed evenly across the
    requested window so compute_gates can compute realistic stats.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE users ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "email TEXT, trading_mode TEXT NOT NULL DEFAULT 'manual')"
        ))
        await conn.execute(sa.text(
            "INSERT INTO users (id, email, trading_mode) VALUES (1, 'u@x', :m)"
        ), {"m": current_mode})
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "symbol TEXT, direction TEXT, timeframe TEXT,"
            "pnl_pct REAL, opened_at TEXT, closed_at TEXT,"
            "prev_hash TEXT, row_hash TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE mode_change_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "user_id INTEGER, old_mode TEXT, new_mode TEXT,"
            "triggered_by TEXT, reason TEXT, gate_snapshot TEXT,"
            "changed_at TEXT, prev_hash TEXT, row_hash TEXT)"
        ))

        # Bresenham-interleaved 11W + 9L block per day so win_rate=55%
        # with low drawdown (Telegram gates pass cleanly).
        block: list[float] = []
        losses_placed = 0
        for i in range(20):
            if losses_placed < (i + 1) * 9 / 20 and losses_placed < 9:
                block.append(pnl_per_loss)
                losses_placed += 1
            else:
                block.append(pnl_per_win)
        # Drop excess to match n_trades_per_day.
        per_day_block = []
        i = 0
        while len(per_day_block) < n_trades_per_day:
            per_day_block.append(block[i % len(block)])
            i += 1

        for day in range(n_days_back):
            for trade_idx, pnl in enumerate(per_day_block):
                opened = _NOW - timedelta(days=day, hours=trade_idx)
                closed = opened + timedelta(minutes=30)
                await conn.execute(sa.text(
                    "INSERT INTO shadow_trades "
                    "(symbol, direction, timeframe, pnl_pct, opened_at, closed_at,"
                    " prev_hash, row_hash) "
                    "VALUES ('BTC/USDT', 'LONG', '1h', :p, :o, :c, '0', :h)"
                ), {
                    "p": pnl, "o": opened.isoformat(),
                    "c": closed.isoformat(),
                    "h": f"{day}-{trade_idx}",
                })
    return engine


@pytest.mark.asyncio
async def test_evaluate_no_op_when_already_at_top_mode() -> None:
    engine = await _setup_user_and_trades(current_mode="fully-auto")
    cfg = AutoPromoteConfig(to_telegram_enabled=True, to_fullyauto_enabled=True)
    async with AsyncSession(engine) as s:
        out = await evaluate_user(s, user_id=1, cfg=cfg, now=_NOW)
    assert out.promoted is False
    assert out.target_mode is None
    assert "no auto-promote" in out.reason


@pytest.mark.asyncio
async def test_evaluate_no_op_when_target_disabled() -> None:
    engine = await _setup_user_and_trades(current_mode="manual")
    cfg = AutoPromoteConfig(
        to_telegram_enabled=False, to_fullyauto_enabled=True,
    )
    async with AsyncSession(engine) as s:
        out = await evaluate_user(s, user_id=1, cfg=cfg, now=_NOW)
    assert out.promoted is False
    assert out.target_mode is None


@pytest.mark.asyncio
async def test_evaluate_promotes_when_streak_satisfied() -> None:
    """40 days of 20 trades/day at 55% wins, low drawdown → gates pass for
    every day in the 7-day streak window."""
    engine = await _setup_user_and_trades(
        current_mode="manual", n_trades_per_day=20, n_days_back=40,
    )
    cfg = AutoPromoteConfig(
        to_telegram_enabled=True, to_fullyauto_enabled=False,
        consecutive_days=7,
    )
    async with AsyncSession(engine) as s:
        out = await evaluate_user(s, user_id=1, cfg=cfg, now=_NOW)
    assert out.promoted is True
    assert out.target_mode == "telegram-approve"
    assert out.snapshots_passed == 7
    # Verify mode actually flipped in DB.
    async with AsyncSession(engine) as s:
        mode = (await s.execute(sa.text(
            "SELECT trading_mode FROM users WHERE id=1"
        ))).scalar()
    assert mode == "telegram-approve"


@pytest.mark.asyncio
async def test_evaluate_blocks_when_streak_not_satisfied() -> None:
    """Only 20 days × 4 trades/day → window has insufficient trade count
    (≤ 80 trades vs 100 required), gates fail → no promotion."""
    engine = await _setup_user_and_trades(
        current_mode="manual", n_trades_per_day=4, n_days_back=20,
    )
    cfg = AutoPromoteConfig(
        to_telegram_enabled=True, to_fullyauto_enabled=False,
        consecutive_days=7,
    )
    async with AsyncSession(engine) as s:
        out = await evaluate_user(s, user_id=1, cfg=cfg, now=_NOW)
    assert out.promoted is False
    # Mode unchanged.
    async with AsyncSession(engine) as s:
        mode = (await s.execute(sa.text(
            "SELECT trading_mode FROM users WHERE id=1"
        ))).scalar()
    assert mode == "manual"


@pytest.mark.asyncio
async def test_evaluate_writes_audit_row_on_promotion() -> None:
    engine = await _setup_user_and_trades(
        current_mode="manual", n_trades_per_day=20, n_days_back=40,
    )
    cfg = AutoPromoteConfig(
        to_telegram_enabled=True, to_fullyauto_enabled=False,
        consecutive_days=7,
    )
    async with AsyncSession(engine) as s:
        await evaluate_user(s, user_id=1, cfg=cfg, now=_NOW)
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT triggered_by, reason FROM mode_change_log "
            "WHERE user_id=1 ORDER BY id DESC LIMIT 1"
        ))).first()
    assert row is not None
    assert row.triggered_by == "auto-demote"  # closest existing enum
    assert "auto-promote" in row.reason
    assert "7-day streak" in row.reason
