import asyncio
import sys

sys.path.insert(0, "/app")

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# ============================================================================
# Item 2, second half: predictions.effective_score/realized_vol_20d showed
# null%24h=0 but null%7d=83.8% in the system-truth run (2026-08-06T11:36Z).
# Two competing hypotheses:
#   (a) deploy-boundary: a fix landed inside the 7d window; all rows before
#       a certain timestamp are null, all after are non-null. Would confirm
#       "freshly-shipped-column-looks-broken-in-naive-window", not a live bug.
#   (b) symbol-scope: the wider multi-symbol predictions mechanism (42
#       distinct symbols/7d, confirmed via system-truth) never gets these
#       columns populated regardless of when -- only BTC/USDT (single-worker
#       path, PR #400's actual scope) does. Would mean the null rate is a
#       standing structural gap, not a transient window artifact.
# This settles which by checking both dimensions directly.
# ============================================================================


async def main():
    db_url = __import__("os").environ["DATABASE_URL"]
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        print("=== (a) DEPLOY-BOUNDARY CHECK: null rate by day, last 10 days, BTC/USDT only ===", flush=True)
        rows = (await session.execute(sa.text(
            """
            SELECT DATE(ts) AS day, COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE effective_score IS NOT NULL) AS n_populated,
                   MIN(ts) FILTER (WHERE effective_score IS NOT NULL) AS first_populated_ts
            FROM predictions
            WHERE symbol = 'BTC/USDT' AND timeframe = '1h'
              AND ts >= now() - interval '10 days'
            GROUP BY DATE(ts)
            ORDER BY day
            """
        ))).all()
        for r in rows:
            pct = 100 * r.n_populated / r.n if r.n else float("nan")
            print(f"  {r.day}  n={r.n:4}  populated={r.n_populated:4} ({pct:5.1f}%)  "
                  f"first_populated_ts={r.first_populated_ts}", flush=True)

        print("\n=== (a) same, but ALL symbols (not just BTC/USDT) ===", flush=True)
        rows2 = (await session.execute(sa.text(
            """
            SELECT DATE(ts) AS day, COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE effective_score IS NOT NULL) AS n_populated
            FROM predictions
            WHERE ts >= now() - interval '10 days'
            GROUP BY DATE(ts)
            ORDER BY day
            """
        ))).all()
        for r in rows2:
            pct = 100 * r.n_populated / r.n if r.n else float("nan")
            print(f"  {r.day}  n={r.n:5}  populated={r.n_populated:5} ({pct:5.1f}%)", flush=True)

        print("\n=== (b) SYMBOL-SCOPE CHECK: null rate by symbol, last 7d, top 15 by volume ===", flush=True)
        rows3 = (await session.execute(sa.text(
            """
            SELECT symbol, COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE effective_score IS NOT NULL) AS n_populated
            FROM predictions
            WHERE ts >= now() - interval '7 days'
            GROUP BY symbol
            ORDER BY n DESC
            LIMIT 15
            """
        ))).all()
        for r in rows3:
            pct = 100 * r.n_populated / r.n if r.n else float("nan")
            print(f"  {r.symbol:14} n={r.n:5}  populated={r.n_populated:5} ({pct:5.1f}%)", flush=True)

        print("\n=== timeframe breakdown for non-BTC/USDT rows (is the wider mechanism 1h too?) ===", flush=True)
        rows4 = (await session.execute(sa.text(
            """
            SELECT timeframe, COUNT(*) AS n, COUNT(DISTINCT symbol) AS n_symbols
            FROM predictions
            WHERE ts >= now() - interval '7 days' AND symbol != 'BTC/USDT'
            GROUP BY timeframe
            ORDER BY n DESC
            """
        ))).all()
        for r in rows4:
            print(f"  timeframe={r.timeframe!r:8} n={r.n:5} distinct_symbols={r.n_symbols}", flush=True)

        print("\n=== cold_start value distribution, 7d (was CONSTANT per system-truth -- what value?) ===", flush=True)
        rows5 = (await session.execute(sa.text(
            """
            SELECT cold_start, COUNT(*) AS n
            FROM predictions
            WHERE ts >= now() - interval '7 days'
            GROUP BY cold_start
            """
        ))).all()
        for r in rows5:
            print(f"  cold_start={r.cold_start!r}  n={r.n}", flush=True)


asyncio.run(main())
