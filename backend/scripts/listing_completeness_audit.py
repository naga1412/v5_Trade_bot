#!/usr/bin/env python3
"""READ-ONLY: is a universe member absent from BINANCE, or absent from OUR
listing computation?

THE QUESTION THIS SETTLES. `app/data/bar_fetch.py` resolves a symbol's
market by POSITIVE membership in the sets `fetch_market_listings`
computes, and raises `UnresolvedMarket` otherwise -- no spot default,
deliberately. That rule is only safe if the sets are COMPLETE. If a
genuinely-listed symbol is missing from them, the raise is a FALSE
POSITIVE and the no-default rule is rejecting real symbols. TONUSDT came
back unresolvable at the 2026-10-26 shadow-seed boundary, so:

  (a) present upstream, missing from our set
      -> fetch_market_listings is INCOMPLETE. A correctness regression,
         fix immediately.
  (b) genuinely absent upstream
      -> the symbol is UNEXITABLE: it drops out of sampling, so no exit
         rule ever evaluates it and it stays in the fleet forever. That
         is #601's uncovered third retention path, and the fix is a hard
         universe constraint rather than hysteresis tuning.

The filters under test (live_fleet_universe.fetch_market_listings):
    spot    : quoteAsset == "USDT" AND status == "TRADING"
    futures : quoteAsset == "USDT" AND contractType == "PERPETUAL"
              AND status == "TRADING"
So a symbol listed on Binance but HALTED, in BREAK, in AUCTION_MATCH or
PRE/POST_TRADING is excluded by `status`, and a non-USDT quote is
excluded by `quoteAsset`. Both are (a)-shaped: present upstream, absent
from our set.

COUNTS THE WHOLE CLASS, not just TONUSDT -- "instance five comes from an
UNKNOWN symbol" was the no-default mandate's own prediction.

Also probes the ARBUSDT case directly. ARB is reported PERMANENT by
#610's mtf counter, but that counter lives in mtf_confluence, which uses
its OWN hardcoded spot URL and never consults these listings -- so an
ARB failure there is a raw HTTP/timeout event, not a resolution failure.
This script fetches real klines for the suspects and prints the actual
status code or exception class, which is the only thing that separates
"404 because not listed" from "timeout because 2.0s was not enough".

No DB writes. No listing-cache mutation.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.db.session import get_session_factory  # noqa: E402
from app.shadow.live_fleet_universe import (  # noqa: E402
    load_baseline_symbols,
)
from app.shadow.universe import load_shadow_universe  # noqa: E402
from app.shadow.worker import (  # noqa: E402
    _compute_subscription_set,
    _load_open_position_symbols,
)

SPOT = "https://api.binance.com"
FUT = "https://fapi.binance.com"
# Suspects worth an explicit per-symbol readout regardless of class.
SUSPECTS = ("TONUSDT", "ARBUSDT", "ETCUSDT", "BNBUSDT", "1000PEPEUSDT")


def _hr(t: str) -> None:
    print()
    print("=" * 72)
    print(t)
    print("=" * 72)


async def _klines_probe(
    http: httpx.AsyncClient, base: str, path: str, symbol: str,
) -> str:
    """Real klines call. Returns a one-line verdict string."""
    try:
        r = await http.get(
            f"{base}{path}",
            params={"symbol": symbol, "interval": "1h", "limit": 2},
            timeout=10.0,
        )
    except Exception as e:  # noqa: BLE001
        return f"EXC {type(e).__name__}: {e}"
    if r.status_code != 200:
        body = r.text[:120].replace("\n", " ")
        return f"HTTP {r.status_code} {body}"
    n = len(r.json()) if r.content else 0
    return f"HTTP 200, {n} bar(s)"


async def main() -> int:
    factory = get_session_factory()
    async with factory() as session:
        top_n = await load_shadow_universe(session, narrow=[])
        open_syms = await _load_open_position_symbols(session)
        baseline = await load_baseline_symbols(session)
    universe = _compute_subscription_set(top_n, open_syms, baseline=baseline)

    _hr("0. THE POPULATION (the real subscription set)")
    print(f"top_n from asset_universe : {len(top_n)}")
    print(f"open-position symbols     : {len(open_syms)}")
    print(f"cohort baseline           : {len(baseline)}")
    print(f"UNIVERSE (deduped union)  : {len(universe)}")

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        _hr("1. RAW exchangeInfo, UNFILTERED")
        fr = await http.get(f"{FUT}/fapi/v1/exchangeInfo")
        fr.raise_for_status()
        fut_all = {s["symbol"]: s for s in fr.json()["symbols"]}
        sr = await http.get(f"{SPOT}/api/v3/exchangeInfo")
        sr.raise_for_status()
        spot_all = {s["symbol"]: s for s in sr.json()["symbols"]}
        print(f"futures symbols (any quote/status/type): {len(fut_all)}")
        print(f"spot    symbols (any quote/status)     : {len(spot_all)}")

        # Our filters, replicated EXACTLY from fetch_market_listings so a
        # drift between this audit and the real function is visible rather
        # than assumed away.
        our_fut = {
            k for k, s in fut_all.items()
            if s.get("quoteAsset") == "USDT"
            and s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
        }
        our_spot = {
            k for k, s in spot_all.items()
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
        }
        our_fut_only = our_fut - our_spot
        print(f"OUR futures set : {len(our_fut)}")
        print(f"OUR spot set    : {len(our_spot)}")
        print(f"OUR futures-only: {len(our_fut_only)}")

        _hr("2. THE WHOLE CLASS -- universe members resolving on NEITHER")
        unresolved = []
        for sym in universe:
            b = sym.replace("/", "")
            if b in our_fut_only or b in our_spot:
                continue
            unresolved.append(b)
        print(f"universe members that RAISE UnresolvedMarket: "
              f"{len(unresolved)} of {len(universe)}")
        if not unresolved:
            print("  (none -- the no-default rule is rejecting nothing)")

        verdicts: dict[str, str] = {}
        for b in unresolved:
            in_spot_raw = b in spot_all
            in_fut_raw = b in fut_all
            s_meta = spot_all.get(b, {})
            f_meta = fut_all.get(b, {})
            if in_spot_raw or in_fut_raw:
                cls = "(a) PRESENT UPSTREAM -> OUR SET IS INCOMPLETE"
            else:
                cls = "(b) GENUINELY ABSENT UPSTREAM -> unexitable member"
            verdicts[b] = cls
            print()
            print(f"  {b}: {cls}")
            print(f"    raw spot   : present={in_spot_raw} "
                  f"status={s_meta.get('status')!r} "
                  f"quote={s_meta.get('quoteAsset')!r}")
            print(f"    raw futures: present={in_fut_raw} "
                  f"status={f_meta.get('status')!r} "
                  f"quote={f_meta.get('quoteAsset')!r} "
                  f"type={f_meta.get('contractType')!r}")

        _hr("3. CLASS TALLY")
        n_a = sum(1 for v in verdicts.values() if v.startswith("(a)"))
        n_b = sum(1 for v in verdicts.values() if v.startswith("(b)"))
        print(f"(a) present upstream, our set incomplete : {n_a}")
        print(f"(b) genuinely absent upstream           : {n_b}")
        if n_a:
            print()
            print("ANY (a) IS A CORRECTNESS REGRESSION in the no-default")
            print("rule: a genuinely-listed symbol is being refused. The")
            print("filters to look at are status== and quoteAsset== in")
            print("live_fleet_universe.fetch_market_listings.")
        if n_b:
            print()
            print("ANY (b) IS AN UNEXITABLE UNIVERSE MEMBER: it drops out")
            print("of sampling, so no exit rule evaluates it and it stays")
            print("in the fleet forever. Hard universe constraint, not")
            print("hysteresis tuning.")

        _hr("4. SUSPECTS -- real klines, actual status / exception class")
        print("mtf_confluence uses its OWN hardcoded spot URL and never")
        print("consults these listings, so an mtf failure is a raw HTTP or")
        print("timeout event. This is what separates 404-not-listed from")
        print("timeout-2.0s-too-short.")
        for b in SUSPECTS:
            print()
            print(f"  {b}")
            print(f"    in OUR spot set       : {b in our_spot}")
            print(f"    in OUR futures-only   : {b in our_fut_only}")
            print(f"    raw spot status       : "
                  f"{spot_all.get(b, {}).get('status')!r}")
            print(f"    raw futures status    : "
                  f"{fut_all.get(b, {}).get('status')!r}")
            print(f"    SPOT klines  -> {await _klines_probe(http, SPOT, '/api/v3/klines', b)}")
            print(f"    FUT  klines  -> {await _klines_probe(http, FUT, '/fapi/v1/klines', b)}")

        _hr("5. SANITY -- non-TRADING USDT symbols upstream")
        print("Sizes the (a) exposure independent of today's universe: any")
        print("of these entering the universe later would be refused.")
        for label, allsy in (("spot", spot_all), ("futures", fut_all)):
            by_status: dict[str, int] = {}
            for s in allsy.values():
                if s.get("quoteAsset") != "USDT":
                    continue
                by_status[s.get("status", "?")] = by_status.get(
                    s.get("status", "?"), 0) + 1
            print(f"  {label}: {dict(sorted(by_status.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
