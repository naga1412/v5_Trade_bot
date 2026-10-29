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

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    print("=" * 72)
    print("UNIVERSE TRADABILITY AUDIT -- read-only")
    print("=" * 72)
    print(f"as-of (UTC): {now}")
    print()

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        listings = await fetch_market_listings_full(http)

    session_factory = get_session_factory()
    async with session_factory() as session:
        snapshot = await load_live_fleet_universe(session)
        open_syms = await get_open_position_symbols(session)

    tradable = set(listings.tradable_futures)
    routable = set(listings.routable_futures_only) | set(listings.routable_spot)
    members = {e.symbol: e for e in snapshot}

    print(f"universe snapshot      : {len(members)} symbols")
    print(f"open positions         : {len(open_syms)} symbols")
    print(f"binance futures TRADING: {len(tradable)} USDT perps")
    print()

    # The removal set, and the two reasons a symbol can be in it, kept
    # SEPARATE because they have different operator consequences.
    not_tradable = sorted(s for s in members if s not in tradable)
    held_open = sorted(s for s in not_tradable if s in open_syms)
    open_not_member = sorted(
        s for s in open_syms if s not in tradable and s not in members
    )

    print("-" * 72)
    print(f"NOT TRADABLE but in the universe: {len(not_tradable)}")
    print("-" * 72)
    if not not_tradable:
        print("  (none -- the universe is fully tradable as of this read)")
    for s in not_tradable:
        e = members[s]
        flags = []
        if s in open_syms:
            flags.append("OPEN POSITION")
        if s in routable:
            flags.append("routable (has history)")
        print(
            f"  {s:<16} cohort={e.cohort:<22} qvol=${e.qvol_24h:>14,.0f}"
            f"  {' | '.join(flags) if flags else ''}"
        )

    print()
    print("-" * 72)
    print(f"RETAINED ONLY BY THE OPEN-POSITION OVERRIDE: {len(held_open)}")
    print("-" * 72)
    if not held_open:
        print("  (none)")
    else:
        for s in held_open:
            print(f"  {s} -- UNEXITABLE: not TRADING, and the position cannot close")

    print()
    print("-" * 72)
    print(f"OPEN POSITION, NOT TRADING, NOT YET A MEMBER: {len(open_not_member)}")
    print("-" * 72)
    print("  (these would be ADMITTED by the override's no-prior-entry branch)")
    if not open_not_member:
        print("  (none)")
    for s in open_not_member:
        print(f"  {s}")

    print()
    print("=" * 72)
    print(
        f"REMOVAL CANDIDATES: {len(not_tradable)}"
        f"  (of which {len(held_open)} are held open)"
    )
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
