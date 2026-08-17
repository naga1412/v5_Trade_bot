from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ws.futures_poll import _load_watermark, _save_watermark

_CREATE_TABLE = (
    "CREATE TABLE live_prediction_watermarks ("
    "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
    "last_open_time INTEGER NOT NULL, "
    "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "PRIMARY KEY (symbol, timeframe))"
)


@pytest.fixture
async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_CREATE_TABLE))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_load_returns_none_when_never_seen(_session_factory) -> None:
    result = await _load_watermark(_session_factory, "SOL/USDT", "1h")
    assert result is None


@pytest.mark.asyncio
async def test_save_then_load_roundtrips(_session_factory) -> None:
    await _save_watermark(_session_factory, "SOL/USDT", "1h", 123456)
    result = await _load_watermark(_session_factory, "SOL/USDT", "1h")
    assert result == 123456


@pytest.mark.asyncio
async def test_save_twice_upserts_not_duplicates(_session_factory) -> None:
    await _save_watermark(_session_factory, "SOL/USDT", "1h", 100)
    await _save_watermark(_session_factory, "SOL/USDT", "1h", 200)
    result = await _load_watermark(_session_factory, "SOL/USDT", "1h")
    assert result == 200
    async with _session_factory() as session:
        count = (await session.execute(sa.text(
            "SELECT COUNT(*) AS n FROM live_prediction_watermarks "
            "WHERE symbol = 'SOL/USDT' AND timeframe = '1h'"
        ))).one()
    assert count.n == 1
