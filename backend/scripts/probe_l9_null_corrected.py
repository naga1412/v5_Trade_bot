import asyncio
import sys

sys.path.insert(0, "/app")

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# ============================================================================
# Correction to l9-news-diagnosis: `layer_scores -> '9' IS NULL` undercounts
# abstention. Postgres distinguishes SQL NULL from a JSONB value that IS the
# JSON literal `null` -- `'null'::jsonb IS NULL` is FALSE. predictor.py sets
# layer_results[9] = None on abstain, which serializes to a JSON null AT an
# EXISTING key '9', not an absent key -- so the outer `IS NULL` check never
# fires. This checks one level deeper (->'direction', which correctly
# becomes SQL NULL when the parent is a JSON null) split BTC/USDT vs ALL
# symbols, to settle whether system-truth's 99.2% (measured over the wider
# 42-symbol population) reflects BTC/USDT specifically or is diluted by
# obscure altcoins with near-zero base-asset-tagged news coverage.
# ============================================================================


async def main():
    db_url = __import__("os").environ["DATABASE_URL"]
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        print("=== jsonb_typeof(layer_scores->'9') distribution, 7d ===", flush=True)
        rows = (await session.execute(sa.text(
            """
            SELECT jsonb_typeof(layer_scores -> '9') AS typ, COUNT(*) AS n
            FROM predictions
            WHERE ts >= now() - interval '7 days'
            GROUP BY jsonb_typeof(layer_scores -> '9')
            """
        ))).all()
        for r in rows:
            print(f"  typeof={r.typ!r:8} n={r.n}", flush=True)

        print("\n=== CORRECTED L9 abstain rate (direction key null), 7d ===", flush=True)
        for label, where in (
            ("ALL symbols", ""),
            ("BTC/USDT only", "AND symbol = 'BTC/USDT'"),
        ):
            r2 = (await session.execute(sa.text(
                f"""
                SELECT COUNT(*) AS n,
                       COUNT(*) FILTER (WHERE layer_scores -> '9' -> 'direction' IS NULL) AS n_abstain
                FROM predictions
                WHERE ts >= now() - interval '7 days' {where}
                """
            ))).one()
            pct = 100 * r2.n_abstain / r2.n if r2.n else float("nan")
            print(f"  {label:16} n={r2.n:6} abstain={r2.n_abstain:6} ({pct:.1f}%)", flush=True)

        print("\n=== BTC/USDT distinct direction values among non-abstain L9 rows, 7d ===", flush=True)
        r3 = (await session.execute(sa.text(
            """
            SELECT layer_scores -> '9' ->> 'direction' AS direction, COUNT(*) AS n
            FROM predictions
            WHERE ts >= now() - interval '7 days' AND symbol = 'BTC/USDT'
              AND layer_scores -> '9' -> 'direction' IS NOT NULL
            GROUP BY layer_scores -> '9' ->> 'direction'
            """
        ))).all()
        if not r3:
            print("  NONE (L9 never once evaluated non-abstain for BTC/USDT in 7d)", flush=True)
        for r in r3:
            print(f"  direction={r.direction!r}  n={r.n}", flush=True)

        print("\n=== per-symbol L9 abstain rate, top 10 by row count, 7d ===", flush=True)
        r4 = (await session.execute(sa.text(
            """
            SELECT symbol, COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE layer_scores -> '9' -> 'direction' IS NULL) AS n_abstain
            FROM predictions
            WHERE ts >= now() - interval '7 days'
            GROUP BY symbol
            ORDER BY n DESC
            LIMIT 10
            """
        ))).all()
        for r in r4:
            pct = 100 * r.n_abstain / r.n if r.n else float("nan")
            print(f"  {r.symbol:14} n={r.n:5}  abstain={r.n_abstain:5} ({pct:.1f}%)", flush=True)


asyncio.run(main())
