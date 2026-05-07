import json
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dataquality.validator import Candle, validate


class OHLCVPipeline:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process(
        self,
        candle: Candle,
        *,
        prev_close: float | None,
        prev_volume_median: float | None,
    ) -> bool:
        result = validate(
            candle, prev_close=prev_close, prev_volume_median=prev_volume_median
        )
        if not result.ok:
            for failure in result.failures:
                await self.session.execute(
                    sa.text(
                        "INSERT INTO data_quality_alerts "
                        "(ts, symbol, timeframe, candle_ts, check_name, details) "
                        "VALUES (CURRENT_TIMESTAMP, :s, :tf, :cts, :ck, :d)"
                    ),
                    {
                        "s": candle.symbol, "tf": candle.timeframe,
                        "cts": candle.ts,
                        "ck": failure,
                        "d": json.dumps({"open": candle.open, "high": candle.high,
                                         "low": candle.low, "close": candle.close,
                                         "volume": candle.volume}),
                    },
                )
            return False

        await self.session.execute(
            sa.text(
                "INSERT INTO ohlcv (symbol, timeframe, ts, open, high, low, close, volume) "
                "VALUES (:s, :tf, :ts, :o, :h, :l, :c, :v) "
                "ON CONFLICT (symbol, timeframe, ts) DO UPDATE SET "
                "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
                "close=EXCLUDED.close, volume=EXCLUDED.volume"
            ),
            {
                "s": candle.symbol, "tf": candle.timeframe,
                "ts": candle.ts,
                "o": candle.open, "h": candle.high,
                "l": candle.low, "c": candle.close, "v": candle.volume,
            },
        )
        return True
