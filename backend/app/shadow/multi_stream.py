"""Binance combined-stream utilities for multi-symbol kline subscriptions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import websockets


def build_combined_stream_url(
    symbols: list[str],
    *,
    timeframe: str = "1h",
    base: str = "wss://fstream.binance.com",
    max_streams: int = 200,
) -> str:
    """Build a Binance combined-stream URL (multiple klines on one connection)."""
    if not symbols:
        raise ValueError("symbols must be non-empty")
    truncated = symbols[:max_streams]
    streams = "/".join(f"{s.lower()}@kline_{timeframe}" for s in truncated)
    return f"{base}/stream?streams={streams}"


@dataclass(frozen=True)
class MultiStreamCandle:
    symbol: str
    timeframe: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class MultiStreamReader:
    """Reads a Binance combined-stream and yields closed candles only.

    Reconnects with backoff on disconnection.
    """

    def __init__(
        self,
        symbols: list[str],
        *,
        timeframe: str = "1h",
        base_url: str = "wss://fstream.binance.com",
        _connect: Callable[[str], AsyncIterator[str]] | None = None,
    ) -> None:
        self.symbols = symbols
        self.timeframe = timeframe
        self.url = build_combined_stream_url(
            symbols=symbols, timeframe=timeframe, base=base_url
        )
        self._connect = _connect

    async def _real_connect(self, url: str) -> AsyncIterator[str]:
        async with websockets.connect(
            url, ping_interval=15, ping_timeout=10, max_size=2**20
        ) as ws:
            async for raw in ws:
                yield raw if isinstance(raw, str) else raw.decode()

    async def stream(self) -> AsyncIterator[MultiStreamCandle]:
        connect = self._connect or self._real_connect
        backoff = 1.0
        while True:
            try:
                async for raw in connect(self.url):
                    backoff = 1.0
                    payload = json.loads(raw)
                    data = payload.get("data") if isinstance(payload, dict) else None
                    if not data:
                        continue
                    kline = data.get("k") if isinstance(data, dict) else None
                    if not kline or not kline.get("x"):
                        continue
                    yield MultiStreamCandle(
                        symbol=kline["s"],
                        timeframe=kline["i"],
                        ts=datetime.fromtimestamp(kline["t"] / 1000, tz=timezone.utc),
                        open=float(kline["o"]),
                        high=float(kline["h"]),
                        low=float(kline["l"]),
                        close=float(kline["c"]),
                        volume=float(kline["v"]),
                    )
            except Exception:  # noqa: BLE001
                await asyncio.sleep(min(30.0, backoff))
                backoff = min(30.0, backoff * 2)
