#!/usr/bin/env python3
"""READ-ONLY: recompute historical `mtf_agreement` for the futures_poll
bar-clearing cohort and run the decision-flip screen.

Pre-registered in docs/superpowers/decisions/
2026-10-26-mtf-backfill-preregistration.md, committed BEFORE this ran.

WHAT IS REUSED AND WHAT IS NOT. `compute_mtf_confluence` has no
historical mode -- it votes on the latest 200 bars from a module-level
cache and takes no timestamp -- so it cannot be called here. This script
therefore:

  * IMPORTS the real decision logic, `_vote_for_tf` and
    `_compute_agreement_and_dominant`, and passes them their native
    input (raw Binance kline rows). The vote is not reimplemented.
  * RE-PATHS ONLY THE FETCH, choosing the market with #609's
    `resolve_klines_url_for_symbol` so a futures-only symbol reaches
    fapi instead of 404ing on spot. That wrong market IS the defect
    being measured.

NO DB WRITE. NO LIVE CHANGE. The output is a flip count.

The threshold is read from the RUNNING settings object inside the
container, never from config.py's default -- the standing rule after the
env-flip incident.
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, "/app")

import httpx  # noqa: E402
import sqlalchemy as sa  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.scoring.mtf_confluence import (  # noqa: E402
    KLINE_LIMIT,
    TIMEFRAMES,
    _compute_agreement_and_dominant,
    _vote_for_tf,
)
from app.core.scoring.types import Direction  # noqa: E402
from app.data.bar_fetch import (  # noqa: E402
    UnresolvedMarket,
    resolve_klines_url_for_symbol,
)
from app.db.session import get_session_factory  # noqa: E402
from app.shadow.cohort_cache import refresh_futures_only_cache  # noqa: E402

LOOKBACK_DAYS = 30
CONCURRENCY = 5

# Bar length in milliseconds, for aligning endTime down to a bar boundary
# so rows sharing a bar share one fetch. Mirrors what the live per-TF
# cache achieves, without pretending to reproduce its staleness.
_TF_MS = {
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
    "1w": 7 * 24 * 60 * 60_000,
}


def _hr(t: str) -> None:
    print()
    print("=" * 72)
    print(t)
    print("=" * 72)


async def _fetch(
    http: httpx.AsyncClient, url: str, symbol: str, tf: str, end_ms: int,
) -> list[list] | None:
    try:
        r = await http.get(
            url,
            params={"symbol": symbol, "interval": tf,
                    "limit": KLINE_LIMIT, "endTime": end_ms},
            timeout=15.0,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        print(f"  ! fetch {symbol}/{tf}@{end_ms}: {type(e).__name__}: {e}")
        return None


async def main() -> int:
    settings = get_settings()
    threshold = settings.MTF_MIN_AGREEMENT_1H

    _hr("0. THE THRESHOLD, FROM THE RUNNING OBJECT")
    print(f"MTF_MIN_AGREEMENT_1H = {threshold}   (config.py default is 3)")
    print("Read via get_settings() inside the running container, i.e. the")
    print("container's own env -- not a grep of .env, not a code default.")
    if threshold == 0:
        print()
        print("THRESHOLD IS 0 -- the veto is in its rollback state and")
        print("PASSES every agreement value. The flip count is 0 by")
        print("construction and the screen below is moot. Reporting anyway.")

    factory = get_session_factory()
    async with factory() as session:
        _hr("1. THE COHORT")
        rows = (await session.execute(sa.text(
            "SELECT id, symbol, ts, final_score, mtf_agreement "
            "FROM predictions "
            "WHERE timeframe = '1h' AND symbol_source = 'futures_poll' "
            f"  AND ts >= NOW() - INTERVAL '{LOOKBACK_DAYS} days' "
            "  AND ABS(final_score) >= 0.36 "
            "ORDER BY ts"
        ))).all()
        null_rows = [r for r in rows if r.mtf_agreement is None]
        print(f"bar-clearing rows      : {len(rows)}")
        print(f"  of which mtf IS NULL : {len(null_rows)}")
        print(f"  distinct symbols     : "
              f"{len({r.symbol for r in null_rows})} (in the NULL set)")
        print(f"  window               : {rows[0].ts} -> {rows[-1].ts}"
              if rows else "  (empty)")

        _hr("2. STEP 0 -- DO REALIZED OUTCOMES EXIST?")
        outcome = (await session.execute(sa.text(
            "SELECT COUNT(v.id) AS have_row, COUNT(v.validated_at) AS have_outcome "
            "FROM predictions p "
            "LEFT JOIN prediction_validations v ON v.prediction_id = p.id "
            "WHERE p.timeframe = '1h' AND p.symbol_source = 'futures_poll' "
            f"  AND p.ts >= NOW() - INTERVAL '{LOOKBACK_DAYS} days' "
            "  AND ABS(p.final_score) >= 0.36"
        ))).first()
        print(f"validation rows present : {outcome.have_row}")
        print(f"carrying validated_at   : {outcome.have_outcome}")
        if not outcome.have_outcome:
            print()
            print("ZERO realized outcomes. The OUTCOME ARM IS ABANDONED --")
            print("data-absent, not underpowered. This is the pre-registered")
            print("step-0 abort, and the cause is item 1's validator outage")
            print("(stopped 2026-09-03; this cohort is entirely inside it).")

    if not null_rows:
        print("\nNothing to backfill.")
        return 0

    # ---- resolve markets ----
    _hr("3. MARKET RESOLUTION (#609's no-default resolver)")
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as http:
        n = await refresh_futures_only_cache(http)
        print(f"listing cache refreshed: futures_only={n if n is None else len(n)}")

        urls: dict[str, str] = {}
        unresolved: list[str] = []
        for sym in sorted({r.symbol for r in null_rows}):
            b = sym.replace("/", "")
            try:
                urls[sym] = resolve_klines_url_for_symbol(b)
            except UnresolvedMarket as e:
                unresolved.append(f"{sym}: {e}")
        for u in unresolved:
            print(f"  UNRESOLVED {u}")
        by_url: dict[str, int] = defaultdict(int)
        for sym, u in urls.items():
            by_url["fapi" if "fapi" in u else "spot"] += 1
        print(f"resolved: {dict(by_url)}  unresolved: {len(unresolved)}")

        # ---- fetch + vote ----
        _hr("4. RECOMPUTE")
        sem = asyncio.Semaphore(CONCURRENCY)
        memo: dict[tuple[str, str, int], list[list] | None] = {}
        memo_lock = asyncio.Lock()

        async def klines_for(sym: str, tf: str, ts_ms: int):
            aligned = (ts_ms // _TF_MS[tf]) * _TF_MS[tf]
            key = (sym, tf, aligned)
            async with memo_lock:
                if key in memo:
                    return memo[key]
            async with sem:
                data = await _fetch(
                    http, urls[sym], sym.replace("/", ""), tf, aligned)
            async with memo_lock:
                memo[key] = data
            return data

        results: list[tuple] = []
        done = 0
        for row in null_rows:
            if row.symbol not in urls:
                continue
            ts_ms = int(row.ts.timestamp() * 1000)
            direction = (
                Direction.LONG if row.final_score > 0 else Direction.SHORT
            )
            tf_data: dict[str, tuple[int, float]] = {}
            degraded = 0
            for tf in TIMEFRAMES:
                k = await klines_for(row.symbol, tf, ts_ms)
                if not k:
                    degraded += 1
                    tf_data[tf] = (0, 0.0)
                    continue
                tf_data[tf] = _vote_for_tf(k)
            agreement, dominant = _compute_agreement_and_dominant(
                direction, tf_data)
            results.append((row.id, row.symbol, row.ts, row.final_score,
                            direction.value, agreement, dominant, degraded))
            done += 1
            if done % 25 == 0:
                print(f"  ... {done}/{len(null_rows)} rows, "
                      f"{len(memo)} distinct fetches", flush=True)

    # ---- the flip screen ----
    _hr("5. THE FLIP SCREEN")
    total = len(results)
    clean = [r for r in results if r[7] == 0]
    degraded = [r for r in results if r[7] > 0]
    print(f"recomputed rows            : {total}")
    print(f"  fully resolved (6/6 TFs) : {len(clean)}")
    print(f"  partially degraded       : {len(degraded)}")

    # NULL already PASSES the veto (fail-open), so pass->block is the only
    # possible transition. A reversal is therefore a BUG signal, not a
    # two-sided lever -- stated in the pre-registration.
    flips = [r for r in clean if r[5] is not None and r[5] < threshold]
    reversals = [r for r in clean if r[5] is None]
    print()
    print(f"FLIPS     (pass -> block, agreement < {threshold}) : "
          f"{len(flips)} / {len(clean)}")
    print(f"REVERSALS (block -> pass)                   : {len(reversals)}")
    if reversals:
        print("  !! NON-ZERO REVERSALS. NULL already passes, so nothing can")
        print("  !! become more permissive. This is a MEASUREMENT BUG, not")
        print("  !! a finding. Investigate before quoting any number above.")
    print()
    dist: dict = defaultdict(int)
    for r in clean:
        dist[r[5]] += 1
    print("agreement distribution (fully-resolved rows):")
    for k in sorted(dist, key=lambda x: (x is None, x)):
        mark = " <-- blocks" if (k is not None and k < threshold) else ""
        print(f"  agreement={k}: {dist[k]}{mark}")

    days = LOOKBACK_DAYS
    print()
    print(f"cards/day removed if routed = {len(flips)}/{days} = "
          f"{len(flips)/days:.3f}/day   (upper bound; see below)")
    print("Sign: the dispatcher veto reads `is not None AND < threshold`, so")
    print("NULL passes today and this lever can only REDUCE card load.")
    print("The delta is in [-x, 0] and cannot be positive.")

    _hr("6. WHAT THIS DOES NOT ESTABLISH")
    print("Nothing here measures whether the mtf veto has EDGE. Zero")
    print("realized outcomes exist for this cohort (section 2), and the")
    print("pre-registration forbids reporting a sub-bar P&L difference as")
    print("directional. This sizes the lever; it does not justify pulling")
    print("it. Routing mtf_confluence remains a live dispatch change")
    print("requiring explicit authorisation.")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("ENV", "production")
    raise SystemExit(asyncio.run(main()))
