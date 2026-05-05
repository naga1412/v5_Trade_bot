"""Binance REST + WS adapter (SP-0.5 -> SP-3 hardened).

SP-3 refactor:
- Class renamed BinanceClient -> BinanceAdapter (Protocol-conformant); the
  old name is kept as an alias for backward compatibility.
- Uses RateLimitedClient with header sync via X-MBX-USED-WEIGHT-1M.
- Accepts canonical-form symbols (BTC/USDT) and translates internally.
- Adds list_symbols() backed by /api/v3/exchangeInfo.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import websockets

from app.core.dataquality.validator import Candle as ValidatorCandle
from app.data.adapters._base import Candle, SymbolInfo
from app.data.ratelimit import RateLimitedClient, TokenBucket
from app.data.symbols import from_native, to_native


log = logging.getLogger(__name__)

_TF_TO_BINANCE = {
    "1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d",
}
_BINANCE_QUOTE_PRIORITY = ("USDT", "USDC", "BUSD", "FDUSD")


def _to_pair(binance_symbol: str) -> str:
    """BTCUSDT -> BTC/USDT (heuristic). Kept for back-compat in WS path."""
    for quote in _BINANCE_QUOTE_PRIORITY:
        if binance_symbol.endswith(quote):
            return f"{binance_symbol[:-len(quote)]}/{quote}"
    return binance_symbol


def _default_rate_client(http: httpx.AsyncClient) -> RateLimitedClient:
    return RateLimitedClient(
        exchange="binance",
        http=http,
        buckets={"default": TokenBucket(capacity=1200, refill_per_sec=20.0)},
        endpoint_weights={"klines": 2, "exchangeInfo": 10},
        sync_header="X-MBX-USED-WEIGHT-1M",
        sync_capacity=1200.0,
    )


def _coerce_canonical(symbol: str) -> str:
    """Accept either canonical 'BTC/USDT' or native 'BTCUSDT' for back-compat.

    SP-3 callers pass canonical form; SP-0.5 callers (worker, live_prediction,
    tab1) historically passed native form. Normalize to canonical so the
    adapter has a single internal contract.
    """
    if "/" in symbol:
        return symbol
    return from_native("binance", symbol)


@dataclass
class BinanceAdapter:
    """SP-3 ExchangeAdapter implementation for Binance Spot."""

    http: httpx.AsyncClient
    base_url: str = "https://api.binance.com"
    rate_client: RateLimitedClient | None = None
    name: str = field(default="binance", init=False)

    def __post_init__(self) -> None:
        if self.rate_client is None:
            self.rate_client = _default_rate_client(self.http)

    async def fetch_klines(
        self,
        symbol: str | None = None,
        timeframe: str | None = None,
        *,
        limit: int = 500,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        """Fetch up to `limit` bars for `symbol` at `timeframe`.

        `symbol` is canonical 'BTC/USDT' (preferred) but also accepts the
        legacy native form 'BTCUSDT' for back-compat with SP-0.5 callers.
        Both `symbol` and `timeframe` may be passed positionally or as kwargs.
        """
        if symbol is None or timeframe is None:
            raise TypeError("symbol and timeframe are required")
        assert self.rate_client is not None
        binance_tf = _TF_TO_BINANCE[timeframe]
        canonical = _coerce_canonical(symbol)
        native = to_native("binance", canonical)
        params: dict[str, str | int] = {
            "symbol": native, "interval": binance_tf, "limit": limit,
        }
        if start is not None:
            params["startTime"] = int(start.timestamp() * 1000)
        if end is not None:
            params["endTime"] = int(end.timestamp() * 1000)
        url = f"{self.base_url}/api/v3/klines"
        try:
            response = await self.rate_client.request(
                "GET", url, endpoint_key="klines", params=params, timeout=10.0,
            )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            log.warning("binance fetch_klines network error: %s", e)
            return []

        result: list[Candle] = []
        for row in response.json():
            result.append(
                Candle(
                    ts=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                    open=float(row[1]), high=float(row[2]),
                    low=float(row[3]),  close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        return result

    async def list_symbols(self) -> list[SymbolInfo]:
        assert self.rate_client is not None
        url = f"{self.base_url}/api/v3/exchangeInfo"
        try:
            response = await self.rate_client.request(
                "GET", url, endpoint_key="exchangeInfo", timeout=15.0,
            )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            log.warning("binance list_symbols network error: %s", e)
            return []

        out: list[SymbolInfo] = []
        for sym in response.json().get("symbols", []):
            if sym.get("status") != "TRADING":
                continue
            if not sym.get("isSpotTradingAllowed", False):
                continue
            native = sym["symbol"]
            try:
                canonical = from_native("binance", native)
            except Exception:  # noqa: BLE001
                continue
            out.append(SymbolInfo(
                canonical=canonical,
                native=native,
                base=sym.get("baseAsset", ""),
                quote=sym.get("quoteAsset", ""),
                listed_at=None,  # Binance exchangeInfo doesn't expose listed_at
                delisted_at=None,
                asset_class="crypto",
            ))
        return out


# Back-compat alias - existing imports of BinanceClient still work.
BinanceClient = BinanceAdapter


# --- WebSocket stream (unchanged from SP-0.5) ---


class BinanceKlineStream:
    """Yields only CLOSED candles (k.x == True). Reconnect-with-backoff per spec."""

    def __init__(
        self, symbol: str, timeframe: str, *,
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

    async def stream(self) -> AsyncIterator[ValidatorCandle]:
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
                    yield ValidatorCandle(
                        symbol=_to_pair(kline["s"]),
                        timeframe=kline["i"],
                        ts=datetime.fromtimestamp(kline["t"] / 1000, tz=timezone.utc),
                        open=float(kline["o"]), high=float(kline["h"]),
                        low=float(kline["l"]),  close=float(kline["c"]),
                        volume=float(kline["v"]),
                    )
            except Exception:  # noqa: BLE001 - resilient WS loop
                await asyncio.sleep(min(30.0, backoff))
                backoff = min(30.0, backoff * 2)
