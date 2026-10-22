import asyncio
import os
import statistics as st
import sys

sys.path.insert(0, "/app")

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

BINANCE_SPOT_KLINES = "https://api.binance.com/api/v3/klines"
PRE_BARS = 96
POST_BARS = 96
FETCH_DELAY_S = 0.08

_TF_TO_INTERVAL = {"1h": ("1h", 3600_000), "15m": ("15m", 900_000)}


async def fetch_bars(client, symbol, interval, start_ms, end_ms, limit):
    out = []
    cursor = start_ms
    while cursor <= end_ms and len(out) < limit:
        resp = await client.get(
            BINANCE_SPOT_KLINES,
            params={"symbol": symbol, "interval": interval, "startTime": cursor,
                    "endTime": end_ms, "limit": 1000},
            timeout=15.0,
        )
        if resp.status_code != 200:
            return out
        rows = resp.json()
        if not rows:
            break
        for r in rows:
            out.append((int(r[0]), float(r[2]), float(r[3])))  # (open_time, high, low)
        if len(rows) < 1000:
            break
        cursor = rows[-1][0] + 1
        await asyncio.sleep(FETCH_DELAY_S)
    return out[:limit]


def pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


async def main():
    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        rows = (await session.execute(sa.text(
            """
            WITH trades AS (
                SELECT
                    s.id, s.symbol, s.timeframe, s.opened_at, s.closed_at,
                    s.entry_price, s.pnl_usdt, s.entry_atr,
                    (SELECT MAX(snapshot_at) FROM asset_universe
                        WHERE snapshot_at <= s.opened_at) AS rank_snap
                FROM shadow_trades s
                WHERE s.closed_at IS NOT NULL
                  AND s.direction = 'LONG'
                  AND s.entry_score >= 0.36
                  AND s.closed_at >= NOW() - INTERVAL '60 days'
                  AND s.entry_price > 0
                  AND s.entry_atr > 0
                  AND s.timeframe IN ('1h', '15m')
            )
            SELECT t.*, au.rank AS rank_at_trade
            FROM trades t
            LEFT JOIN asset_universe au
              ON au.snapshot_at = t.rank_snap AND au.symbol = t.symbol
            ORDER BY t.opened_at
            """
        ))).all()

    from app.config import get_settings
    from app.trading.symbol_allowlist import is_stablecoin_pair
    _settings = get_settings()
    bl = set(_settings.SHADOW_SPOT_BLACKLIST)

    candidates = []
    for r in rows:
        if r.symbol in bl or is_stablecoin_pair(r.symbol, _settings):
            continue
        rank = r.rank_at_trade
        is_top20 = (r.symbol == "BTCUSDT") or (rank is not None and rank <= 20)
        if not is_top20:
            continue
        entry_atr = float(r.entry_atr)
        entry_price = float(r.entry_price)
        atr_class = "cap_bound" if (1.5 * entry_atr) > (0.05 * entry_price) else "atr_bound"
        if atr_class != "atr_bound":
            continue
        candidates.append(r)

    print(f"population after top20+atr_bound+blacklist filter: n={len(candidates)}", flush=True)

    results = []  # (tf, captured_fraction, bars_low_to_entry, is_winner)
    skipped = 0

    async with httpx.AsyncClient() as client:
        for r in candidates:
            tf = r.timeframe
            if tf not in _TF_TO_INTERVAL:
                skipped += 1
                continue
            interval, bar_ms = _TF_TO_INTERVAL[tf]
            entry_ms = int(r.opened_at.timestamp() * 1000)
            pre_start = entry_ms - PRE_BARS * bar_ms
            post_end = entry_ms + POST_BARS * bar_ms

            pre = await fetch_bars(client, r.symbol, interval, pre_start, entry_ms, PRE_BARS + 1)
            post = await fetch_bars(client, r.symbol, interval, entry_ms, post_end, POST_BARS + 1)
            await asyncio.sleep(FETCH_DELAY_S)
            if len(pre) < 5 or len(post) < 5:
                skipped += 1
                continue

            low_idx = min(range(len(pre)), key=lambda i: pre[i][2])
            pre_move_low = pre[low_idx][2]
            bars_low_to_entry = len(pre) - 1 - low_idx

            entry_price = float(r.entry_price)
            peak = max(h for _, h, _ in post)

            denom = peak - pre_move_low
            if denom <= 0:
                skipped += 1
                continue
            captured_fraction = (peak - entry_price) / denom

            is_winner = (r.pnl_usdt or 0) > 0
            results.append((tf, captured_fraction, bars_low_to_entry, is_winner))

    n = len(results)
    print(f"usable trades: {n}  (skipped: {skipped})", flush=True)
    if not n:
        return

    cf = [x[1] for x in results]
    bl_bars = [x[2] for x in results]
    print(
        f"captured_fraction: p10={pct(cf,.10):+.3f} p25={pct(cf,.25):+.3f} "
        f"median={pct(cf,.50):+.3f} p75={pct(cf,.75):+.3f} p90={pct(cf,.90):+.3f} "
        f"mean={st.mean(cf):+.3f}", flush=True,
    )
    buckets = {"<=0.0": 0, "0.0-0.2": 0, "0.2-0.5": 0, "0.5-0.8": 0, "0.8-1.0": 0, ">1.0 or <-": 0}
    for v in cf:
        if v <= 0.0:
            buckets["<=0.0"] += 1
        elif v <= 0.2:
            buckets["0.0-0.2"] += 1
        elif v <= 0.5:
            buckets["0.2-0.5"] += 1
        elif v <= 0.8:
            buckets["0.5-0.8"] += 1
        elif v <= 1.0:
            buckets["0.8-1.0"] += 1
        else:
            buckets[">1.0 or <-"] += 1
    print(f"buckets (% of n={n}): " + ", ".join(f"{k}={100*v/n:.1f}%" for k, v in buckets.items()), flush=True)
    print(
        f"bars low->entry: p25={pct(bl_bars,.25):.0f} median={pct(bl_bars,.50):.0f} "
        f"p75={pct(bl_bars,.75):.0f} max={max(bl_bars):.0f}  "
        f"pct_20plus_late={100*sum(1 for b in bl_bars if b>=20)/n:.1f}%", flush=True,
    )
    for tf in ("1h", "15m"):
        sub = [x[1] for x in results if x[0] == tf]
        if sub:
            print(f"  TF={tf}: n={len(sub)} median_captured={pct(sub,.50):+.3f}", flush=True)

    winners = [x[1] for x in results if x[3]]
    losers = [x[1] for x in results if not x[3]]
    print(
        f"winners vs losers: winners n={len(winners)} median_captured="
        f"{pct(winners,.50) if winners else float('nan'):+.3f}  "
        f"losers n={len(losers)} median_captured="
        f"{pct(losers,.50) if losers else float('nan'):+.3f}", flush=True,
    )


asyncio.run(main())
