"""Verifier must use HASH_PAYLOAD_COLUMNS — otherwise a recording-only
column would falsely 'break' the chain on existing rows."""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.db.audit import insert_with_chain
# audit_verify.py is the actual module (not audit_verifier.py)
from app.db.audit_verify import verify_chain


@pytest.fixture
async def session_with_recording_only_column():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, symbol TEXT, timeframe TEXT,
                ts TEXT, layer_scores TEXT, final_score REAL,
                direction TEXT, confidence REAL, inputs_hash TEXT,
                model_version TEXT, cold_start INTEGER,
                mtf_agreement INTEGER,
                prev_hash TEXT, row_hash TEXT
            )
        """))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_verifier_ignores_non_whitelisted_column(session_with_recording_only_column):
    s = session_with_recording_only_column
    payload = {
        "user_id": 1, "symbol": "BTCUSDT", "timeframe": "1h",
        "ts": "2026-05-16T10:00:00+00:00",
        "layer_scores": "{}", "final_score": 0.35,
        "direction": "LONG", "confidence": 0.6,
        "inputs_hash": "abc", "model_version": "sp-0",
        "cold_start": 0,
        "mtf_agreement": 4,  # NON-hashed
    }
    await insert_with_chain(s, "predictions", payload)
    # verify_chain without explicit columns — uses HASH_PAYLOAD_COLUMNS internally
    result = await verify_chain(s, "predictions")
    assert result.violations == [], (
        f"verifier reported false break — recording-only column "
        f"changed the verifier's expected hash: {result.violations}"
    )
