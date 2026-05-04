"""Unit tests for app/ml/export.py — local Parquet writer (SP-1 A4)."""
import json
from datetime import datetime, timedelta, timezone

import pyarrow.parquet as pq
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.ml.export import ExportManifest, export_recent_to_parquet


@pytest_asyncio.fixture
async def engine_with_minimal_tables(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
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
        ))
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL, exit_price REAL NOT NULL, "
            "stop_loss REAL NOT NULL, take_profit REAL NOT NULL, "
            "pnl_pct REAL NOT NULL, pnl_usdt REAL NOT NULL, "
            "exit_reason TEXT NOT NULL, bars_held INTEGER NOT NULL, "
            "opened_at TEXT NOT NULL, closed_at TEXT NOT NULL, "
            "signal_id TEXT NOT NULL, "
            "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL)"
        ))
    yield engine, tmp_path
    await engine.dispose()


@pytest.mark.asyncio
async def test_export_writes_three_parquets_plus_manifest(engine_with_minimal_tables) -> None:
    engine, tmp_path = engine_with_minimal_tables
    now = datetime.now(timezone.utc)

    async with AsyncSession(engine) as session:
        for i in range(5):
            await session.execute(sa.text(
                "INSERT INTO predictions "
                "(user_id, symbol, timeframe, ts, final_score, direction, confidence, "
                "inputs_hash, model_version, cold_start, layer_scores, "
                "prev_hash, row_hash) "
                "VALUES (1, 'BTC/USDT', '1h', :ts, 0.5, 'LONG', 0.7, "
                "'h', 'sp-0', 0, '{}', '0', 'r')"
            ), {"ts": (now - timedelta(hours=i)).isoformat()})
        await session.commit()

    manifest = await export_recent_to_parquet(
        session_factory=lambda: AsyncSession(engine),
        out_dir=tmp_path,
        days=30,
        now=now,
    )

    assert (tmp_path / "predictions_30d.parquet").exists()
    # may be 0 rows if no ohlcv table; export still creates empty file
    assert (tmp_path / "ohlcv_1h_30d.parquet").exists()
    assert (tmp_path / "shadow_trades_30d.parquet").exists()
    assert (tmp_path / "manifest.json").exists()

    table = pq.read_table(tmp_path / "predictions_30d.parquet")
    assert table.num_rows == 5
    assert "ghost_close" in table.schema.names

    manifest_json = json.loads((tmp_path / "manifest.json").read_text())
    assert "predictions_30d.parquet" in manifest_json["files"]
    assert len(manifest_json["files"]["predictions_30d.parquet"]["sha256"]) == 64
    assert manifest_json["window_days"] == 30
    assert manifest.window_days == 30


@pytest.mark.asyncio
async def test_export_returns_manifest_dataclass(engine_with_minimal_tables) -> None:
    engine, tmp_path = engine_with_minimal_tables
    manifest = await export_recent_to_parquet(
        session_factory=lambda: AsyncSession(engine),
        out_dir=tmp_path,
        days=30,
    )
    assert isinstance(manifest, ExportManifest)
    assert manifest.window_days == 30
    assert manifest.created_at is not None
