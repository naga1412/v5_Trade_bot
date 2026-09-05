"""Tests for `app.ml.patterns.update_pattern_stats` (SP-1 §4.3)."""
from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.ml.patterns import _extract_patterns, update_pattern_stats

# Real L2 layer_scores["2"] payloads captured directly from production
# (2026-08-20, via sql-select against predictions.layer_scores->'2') --
# NOT hand-written. The original test fixture hand-wrote
# `{"L2": {"patterns": ["hammer"]}}`, which is exactly how this bug
# shipped: the test was written against the same imagined schema as the
# code, so it passed while production silently did nothing. A complete
# 3-pattern real payload and a genuinely truncated 10-pattern real
# payload (layer2_patterns.NOTES_MAX_CHARS=500 cuts it off mid-object at
# write time -- confirmed by capturing an actual truncated row, not
# simulated) are both exercised below.
REAL_L2_COMPLETE = (
    '{"notes": "{\\"n\\":3,\\"patterns\\":[{\\"id\\":\\"triple_top\\",\\"dir\\":\\"SHORT\\",'
    '\\"s\\":0.75,\\"c\\":0.55},{\\"id\\":\\"rounding_top\\",\\"dir\\":\\"SHORT\\",\\"s\\":0.65,'
    '\\"c\\":0.48},{\\"id\\":\\"saucer_top\\",\\"dir\\":\\"SHORT\\",\\"s\\":0.55,\\"c\\":0.48}]}", '
    '"strength": 0.16327542833737899, "direction": "SHORT", "confidence": 0.6295}'
)
REAL_L2_TRUNCATED = (
    '{"notes": "{\\"n\\":10,\\"patterns\\":[{\\"id\\":\\"channel_up\\",\\"dir\\":\\"LONG\\",'
    '\\"s\\":0.65,\\"c\\":0.55},{\\"id\\":\\"regression_channel_up\\",\\"dir\\":\\"LONG\\",'
    '\\"s\\":0.7,\\"c\\":0.6},{\\"id\\":\\"parallel_channel\\",\\"dir\\":\\"LONG\\",\\"s\\":0.65,'
    '\\"c\\":0.55},{\\"id\\":\\"andrews_pitchfork\\",\\"dir\\":\\"LONG\\",\\"s\\":0.55,\\"c\\":0.5},'
    '{\\"id\\":\\"mean_reversion_to_ma\\",\\"dir\\":\\"SHORT\\",\\"s\\":0.6,\\"c\\":0.5},{\\"id\\":'
    '\\"rounding_bottom\\",\\"dir\\":\\"LONG\\",\\"s\\":0.65,\\"c\\":0.48},{\\"id\\":\\"saucer_bottom\\",'
    '\\"dir\\":\\"LONG\\",\\"s\\":0.55,\\"c\\":0.48},{\\"id\\":\\"three_drives_top\\",\\"dir\\":\\"SHORT\\",'
    '\\"s\\":0.7,\\"c\\":0.52},{\\"id\\":\\"multi_tf_confluence_l", '
    '"strength": 0.3318596997928934, "direction": "LONG", "confidence": 0.63975}'
)


def test_extract_patterns_reads_real_l2_payload_shape() -> None:
    """The real shape: key "2" (not "L2"), patterns nested inside a
    JSON-STRING `notes` field (not a direct dict key), each pattern a
    dict keyed by "id" (not a bare string)."""
    layer_scores = json.dumps({"2": json.loads(REAL_L2_COMPLETE)})
    assert _extract_patterns(layer_scores) == [
        "triple_top", "rounding_top", "saucer_top",
    ]


def test_extract_patterns_handles_real_truncated_notes() -> None:
    """A real production row where NOTES_MAX_CHARS=500 truncated `notes`
    mid-object -- must return [] gracefully, not raise."""
    layer_scores = json.dumps({"2": json.loads(REAL_L2_TRUNCATED)})
    assert _extract_patterns(layer_scores) == []


def test_extract_patterns_accepts_already_parsed_dict() -> None:
    """SQLAlchemy's asyncpg dialect returns JSONB as an already-parsed
    dict for some query shapes -- must not assume a string."""
    layer_scores = {"2": json.loads(REAL_L2_COMPLETE)}
    assert _extract_patterns(layer_scores) == [
        "triple_top", "rounding_top", "saucer_top",
    ]


def test_extract_patterns_wrong_key_returns_empty() -> None:
    """The bug this fix closes: "L2" was never the real key."""
    assert _extract_patterns(json.dumps({"L2": {"patterns": ["hammer"]}})) == []


@pytest.mark.asyncio
async def test_update_pattern_stats_creates_rows_from_shadow_trades() -> None:
    """No `predictions` join -- shadow_trades.layer_scores is
    self-sufficient (bug 3: the old join key never matched anything real,
    see the module docstring)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "symbol TEXT, timeframe TEXT, signal_id TEXT, layer_scores TEXT, "
            "exit_reason TEXT, closed_at TEXT)"
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

    # Seed: 3 closed shadow_trades carrying the real L2 shape
    # (triple_top/rounding_top/saucer_top fire on all 3), 2 with TP.
    async with AsyncSession(engine) as session:
        for i, won in enumerate([True, True, False]):
            await session.execute(sa.text(
                "INSERT INTO shadow_trades (user_id, symbol, timeframe, "
                "signal_id, layer_scores, exit_reason, closed_at) "
                "VALUES (1, 'BTC/USDT', '1h', :sig, :ls, :reason, :ca)"
            ), {
                "sig": f"sig{i}",
                "ls": json.dumps({"2": json.loads(REAL_L2_COMPLETE)}),
                "reason": "TAKE_PROFIT" if won else "STOP_LOSS",
                "ca": f"2026-05-{i+1:02d}T13:00:00",
            })
        await session.commit()

        n_updated = await update_pattern_stats(session)
        await session.commit()

        rows = (await session.execute(sa.text(
            "SELECT pattern_id, n_samples, n_correct FROM pattern_stats"
        ))).all()

    assert n_updated == 3  # triple_top, rounding_top, saucer_top
    by_pat = {r.pattern_id: r for r in rows}
    for pid in ("triple_top", "rounding_top", "saucer_top"):
        assert pid in by_pat
        assert by_pat[pid].n_samples == 3
        assert by_pat[pid].n_correct == 2


@pytest.mark.asyncio
async def test_update_with_no_shadow_trades_is_noop() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY, symbol TEXT, timeframe TEXT, "
            "layer_scores TEXT, exit_reason TEXT)"
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
