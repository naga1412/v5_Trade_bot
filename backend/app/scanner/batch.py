"""60-second batch scanner worker (Feature 4 — sub-PR 2).

Continuously scans the asset universe and caches the latest result per
symbol. The HTTP endpoint reads from this cache so requests are O(1)
no matter how many symbols are in the universe.

Why not run the scan inside the request? Two reasons:
  1. Even at 5ms per symbol, 200 symbols = 1s of CPU per request. Cache
     once, serve everyone.
  2. Each scan needs ~80 klines per symbol. Binance public API limits
     1200 req/min/IP. A 60s batch with 500ms stagger keeps us well
     inside the budget AND amortizes the cost across all connected
     users.

The cache is module-level (process-local). It's lost on restart but
that's fine — the first batch tick after startup rebuilds it in <2
minutes, and we'd rather rebuild from live data than serve stale data
from disk. Heartbeats go through the standard ``record_heartbeat``
path so the worker watchdog can see this task is alive.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.adapters.binance import BinanceClient
from app.ops.heartbeat import record_heartbeat
from app.scanner.fast_scan import FastScanResult, fast_scan

log = logging.getLogger(__name__)

BATCH_INTERVAL_SECONDS: int = 60
PER_SYMBOL_STAGGER_MS: int = 100  # 100ms between symbol fetches
SCAN_KLINES_LIMIT: int = 80
SCAN_TIMEFRAME: str = "1h"
WORKER_NAME: str = "scanner_batch_task"

# Fallback watchlist when asset_universe is empty.
_DEFAULT_WATCHLIST: tuple[str, ...] = (
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT",
)


@dataclass(frozen=True)
class CachedScan:
    """One row in the in-process cache."""
    result: FastScanResult
    scanned_at: datetime


# Module-level cache. {(symbol, timeframe): CachedScan}. Read by the API
# endpoint; written by the batch worker. The tuple key allows future
# multi-timeframe scans without a schema change.
_CACHE: dict[tuple[str, str], CachedScan] = {}


def get_cache_snapshot() -> dict[tuple[str, str], CachedScan]:
    """Return a shallow copy of the cache. Safe for concurrent reads."""
    return dict(_CACHE)


def cache_size() -> int:
    return len(_CACHE)


def _clear_cache_for_tests() -> None:
    """Test-only: wipe the module-level cache between scenarios."""
    _CACHE.clear()


async def _load_watchlist(session: AsyncSession) -> list[str]:
    """Read the most recent asset_universe snapshot. Fall back to defaults."""
    try:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT symbol FROM asset_universe "
                    "WHERE snapshot_at = (SELECT max(snapshot_at) FROM asset_universe) "
                    "ORDER BY rank ASC LIMIT 200"
                )
            )
        ).all()
        symbols = [str(r.symbol) for r in rows]
    except Exception as e:  # noqa: BLE001
        log.warning("scanner_batch: asset_universe read failed: %s", e)
        symbols = []
    if not symbols:
        return list(_DEFAULT_WATCHLIST)
    return symbols


def _to_pair(asset_universe_symbol: str) -> str:
    """asset_universe stores 'BTCUSDT'; the API wants 'BTC/USDT'.

    Best-effort: split on the canonical USDT/USDC/BUSD suffix. If the
    symbol doesn't match a known suffix we pass it through unchanged.
    """
    s = asset_universe_symbol.upper()
    for suffix in ("USDT", "USDC", "BUSD", "FDUSD", "BTC", "ETH"):
        if s.endswith(suffix) and len(s) > len(suffix):
            return f"{s[:-len(suffix)]}/{suffix}"
    return s


async def _scan_one_symbol(
    http: httpx.AsyncClient, pair: str,
) -> FastScanResult | None:
    """Fetch klines for one symbol and run the fast scan. Best-effort."""
    binance_sym = pair.replace("/", "")
    client = BinanceClient(http=http)
    try:
        bars_raw = await client.fetch_klines(
            binance_sym, SCAN_TIMEFRAME, limit=SCAN_KLINES_LIMIT,
        )
    except (httpx.HTTPStatusError, httpx.HTTPError) as e:
        # Spot symbol not on Binance Spot, rate limit, transient network —
        # all treated the same: skip this tick, keep the prior cached
        # result if any.
        log.debug("scanner_batch: fetch_klines failed for %s: %s", pair, e)
        return None
    if not bars_raw:
        return None
    df = pd.DataFrame([c.__dict__ for c in bars_raw])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts")[["open", "high", "low", "close", "volume"]]
    return fast_scan(df, symbol=pair, timeframe=SCAN_TIMEFRAME)


async def run_one_pass(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    http: httpx.AsyncClient | None = None,
    stagger_ms: int = PER_SYMBOL_STAGGER_MS,
) -> int:
    """One batch tick. Returns the number of symbols successfully scanned.

    Extracted so unit tests can drive it without spinning up the loop.
    """
    close_http = False
    if http is None:
        http = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        close_http = True
    scanned = 0
    try:
        async with session_factory() as session:
            watchlist_raw = await _load_watchlist(session)
        # Normalize 'BTCUSDT' -> 'BTC/USDT' so cache keys match the API.
        pairs = [_to_pair(s) for s in watchlist_raw]
        now = datetime.now(timezone.utc)
        for pair in pairs:
            try:
                result = await _scan_one_symbol(http, pair)
            except Exception as e:  # noqa: BLE001
                log.warning("scanner_batch: scan failed for %s: %s", pair, e)
                continue
            if result is not None:
                _CACHE[(pair, SCAN_TIMEFRAME)] = CachedScan(
                    result=result, scanned_at=now,
                )
                scanned += 1
            if stagger_ms > 0:
                await asyncio.sleep(stagger_ms / 1000.0)
    finally:
        if close_http:
            await http.aclose()
    return scanned


async def _loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    interval_s: float = BATCH_INTERVAL_SECONDS,
) -> None:
    log.info(
        "scanner_batch: starting (interval=%ds, stagger=%dms/symbol)",
        int(interval_s), PER_SYMBOL_STAGGER_MS,
    )
    while True:
        try:
            n = await run_one_pass(session_factory)
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="ok", details={"scanned": n, "cache_size": cache_size()},
            )
            if n > 0:
                log.info("scanner_batch: scanned %d symbols this tick", n)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("scanner_batch tick failed: %s", e)
        await asyncio.sleep(interval_s)


def start_scanner_batch_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    return asyncio.create_task(_loop(session_factory))


__all__ = [
    "BATCH_INTERVAL_SECONDS",
    "CachedScan",
    "SCAN_TIMEFRAME",
    "WORKER_NAME",
    "_clear_cache_for_tests",
    "cache_size",
    "get_cache_snapshot",
    "run_one_pass",
    "start_scanner_batch_task",
]


def _api_dict(_: Any) -> dict[str, Any]:
    """Reserved for the API layer's serializer (overridden by routes.scanner_fast)."""
    return {}
