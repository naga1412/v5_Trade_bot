#!/usr/bin/env python3
"""READ-ONLY: which universe members are NOT TRADABLE right now.

WHY THIS IS A SCRIPT AND NOT A PASTED QUERY: the candidate list turns
over daily. `2026-10-27-routing-predicate-fix.md` named `HFTUSDT` and
`VICUSDT` as that morning's candidates and said in the same breath that
the real PR must RE-MEASURE rather than quote it -- the set had already
turned over three symbols in twelve hours. A list in a markdown file is
stale the day after it is written; a script re-derives it on demand.

IT CALLS THE DEPLOYED FUNCTION. `fetch_market_listings_full` is imported
and invoked, not reimplemented, for the reason `deploy_readback.py`
exists: `listing_completeness_audit.py` replicated the two status filters
so it could compare them against raw exchangeInfo, and consequently
reported "0 of 31 would raise" while the routing fix was still
uncommitted. A verification that reimplements the logic under test
verifies the reimplementation. This is a REPORT rather than a check, but
the same rule applies to where its inputs come from.

THE QUESTION: a symbol whose futures status is not TRADING cannot be
entered OR EXITED by anyone. That is unexitable in the strongest sense --
not "illiquid", but "no counterparty exists". The fleet's liquidity
hysteresis cannot detect it, because hysteresis measures depth and
spread on an order book that is not there.

Entry is already safe: `refresh_live_fleet_universe` builds `candidates`
from `tradable_futures`, so a non-TRADING symbol cannot be admitted. The
exposure is RETENTION -- the open-position override re-adds every symbol
in `get_open_position_symbols()` unconditionally, and a position on a
non-TRADING symbol can never close, so the override re-adds it on every
refresh, forever.

Exits 0 whether or not it finds something. A finding is not a
malfunction, and a red run for a finding trains people to ignore red
runs.

IT IS NO LONGER THE ONLY CONSUMER, AND NOT THE ONLY IMPLEMENTATION.
`app.shadow.universe_tradability.audit_tradability` holds the
classification, and this script and the scheduled producer in
`app.ops.check_events.record_universe_tradability` both call it. The
two differ ONLY in where the listings come from, which is stated
rather than left to be discovered: this script FETCHES them fresh
(it is an on-demand forensic read and should not inherit a cache's
staleness), while the producer uses the 24h-refreshed cohort cache
(two exchangeInfo calls every five minutes would be 576 requests a
day for a property that changes on the order of weeks).

A SCHEDULED PRODUCER EXISTS NOW. Before 2026-10-31 this script was
the whole mechanism, and the universe constraint's re-open trigger
was 'when the audit reports non-zero' -- which nothing could ever
report, because nothing ran it. The check is now in
`EXPECTED_CHECKS`, so its SILENCE is itself a finding.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> int:
    import datetime as dt

    import httpx

    from app.db.session import get_session_factory
    from app.shadow.live_fleet_universe import (
        fetch_market_listings_full,
        get_open_position_symbols,
        load_live_fleet_universe,
    )
    from app.shadow.universe_tradability import (
        audit_tradability,
        describe_market_status,
    )

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    print("=" * 72)
    print("UNIVERSE TRADABILITY AUDIT -- read-only")
    print("=" * 72)
    print(f"as-of (UTC): {now}")
    print("listings    : FETCHED FRESH (not the 24h cohort cache)")
    print()

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        listings = await fetch_market_listings_full(http)

    session_factory = get_session_factory()
    async with session_factory() as session:
        snapshot = await load_live_fleet_universe(session)
        open_syms = await get_open_position_symbols(session)

    members = {e.symbol: e for e in snapshot}
    tradable_fut = set(listings.tradable_futures)
    tradable_spot = set(listings.tradable_spot)
    routable = set(listings.routable_futures_only) | set(listings.routable_spot)

    # THE CLASSIFICATION IS NOT DONE HERE. It is one shared pure
    # function, so this report and the scheduled producer cannot drift
    # apart -- the same reason this script calls the deployed
    # fetch_market_listings_full instead of replicating its two status
    # filters, which is how listing_completeness_audit.py came to report
    # '0 of 31 would raise' while the routing fix was still uncommitted.
    audit = audit_tradability(
        members=set(members),
        open_positions=set(open_syms),
        tradable_futures=tradable_fut,
        tradable_spot=tradable_spot,
    )

    def where(sym: str) -> str:
        return describe_market_status(
            sym, tradable_futures=tradable_fut,
            tradable_spot=tradable_spot, routable=routable,
        )

    print(f"universe snapshot      : {audit.members} symbols")
    print(f"open positions         : {audit.open_positions} symbols")
    print(f"binance futures TRADING: {audit.tradable_futures} USDT perps")
    print(f"binance spot TRADING   : {audit.tradable_spot} USDT pairs")
    if audit.inputs_look_empty:
        print()
        print("  !! AN INPUT IS EMPTY -- a zero finding below would be")
        print("     VACUOUS, not a clean result. Fix the input first.")
    print()

    print("-" * 72)
    print(f"NOT TRADABLE but in the universe: {len(audit.not_tradable)}")
    print("-" * 72)
    if not audit.not_tradable:
        print("  (none -- the universe is fully tradable as of this read)")
    for sym in audit.not_tradable:
        e = members[sym]
        flags = "OPEN POSITION" if sym in set(open_syms) else ""
        print(
            f"  {sym:<16} cohort={e.cohort:<22} qvol=${e.qvol_24h:>14,.0f}"
            f"  {where(sym)}  {flags}"
        )

    print()
    print("-" * 72)
    print(f"RETAINED ONLY BY THE OPEN-POSITION OVERRIDE: {len(audit.held_open)}")
    print("-" * 72)
    if not audit.held_open:
        print("  (none)")
    for sym in audit.held_open:
        print(f"  {sym} -- UNEXITABLE: not TRADING, and the position cannot close")

    print()
    print("-" * 72)
    print(f"OPEN POSITION, NOT TRADING, NOT YET A MEMBER: {len(audit.open_not_member)}")
    print("-" * 72)
    print("  (these would be ADMITTED by the override's no-prior-entry branch)")
    if not audit.open_not_member:
        print("  (none)")
    for sym in audit.open_not_member:
        print(f"  {sym:<16} {where(sym)}")

    print()
    print("-" * 72)
    print(
        f"NOT A FINDING -- open position, spot-TRADING, not a USDT perp: "
        f"{len(audit.open_spot_only)}"
    )
    print("-" * 72)
    print("  (tradable; the universe predicate just does not describe them)")
    if not audit.open_spot_only:
        print("  (none)")
    for sym in audit.open_spot_only:
        print(f"  {sym:<16} {where(sym)}")

    print()
    print("=" * 72)
    print(
        f"REMOVAL CANDIDATES: {len(audit.not_tradable)}"
        f"  (of which {len(audit.held_open)} are held open)"
    )
    print(f"HAS FINDING: {audit.has_finding}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
