"""Stablecoin hygiene probe — ops-debug probe 'stablecoin-hygiene'.

READ-ONLY diagnostic. Three sections:

1. POST-BLACKLIST ACTIVITY: shadow_trades + shadow_open_positions with any
   known stablecoin/pegged-token symbol AFTER 2026-07-11 (post-#283 deploy).
   Reports which are still appearing in closed or open positions.

2. LOW-ATR SWEEP: any symbol in shadow_trades (last 30d) where avg daily
   ATR% < 0.05 — the "not tradeable" heuristic. Flags anything that should
   potentially be added to the blacklist (stablecoin or not).

3. CONFIG STATE: current SHADOW_SPOT_BLACKLIST and MIN_ENTRY_SCORE_LONG.

Usage (inside backend container via ops-debug.yml probe):
    docker compose exec -T backend python /app/scripts/stablecoin_hygiene.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.config import get_settings
from app.db.session import get_session_factory

# Known stablecoin / pegged-token patterns to check
KNOWN_STABLECOIN_SYMBOLS = [
    "RLUSDUSDT", "FDUSDUSDT", "USDCUSDT", "USDEUSDT", "USD1USDT",
    "EURUSDT", "USDPUSDT", "TUSDUSDT", "DAIUSDT", "BUSDUSDT",
    "SPCXBUSDT", "SNDKBUSDT",
]

POST_BLACKLIST_DEPLOY = "2026-07-11 00:00:00+00"
LOW_ATR_PCT_THRESHOLD = 0.05  # avg ATR% below this = "not tradeable"


async def main() -> None:
    settings = get_settings()
    blacklist = list(settings.SHADOW_SPOT_BLACKLIST)
    min_score = getattr(settings, "MIN_ENTRY_SCORE_LONG", "NOT SET")

    sf = get_session_factory()
    async with sf() as session:

        # ── Section 1a: closed trades from stablecoin symbols post-deploy ─────
        stablecoin_in_clause = ", ".join(f"'{s}'" for s in KNOWN_STABLECOIN_SYMBOLS)
        closed_rows = (
            await session.execute(
                text(
                    f"SELECT symbol, COUNT(*) AS n,"
                    f" MIN(opened_at) AS first_open, MAX(closed_at) AS last_close"
                    f" FROM shadow_trades"
                    f" WHERE opened_at >= '{POST_BLACKLIST_DEPLOY}'"
                    f"   AND symbol IN ({stablecoin_in_clause})"
                    f" GROUP BY symbol ORDER BY symbol"
                )
            )
        ).fetchall()

        # ── Section 1b: open positions from stablecoin symbols ────────────────
        open_rows = (
            await session.execute(
                text(
                    f"SELECT symbol, COUNT(*) AS n,"
                    f" MIN(opened_at) AS first_open"
                    f" FROM shadow_open_positions"
                    f" WHERE symbol IN ({stablecoin_in_clause})"
                    f" GROUP BY symbol ORDER BY symbol"
                )
            )
        ).fetchall()

        # ── Section 2: low-ATR sweep across universe (last 30d) ───────────────
        low_atr_rows = (
            await session.execute(
                text(
                    "SELECT symbol,"
                    " COUNT(*) AS n,"
                    " ROUND((AVG(entry_atr / NULLIF(entry_price, 0)) * 100)::numeric, 4) AS avg_atr_pct,"
                    " ROUND((100.0 * SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) / COUNT(*))::numeric, 1) AS wr_pct"
                    " FROM shadow_trades"
                    " WHERE closed_at >= NOW() - INTERVAL '30 days'"
                    "   AND closed_at IS NOT NULL"
                    "   AND entry_price > 0 AND entry_atr > 0"
                    " GROUP BY symbol"
                    f" HAVING AVG(entry_atr / NULLIF(entry_price, 0)) * 100 < {LOW_ATR_PCT_THRESHOLD}"
                    " ORDER BY avg_atr_pct ASC"
                )
            )
        ).fetchall()

    # ── Print results ─────────────────────────────────────────────────────────
    print("=" * 72)
    print("STABLECOIN HYGIENE PROBE")
    print("=" * 72)

    print(f"\n--- 1a. CLOSED TRADES from stablecoin symbols (post {POST_BLACKLIST_DEPLOY}) ---")
    if closed_rows:
        print(f"  {'Symbol':<14}  {'N':>6}  {'First open':>25}  {'Last close':>25}")
        print(f"  {'-'*14}  {'-'*6}  {'-'*25}  {'-'*25}")
        for r in closed_rows:
            in_bl = " [BLACKLISTED]" if r.symbol in blacklist else " *** NOT IN BLACKLIST ***"
            print(f"  {r.symbol:<14}  {r.n:>6}  {str(r.first_open):>25}  {str(r.last_close):>25}{in_bl}")
    else:
        print("  (none — all stablecoin symbols successfully excluded from closed trades)")

    print(f"\n--- 1b. OPEN POSITIONS from stablecoin symbols ---")
    if open_rows:
        print(f"  {'Symbol':<14}  {'N':>6}  {'First open':>25}")
        print(f"  {'-'*14}  {'-'*6}  {'-'*25}")
        for r in open_rows:
            in_bl = " [BLACKLISTED]" if r.symbol in blacklist else " *** NOT IN BLACKLIST ***"
            print(f"  {r.symbol:<14}  {r.n:>6}  {str(r.first_open):>25}{in_bl}")
    else:
        print("  (none — no stablecoin symbols in current open positions)")

    print(f"\n--- 2. LOW-ATR SWEEP (last 30d, avg ATR% < {LOW_ATR_PCT_THRESHOLD}%) ---")
    if low_atr_rows:
        print(f"  {'Symbol':<14}  {'N':>6}  {'AvgATR%':>8}  {'WR%':>6}  {'Status'}")
        print(f"  {'-'*14}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*30}")
        for r in low_atr_rows:
            in_bl = "BLACKLISTED" if r.symbol in blacklist else "*** ADD TO BLACKLIST? ***"
            print(f"  {r.symbol:<14}  {r.n:>6}  {float(r.avg_atr_pct):>8.4f}  {float(r.wr_pct):>6.1f}  {in_bl}")
    else:
        print(f"  (no symbols found with avg ATR% < {LOW_ATR_PCT_THRESHOLD}% — universe looks clean)")

    print("\n--- 3. CONFIG STATE ---")
    print(f"  MIN_ENTRY_SCORE_LONG = {min_score}")
    print(f"  SHADOW_SPOT_BLACKLIST ({len(blacklist)} entries):")
    for sym in sorted(blacklist):
        print(f"    {sym}")

    print("\nDone.")


asyncio.run(main())
