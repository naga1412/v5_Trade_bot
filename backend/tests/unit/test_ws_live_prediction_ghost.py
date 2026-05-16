"""SP-1 Phase D1: ghost-column persistence + live_prediction worker wiring.

The worker now optionally calls predict_ghost_candle when an active ML
checkpoint is loaded (`get_active_model_and_checkpoint()` returns a tuple)
and merges the seven ghost_* columns + model_checkpoint_id into the
persistence payload. When no checkpoint is loaded, the path is a no-op
and predictions persist exactly as before — this test covers both paths
at the persistence layer.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.execution.persistence import persist_prediction


CREATE_PREDICTIONS_TABLE = (
    "CREATE TABLE predictions ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
    "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, ts TEXT NOT NULL, "
    "final_score REAL NOT NULL, direction TEXT NOT NULL, "
    "confidence REAL NOT NULL, inputs_hash TEXT NOT NULL, "
    "model_version TEXT NOT NULL, cold_start INTEGER NOT NULL, "
    "layer_scores TEXT NOT NULL, "
    "ghost_open REAL, ghost_high REAL, ghost_low REAL, ghost_close REAL, "
    "ghost_p5_low REAL, ghost_p95_high REAL, ghost_uncertainty REAL, "
    "model_checkpoint_id INTEGER, "
    "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL)"
)


@pytest.mark.asyncio
async def test_persist_prediction_accepts_ghost_columns() -> None:
    """persist_prediction must accept the eight new ghost-related keys."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(CREATE_PREDICTIONS_TABLE))

    payload = {
        "user_id": 1,
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "ts": datetime.now(timezone.utc).isoformat(),
        "final_score": 0.5,
        "direction": "LONG",
        "confidence": 0.7,
        "inputs_hash": "h",
        "model_version": "sp-0",
        "cold_start": 0,
        "layer_scores": json.dumps({}),
        "ghost_open": 80100.0,
        "ghost_high": 80300.0,
        "ghost_low": 79900.0,
        "ghost_close": 80200.0,
        "ghost_p5_low": 79500.0,
        "ghost_p95_high": 80800.0,
        "ghost_uncertainty": 0.005,
        "model_checkpoint_id": 42,
    }
    async with AsyncSession(engine) as session:
        pred_id, row_hash = await persist_prediction(session, payload)
        await session.commit()
        row = (
            await session.execute(
                sa.text(
                    "SELECT ghost_close, model_checkpoint_id FROM predictions"
                )
            )
        ).one()
    assert row.ghost_close == 80200.0
    assert row.model_checkpoint_id == 42
    assert isinstance(row_hash, str) and len(row_hash) == 64
    assert isinstance(pred_id, int) and pred_id > 0


@pytest.mark.asyncio
async def test_persist_prediction_omitting_ghost_keys_still_works() -> None:
    """Ghost keys are optional — when no model loaded, persistence works without them."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(CREATE_PREDICTIONS_TABLE))

    payload = {
        "user_id": 1,
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "ts": datetime.now(timezone.utc).isoformat(),
        "final_score": 0.5,
        "direction": "LONG",
        "confidence": 0.7,
        "inputs_hash": "h",
        "model_version": "sp-0",
        "cold_start": 0,
        "layer_scores": json.dumps({}),
    }
    async with AsyncSession(engine) as session:
        await persist_prediction(session, payload)
        await session.commit()
        row = (
            await session.execute(sa.text("SELECT ghost_close FROM predictions"))
        ).one()
    assert row.ghost_close is None
