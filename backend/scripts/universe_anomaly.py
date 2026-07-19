"""Item 2c probe — universe anomaly investigation.

Checks:
1. What non-ASCII/unusual symbols have ever appeared in asset_universe
2. First and last appearance of 币安人生USDT specifically
3. All symbols in shadow_trades that are NOT in current asset_universe top-30 snapshot
   (orphan trades from symbols that briefly ranked high then dropped)
4. Current Binance SPOT ticker validation: checks top-100 for any non-alphanumeric
   USDT symbols that would pass the current endswith("USDT") filter

Read-only. No code changes.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.config import get_settings

settings = get_settings()
STANDARD_SYMBOL_RE = re.compile(r'^[A-Z0-9]+USDT$')


async def run(session: AsyncSession) -> None:
    print("=" * 60)
    print("UNIVERSE ANOMALY INVESTIGATION")
    print("=" * 60)

    # 1. Non-standard symbols in asset_universe (ever)
    result = await session.execute(
        sa.text(
            "SELECT DISTINCT symbol FROM asset_universe "
            "WHERE symbol NOT SIMILAR TO '[A-Z0-9]+USDT' "
            "ORDER BY symbol"
        )
    )
    non_standard = [r[0] for r in result.fetchall()]
    print(f"\n1. Non-standard symbols in asset_universe (ever): {len(non_standard)}")
    for s in non_standard:
        print(f"   {s!r}")

    # 2. 币安人生USDT specifically
    result = await session.execute(
        sa.text(
            "SELECT MIN(snapshot_at) AS first_seen, MAX(snapshot_at) AS last_seen, "
            "       COUNT(*) AS snapshots, AVG(rank) AS avg_rank "
            "FROM asset_universe WHERE symbol = '币安人生USDT'"
        )
    )
    row = result.fetchone()
    if row and row[0]:
        print("\n2. 币安人生USDT in asset_universe:")
        print(f"   first_seen:  {row[0]}")
        print(f"   last_seen:   {row[1]}")
        print(f"   snapshots:   {row[2]}")
        print(f"   avg_rank:    {row[3]:.1f}")
    else:
        print("\n2. 币安人生USDT: NOT FOUND in asset_universe (historical shadow_trades only)")

    # 3. Shadow_trades symbols never in asset_universe
    result = await session.execute(
        sa.text(
            "SELECT st.symbol, COUNT(*) AS trades, "
            "       MIN(st.opened_at) AS first_trade "
            "FROM shadow_trades st "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM asset_universe au WHERE au.symbol = st.symbol"
            ") "
            "GROUP BY st.symbol ORDER BY trades DESC LIMIT 20"
        )
    )
    orphans = result.fetchall()
    print(f"\n3. Symbols in shadow_trades with no asset_universe record: {len(orphans)}")
    if orphans:
        print(f"   {'Symbol':<20} {'Trades':>6}  {'First Trade'}")
        for r in orphans:
            print(f"   {r[0]:<20} {r[1]:>6}  {r[2]}")

    # 4. Live Binance SPOT check — scan for non-standard USDT tickers
    print("\n4. Live Binance SPOT /api/v3/ticker/24hr — non-standard USDT tickers in top-100:")
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get("https://api.binance.com/api/v3/ticker/24hr")
            resp.raise_for_status()
            tickers = resp.json()

        usdt_tickers = [
            t for t in tickers
            if t.get("symbol", "").endswith("USDT")
        ]
        usdt_tickers.sort(key=lambda t: float(t.get("quoteVolume", 0)), reverse=True)
        top100 = usdt_tickers[:100]

        non_std_live = [t for t in top100 if not STANDARD_SYMBOL_RE.match(t["symbol"])]
        print(f"   Top-100 USDT tickers with non-standard names: {len(non_std_live)}")
        for t in non_std_live:
            print(f"   {t['symbol']!r}  vol={float(t['quoteVolume']):.0f}")

        if not non_std_live:
            print("   None found — universe filter gap is historical or symbol dropped out of top-100.")
    except Exception as e:
        print(f"   Binance API call failed: {e}")

    print("\nDone.")


async def main() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await run(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
