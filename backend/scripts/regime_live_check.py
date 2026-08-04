"""Live diagnostic for the BUG 1 regime investigation (2026-08-05).

READ-ONLY. Isolates whether `get_cached_market_regime()` is stuck because
the fetch is failing, the cache never persists a fresh value, or something
else — the DB alone can't tell (both "genuine neutral" and "fetch failed,
fell through to None" map to the same stored "sideways_grind" string, see
`app/shadow/observation.py::REGIME_MAPPING`).

Reports, in order:
  1. The module-level cache state (`_cached_regime`, age) exactly as this
     process holds it right now.
  2. A FRESH fetch attempt (`_fetch_btc_daily_ohlcv`), reporting success/
     failure explicitly rather than swallowing it.
  3. If the fetch succeeded: the freshly computed regime plus the raw
     indicator values (ADX14, RSI14, EMA21/55/200, DI+/DI-) that fed the
     classification, so a human can sanity-check it against price action.
  4. `REGIME_GATE_ENABLED` / `ADX_GATE_ENABLED` current settings values —
     answers whether the dispatch-path gate (entry_quality.py:153) is even
     opted into currently, independent of whether the classifier itself
     is broken.

Usage (inside backend container via ops-debug probe):
    docker compose exec -T backend python /app/scripts/regime_live_check.py

Read-only guarantees:
    - No writes to postgres
    - The only network call is the SAME BTCUSDT daily-klines GET the
      production code path already makes
    - No mutation of any in-memory state beyond what a normal
      `get_cached_market_regime()` call already does (this call IS what
      the live predictor path does on every prediction)
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.core.regime.market_regime as regime_mod
from app.config import get_settings


async def main() -> None:
    print("===== Regime live-process diagnostic (BUG 1) =====")

    print("\n--- 1. Cache state (this process, right now) ---")
    cached_before = regime_mod._cached_regime
    cached_at_before = regime_mod._cached_at
    age = (time.time() - cached_at_before) if cached_at_before else None
    print(f"  _cached_regime = {cached_before!r}")
    print(f"  _cached_at     = {cached_at_before!r}")
    print(f"  age_seconds    = {age!r}")
    print(f"  CACHE_TTL_SECONDS = {regime_mod.CACHE_TTL_SECONDS}")

    print("\n--- 2. Fresh fetch attempt (_fetch_btc_daily_ohlcv) ---")
    t0 = time.time()
    try:
        df = await regime_mod._fetch_btc_daily_ohlcv()
    except Exception as e:  # noqa: BLE001 — diagnostic, want to SEE this
        print(f"  UNCAUGHT EXCEPTION escaped _fetch_btc_daily_ohlcv: "
              f"{type(e).__name__}: {e}")
        df = None
    elapsed = time.time() - t0
    print(f"  elapsed = {elapsed:.2f}s")
    print(f"  result  = {'DataFrame with ' + str(len(df)) + ' rows' if df is not None else 'None (fetch failed)'}")

    if df is not None:
        print("\n--- 3. Freshly computed regime + raw indicators ---")
        import numpy as np
        import talib

        close = df["close"].to_numpy(dtype=np.float64)
        high = df["high"].to_numpy(dtype=np.float64)
        low = df["low"].to_numpy(dtype=np.float64)
        ema_fast = talib.EMA(close, timeperiod=regime_mod.EMA_FAST)
        ema_mid = talib.EMA(close, timeperiod=regime_mod.EMA_MID)
        ema_slow = talib.EMA(close, timeperiod=regime_mod.EMA_SLOW)
        adx = talib.ADX(high, low, close, timeperiod=regime_mod.ADX_PERIOD)
        plus_di = talib.PLUS_DI(high, low, close, timeperiod=regime_mod.ADX_PERIOD)
        minus_di = talib.MINUS_DI(high, low, close, timeperiod=regime_mod.ADX_PERIOD)
        rsi = talib.RSI(close, timeperiod=regime_mod.RSI_PERIOD)

        print(f"  price_now      = {close[-1]:.2f}")
        print(f"  ema21/55/200   = {ema_fast[-1]:.2f} / {ema_mid[-1]:.2f} / {ema_slow[-1]:.2f}")
        print(f"  adx14          = {adx[-1]:.2f}  (trend floor = {regime_mod.ADX_TREND_FLOOR})")
        print(f"  plus_di/minus_di = {plus_di[-1]:.2f} / {minus_di[-1]:.2f}")
        print(f"  rsi14          = {rsi[-1]:.2f}")

        fresh_regime = regime_mod.compute_market_regime(df)
        print(f"  compute_market_regime(df) = {fresh_regime!r}")

    print("\n--- 4. get_cached_market_regime() (production call, mutates cache) ---")
    result = await regime_mod.get_cached_market_regime()
    print(f"  result = {result!r}")
    print(f"  _cached_regime (after) = {regime_mod._cached_regime!r}")
    print(f"  _cached_at (after)     = {regime_mod._cached_at!r}")

    print("\n--- 5. Gate flag values ---")
    s = get_settings()
    print(f"  REGIME_GATE_ENABLED = {getattr(s, 'REGIME_GATE_ENABLED', 'NOT_SET')}")
    print(f"  ADX_GATE_ENABLED = {getattr(s, 'ADX_GATE_ENABLED', 'NOT_SET')}")
    print(f"  MIN_ADX_TREND_STRENGTH = {getattr(s, 'MIN_ADX_TREND_STRENGTH', 'NOT_SET')}")
    print(f"  PATTERN_BOOST_ENABLED = {getattr(s, 'PATTERN_BOOST_ENABLED', 'NOT_SET')}")


if __name__ == "__main__":
    asyncio.run(main())
