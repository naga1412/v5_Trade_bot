import asyncio
import sys

sys.path.insert(0, "/app")

import httpx
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# ============================================================================
# Follow-up to gate-counterfactual: item 1(a)'s "regime_only" result showed
# ZERO 'blocked_wrong_regime' rejections out of 718 (all blocks were
# below_long_threshold instead). Reading the real gate code
# (app/core/gates/entry_quality.py) confirms this is only possible if,
# across the whole 30d window, no row had (direction=LONG, regime=bear) or
# (direction=SHORT, regime=bull) -- i.e. regime never actually opposed the
# signal direction in this specific window, OR the population skews so
# heavily toward one direction that the opposing case never arose. This
# script settles which, directly, rather than inferring from the wider
# 798-day distinct-values list (which only proves regime CAN vary, not that
# it did during the live-dispatch window that matters for 1a).
# ============================================================================

BINANCE_SPOT = "https://api.binance.com/api/v3/klines"


async def fetch_klines_df(client, symbol, interval, limit):
    params = {"symbol": symbol, "interval": interval, "limit": str(limit)}
    resp = await client.get(BINANCE_SPOT, params=params, timeout=15.0)
    resp.raise_for_status()
    rows = resp.json()
    return pd.DataFrame({
        "ts": [int(r[0]) for r in rows],
        "open": [float(r[1]) for r in rows],
        "high": [float(r[2]) for r in rows],
        "low": [float(r[3]) for r in rows],
        "close": [float(r[4]) for r in rows],
    })


async def main():
    from app.core.regime.market_regime import compute_market_regime, EMA_SLOW

    db_url = __import__("os").environ["DATABASE_URL"]
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        rows = (await session.execute(sa.text(
            """
            SELECT direction, COUNT(*) AS n
            FROM predictions
            WHERE symbol = 'BTC/USDT' AND timeframe = '1h'
              AND ts >= now() - interval '30 days'
            GROUP BY direction
            ORDER BY direction
            """
        ))).all()
    print("=== direction mix, BTC/USDT 1h predictions, 30d ===", flush=True)
    for r in rows:
        print(f"  {r.direction}: {r.n}", flush=True)

    print("\n=== regime, real compute_market_regime(), walk-forward, last ~30 calendar days ===", flush=True)

    async with httpx.AsyncClient() as client:
        hist = await fetch_klines_df(client, "BTCUSDT", "1d", 1000)
    print(f"  fetched {len(hist)} daily BTC bars (need {EMA_SLOW+2} min history per point)", flush=True)

    results = []
    for i in range(len(hist) - 31, len(hist)):
        if i < EMA_SLOW + 2:
            continue
        window = hist.iloc[: i + 1]
        regime = compute_market_regime(window)
        day = pd.Timestamp(int(hist.iloc[i]["ts"]), unit="ms", tz="UTC").date()
        results.append((day, regime))

    for day, regime in results:
        print(f"  {day}  regime={regime}", flush=True)

    distinct = sorted(set(r for _, r in results))
    print(f"\n  distinct values over this exact window: {distinct}", flush=True)


asyncio.run(main())
