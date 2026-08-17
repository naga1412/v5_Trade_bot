"""Postgres-only: verifies the live_prediction_watermarks table shape."""
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="Postgres DATABASE_URL not set — migration tests are CI-only. "
           "Set DATABASE_URL=postgresql+asyncpg://... to run locally.",
)


@pytest.mark.asyncio
async def test_watermark_table_upsert_and_pk() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO live_prediction_watermarks (symbol, timeframe, last_open_time) "
            "VALUES ('SOL/USDT', '1h', 1000) "
            "ON CONFLICT (symbol, timeframe) DO UPDATE SET last_open_time = EXCLUDED.last_open_time"
        ))
        await conn.execute(sa.text(
            "INSERT INTO live_prediction_watermarks (symbol, timeframe, last_open_time) "
            "VALUES ('SOL/USDT', '1h', 2000) "
            "ON CONFLICT (symbol, timeframe) DO UPDATE SET last_open_time = EXCLUDED.last_open_time"
        ))
        row = (await conn.execute(sa.text(
            "SELECT last_open_time FROM live_prediction_watermarks "
            "WHERE symbol = 'SOL/USDT' AND timeframe = '1h'"
        ))).one()
        assert row.last_open_time == 2000
        count = (await conn.execute(sa.text(
            "SELECT COUNT(*) AS n FROM live_prediction_watermarks "
            "WHERE symbol = 'SOL/USDT' AND timeframe = '1h'"
        ))).one()
        assert count.n == 1  # PK enforced upsert, not a second row
    await engine.dispose()
