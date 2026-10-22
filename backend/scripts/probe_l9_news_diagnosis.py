import asyncio
import sys

sys.path.insert(0, "/app")

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# ============================================================================
# Item 3: L9 news 99.2% abstain diagnosis. layer9_news.score() abstains
# (returns None) when either (a) session is None (caller didn't pass one),
# or (b) zero news_items rows match `published_at >= now-60min AND base =
# ANY(affected_assets) AND sentiment_score IS NOT NULL`. Separately,
# ingest_worker._build_adapters() makes CryptoPanic OPTIONAL: if
# CRYPTOPANIC_API_KEY isn't set, only Yahoo macro RSS runs (30min cadence,
# not asset-tagged the same way) -- a real prior incident (see the comment
# in _build_adapters, post-PR-#97) confirms prod HAS shipped with this key
# unset before. This checks all of it directly against real prod state
# rather than re-guessing.
# ============================================================================


async def main():
    from app.config import get_settings

    settings = get_settings()
    key_set = bool(settings.cryptopanic_api_key)
    print(f"=== CRYPTOPANIC_API_KEY set in prod: {key_set} ===", flush=True)

    db_url = __import__("os").environ["DATABASE_URL"]
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        print("\n=== news_items: totals + freshness by source ===", flush=True)
        rows = (await session.execute(sa.text(
            """
            SELECT source, COUNT(*) AS n,
                   MAX(published_at) AS most_recent,
                   COUNT(*) FILTER (WHERE published_at >= now() - interval '24 hours') AS n_24h,
                   COUNT(*) FILTER (WHERE published_at >= now() - interval '7 days') AS n_7d
            FROM news_items
            GROUP BY source
            ORDER BY n DESC
            """
        ))).all()
        if not rows:
            print("  ZERO rows in news_items at all", flush=True)
        for r in rows:
            print(f"  source={r.source!r:20} total={r.n:6} last_24h={r.n_24h:4} "
                  f"last_7d={r.n_7d:5} most_recent={r.most_recent}", flush=True)

        print("\n=== news_items: BTC-tagged rows (affected_assets @> ARRAY['BTC']) ===", flush=True)
        r = (await session.execute(sa.text(
            """
            SELECT COUNT(*) AS n,
                   MAX(published_at) AS most_recent,
                   COUNT(*) FILTER (WHERE published_at >= now() - interval '24 hours') AS n_24h,
                   COUNT(*) FILTER (WHERE published_at >= now() - interval '7 days') AS n_7d,
                   COUNT(*) FILTER (WHERE sentiment_score IS NOT NULL) AS n_scored
            FROM news_items
            WHERE 'BTC' = ANY(affected_assets)
            """
        ))).one()
        print(f"  total={r.n} last_24h={r.n_24h} last_7d={r.n_7d} "
              f"scored={r.n_scored} most_recent={r.most_recent}", flush=True)

        print("\n=== rolling 60-min-window coverage check: how many of the last 7 daily", flush=True)
        print("    UTC-midnight-anchored 60min windows had >=1 qualifying BTC row? ===", flush=True)
        r2 = (await session.execute(sa.text(
            """
            WITH windows AS (
              SELECT generate_series(
                now() - interval '7 days', now(), interval '1 hour'
              ) AS win_start
            )
            SELECT COUNT(*) AS total_windows,
                   COUNT(*) FILTER (WHERE EXISTS (
                     SELECT 1 FROM news_items ni
                     WHERE ni.published_at >= windows.win_start
                       AND ni.published_at < windows.win_start + interval '1 hour'
                       AND 'BTC' = ANY(ni.affected_assets)
                       AND ni.sentiment_score IS NOT NULL
                   )) AS windows_with_coverage
            FROM windows
            """
        ))).one()
        pct = 100 * r2.windows_with_coverage / r2.total_windows if r2.total_windows else float("nan")
        print(f"  {r2.windows_with_coverage}/{r2.total_windows} hourly windows had >=1 qualifying "
              f"BTC row ({pct:.1f}%)", flush=True)

        print("\n=== predictions.layer_scores->'9' null-rate, last 30d ===", flush=True)
        for label, where in (
            ("ALL symbols", ""),
            ("BTC/USDT only", "AND symbol = 'BTC/USDT'"),
        ):
            r3 = (await session.execute(sa.text(
                f"""
                SELECT COUNT(*) AS n,
                       COUNT(*) FILTER (WHERE layer_scores -> '9' IS NULL) AS n_null,
                       COUNT(*) FILTER (WHERE layer_scores ? '9' = false) AS n_key_absent
                FROM predictions
                WHERE ts >= now() - interval '30 days' {where}
                """
            ))).one()
            pct_null = 100 * r3.n_null / r3.n if r3.n else float("nan")
            print(f"  {label:16} n={r3.n:6} l9_null={r3.n_null:6} ({pct_null:.1f}%) "
                  f"key_absent={r3.n_key_absent}", flush=True)

        print("\n=== sample of 5 non-null layer_scores->'9' values (any symbol, all-time) ===", flush=True)
        r4 = (await session.execute(sa.text(
            """
            SELECT symbol, ts, layer_scores -> '9' AS l9
            FROM predictions
            WHERE layer_scores -> '9' IS NOT NULL
            ORDER BY ts DESC
            LIMIT 5
            """
        ))).all()
        if not r4:
            print("  NONE FOUND -- L9 has never once produced a non-null score in predictions", flush=True)
        for row in r4:
            print(f"  {row.symbol} {row.ts}: {row.l9}", flush=True)


asyncio.run(main())
