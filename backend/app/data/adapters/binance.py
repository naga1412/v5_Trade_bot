import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from dataclasses import dataclass

import httpx
import websockets

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
    """Yields only CLOSED candles (k.x == True). Skips intra-bar updates.

    Reconnect-with-backoff is handled here per §5.8.
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        *,
        base_ws_url: str = "wss://stream.binance.com:9443",
        _connect: Callable[[str], AsyncIterator[str]] | None = None,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.base_ws_url = base_ws_url
        self._connect = _connect
        pair = symbol.lower()
        self.url = f"{base_ws_url}/ws/{pair}@kline_{timeframe}"

    async def _real_connect(self, url: str) -> AsyncIterator[str]:
        async with websockets.connect(url, ping_interval=15, ping_timeout=10) as ws:
            async for msg in ws:
                yield msg if isinstance(msg, str) else msg.decode()

    async def stream(self) -> AsyncIterator[Candle]:
        connect = self._connect or self._real_connect
        backoff = 1.0
        while True:
            try:
                async for raw in connect(self.url):
                    backoff = 1.0
                    payload = json.loads(raw)
                    kline = payload.get("k") if isinstance(payload, dict) else None
                    if not kline or not kline.get("x"):
                        continue
                    yield Candle(
                        symbol=_to_pair(kline["s"]),
                        timeframe=kline["i"],
                        ts=datetime.fromtimestamp(kline["t"] / 1000, tz=timezone.utc),
                        open=float(kline["o"]),
                        high=float(kline["h"]),
                        low=float(kline["l"]),
                        close=float(kline["c"]),
                        volume=float(kline["v"]),
                    )
            except Exception:  # noqa: BLE001 — resilient WS loop
                await asyncio.sleep(min(30.0, backoff))
                backoff = min(30.0, backoff * 2)
