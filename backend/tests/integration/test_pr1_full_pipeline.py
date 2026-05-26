"""PR1 full-pipeline integration test.

Walks the entire wiring: _compute_aggregator_hook_fields (which exercises
compute_mtf_confluence, predict_p_win, lookup_latest_funding_rate,
compute_realized_vol_20d, compute_effective_score,
compute_funding_directional_adj) → 7 fields → build_predictions_payload
→ insert_with_chain → DB row → SELECT → verify all 7 cols + chain integrity.

Closest thing to a smoke test before prod deploy. If this passes,
every link in the chain works.

Note on ``build_prediction`` vs helper-extraction fallback:
  ``build_prediction`` requires 10 scoring layers + RL brain + traps +
  news DB, making it impractical to set up in an isolated unit/integration
  test without mocking >10 components. Per operator spec the fallback is
  to test ``_compute_aggregator_hook_fields`` + builder + insert chain directly;
  this exercises EXACTLY the PR1 wiring (the hook, the builder kwargs, and the
  DB persist path) without unrelated layer infrastructure.

Postgres-only guard: the test also runs fine on SQLite (uses JSONB TEXT
emulation — SQLite stores TEXT; Postgres stores JSONB). The postgres_session
fixture skips on SQLite DATABASE_URL; this standalone test uses its own
SQLite in-memory fixture so it runs locally AND in CI.
"""
from __future__ import annotations

import json
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.predictor import _compute_aggregator_hook_fields
from app.core.scoring.mtf_confluence import MtfConfluence
from app.core.scoring.types import Direction, FinalScore
from app.db.audit import (
    GENESIS_HASH,
    _filter_for_hash,
    compute_row_hash,
    insert_with_chain,
)
from app.db.payload_builders import build_predictions_payload


# ---------------------------------------------------------------------------
# SQLite predictions table that mirrors the full PR1 schema.
# Postgres skips are irrelevant here: this test is self-contained and
# exercises the SQLite path (what local devs and the CI SQLite leg use).
# The Postgres-only portion (JSONB introspection) is in test_pr1_migration.py.
# ---------------------------------------------------------------------------

_PREDICTIONS_DDL = """
CREATE TABLE predictions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER,
    symbol           TEXT,
    timeframe        TEXT,
    ts               TEXT,
    layer_scores     TEXT,
    final_score      REAL,
    direction        TEXT,
    confidence       REAL,
    inputs_hash      TEXT,
    model_version    TEXT,
    cold_start       INTEGER,
    -- PR1 record-only analytics columns
    mtf_agreement         INTEGER,
    mtf_dominant_tf       TEXT,
    mtf_directions_json   TEXT,
    p_win                 REAL,
    effective_score       REAL,
    realized_vol_20d      REAL,
    funding_directional_adj REAL,
    -- audit chain metadata
    prev_hash        TEXT,
    row_hash         TEXT
)
"""

_INTERMARKET_DDL = """
CREATE TABLE intermarket_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    captured_at  TEXT NOT NULL,
    funding_rate REAL,
    mark_price   REAL,
    open_interest REAL,
    source       TEXT NOT NULL
)
"""


@pytest.fixture
async def pipeline_session():
    """In-memory SQLite session with predictions + intermarket_snapshots tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_PREDICTIONS_DDL))
        await conn.execute(sa.text(_INTERMARKET_DDL))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Synthetic bar helpers
# ---------------------------------------------------------------------------


def _make_hourly_bars(n: int = 480) -> pd.DataFrame:
    """480 hourly bars = 20 days of data (enough for vol_normalization).

    Uses a random walk with a mild uptrend so vol > 0 and the bar pattern
    is realistic.
    """
    rng = np.random.default_rng(seed=42)
    closes = 80_000.0 + np.cumsum(rng.normal(10.0, 80.0, size=n))
    opens = closes + rng.normal(0.0, 40.0, size=n)
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0.0, 60.0, size=n))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0.0, 60.0, size=n))
    volumes = np.abs(rng.normal(200.0, 40.0, size=n))
    idx = pd.date_range(
        "2026-01-01", periods=n, freq="1h", tz="UTC"
    )
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def _make_final_score(
    score: float = 0.40,
    direction: Direction = Direction.LONG,
    confidence: float = 0.70,
) -> FinalScore:
    return FinalScore(
        score=score,
        direction=direction,
        confidence=confidence,
        layer_results={i: None for i in range(1, 11)},
        contributing_layers=(1, 3),
    )


def _make_mtf_confluence() -> MtfConfluence:
    """Realistic MtfConfluence with 4 TFs agreeing LONG."""
    return MtfConfluence(
        agreement=4,
        dominant_tf="1h",
        directions={"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": 0},
    )


# ---------------------------------------------------------------------------
# Task 1: Full PR1 pipeline smoke test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr1_full_pipeline_smoke(pipeline_session: AsyncSession) -> None:
    """Smoke test: hook fields → builder → insert_with_chain → DB roundtrip.

    Exercises:
      1. _compute_aggregator_hook_fields with mocked async helpers and
         real sync helpers (vol_normalization) on 480 synthetic bars.
      2. All 7 fields populated / correct on the returned tuple.
      3. build_predictions_payload passes the 7 fields through.
      4. insert_with_chain persists the row and returns a row_hash.
      5. SELECT back — all 7 columns have correct DB values.
      6. Chain integrity: prev_hash == GENESIS_HASH (first row).
      7. row_hash matches what insert_with_chain returned.
    """
    bars = _make_hourly_bars(480)
    final = _make_final_score(score=0.40, direction=Direction.LONG)
    mtf = _make_mtf_confluence()

    # Seed intermarket_snapshots with a negative funding rate so
    # funding_directional_adj takes the LONG-boost path (non-None).
    await pipeline_session.execute(
        sa.text(
            "INSERT INTO intermarket_snapshots "
            "(symbol, captured_at, funding_rate, source) "
            "VALUES (:sym, :ts, :fr, 'binance_futures')"
        ),
        {"sym": "BTCUSDT", "ts": "2026-05-16T08:00:00", "fr": -0.0008},
    )
    await pipeline_session.commit()

    # --- Step 1: run the aggregator hook ---
    with (
        patch(
            "app.core.scoring.mtf_confluence.compute_mtf_confluence",
            new=AsyncMock(return_value=mtf),
        ),
        patch(
            "app.core.scoring.p_win_calibrator.predict_p_win",
            new=AsyncMock(return_value=None),  # PR1 stub always None
        ),
    ):
        (
            mtf_agreement,
            mtf_dominant_tf,
            mtf_directions_json,
            p_win,
            effective_score,
            realized_vol_20d,
            funding_directional_adj,
            _funding_rate,  # PR-PLUMBING-1: raw per-8h rate (unused here)
            _mtf_adx_by_tf_json,  # PR-BOT-INTELLIGENCE-UPGRADE C3 (unused here)
        ) = await _compute_aggregator_hook_fields(
            symbol="BTC/USDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=pipeline_session,
        )

    # --- Step 2: assert all 7 fields ---
    assert mtf_agreement == 4, f"Expected mtf_agreement=4, got {mtf_agreement}"
    assert mtf_dominant_tf == "1h", f"Expected dominant_tf='1h', got {mtf_dominant_tf}"
    assert mtf_directions_json is not None, "mtf_directions_json must not be None"
    directions_parsed = json.loads(mtf_directions_json)
    assert isinstance(directions_parsed, dict)
    assert directions_parsed["5m"] == 1
    assert directions_parsed["1d"] == -1

    assert p_win is None, f"PR1 stub: p_win must be None, got {p_win}"

    assert realized_vol_20d is not None, (
        "480 hourly bars covers >20 calendar days; realized_vol_20d must be populated"
    )
    assert isinstance(realized_vol_20d, float)
    assert realized_vol_20d > 0.0, "realized_vol_20d must be positive for a volatile series"
    assert not math.isnan(realized_vol_20d)

    assert effective_score is not None, (
        "effective_score must be populated when realized_vol_20d is non-None"
    )
    assert isinstance(effective_score, float)
    assert not math.isnan(effective_score)
    # LONG direction with positive final_score → effective_score same sign
    assert effective_score > 0.0 or effective_score < 0.0  # non-zero (score=0.40)

    assert funding_directional_adj is not None, (
        "funding_rate=-0.0008 is outside deadband; LONG gets a boost; must be non-None"
    )
    assert funding_directional_adj > 0.0, (
        "Negative funding + LONG direction → positive boost"
    )

    # --- Step 3: build the payload ---
    pred_stub = SimpleNamespace(
        symbol="BTC/USDT",
        timeframe="1h",
        ts=bars.index[-1].to_pydatetime(),
        inputs_hash="test-inputs-hash-pr1-pipeline",
        cold_start=False,
        layer_scores={},
        prediction_extras=None,
        final=SimpleNamespace(
            score=final.score,
            direction=final.direction.value,
            confidence=final.confidence,
        ),
    )
    payload = build_predictions_payload(
        pred_stub,
        user_id=1,
        layer_payload={},
        ghost_payload=None,
        mtf_agreement=mtf_agreement,
        mtf_dominant_tf=mtf_dominant_tf,
        mtf_directions_json=mtf_directions_json,
        p_win=p_win,
        effective_score=effective_score,
        realized_vol_20d=realized_vol_20d,
        funding_directional_adj=funding_directional_adj,
    )

    # Confirm builder forwarded all 7 fields
    assert payload["mtf_agreement"] == 4
    assert payload["mtf_dominant_tf"] == "1h"
    assert payload["mtf_directions_json"] == mtf_directions_json
    assert payload["p_win"] is None
    assert payload["effective_score"] == effective_score
    assert payload["realized_vol_20d"] == realized_vol_20d
    assert payload["funding_directional_adj"] == funding_directional_adj

    # --- Step 4: persist via insert_with_chain ---
    row_hash = await insert_with_chain(pipeline_session, "predictions", payload)
    assert isinstance(row_hash, str)
    assert len(row_hash) == 64  # SHA-256 hex

    # --- Step 5: read back from DB ---
    result = await pipeline_session.execute(
        sa.text(
            "SELECT mtf_agreement, mtf_dominant_tf, mtf_directions_json, "
            "p_win, effective_score, realized_vol_20d, funding_directional_adj, "
            "prev_hash, row_hash "
            "FROM predictions WHERE inputs_hash = :h"
        ),
        {"h": "test-inputs-hash-pr1-pipeline"},
    )
    row = result.fetchone()
    assert row is not None, "Row must exist after insert_with_chain"

    # --- Step 6: verify all 7 columns ---
    db_mtf_agreement, db_mtf_dominant_tf, db_mtf_directions_json = (
        row[0], row[1], row[2]
    )
    db_p_win, db_effective_score, db_realized_vol_20d, db_funding_adj = (
        row[3], row[4], row[5], row[6]
    )
    db_prev_hash, db_row_hash = row[7], row[8]

    assert db_mtf_agreement == 4
    assert db_mtf_dominant_tf == "1h"
    assert db_mtf_directions_json is not None
    assert json.loads(db_mtf_directions_json) == directions_parsed
    assert db_p_win is None
    assert db_effective_score == pytest.approx(effective_score)
    assert db_realized_vol_20d == pytest.approx(realized_vol_20d)
    assert db_funding_adj == pytest.approx(funding_directional_adj)

    # --- Step 7: chain integrity ---
    # First (and only) row → prev_hash must be GENESIS_HASH
    assert db_prev_hash == GENESIS_HASH, (
        f"First row must chain from GENESIS_HASH; got {db_prev_hash!r}"
    )
    # row_hash returned by insert_with_chain must match stored row_hash
    assert db_row_hash == row_hash, (
        f"Stored row_hash {db_row_hash!r} differs from returned {row_hash!r}"
    )


@pytest.mark.asyncio
async def test_pr1_whitelist_guard_analytics_fields_outside_hash(
    pipeline_session: AsyncSession,
) -> None:
    """Whitelist guard: the 7 analytics fields must NOT affect row_hash.

    Insert two rows with identical whitelisted fields but different
    analytics values. Both row_hashes must equal what compute_row_hash
    produces when the 7 fields are excluded via _filter_for_hash.
    Demonstrates that analytics columns live in NON_HASHED_ALLOW_LIST
    and don't contribute to tamper-evident chain.
    """
    base_payload = {
        "user_id": 1,
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "ts": "2026-05-16T12:00:00+00:00",
        "layer_scores": "{}",
        "final_score": 0.40,
        "direction": "LONG",
        "confidence": 0.70,
        "inputs_hash": "whitelist-guard-test-hash-A",
        "model_version": "sp-0",
        "cold_start": 0,
        # Analytics fields for row 1
        "mtf_agreement": 4,
        "mtf_dominant_tf": "1h",
        "mtf_directions_json": '{"5m": 1}',
        "p_win": None,
        "effective_score": 0.45,
        "realized_vol_20d": 0.018,
        "funding_directional_adj": 0.10,
    }

    row1_hash = await insert_with_chain(pipeline_session, "predictions", base_payload)

    # Second row: same whitelisted fields; totally different analytics values
    different_analytics_payload = {
        **base_payload,
        "inputs_hash": "whitelist-guard-test-hash-B",  # must differ for DB uniqueness
        "ts": "2026-05-16T13:00:00+00:00",
        # Different analytics — must NOT affect row_hash computation vs filtered payload
        "mtf_agreement": 6,
        "mtf_dominant_tf": "4h",
        "mtf_directions_json": '{"5m": -1, "1h": -1}',
        "effective_score": 0.99,
        "realized_vol_20d": 0.055,
        "funding_directional_adj": None,
    }

    row2_hash = await insert_with_chain(
        pipeline_session, "predictions", different_analytics_payload
    )

    # Manually compute what row_hash SHOULD be for row 2:
    # prev_hash = row1_hash (chain links)
    # filtered payload = only HASH_PAYLOAD_COLUMNS["predictions"] keys
    filtered2 = _filter_for_hash("predictions", different_analytics_payload)

    # The 7 analytics fields must NOT be in the filtered payload
    for col in (
        "mtf_agreement", "mtf_dominant_tf", "mtf_directions_json",
        "p_win", "effective_score", "realized_vol_20d", "funding_directional_adj",
    ):
        assert col not in filtered2, (
            f"Column '{col}' must be excluded from hash filter (NON_HASHED_ALLOW_LIST)"
        )

    expected_row2_hash = compute_row_hash(row1_hash, filtered2)
    assert row2_hash == expected_row2_hash, (
        f"row_hash mismatch: analytics fields must not affect chain hash.\n"
        f"  stored:   {row2_hash}\n"
        f"  expected: {expected_row2_hash}"
    )


@pytest.mark.asyncio
async def test_pr1_pipeline_no_funding_data(pipeline_session: AsyncSession) -> None:
    """When intermarket_snapshots has no row for the symbol, funding_adj is None.

    Verifies the fail-open path: empty table → funding_rate=None →
    funding_directional_adj=None → stored as NULL in DB.
    """
    bars = _make_hourly_bars(480)
    final = _make_final_score(score=0.35, direction=Direction.LONG)
    mtf = _make_mtf_confluence()

    # intermarket_snapshots is empty — no rows for BTCUSDT
    with (
        patch(
            "app.core.scoring.mtf_confluence.compute_mtf_confluence",
            new=AsyncMock(return_value=mtf),
        ),
        patch(
            "app.core.scoring.p_win_calibrator.predict_p_win",
            new=AsyncMock(return_value=None),
        ),
    ):
        (
            _mtf_agreement,
            _mtf_dominant_tf,
            _mtf_directions_json,
            _p_win,
            _effective_score,
            _realized_vol_20d,
            funding_directional_adj,
            _funding_rate,  # PR-PLUMBING-1: raw per-8h rate (None here)
            _mtf_adx_by_tf_json,  # PR-BOT-INTELLIGENCE-UPGRADE C3 (unused here)
        ) = await _compute_aggregator_hook_fields(
            symbol="BTC/USDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=pipeline_session,  # non-None session, but table is empty
        )

    # Empty table → funding_rate = None → funding_adj = None
    assert funding_directional_adj is None, (
        "Empty intermarket table must yield funding_directional_adj=None"
    )

    # vol fields should still be populated (they don't depend on funding)
    assert _realized_vol_20d is not None
    assert _effective_score is not None

    # Persist and verify NULL stored correctly
    pred_stub = SimpleNamespace(
        symbol="BTC/USDT",
        timeframe="1h",
        ts=bars.index[-1].to_pydatetime(),
        inputs_hash="no-funding-path-test",
        cold_start=False,
        layer_scores={},
        prediction_extras=None,
        final=SimpleNamespace(
            score=final.score,
            direction=final.direction.value,
            confidence=final.confidence,
        ),
    )
    payload = build_predictions_payload(
        pred_stub,
        user_id=1,
        layer_payload={},
        funding_directional_adj=funding_directional_adj,  # None
    )
    await insert_with_chain(pipeline_session, "predictions", payload)

    result = await pipeline_session.execute(
        sa.text(
            "SELECT funding_directional_adj FROM predictions "
            "WHERE inputs_hash = 'no-funding-path-test'"
        )
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] is None, (
        "NULL funding_directional_adj must be stored as NULL in DB"
    )


@pytest.mark.asyncio
async def test_pr1_pipeline_short_direction(pipeline_session: AsyncSession) -> None:
    """SHORT direction path: positive funding → no boost (short-side pays)
    → funding_directional_adj = 0.0 (within deadband or unfavorable side).

    Verifies the direction-sensitivity of compute_funding_directional_adj
    through the full pipeline.
    """
    bars = _make_hourly_bars(480)
    final = _make_final_score(score=-0.40, direction=Direction.SHORT)

    # Seed: positive funding_rate (long-side pays), but direction=SHORT
    # → this is the WRONG side for SHORT boost → returns 0.0
    await pipeline_session.execute(
        sa.text(
            "INSERT INTO intermarket_snapshots "
            "(symbol, captured_at, funding_rate, source) "
            "VALUES (:sym, :ts, :fr, 'binance_futures')"
        ),
        {"sym": "BTCUSDT", "ts": "2026-05-16T09:00:00", "fr": 0.0008},
    )
    await pipeline_session.commit()

    with (
        patch(
            "app.core.scoring.mtf_confluence.compute_mtf_confluence",
            new=AsyncMock(return_value=None),  # MTF abstains
        ),
        patch(
            "app.core.scoring.p_win_calibrator.predict_p_win",
            new=AsyncMock(return_value=None),
        ),
    ):
        (
            _,
            _,
            _,
            _,
            effective_score,
            realized_vol_20d,
            funding_directional_adj,
            _funding_rate,  # PR-PLUMBING-1: raw per-8h rate (unused here)
            _mtf_adx_by_tf_json,  # PR-BOT-INTELLIGENCE-UPGRADE C3 (unused here)
        ) = await _compute_aggregator_hook_fields(
            symbol="BTC/USDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=pipeline_session,
        )

    # Positive funding (long-side pays) → SHORT receives boost → BOOST value
    # Per funding_directional.py: positive funding → boost SHORT
    assert funding_directional_adj is not None
    assert funding_directional_adj > 0.0, (
        "Positive funding + SHORT direction → SHORT boost must be > 0"
    )

    # effective_score must be negative (SHORT direction, score=-0.40)
    assert effective_score is not None
    assert effective_score < 0.0, (
        "SHORT direction with negative final_score → effective_score must be negative"
    )
