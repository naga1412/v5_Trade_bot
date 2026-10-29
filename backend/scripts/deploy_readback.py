#!/usr/bin/env python3
"""READ-BACK: import the PRODUCTION module and CALL the real function.

THE RULE THIS EXISTS TO ENFORCE, stated in every failure message:

    A VERIFICATION THAT REIMPLEMENTS THE LOGIC UNDER TEST VERIFIES THE
    REIMPLEMENTATION.

That is not hypothetical here. `listing_completeness_audit.py` replicates
`fetch_market_listings`' two filters so it can compare them against raw
exchangeInfo -- correct for an AUDIT, and useless as a verification,
because it would pass unchanged whether or not the deployed resolver had
the fix. The audit reported "0 of 31 would raise" while the routing fix
was, at that moment, still uncommitted.

So this probe does the opposite: it imports the deployed module, calls
the deployed callable with a pinned or discovered input, and checks the
value that comes back. Nothing here reimplements app logic.

GENERIC ON PURPOSE. Each check is a declarative `ReadBack` of
(module, callable, input, expectation). A future deploy adds a row; it
does not write a new probe. The argument for building it generically now
is that two fixes needed the identical thing one day apart.

Exit code is 1 if any check fails, so the workflow goes red -- unlike a
report probe, a failed read-back means the deploy did not take.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

THE_RULE = (
    "A VERIFICATION THAT REIMPLEMENTS THE LOGIC UNDER TEST VERIFIES THE "
    "REIMPLEMENTATION. This probe imports the deployed module and calls "
    "the deployed callable; if it is failing, the DEPLOY did not take, or "
    "the behaviour genuinely differs from what shipped."
)


@dataclass(frozen=True)
class ReadBack:
    """One deployed-behaviour check."""

    name: str
    module: str
    attr: str
    why: str
    # literal positional args, or a prepare() that returns them
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    # "module:callable" run (awaited if needed) before the check
    setup: str | None = None
    # A bare function name in THIS module returning the args tuple, for
    # inputs that cannot be pinned because the live set turns over.
    # Resolved from globals() rather than a "module:callable" path:
    # __name__ is "__main__" when run as a script and
    # "scripts.deploy_readback" when imported, so a self-referential
    # module path would mean two different things in the two cases.
    prepare: str | None = None
    # one of: equals / truthy / not_raises / isinstance / attr_equals / in_
    check: str = "not_raises"
    expect: Any = None
    attr_path: str | None = None
    skip_if_prepare_empty: bool = False


def _load(dotted: str) -> Any:
    mod_name, _, attr = dotted.partition(":")
    mod = importlib.import_module(mod_name)
    return getattr(mod, attr) if attr else mod


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value


def _dig(obj: Any, path: str) -> Any:
    """Walk a dotted path, accepting MAPPING KEYS as well as attributes.

    `endpoint_weights.klines` is a dict lookup, not an attribute. The
    first version of this used getattr only and raised
    `AttributeError: 'dict' object has no attribute 'klines'` -- found by
    running the probe rather than by reading it, which is the whole
    argument for running a read-back locally before trusting it in CI.
    """
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj[part]
        else:
            obj = getattr(obj, part)
    return obj


# ===========================================================================
# PREPARE helpers -- these DISCOVER an input, they do not reimplement logic.
# ===========================================================================


async def prepare_break_or_settling_symbol() -> tuple[Any, ...]:
    """Find a symbol that is LISTED but not TRADING, from the live listing.

    Pinning one would be wrong rather than merely brittle: the set turned
    over three symbols in twelve hours (TONUSDT -> HFTUSDT, VICUSDT), so
    a pinned name would go stale and the check would silently start
    verifying nothing. This asks the deployed `fetch_market_listings_full`
    for the two views and takes a symbol in the ROUTABLE set but not the
    TRADABLE one -- which is exactly the class the routing fix exists to
    serve.
    """
    import httpx

    from app.shadow.cohort_cache import refresh_futures_only_cache
    from app.shadow.live_fleet_universe import fetch_market_listings_full

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        # Populate the DEPLOYED cache via the DEPLOYED refresh, because
        # the resolver reads that cache and would otherwise raise on a
        # cold one -- which would look like the fix failing when it is
        # the probe that was unprepared.
        await refresh_futures_only_cache(http)
        lst = await fetch_market_listings_full(http)
    candidates = sorted(set(lst.routable_spot) - set(lst.tradable_spot))
    if not candidates:
        return ()
    return (candidates[0],)


async def prepare_nothing() -> tuple[Any, ...]:
    return ()


# ===========================================================================
# THE CHECKS
# ===========================================================================

CHECKS: tuple[ReadBack, ...] = (
    # --- fix 1: the routing predicate ---------------------------------
    ReadBack(
        name="routing: a listed-but-not-TRADING symbol ROUTES, not raises",
        module="app.data.bar_fetch",
        attr="resolve_klines_url_for_symbol",
        prepare="prepare_break_or_settling_symbol",
        check="truthy",
        skip_if_prepare_empty=True,
        why=(
            "Halted (BREAK) and settling (SETTLING) symbols still have "
            "history. Routing asks whether DATA EXISTS, not whether you "
            "may TRADE. Before the two-set split this raised "
            "UnresolvedMarket for symbols whose klines were right there, "
            "which cost TONUSDT its shadow seed."
        ),
    ),
    # --- 1(b): the transport ------------------------------------------
    ReadBack(
        name="transport: the klines client is a RateLimitedClient",
        module="app.core.scoring.mtf_confluence",
        attr="get_rate_client",
        check="isinstance",
        expect="app.data.ratelimit:RateLimitedClient",
        why=(
            "mtf_confluence built a raw httpx.AsyncClient PER CALL with no "
            "limiter, no bucket and no semaphore, while four sibling "
            "modules used the shared RateLimitedClient. A fresh client per "
            "call never reuses a connection, so every scoring cycle "
            "churned one socket per request."
        ),
    ),
    ReadBack(
        name="transport: the connection pool is BOUNDED below httpx's 100",
        module="app.core.scoring.mtf_confluence",
        attr="get_rate_client",
        check="attr_equals",
        attr_path="http._transport._pool._max_connections",
        expect=20,
        why=(
            "An unbounded pool means a 180-request burst opens 100 sockets "
            "at once, which is the churn the fix exists to prevent. NOTE: "
            "this reads a PRIVATE httpx path that already moved once "
            "(0.28.1 has no AsyncClient._limits). If it breaks on an "
            "upgrade, REPAIR THE PATH -- a deleted bound is an unbounded "
            "pool with a passing check."
        ),
    ),
    ReadBack(
        name="transport: klines weight agrees with the binance adapter",
        module="app.core.scoring.mtf_confluence",
        attr="get_rate_client",
        check="attr_equals",
        attr_path="endpoint_weights.klines",
        expect=2,
        why=(
            "Two clients sharing Binance's per-IP budget must agree on "
            "what a request costs, or one is accounting against a budget "
            "shape the other does not recognise."
        ),
    ),
)


async def _run_one(rb: ReadBack) -> tuple[str, str]:
    """Returns (status, detail). status in {PASS, FAIL, SKIP}."""
    try:
        if rb.setup:
            await _maybe_await(_load(rb.setup)())

        args = rb.args
        if rb.prepare:
            args = await _maybe_await(globals()[rb.prepare]())
            if not args and rb.skip_if_prepare_empty:
                return ("SKIP", "prepare found no candidate input")

        fn = _load(f"{rb.module}:{rb.attr}")
        result = await _maybe_await(fn(*args, **rb.kwargs))

        if rb.check == "not_raises":
            return ("PASS", f"returned {result!r}")
        if rb.check == "truthy":
            if result:
                return ("PASS", f"input={args!r} -> {result!r}")
            return ("FAIL", f"input={args!r} returned falsy {result!r}")
        if rb.check == "equals":
            if result == rb.expect:
                return ("PASS", f"== {rb.expect!r}")
            return ("FAIL", f"expected {rb.expect!r}, got {result!r}")
        if rb.check == "isinstance":
            cls = _load(rb.expect)
            if isinstance(result, cls):
                return ("PASS", f"is a {cls.__name__}")
            return ("FAIL", f"expected {cls.__name__}, got {type(result).__name__}")
        if rb.check == "attr_equals":
            assert rb.attr_path is not None
            got = _dig(result, rb.attr_path)
            if got == rb.expect:
                return ("PASS", f"{rb.attr_path} == {got!r}")
            return ("FAIL", f"{rb.attr_path}: expected {rb.expect!r}, got {got!r}")
        if rb.check == "in_":
            if result in rb.expect:
                return ("PASS", f"{result!r} in expected set")
            return ("FAIL", f"{result!r} not in {rb.expect!r}")
        return ("FAIL", f"unknown check kind {rb.check!r}")
    except Exception as e:  # noqa: BLE001
        return ("FAIL", f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


async def main() -> int:
    import datetime as dt

    print("=" * 72)
    print("DEPLOY READ-BACK -- deployed modules, deployed callables")
    print("=" * 72)
    print(f"as-of (UTC): {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}")
    print()
    print(THE_RULE)
    print()

    failures = 0
    skips = 0
    for rb in CHECKS:
        status, detail = await _run_one(rb)
        mark = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}[status]
        print("-" * 72)
        print(f"[{mark}] {rb.name}")
        print(f"       {rb.module}:{rb.attr}  check={rb.check}")
        first, _, rest = detail.partition("\n")
        print(f"       {first}")
        if status == "FAIL":
            failures += 1
            print(f"       WHY THIS CHECK EXISTS: {rb.why}")
            print(f"       {THE_RULE}")
            if rest:
                for line in rest.splitlines()[-12:]:
                    print(f"       | {line}")
        elif status == "SKIP":
            skips += 1
            print("       (no input available; this check verified NOTHING "
                  "on this run)")
    print("-" * 72)
    total = len(CHECKS)
    print(f"{total - failures - skips} passed, {failures} failed, {skips} skipped")
    if failures:
        print()
        print("A FAILED READ-BACK MEANS THE DEPLOY DID NOT TAKE, or the "
              "deployed behaviour differs from what shipped. Exiting 1.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
