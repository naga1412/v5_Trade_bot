"""HOTFIX: predictions stopped writing 2026-05-11 because validator
NotNullViolationError poisoned the shared session transaction.

This test file covers the two-session isolation pattern that fixes it:
  - Transaction 1: persist the prediction. Source-of-truth write; MUST
    commit even if downstream telemetry fails.
  - Transaction 2: record pending validation row. Best-effort. Failure
    here MUST log a warning but MUST NOT roll back the prediction.

Repro: pre-hotfix code called record_pending_validation(prediction_id=None, ...)
in the same session as persist_prediction(). The validator's INSERT
violates a NOT NULL constraint on prediction_id, which marks the
Postgres transaction as ABORTED. Subsequent session.commit() fails,
session __aexit__ rolls back the prediction insert. Net effect: zero
prediction rows persisted for 6 days while build_prediction kept
firing successfully.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


_PREDICTIONS_DDL = """
CREATE TABLE predictions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    symbol        TEXT,
    timeframe     TEXT,
    ts            TEXT,
    layer_scores  TEXT,
    final_score   REAL,
    direction     TEXT,
    confidence    REAL,
    inputs_hash   TEXT,
    model_version TEXT,
    cold_start    INTEGER,
    -- PR1 record-only analytics columns
    mtf_agreement         INTEGER,
    mtf_dominant_tf       TEXT,
    mtf_directions_json   TEXT,
    p_win                 REAL,
    effective_score       REAL,
    realized_vol_20d      REAL,
    funding_directional_adj REAL,
    -- audit chain metadata
    prev_hash     TEXT,
    row_hash      TEXT
)
"""

# Mirrors backend/alembic/versions/2026_05_12_0018_prediction_validations.py
# — the prediction_id NOT NULL constraint is the trigger for the bug.
_PREDICTION_VALIDATIONS_DDL = """
CREATE TABLE prediction_validations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    direction     TEXT NOT NULL,
    score         REAL NOT NULL,
    confidence    REAL NOT NULL,
    anchor_ts     TEXT NOT NULL,
    anchor_close  REAL NOT NULL,
    target_ts     TEXT NOT NULL,
    target_close  REAL,
    was_correct   INTEGER,
    pnl_pct       REAL,
    validated_at  TEXT
)
"""


@pytest.fixture
async def factory_with_tables():
    """In-memory SQLite async session factory with both schema tables.

    Yields a callable matching ``async_sessionmaker``'s interface so the
    hotfix helper can call ``async with factory() as session:`` exactly as
    the production code does.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_PREDICTIONS_DDL))
        await conn.execute(sa.text(_PREDICTION_VALIDATIONS_DDL))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _sample_predictions_payload() -> dict:
    return {
        "user_id": 1,
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "ts": "2026-05-17T22:00:00+00:00",
        "layer_scores": "{}",
        "final_score": 0.42,
        "direction": "LONG",
        "confidence": 0.70,
        "inputs_hash": "abc123",
        "model_version": "sp-0",
        "cold_start": 0,
    }


@pytest.mark.asyncio
async def test_prediction_persists_when_validator_succeeds(factory_with_tables) -> None:
    """Happy path: both sessions commit. predictions has the row, and
    prediction_validations has a row whose prediction_id points at it."""
    from app.ws.live_prediction import _persist_prediction_and_schedule_validation

    pred_id = await _persist_prediction_and_schedule_validation(
        factory_with_tables,
        predictions_payload=_sample_predictions_payload(),
        user_id=1,
        symbol="BTC/USDT",
        timeframe="1h",
        direction="LONG",
        score=0.42,
        confidence=0.70,
        anchor_ts=datetime(2026, 5, 17, 22, tzinfo=timezone.utc),
        anchor_close=80_000.0,
    )

    assert isinstance(pred_id, int) and pred_id > 0

    async with factory_with_tables() as session:
        pred_row = (
            await session.execute(
                sa.text("SELECT id, symbol, direction FROM predictions WHERE id = :i"),
                {"i": pred_id},
            )
        ).first()
        assert pred_row is not None
        assert pred_row.symbol == "BTC/USDT"
        assert pred_row.direction == "LONG"

        val_rows = (
            await session.execute(
                sa.text(
                    "SELECT prediction_id, symbol, direction FROM prediction_validations "
                    "WHERE prediction_id = :i"
                ),
                {"i": pred_id},
            )
        ).all()
        assert len(val_rows) == 1
        assert val_rows[0].prediction_id == pred_id
        assert val_rows[0].symbol == "BTC/USDT"


@pytest.mark.asyncio
async def test_prediction_persists_when_validator_fails(
    factory_with_tables, caplog
) -> None:
    """Hotfix property: when ``record_pending_validation`` raises, the
    prediction MUST still be persisted. The validator failure must be
    logged as a WARNING but MUST NOT propagate or roll back."""
    from app.ws import live_prediction as live_pred_module

    # Force the validator call to raise an unexpected exception (bypassing
    # the function's internal try/except — which only catches DB-level
    # errors). This simulates the worst case where the validator transaction
    # collapses entirely.
    raising_mock = AsyncMock(
        side_effect=RuntimeError("simulated validator collapse"),
    )

    with patch.object(
        live_pred_module, "record_pending_validation", raising_mock
    ), caplog.at_level("WARNING"):
        pred_id = await live_pred_module._persist_prediction_and_schedule_validation(
            factory_with_tables,
            predictions_payload=_sample_predictions_payload(),
            user_id=1,
            symbol="BTC/USDT",
            timeframe="1h",
            direction="LONG",
            score=0.42,
            confidence=0.70,
            anchor_ts=datetime(2026, 5, 17, 22, tzinfo=timezone.utc),
            anchor_close=80_000.0,
        )

    # Hotfix property #1: prediction MUST be persisted (this is the bug
    # the hotfix fixes — pre-hotfix, the validator failure rolled it back).
    assert isinstance(pred_id, int) and pred_id > 0
    async with factory_with_tables() as session:
        pred_row = (
            await session.execute(
                sa.text("SELECT id FROM predictions WHERE id = :i"), {"i": pred_id},
            )
        ).first()
        assert pred_row is not None, (
            f"Prediction id={pred_id} was rolled back — hotfix regressed"
        )

    # Hotfix property #2: zero validation rows (validator failed, no row written).
    async with factory_with_tables() as session:
        val_count = (
            await session.execute(
                sa.text("SELECT count(*) FROM prediction_validations")
            )
        ).scalar_one()
        assert val_count == 0

    # Hotfix property #3: a WARNING was logged with the failure context.
    warning_msgs = [
        rec.message for rec in caplog.records if rec.levelname == "WARNING"
    ]
    assert any("record_pending_validation failed" in m for m in warning_msgs), (
        f"Expected 'record_pending_validation failed' warning. Got: {warning_msgs}"
    )
    assert any(str(pred_id) in m for m in warning_msgs), (
        f"Warning should reference the persisted prediction id={pred_id}"
    )

    # Hotfix property #4: the validator mock was actually called (proving
    # the hotfix didn't accidentally skip the validator call entirely).
    assert raising_mock.await_count == 1
