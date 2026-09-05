"""Binance combined-stream utilities for multi-symbol kline subscriptions."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import websockets

log = logging.getLogger(__name__)


def build_combined_stream_url(
    symbols: list[str],
    *,
    timeframe: str = "1h",
    base: str = "wss://stream.binance.com:9443",
    max_streams: int = 200,
) -> str:
    """Build a Binance combined-stream URL (multiple klines on one connection).

    Default base is the SPOT endpoint (stream.binance.com:9443), NOT the
    Futures endpoint (fstream.binance.com). Reason: Binance silently
    geoblocks Futures market-data WS streams from EEA jurisdictions —
    handshake completes but zero frames are ever delivered. Confirmed
    from the production Hetzner Helsinki box on 2026-05-11 via
    ops-debug ws-probe (SPOT WS returned aggTrade frames immediately;
    Futures WS timed out at 10s with no message). REST endpoints
    (fapi.binance.com) are unaffected — only the live-stream path.

    The shadow worker uses the kline price action to evaluate paper
    trade entries; SPOT and Futures prices for liquid pairs are
    arbitrage-locked within basis points, so SPOT is fine for this use.
    Funding rate / open interest still come from REST polls in the
    intermarket_snapshot_task.
    """
    if not symbols:
        raise ValueError("symbols must be non-empty")
    truncated = symbols[:max_streams]
    if len(symbols) > max_streams:
        # 2026-08-30 operator ruling on the cohort-tag-defect thread:
        # NEVER truncate silently. Before this, symbols past index
        # max_streams (200) were dropped from the WS subscription with
        # zero signal anywhere -- and since callers pass an
        # ALPHABETICALLY SORTED list (see worker.py's
        # _compute_subscription_set), the drop is not random: it
        # systematically favors tickers early in the alphabet over
        # ones late in it (e.g. XRPUSDT/ZECUSDT/TRXUSDT), regardless of
        # liquidity, cohort, or trading merit. A loud log line here is
        # the minimum bar; callers with async context (worker.py's
        # _build_default_worker) additionally route this through
        # app.ops.alert_routing.alert_admin at "critical" so it pages
        # instead of waiting to be noticed in a log grep.
        log.error(
            "multi_stream: %d symbols requested but max_streams=%d -- "
            "TRUNCATING %d symbol(s), WS coverage lost for: %s",
            len(symbols), max_streams, len(symbols) - max_streams,
            symbols[max_streams:],
        )
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
        base_url: str = "wss://stream.binance.com:9443",
        max_streams: int = 200,
        _connect: Callable[[str], AsyncIterator[str]] | None = None,
    ) -> None:
        self.symbols = symbols
        self.timeframe = timeframe
        self.url = build_combined_stream_url(
            symbols=symbols, timeframe=timeframe, base=base_url, max_streams=max_streams,
        )
        # Exposed so an async caller (worker.py's _build_default_worker)
        # can route this through alert_admin -- build_combined_stream_url
        # itself stays sync and only logs (see its own comment for why).
        self.truncated_symbols: list[str] = symbols[max_streams:] if len(symbols) > max_streams else []
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
        # Visibility: production was running silent for hours with zero
        # shadow trades because every disconnect/error here was swallowed.
        # We now log connect, the first few raw frames + candles per
        # session, and any exception with its type so we can tell:
        #   - clean disconnect (stream ended) from auth/network/parse failure
        #   - successful subscription (raw frames flowing) from a rejected
        #     combined-stream subscription (silence after handshake).
        # The post-PR-95 prod run uncovered the second mode: WS handshake
        # OK, "connecting" logged, then literal silence for 20+ minutes
        # because one or more invalid symbols in the combined sub left the
        # whole subscription rejected by Binance.
        log.info(
            "MultiStreamReader: connecting (symbols=%d timeframe=%s url=%s)",
            len(self.symbols), self.timeframe, self.url[:200],
        )
        while True:
            candles_seen = 0
            frames_seen = 0
            try:
                async for raw in connect(self.url):
                    backoff = 1.0
                    frames_seen += 1
                    if frames_seen <= 3:
                        log.info(
                            "MultiStreamReader: raw frame #%d (len=%d) %.200s",
                            frames_seen, len(raw), raw,
                        )
                    payload = json.loads(raw)
                    data = payload.get("data") if isinstance(payload, dict) else None
                    if not data:
                        # Binance returns {"error": {...}} or {"result": null,
                        # "id": N} on subscription confirmations / rejections.
                        # Surface those once so we know *why* no klines flow.
                        if isinstance(payload, dict) and (
                            "error" in payload or "result" in payload
                        ):
                            log.warning(
                                "MultiStreamReader: control frame: %.300s", raw,
                            )
                        continue
                    kline = data.get("k") if isinstance(data, dict) else None
                    if not kline or not kline.get("x"):
                        continue
                    candle = MultiStreamCandle(
                        symbol=kline["s"],
                        timeframe=kline["i"],
                        ts=datetime.fromtimestamp(kline["t"] / 1000, tz=timezone.utc),
                        open=float(kline["o"]),
                        high=float(kline["h"]),
                        low=float(kline["l"]),
                        close=float(kline["c"]),
                        volume=float(kline["v"]),
                    )
                    candles_seen += 1
                    if candles_seen <= 3:
                        log.info(
                            "MultiStreamReader: closed candle #%d %s ts=%s close=%s",
                            candles_seen, candle.symbol, candle.ts, candle.close,
                        )
                    yield candle
                log.warning(
                    "MultiStreamReader: stream ended cleanly after %d frames "
                    "/ %d candles; reconnecting",
                    frames_seen, candles_seen,
                )
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "MultiStreamReader: %s after %d frames / %d candles; "
                    "backing off %.1fs",
                    type(e).__name__, frames_seen, candles_seen,
                    min(30.0, backoff), exc_info=True,
                )
                await asyncio.sleep(min(30.0, backoff))
                backoff = min(30.0, backoff * 2)
