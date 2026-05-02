from datetime import datetime, timezone
from dataclasses import dataclass

import httpx

from app.core.dataquality.validator import Candle
from app.data.ratelimit import TokenBucket


_TF_TO_BINANCE = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}


def _to_pair(binance_symbol: str) -> str:
    """BTCUSDT -> BTC/USDT (heuristic: split before USDT/USDC/BUSD)."""
    for quote in ("USDT", "USDC", "BUSD", "FDUSD"):
        if binance_symbol.endswith(quote):
            return f"{binance_symbol[:-len(quote)]}/{quote}"
    return binance_symbol


@dataclass
class BinanceClient:
    http: httpx.AsyncClient
    base_url: str = "https://api.binance.com"
    bucket: TokenBucket | None = None

    def __post_init__(self) -> None:
        if self.bucket is None:
            # Binance REST: 1200 weight per minute = 20/sec
            self.bucket = TokenBucket(capacity=1200, refill_per_sec=20.0)

    async def fetch_klines(
        self, symbol: str, timeframe: str, *, limit: int = 500
    ) -> list[Candle]:
        assert self.bucket is not None
        await self.bucket.acquire(weight=2)  # /klines weight = 2 for limit<=100

        binance_tf = _TF_TO_BINANCE[timeframe]
        url = f"{self.base_url}/api/v3/klines"
        params = {"symbol": symbol, "interval": binance_tf, "limit": limit}
        response = await self.http.get(url, params=params, timeout=10.0)
        response.raise_for_status()

        result: list[Candle] = []
        pair = _to_pair(symbol)
        for row in response.json():
            result.append(
                Candle(
                    symbol=pair,
                    timeframe=timeframe,
                    ts=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        return result


class BinanceKlineStream:
    """Subscribes to wss://stream.binance.com:9443/ws/<symbol>@kline_<tf>."""
    pass  # next task implements
