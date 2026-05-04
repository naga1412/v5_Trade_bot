"""Tests for `app.ml.patterns.update_pattern_stats` (SP-1 §4.3)."""
from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.ml.patterns import update_pattern_stats


@pytest.mark.asyncio
async def test_update_pattern_stats_creates_rows_from_join() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # Schema enough for the join — predictions + shadow_trades + pattern_stats
        await conn.execute(sa.text(
            "CREATE TABLE predictions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "symbol TEXT, timeframe TEXT, ts TEXT, layer_scores TEXT, "
            "inputs_hash TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "symbol TEXT, timeframe TEXT, signal_id TEXT, exit_reason TEXT, "
            "closed_at TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE pattern_stats ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "pattern_id TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
            "n_samples INTEGER NOT NULL DEFAULT 0, "
            "n_correct INTEGER NOT NULL DEFAULT 0, "
            "last_updated TEXT NOT NULL DEFAULT (datetime('now')), "
            "UNIQUE (pattern_id, symbol, timeframe))"
        ))

    # Seed: 3 predictions with pattern 'hammer' on BTC, 2 of them with TP
    # shadow_trades.
    async with AsyncSession(engine) as session:
        for i, won in enumerate([True, True, False]):
            await session.execute(sa.text(
                "INSERT INTO predictions (user_id, symbol, timeframe, ts, "
                "layer_scores, inputs_hash) "
                "VALUES (1, 'BTC/USDT', '1h', :ts, :ls, :h)"
            ), {
                "ts": f"2026-05-{i+1:02d}T12:00:00",
                "ls": json.dumps({"L2": {"patterns": ["hammer"]}}),
                "h": f"hash{i}",
            })
            await session.execute(sa.text(
                "INSERT INTO shadow_trades (user_id, symbol, timeframe, "
                "signal_id, exit_reason, closed_at) "
                "VALUES (1, 'BTC/USDT', '1h', :sig, :reason, :ca)"
            ), {
                "sig": f"hash{i}",
                "reason": "TAKE_PROFIT" if won else "STOP_LOSS",
                "ca": f"2026-05-{i+1:02d}T13:00:00",
            })
        await session.commit()

        n_updated = await update_pattern_stats(session)
        await session.commit()

        rows = (await session.execute(sa.text(
            "SELECT pattern_id, n_samples, n_correct FROM pattern_stats"
        ))).all()

    assert n_updated >= 1
    by_pat = {r.pattern_id: r for r in rows}
    assert "hammer" in by_pat
    assert by_pat["hammer"].n_samples == 3
    assert by_pat["hammer"].n_correct == 2


@pytest.mark.asyncio
async def test_update_with_no_predictions_is_noop() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions ("
            "id INTEGER PRIMARY KEY, symbol TEXT, timeframe TEXT, "
            "layer_scores TEXT, inputs_hash TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY, signal_id TEXT, exit_reason TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE pattern_stats ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, pattern_id TEXT, symbol TEXT, "
            "timeframe TEXT, n_samples INTEGER DEFAULT 0, n_correct INTEGER DEFAULT 0, "
            "last_updated TEXT, UNIQUE(pattern_id, symbol, timeframe))"
        ))

    async with AsyncSession(engine) as session:
        n = await update_pattern_stats(session)
        await session.commit()
    assert n == 0
