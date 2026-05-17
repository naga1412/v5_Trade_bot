"""SP-2 Phase F4 — full L2 pipeline E2E.

Wires together everything that lands an L2 pattern score in the database:
  1. Spin up an in-memory SQLite engine carrying the SP-2 schema:
     ``predictions`` (audit-chained), plus ``pattern_stats`` seeded with
     three rows including ``hammer`` at 60 % accuracy (n_samples=100).
  2. Synthesise 30 bars of OHLCV that culminate in a TA-Lib-recognised
     ``hammer`` (long lower wick, tiny body, prior downtrend).
  3. Build a :class:`PatternStatsLookup` from the seeded rows and pass it to
     :func:`build_prediction` so the L2 layer is exercised.
  4. Assert ``layer_scores["2"]`` is non-None and has direction ``LONG``
     (hammer + ancillary bullish patterns + 0.6 historical accuracy).
  5. Persist via :func:`persist_prediction` and read back the row.
  6. Verify the L2 score round-trips into the ``layer_scores`` JSON column
     and that the audit-chain ``row_hash`` is a 64-char hex digest derived
     from the canonical payload (so the L2 score is, by construction,
     part of the chain).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.execution.persistence import persist_prediction
from app.core.predictor import build_prediction
from app.core.scoring.layer2_patterns import PatternStatsLookup
from app.db.audit import canonical_row_json, compute_row_hash

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

CREATE_PATTERN_STATS_TABLE = (
    "CREATE TABLE pattern_stats ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "pattern_id TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
    "n_samples INTEGER NOT NULL DEFAULT 0, "
    "n_correct INTEGER NOT NULL DEFAULT 0, "
    "last_updated TEXT NOT NULL DEFAULT (datetime('now')), "
    "UNIQUE (pattern_id, symbol, timeframe))"
)


def _hammer_bars() -> pd.DataFrame:
    """30-bar synthetic OHLCV ending in a TA-Lib-recognised hammer.

    A 29-bar bear trend (close-by-close decline ~0.8) followed by a final
    bar with a tiny green body at the top of a wide range — long lower
    wick, no upper wick. CDLHAMMER returns +100 on this configuration with
    the current TA-Lib (verified at fixture creation).
    """
    n = 30
    opens = np.zeros(n)
    highs = np.zeros(n)
    lows = np.zeros(n)
    closes = np.zeros(n)
    for i in range(n - 1):
        open_ = 100.0 - i * 0.8
        close_ = open_ - 0.5
        opens[i] = open_
        closes[i] = close_
        highs[i] = open_ + 0.3
        lows[i] = close_ - 0.3
    # Final bar — hammer at the bottom of the downtrend.
    opens[-1] = closes[-2] - 0.2
    closes[-1] = opens[-1] + 0.1
    highs[-1] = closes[-1] + 0.05
    lows[-1] = opens[-1] - 5.0
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.full(n, 1_000.0),
        },
        index=idx,
    )


@pytest.mark.asyncio
async def test_l2_layer_lands_in_prediction_and_persists() -> None:
    """build_prediction with a PatternStatsLookup populates layer_scores["2"]."""
    bars = _hammer_bars()
    lookup = PatternStatsLookup(
        by_pattern={"hammer": 0.6, "doji": 0.55, "dragonfly_doji": 0.7},
    )
    pred = await build_prediction(
        symbol="BTC/USDT",
        timeframe="1h",
        bars=bars,
        pattern_stats_lookup=lookup,
    )

    l2 = pred.layer_scores.get("2")
    assert l2 is not None, (
        "L2 layer must be populated when pattern_stats_lookup is passed"
    )
    assert l2.direction == "LONG"
    assert 0.0 < l2.strength <= 1.0
    assert 0.0 <= l2.confidence <= 1.0

    # Notes is a compact JSON listing patterns that fired; cap respected.
    notes_payload = json.loads(l2.notes)
    fired_ids = {p["id"] for p in notes_payload["patterns"]}
    assert "hammer" in fired_ids


@pytest.mark.asyncio
async def test_l2_pipeline_e2e_persists_and_audit_chain_includes_l2() -> None:
    """build_prediction -> persist -> read back; audit hash binds the L2 score."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text(CREATE_PREDICTIONS_TABLE))
            await conn.execute(sa.text(CREATE_PATTERN_STATS_TABLE))

        # --- Seed pattern_stats with three rows ---------------------------
        async with AsyncSession(engine) as session:
            for pid, n_samples, n_correct in (
                ("hammer", 100, 60),       # accuracy 0.60
                ("doji", 80, 44),          # accuracy 0.55
                ("dragonfly_doji", 60, 42),  # accuracy 0.70
            ):
                await session.execute(
                    sa.text(
                        "INSERT INTO pattern_stats "
                        "(pattern_id, symbol, timeframe, n_samples, n_correct) "
                        "VALUES (:p, :s, :tf, :n, :w)"
                    ),
                    {"p": pid, "s": "BTC/USDT", "tf": "1h",
                     "n": n_samples, "w": n_correct},
                )
            await session.commit()

        # --- Build the lookup from the same data --------------------------
        bars = _hammer_bars()
        lookup = PatternStatsLookup(
            by_pattern={"hammer": 0.6, "doji": 0.55, "dragonfly_doji": 0.7},
        )
        pred = await build_prediction(
            symbol="BTC/USDT",
            timeframe="1h",
            bars=bars,
            pattern_stats_lookup=lookup,
        )
        l2 = pred.layer_scores["2"]
        assert l2 is not None
        assert l2.direction == "LONG"

        # --- Persist via persist_prediction --------------------------------
        layer_scores_json = json.dumps({
            k: (
                None if v is None else {
                    "direction": v.direction,
                    "strength": v.strength,
                    "confidence": v.confidence,
                    "notes": v.notes,
                }
            )
            for k, v in pred.layer_scores.items()
        })
        payload: dict = {
            "user_id": 1,
            "symbol": pred.symbol,
            "timeframe": pred.timeframe,
            "ts": pred.ts.isoformat(),
            "final_score": pred.final.score,
            "direction": pred.final.direction,
            "confidence": pred.final.confidence,
            "inputs_hash": pred.inputs_hash,
            "model_version": "sp-2-l2-e2e",
            "cold_start": int(pred.cold_start),
            "layer_scores": layer_scores_json,
            # Ghost columns NULL — this exercises the L2 path, not L1.5.
            "ghost_open": None, "ghost_high": None, "ghost_low": None,
            "ghost_close": None, "ghost_p5_low": None, "ghost_p95_high": None,
            "ghost_uncertainty": None, "model_checkpoint_id": None,
        }

        async with AsyncSession(engine) as session:
            _pred_id, row_hash = await persist_prediction(session, payload)
            await session.commit()

        # --- Read back + assert L2 round-tripped --------------------------
        async with AsyncSession(engine) as session:
            row = (await session.execute(sa.text(
                "SELECT layer_scores, row_hash, prev_hash FROM predictions"
            ))).one()

        persisted_layers = json.loads(row.layer_scores)
        assert persisted_layers["2"] is not None
        assert persisted_layers["2"]["direction"] == "LONG"
        assert persisted_layers["2"]["strength"] > 0.0
        assert "hammer" in persisted_layers["2"]["notes"]

        # Audit chain — first row, prev=genesis, hash deterministic over payload.
        assert row.prev_hash == "0" * 64
        expected_hash = compute_row_hash("0" * 64, payload)
        assert row.row_hash == row_hash == expected_hash

        # Sanity — flipping the L2 score in payload changes the chained hash.
        # (By construction; documents that L2 is part of the audit chain.)
        tampered = {**payload, "layer_scores": json.dumps({"2": None})}
        tampered_hash = compute_row_hash("0" * 64, tampered)
        assert tampered_hash != expected_hash
        # And canonical_row_json includes the layer_scores key so the diff
        # is real and not a sort-key artefact.
        assert "layer_scores" in canonical_row_json(payload)
    finally:
        await engine.dispose()
