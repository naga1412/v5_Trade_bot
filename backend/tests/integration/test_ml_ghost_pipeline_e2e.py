"""SP-1 Phase F (task F4): full ghost-candle pipeline end-to-end test.

Wires together everything that lands a ghost candle in the database:
  1. Build a fresh ``ConvLSTMPredictor`` with random init.
  2. Save its weights to a temp file, register the file as an
     ``ml_checkpoints`` row, then ``set_active(...)`` it via
     ``app.ml.checkpoints``.
  3. Synthesize 256 bars of OHLCV.
  4. Call ``predict_ghost_candle(model, bars, last_close)`` directly
     (mirrors the worker's flow without needing a live Binance WS).
  5. Persist a prediction row via ``persist_prediction(...)`` with the
     ghost_* + model_checkpoint_id columns populated.
  6. Read the row back and assert:
        - all seven ghost_* columns are non-NULL,
        - ``model_checkpoint_id`` matches the active checkpoint id,
        - ``row_hash`` is a 64-char hex digest of the audit chain.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
import torch
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.execution.persistence import persist_prediction
from app.ml.checkpoints import (
    ActiveCheckpoint,
    clear_active,
    get_active_model_and_checkpoint,
    set_active,
)
from app.ml.inference import predict_ghost_candle
from app.ml.model import ConvLSTMPredictor


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


def _synthetic_bars(n: int = 300, *, seed: int = 0) -> pd.DataFrame:
    """Generate ``n`` rows of plausible BTC/USDT-shaped OHLCV bars.

    Random walk with deterministic seed so the pipeline is reproducible
    across test runs.
    """
    rng = np.random.default_rng(seed)
    closes = 80_000.0 + np.cumsum(rng.normal(0.0, 50.0, size=n))
    opens = closes + rng.normal(0.0, 25.0, size=n)
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0.0, 30.0, size=n))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0.0, 30.0, size=n))
    volumes = np.abs(rng.normal(100.0, 20.0, size=n))
    idx = pd.date_range("2026-05-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=idx,
    )


@pytest.mark.asyncio
async def test_full_ghost_pipeline_persists_row_with_audit_chain(
    tmp_path: Path,
) -> None:
    """End-to-end: stub model -> set_active -> infer -> persist -> verify."""
    # --- 1) build + serialize stub model ----------------------------------
    torch.manual_seed(7)
    model = ConvLSTMPredictor()
    ckpt_path = tmp_path / "stub_conv_lstm.pt"
    torch.save(model.state_dict(), ckpt_path)
    sha = hashlib.sha256(ckpt_path.read_bytes()).hexdigest()

    # --- 2) DB setup: ml_checkpoints + predictions tables -----------------
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE ml_checkpoints ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, model_name TEXT NOT NULL, "
            "version TEXT NOT NULL, checkpoint_uri TEXT NOT NULL, "
            "sha256 TEXT NOT NULL, trained_at TEXT NOT NULL, "
            "train_data_window TEXT NOT NULL, eval_results TEXT NOT NULL, "
            "is_active INTEGER NOT NULL DEFAULT 0, "
            "activated_at TEXT, deactivated_at TEXT, notes TEXT, "
            "UNIQUE (model_name, version))"
        ))
        await conn.execute(sa.text(CREATE_PREDICTIONS_TABLE))

    async with AsyncSession(engine) as session:
        await session.execute(sa.text(
            "INSERT INTO ml_checkpoints "
            "(model_name, version, checkpoint_uri, sha256, trained_at, "
            "train_data_window, eval_results, is_active, activated_at) "
            "VALUES (:n, :v, :u, :s, :t, :w, :e, 1, :a)"
        ), {
            "n": "conv_lstm_predictor",
            "v": "0.0.1-stub",
            "u": ckpt_path.as_uri(),
            "s": sha,
            "t": datetime.now(timezone.utc).isoformat(),
            "w": "synthetic",
            "e": json.dumps({}),
            "a": datetime.now(timezone.utc).isoformat(),
        })
        await session.commit()
        row = (await session.execute(sa.text(
            "SELECT id FROM ml_checkpoints WHERE version = '0.0.1-stub'"
        ))).one()
        checkpoint_id = int(row.id)

    # Pin module state for the inference path to consume.
    clear_active()
    active_ck = ActiveCheckpoint(
        id=checkpoint_id,
        model_name="conv_lstm_predictor",
        version="0.0.1-stub",
        sha256=sha,
        checkpoint_uri=ckpt_path.as_uri(),
    )
    set_active(model, active_ck)
    try:
        # --- 3) Synthesize bars + 4) infer the ghost candle ---------------
        bars = _synthetic_bars(n=300, seed=42)
        last_close = float(bars["close"].iloc[-1])

        active = get_active_model_and_checkpoint()
        assert active is not None, "module state not pinned"
        loaded_model, loaded_ck = active
        ghost = predict_ghost_candle(
            model=loaded_model, bars=bars, last_close=last_close, n_samples=4,
        )

        # Sanity — predictions are real numbers anchored near last_close.
        assert all(
            v == v  # not NaN
            for v in (
                ghost.open, ghost.high, ghost.low, ghost.close,
                ghost.p5_low, ghost.p95_high, ghost.uncertainty,
            )
        )
        assert ghost.uncertainty >= 0.0

        # --- 5) Persist a prediction row carrying the ghost payload -------
        payload = {
            "user_id": 1,
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "ts": bars.index[-1].isoformat(),
            "final_score": 0.42,
            "direction": "LONG",
            "confidence": 0.6,
            "inputs_hash": "e2e-stub-hash",
            "model_version": "sp-1-e2e",
            "cold_start": 0,
            "layer_scores": json.dumps({"L1": None}),
            "ghost_open": ghost.open,
            "ghost_high": ghost.high,
            "ghost_low": ghost.low,
            "ghost_close": ghost.close,
            "ghost_p5_low": ghost.p5_low,
            "ghost_p95_high": ghost.p95_high,
            "ghost_uncertainty": ghost.uncertainty,
            "model_checkpoint_id": loaded_ck.id,
        }
        async with AsyncSession(engine) as session:
            row_hash = await persist_prediction(session, payload)
            await session.commit()

        # --- 6) Read back + verify ----------------------------------------
        async with AsyncSession(engine) as session:
            persisted = (await session.execute(sa.text(
                "SELECT ghost_open, ghost_high, ghost_low, ghost_close, "
                "ghost_p5_low, ghost_p95_high, ghost_uncertainty, "
                "model_checkpoint_id, row_hash, prev_hash "
                "FROM predictions"
            ))).one()

        assert persisted.ghost_open is not None
        assert persisted.ghost_high is not None
        assert persisted.ghost_low is not None
        assert persisted.ghost_close is not None
        assert persisted.ghost_p5_low is not None
        assert persisted.ghost_p95_high is not None
        assert persisted.ghost_uncertainty is not None
        assert persisted.model_checkpoint_id == checkpoint_id

        assert isinstance(row_hash, str)
        assert len(row_hash) == 64
        assert all(c in "0123456789abcdef" for c in row_hash)
        assert persisted.row_hash == row_hash
        # First row in the chain — prev_hash must be the genesis (all zeros).
        assert persisted.prev_hash == "0" * 64
    finally:
        clear_active()
        await engine.dispose()
