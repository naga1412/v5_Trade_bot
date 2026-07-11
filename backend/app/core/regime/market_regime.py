"""BTC market-regime classifier (bull / bear / neutral).

Used by the entry-quality gate (CHANGE 1 of PR-BOT-INTELLIGENCE-UPGRADE) to
block LONG signals during a confirmed bear regime (and SHORT during bull) —
unless Layer 2 emits a strong contrarian pattern that overrides.

Definitions (spec §1):
  Bull:    price > EMA21 > EMA55 > EMA200  AND  ADX(14) > 25  AND  DI+ > DI-
           AND  RSI(14) > 50
  Bear:    price < EMA21 < EMA55 < EMA200  AND  ADX(14) > 25  AND  DI- > DI+
           AND  RSI(14) < 50
  Neutral: anything else (chop, mixed signals, low trend strength)

Cache: 24-hour TTL. BTC daily-candle regime only changes on the daily close,
so a single refresh per UTC day is sufficient. Module-level dict — no Redis.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal

import httpx
import numpy as np
import pandas as pd
import talib

log = logging.getLogger(__name__)


Regime = Literal["bull", "bear", "neutral"]

EMA_FAST: int = 21
EMA_MID: int = 55
EMA_SLOW: int = 200
ADX_PERIOD: int = 14
RSI_PERIOD: int = 14
ADX_TREND_FLOOR: float = 25.0
RSI_BULL_FLOOR: float = 50.0
RSI_BEAR_CEILING: float = 50.0

CACHE_TTL_SECONDS: int = 24 * 3600
BINANCE_SPOT_KLINES = "https://api.binance.com/api/v3/klines"
BTC_SYMBOL: str = "BTCUSDT"
DAILY_INTERVAL: str = "1d"
KLINE_LIMIT: int = 250  # 250 daily bars covers EMA200 warm-up with margin
FETCH_TIMEOUT_S: float = 4.0


def compute_market_regime(daily_ohlcv: pd.DataFrame) -> Regime:
    """Classify the current regime from a daily-OHLCV DataFrame.

    Args:
        daily_ohlcv: DataFrame with columns ``open``, ``high``, ``low``, ``close``
            (volume optional, ignored). Must contain at least ``EMA_SLOW + 2``
            rows for EMA200 to warm up; shorter inputs return ``"neutral"``.

    Returns:
        ``"bull"``, ``"bear"``, or ``"neutral"``. Never raises on insufficient
        data — falls open to ``"neutral"`` so callers can treat ``neutral`` as
        "no opinion, don't block".
    """
    if len(daily_ohlcv) < EMA_SLOW + 2:
        return "neutral"

    close = daily_ohlcv["close"].to_numpy(dtype=np.float64)
    high = daily_ohlcv["high"].to_numpy(dtype=np.float64)
    low = daily_ohlcv["low"].to_numpy(dtype=np.float64)

    ema_fast = talib.EMA(close, timeperiod=EMA_FAST)
    ema_mid = talib.EMA(close, timeperiod=EMA_MID)
    ema_slow = talib.EMA(close, timeperiod=EMA_SLOW)
    adx = talib.ADX(high, low, close, timeperiod=ADX_PERIOD)
    plus_di = talib.PLUS_DI(high, low, close, timeperiod=ADX_PERIOD)
    minus_di = talib.MINUS_DI(high, low, close, timeperiod=ADX_PERIOD)
    rsi = talib.RSI(close, timeperiod=RSI_PERIOD)

    price_now = float(close[-1])
    ema_fast_now = float(ema_fast[-1])
    ema_mid_now = float(ema_mid[-1])
    ema_slow_now = float(ema_slow[-1])
    adx_now = float(adx[-1])
    plus_di_now = float(plus_di[-1])
    minus_di_now = float(minus_di[-1])
    rsi_now = float(rsi[-1])

    if any(
        not np.isfinite(v)
        for v in (
            ema_fast_now, ema_mid_now, ema_slow_now,
            adx_now, plus_di_now, minus_di_now, rsi_now,
        )
    ):
        return "neutral"

    if adx_now <= ADX_TREND_FLOOR:
        return "neutral"

    if (
        price_now > ema_fast_now > ema_mid_now > ema_slow_now
        and plus_di_now > minus_di_now
        and rsi_now > RSI_BULL_FLOOR
    ):
        return "bull"

    if (
        price_now < ema_fast_now < ema_mid_now < ema_slow_now
        and minus_di_now > plus_di_now
        and rsi_now < RSI_BEAR_CEILING
    ):
        return "bear"

    return "neutral"


# --- 24h cache for the async fetch path ---------------------------------

_cached_regime: Regime | None = None
_cached_at: float = 0.0


def reset_cache_for_tests() -> None:
    """Test helper. Production never calls this."""
    global _cached_regime, _cached_at
    _cached_regime = None
    _cached_at = 0.0


async def _fetch_btc_daily_ohlcv(
    http: httpx.AsyncClient | None = None,
) -> pd.DataFrame | None:
    """Fetch the last ~250 BTCUSDT daily candles from Binance SPOT REST.

    Returns None on any failure (network, parse, HTTP) — callers fall open to
    ``neutral`` regime.
    """
    own_http = http is None
    client = http or httpx.AsyncClient(timeout=FETCH_TIMEOUT_S)
    try:
        resp = await client.get(
            BINANCE_SPOT_KLINES,
            params={
                "symbol": BTC_SYMBOL,
                "interval": DAILY_INTERVAL,
                "limit": KLINE_LIMIT,
            },
            timeout=FETCH_TIMEOUT_S,
        )
        resp.raise_for_status()
        raw = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        log.warning("market_regime fetch failed: %s", exc)
        return None
    finally:
        if own_http:
            await client.aclose()

    if not isinstance(raw, list) or len(raw) == 0:
        return None

    # Binance kline row: [open_time, o, h, l, c, vol, close_time, ...]
    df = pd.DataFrame(
        [
            {
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
            }
            for r in raw
        ]
    )
    return df


async def get_cached_market_regime(
    *,
    now: float | None = None,
    _http: httpx.AsyncClient | None = None,
) -> Regime | None:
    """Return the cached regime, refreshing if older than ``CACHE_TTL_SECONDS``.

    Returns ``None`` only when the cache is cold AND the fetch failed — callers
    treat that as "no opinion, do not block".

    Concurrency: a single dispatcher process calls this; no inter-process
    coordination. If two coroutines race the fetch, both refresh; the second
    just overwrites with the same value.
    """
    global _cached_regime, _cached_at
    n = now if now is not None else time.time()
    if _cached_regime is not None and (n - _cached_at) < CACHE_TTL_SECONDS:
        return _cached_regime

    df = await _fetch_btc_daily_ohlcv(http=_http)
    if df is None:
        return _cached_regime  # may be None if cold; honest failure mode

    regime = compute_market_regime(df)
    _cached_regime = regime
    _cached_at = n
    return regime


__all__ = [
    "ADX_PERIOD",
    "ADX_TREND_FLOOR",
    "BTC_SYMBOL",
    "CACHE_TTL_SECONDS",
    "DAILY_INTERVAL",
    "EMA_FAST",
    "EMA_MID",
    "EMA_SLOW",
    "RSI_PERIOD",
    "Regime",
    "compute_market_regime",
    "get_cached_market_regime",
    "reset_cache_for_tests",
]


# Avoid F401 for asyncio (re-exported only inside async helpers)
_ = asyncio
