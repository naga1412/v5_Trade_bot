import pytest
import sqlalchemy as sa
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.core.dataquality.validator import Candle
from app.data.ohlcv_pipeline import OHLCVPipeline


@pytest.mark.asyncio
async def test_pipeline_upserts_valid_candle_and_skips_invalid() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE ohlcv (symbol TEXT, timeframe TEXT, ts TEXT, "
            "open REAL, high REAL, low REAL, close REAL, volume REAL, "
            "PRIMARY KEY(symbol,timeframe,ts))"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE data_quality_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts TEXT, symbol TEXT, timeframe TEXT, candle_ts TEXT, "
            "check_name TEXT, details TEXT)"
        ))

    valid = Candle("BTC/USDT","1h",datetime(2026,5,1,12,tzinfo=timezone.utc),
                   100,110,95,105,1000)
    invalid = Candle("BTC/USDT","1h",datetime(2026,5,1,13,tzinfo=timezone.utc),
                     200,110,95,105,1000)  # open outside range

    async with AsyncSession(engine) as session:
        pipe = OHLCVPipeline(session)
        await pipe.process(valid, prev_close=99.0, prev_volume_median=1000.0)
        await pipe.process(invalid, prev_close=105.0, prev_volume_median=1000.0)
        await session.commit()

        ohlcv_rows = (await session.execute(sa.text("SELECT COUNT(*) FROM ohlcv"))).scalar()
        dqa_rows = (await session.execute(sa.text("SELECT COUNT(*) FROM data_quality_alerts"))).scalar()
    assert ohlcv_rows == 1
    assert dqa_rows == 1
