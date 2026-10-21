import asyncio
import json
import os
import statistics as st
import sys

sys.path.insert(0, "/app")

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import sqlalchemy as sa


SAMPLE_N = 100
PRE_WINDOW_H = 72   # hours of 1h klines fetched BEFORE entry, to find local low
POST_WINDOW_H = 72  # hours of 1h klines fetched AFTER entry, to find eventual peak


async def fetch_klines(client, symbol, start_ms, end_ms):
    resp = await client.get(
        "https://api.binance.com/api/v3/klines",
        params={
            "symbol": symbol, "interval": "1h",
            "startTime": start_ms, "endTime": end_ms, "limit": 1000,
        },
        timeout=10.0,
    )
    if resp.status_code != 200:
        return None
    rows = resp.json()
    return [(int(r[0]), float(r[4])) for r in rows]  # (open_time_ms, close)


async def main():
    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        rows = (await session.execute(sa.text(
            """
            SELECT symbol, opened_at, entry_price, pnl_pct
            FROM shadow_trades
            WHERE direction = 'LONG' AND closed_at IS NOT NULL AND pnl_usdt > 0
            ORDER BY opened_at DESC
            LIMIT :n
            """
        ), {"n": SAMPLE_N})).all()

    print(f"sampled {len(rows)} winning LONG trades (most recent)", flush=True)

    bars_late_list = []
    capture_pct_list = []
    skipped = 0

    async with httpx.AsyncClient() as client:
        for r in rows:
            symbol_native = r.symbol.replace("/", "")
            entry_ms = int(r.opened_at.timestamp() * 1000)
            pre_start = entry_ms - PRE_WINDOW_H * 3600 * 1000
            post_end = entry_ms + POST_WINDOW_H * 3600 * 1000

            pre = await fetch_klines(client, symbol_native, pre_start, entry_ms)
            post = await fetch_klines(client, symbol_native, entry_ms, post_end)
            if not pre or not post or len(pre) < 5 or len(post) < 5:
                skipped += 1
                continue

            # Local low in the pre-entry window.
            low_idx = min(range(len(pre)), key=lambda i: pre[i][1])
            low_close = pre[low_idx][1]
            bars_late = len(pre) - 1 - low_idx  # bars between local low and entry candle

            entry_close = pre[-1][1]  # last pre-entry closed candle ~ entry reference
            peak_close = max(c for _, c in post)

            move_total = peak_close - low_close
            move_captured = peak_close - entry_close
            if move_total <= 0:
                skipped += 1
                continue
            capture_pct = move_captured / move_total

            bars_late_list.append(bars_late)
            capture_pct_list.append(capture_pct)

    n = len(bars_late_list)
    print(f"usable trades: {n}  (skipped: {skipped})", flush=True)
    if n:
        print(f"bars_late (local-low -> entry): median={st.median(bars_late_list):.1f} "
              f"mean={st.mean(bars_late_list):.2f}", flush=True)
        print(f"capture_pct (of low->peak move captured from entry): "
              f"median={st.median(capture_pct_list)*100:.1f}% "
              f"mean={st.mean(capture_pct_list)*100:.1f}%", flush=True)
        # Distribution buckets for bars_late.
        buckets = {"0-5": 0, "6-20": 0, "21-50": 0, "51-72": 0}
        for b in bars_late_list:
            if b <= 5:
                buckets["0-5"] += 1
            elif b <= 20:
                buckets["6-20"] += 1
            elif b <= 50:
                buckets["21-50"] += 1
            else:
                buckets["51-72"] += 1
        print(f"bars_late distribution: {buckets}", flush=True)


asyncio.run(main())
