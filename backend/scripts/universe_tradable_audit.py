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

    # TRADABILITY IS A TWO-MARKET QUESTION, and the first version of
    # this script got it wrong. It tested `s not in tradable_futures`
    # alone, which conflates two completely different things:
    #
    #   (1) listed as a USDT perp but HALTED or SETTLING -- genuinely
    #       untradable, the case this audit exists for; and
    #   (2) NOT A USDT PERP AT ALL -- a spot-only symbol, which is
    #       perfectly tradable, just not on the market the fleet
    #       universe is built from.
    #
    # Shadow open positions include spot-only symbols, so applying a
    # futures-membership test to that population reported PEPEUSDT
    # (spot; the perp is 1000PEPEUSDT) and SOXLBUSDT as untradable when
    # both are fine. Same shape as the standing pooled-rate rule: name
    # the population a predicate was defined over AND the one it is
    # applied to, and if they differ, that IS the defect.
    tradable_fut = set(listings.tradable_futures)
    tradable_spot = set(listings.tradable_spot)
    routable = set(listings.routable_futures_only) | set(listings.routable_spot)
    members = {e.symbol: e for e in snapshot}

    def untradable(sym: str) -> bool:
        """No market will take an order for this symbol, either side."""
        return sym not in tradable_fut and sym not in tradable_spot

    def where(sym: str) -> str:
        """Explicit per-market verdict -- never an inference from absence."""
        bits = []
        bits.append("futures=TRADING" if sym in tradable_fut else "futures=not-TRADING/absent")
        bits.append("spot=TRADING" if sym in tradable_spot else "spot=not-TRADING/absent")
        bits.append("has-history" if sym in routable else "no-history")
        return " ".join(bits)

    print(f"universe snapshot      : {len(members)} symbols")
    print(f"open positions         : {len(open_syms)} symbols")
    print(f"binance futures TRADING: {len(tradable_fut)} USDT perps")
    print(f"binance spot TRADING   : {len(tradable_spot)} USDT pairs")
    print()

    # The removal set, and the two reasons a symbol can be in it, kept
    # SEPARATE because they have different operator consequences.
    not_tradable = sorted(s for s in members if untradable(s))
    held_open = sorted(s for s in not_tradable if s in open_syms)
    open_not_member = sorted(
        s for s in open_syms if untradable(s) and s not in members
    )
    # Reported SEPARATELY and NOT as a finding: an open position on a
    # symbol that is tradable but is not a USDT perp. Perfectly normal
    # for a shadow position; it is only interesting that the universe
    # predicate does not describe it.
    open_spot_only = sorted(
        s for s in open_syms
        if s not in tradable_fut and s in tradable_spot
    )

    print("-" * 72)
    print(f"NOT TRADABLE but in the universe: {len(not_tradable)}")
    print("-" * 72)
    if not not_tradable:
        print("  (none -- the universe is fully tradable as of this read)")
    for s in not_tradable:
        e = members[s]
        flags = ["OPEN POSITION"] if s in open_syms else []
        print(
            f"  {s:<16} cohort={e.cohort:<22} qvol=${e.qvol_24h:>14,.0f}"
            f"  {where(s)}  {' | '.join(flags)}"
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
        print(f"  {s:<16} {where(s)}")

    print()
    print("-" * 72)
    print(f"NOT A FINDING -- open position, spot-TRADING, not a USDT perp: "
          f"{len(open_spot_only)}")
    print("-" * 72)
    print("  (tradable; the universe predicate just does not describe them)")
    if not open_spot_only:
        print("  (none)")
    for s in open_spot_only:
        print(f"  {s:<16} {where(s)}")

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
