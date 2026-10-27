"""Routing asks whether DATA EXISTS, not whether you may TRADE.

THE DEFECT THIS PINS. `fetch_market_listings` filtered on
`status == "TRADING"` and one predicate served two consumers:

  * cohort / fleet SELECTION -- "may I trade this?"  TRADING is correct.
  * bar-fetch ROUTING        -- "does this market have bars?"  It is NOT.

A halted (`BREAK`) or settling (`SETTLING`) symbol is not tradable but
its history still exists, so routing on the TRADING predicate RAISED
`UnresolvedMarket` for symbols whose klines were right there.

Measured 2026-10-27 against public exchangeInfo: TONUSDT, HFTUSDT and
VICUSDT were each spot=BREAK / futures=SETTLING, and 390 USDT symbols
upstream sit in a non-TRADING status (254 spot BREAK, 135 futures
SETTLING, 1 PENDING_TRADING). Two of them were in the live universe.

The fix is TWO SETS, not a widened one: a fix that keeps a single
predicate has not fixed the class, because the next consumer inherits
whichever question the set happens to answer.
"""
from __future__ import annotations

import httpx
import pytest

from app.data import bar_fetch
from app.data.bar_fetch import (
    FUTURES_KLINES_URL,
    SPOT_KLINES_URL,
    UnresolvedMarket,
    resolve_klines_url_for_symbol,
)
from app.shadow import cohort_cache
from app.shadow.live_fleet_universe import fetch_market_listings_full


@pytest.fixture(autouse=True)
def _reset():
    cohort_cache._reset_for_tests()
    yield
    cohort_cache._reset_for_tests()


def _exchange_info_payload():
    """One spot + one futures payload covering every status that matters."""
    spot = {"symbols": [
        {"symbol": "BTCUSDT", "quoteAsset": "USDT", "status": "TRADING"},
        # halted on spot, settling on futures -- the TONUSDT/HFTUSDT shape
        {"symbol": "HFTUSDT", "quoteAsset": "USDT", "status": "BREAK"},
        # never traded: genuinely no bars, must NOT become routable
        {"symbol": "NEWUSDT", "quoteAsset": "USDT",
         "status": "PENDING_TRADING"},
        # wrong quote asset -- out of scope entirely
        {"symbol": "BTCEUR", "quoteAsset": "EUR", "status": "TRADING"},
    ]}
    futures = {"symbols": [
        {"symbol": "BTCUSDT", "quoteAsset": "USDT",
         "contractType": "PERPETUAL", "status": "TRADING"},
        {"symbol": "1000PEPEUSDT", "quoteAsset": "USDT",
         "contractType": "PERPETUAL", "status": "TRADING"},
        {"symbol": "HFTUSDT", "quoteAsset": "USDT",
         "contractType": "PERPETUAL", "status": "SETTLING"},
        {"symbol": "NEWUSDT", "quoteAsset": "USDT",
         "contractType": "PERPETUAL", "status": "PENDING_TRADING"},
    ]}
    return spot, futures


class _FakeHttp:
    def __init__(self, spot, futures):
        self._spot, self._futures = spot, futures

    async def get(self, url, **kw):
        body = self._futures if "fapi" in url else self._spot
        return httpx.Response(
            200, json=body, request=httpx.Request("GET", url),
        )


@pytest.mark.asyncio
async def test_the_two_views_differ_exactly_where_the_bug_was() -> None:
    """Hand-derived from the payload above.

    tradable_spot          = {BTCUSDT}            (HFT is BREAK)
    routable_spot          = {BTCUSDT, HFTUSDT}   (BREAK has history)
    tradable_futures_only  = {1000PEPEUSDT}
    routable_futures_only  = {1000PEPEUSDT}       (HFT is spot-routable,
                                                   so not futures-ONLY)
    NEWUSDT appears in NEITHER view: PENDING_TRADING never traded.
    """
    spot, futures = _exchange_info_payload()
    lst = await fetch_market_listings_full(_FakeHttp(spot, futures))

    assert set(lst.tradable_spot) == {"BTCUSDT"}
    assert set(lst.routable_spot) == {"BTCUSDT", "HFTUSDT"}
    assert set(lst.tradable_futures_only) == {"1000PEPEUSDT"}
    assert set(lst.routable_futures_only) == {"1000PEPEUSDT"}

    assert "NEWUSDT" not in lst.routable_spot, (
        "PENDING_TRADING has never traded, so it has NO bars -- widening "
        "the routing set to include it would trade one false negative for "
        "a false positive"
    )
    assert "NEWUSDT" not in lst.routable_futures_only
    assert "BTCEUR" not in lst.routable_spot, "non-USDT quote is out of scope"


@pytest.mark.asyncio
async def test_a_listed_but_not_TRADING_symbol_ROUTES_instead_of_RAISING(
) -> None:
    """THE REGRESSION TEST. This is the symbol class that was refused.

    Halted and settling symbols still have history; routing asks whether
    DATA EXISTS, not whether you may TRADE. Before the two-set split this
    raised UnresolvedMarket and the caller lost the bars -- which cost
    TONUSDT its shadow seed and would have dead-lettered its validation
    rows.
    """
    spot, futures = _exchange_info_payload()
    await cohort_cache.refresh_futures_only_cache(_FakeHttp(spot, futures))

    url = resolve_klines_url_for_symbol("HFTUSDT")
    assert url == SPOT_KLINES_URL, (
        "a spot-listed-but-halted symbol must route to SPOT klines. "
        "Halted and settling symbols still have history; routing asks "
        "whether data exists, not whether you may trade."
    )


@pytest.mark.asyncio
async def test_selection_still_sees_only_TRADING_symbols() -> None:
    """THE OTHER HALF, and the reason this is two sets and not one wider one.

    If the fix had merely widened the existing set, cohort and fleet
    SELECTION would start treating a settling contract as tradable --
    turning a false negative on data into a false positive on
    tradability, which is far worse. The tradable cache must be unmoved.
    """
    spot, futures = _exchange_info_payload()
    await cohort_cache.refresh_futures_only_cache(_FakeHttp(spot, futures))

    tradable_spot = cohort_cache.get_spot_listed_cache()
    assert tradable_spot == {"BTCUSDT"}, tradable_spot
    assert "HFTUSDT" not in tradable_spot, (
        "a SETTLING/BREAK symbol must never look tradable to cohort "
        "selection -- that is the question TRADING correctly answers"
    )
    # ...while the routing view does include it.
    assert "HFTUSDT" in (cohort_cache.get_routable_spot_cache() or set())


@pytest.mark.asyncio
async def test_a_never_traded_symbol_still_RAISES() -> None:
    """The no-default rule is intact for symbols with genuinely no bars.

    POSITIVE CONTROL on the narrowing: if the routing set had been
    widened to "everything in exchangeInfo", this would route and then
    fetch zero bars, which is the silent-empty-result failure the
    no-default rule exists to prevent.
    """
    spot, futures = _exchange_info_payload()
    await cohort_cache.refresh_futures_only_cache(_FakeHttp(spot, futures))

    with pytest.raises(UnresolvedMarket):
        resolve_klines_url_for_symbol("NEWUSDT")


@pytest.mark.asyncio
async def test_an_unknown_symbol_still_RAISES() -> None:
    """The original FU-53 contract is unchanged: no spot default, ever."""
    spot, futures = _exchange_info_payload()
    await cohort_cache.refresh_futures_only_cache(_FakeHttp(spot, futures))

    with pytest.raises(UnresolvedMarket):
        resolve_klines_url_for_symbol("TOTALLYMADEUPUSDT")


@pytest.mark.asyncio
async def test_a_futures_only_symbol_still_routes_to_fapi() -> None:
    """No regression in the case FU-53 was built for."""
    spot, futures = _exchange_info_payload()
    await cohort_cache.refresh_futures_only_cache(_FakeHttp(spot, futures))

    assert resolve_klines_url_for_symbol("1000PEPEUSDT") == FUTURES_KLINES_URL


def test_the_never_traded_statuses_are_named_not_inferred() -> None:
    """The exclusion list is explicit and minimal.

    Only PRE_TRADING and PENDING_TRADING mean "never traded". BREAK and
    SETTLING must NOT be in it -- putting them there is the original bug.
    """
    from app.shadow.live_fleet_universe import _NO_HISTORY_STATUSES

    assert "PENDING_TRADING" in _NO_HISTORY_STATUSES
    assert "PRE_TRADING" in _NO_HISTORY_STATUSES
    assert "BREAK" not in _NO_HISTORY_STATUSES, (
        "BREAK means halted, not never-traded -- it HAS history"
    )
    assert "SETTLING" not in _NO_HISTORY_STATUSES, (
        "SETTLING means the contract is winding down -- it HAS history"
    )
    assert "TRADING" not in _NO_HISTORY_STATUSES


def test_bar_fetch_reads_the_ROUTING_accessors_not_the_tradable_ones() -> None:
    """Structural: the fix is which accessor is called, so assert that.

    A revert that pointed resolve_klines_url_for_symbol back at
    get_futures_only_cache/get_spot_listed_cache would restore the bug
    while leaving every behavioural test above passing IF the two caches
    happened to agree in the fixture. Pinning the call site removes that
    dependence.
    """
    import ast
    import pathlib

    src = pathlib.Path(bar_fetch.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and n.name == "resolve_klines_url_for_symbol"
    )
    called = {
        n.func.id for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "get_routable_futures_only_cache" in called, called
    assert "get_routable_spot_cache" in called, called
    # ALL FOUR, and the tradable ones FIRST. An earlier version of this
    # fix consulted only the routable sets and asserted the tradable ones
    # were absent -- which silently changed precedence for a symbol BREAK
    # on spot and TRADING on futures, sending it to the HALTED spot pair
    # instead of the healthy futures contract. The layering is the fix,
    # so the layering is what is pinned.
    assert "get_futures_only_cache" in called, called
    assert "get_spot_listed_cache" in called, called

    src_lines = src.splitlines()
    def _line_of(name: str) -> int:
        for n in ast.walk(fn):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == name):
                return n.lineno
        raise AssertionError(name)
    assert _line_of("get_futures_only_cache") < _line_of(
        "get_routable_futures_only_cache"
    ), "the TRADABLE futures lookup must precede the routable one"
    assert _line_of("get_spot_listed_cache") < _line_of(
        "get_routable_spot_cache"
    ), "the TRADABLE spot lookup must precede the routable one"
    assert len(src_lines) > 0


# ---------------------------------------------------------------------------
# PRECEDENCE -- the sibling to "the tradable view is unmoved".
# ---------------------------------------------------------------------------
#
# Hand-derived over EVERY status pairing, OLD resolver vs the layered one.
# "OLD" is the pre-2026-10-27 rule: tradable_futures_only -> FUTURES,
# else tradable_spot -> SPOT, else raise.
#
# The pairing that matters is (spot=BREAK, futures=TRADING). The first
# version of this fix resolved on the ROUTABLE sets alone, which sent that
# symbol to the HALTED spot pair instead of the healthy trading futures
# contract -- worse data, silently. Layering removes it, and this table is
# the proof that the change is STRICTLY ADDITIVE: identical wherever the
# old rule had an answer, new answers only where it raised.
_PRECEDENCE: tuple[tuple[str | None, str | None, str, str], ...] = (
    # spot status,   futures status,  OLD,       LAYERED
    ("TRADING", "TRADING", "SPOT", "SPOT"),
    ("TRADING", "SETTLING", "SPOT", "SPOT"),
    ("TRADING", None, "SPOT", "SPOT"),
    ("TRADING", "PENDING_TRADING", "SPOT", "SPOT"),
    ("BREAK", "TRADING", "FUTURES", "FUTURES"),
    ("PENDING_TRADING", "TRADING", "FUTURES", "FUTURES"),
    (None, "TRADING", "FUTURES", "FUTURES"),
    # --- below: the old rule RAISED; these are the intended new answers ---
    ("BREAK", "SETTLING", "raise", "SPOT"),
    ("BREAK", None, "raise", "SPOT"),
    (None, "SETTLING", "raise", "FUTURES"),
    # --- below: still unresolvable, by design ---
    ("PENDING_TRADING", "PENDING_TRADING", "raise", "raise"),
    (None, None, "raise", "raise"),
)


def _payload(spot_status, futures_status):
    spot = {"symbols": []}
    futures = {"symbols": []}
    if spot_status is not None:
        spot["symbols"].append(
            {"symbol": "DUALUSDT", "quoteAsset": "USDT",
             "status": spot_status}
        )
    if futures_status is not None:
        futures["symbols"].append(
            {"symbol": "DUALUSDT", "quoteAsset": "USDT",
             "contractType": "PERPETUAL", "status": futures_status}
        )
    return spot, futures


@pytest.mark.parametrize(
    ("spot_status", "futures_status", "old", "expected"), _PRECEDENCE
)
@pytest.mark.asyncio
async def test_routing_precedence_is_strictly_additive(
    spot_status, futures_status, old, expected,
) -> None:
    """Every pairing: identical to OLD, or a new answer where OLD raised.

    The assertion message states which of the two it is, so a failure
    distinguishes "you broke an existing route" from "you changed a new
    answer" without reading this table.
    """
    spot, futures = _payload(spot_status, futures_status)
    await cohort_cache.refresh_futures_only_cache(_FakeHttp(spot, futures))

    want = {
        "SPOT": SPOT_KLINES_URL,
        "FUTURES": FUTURES_KLINES_URL,
    }.get(expected)

    if want is None:
        with pytest.raises(UnresolvedMarket):
            resolve_klines_url_for_symbol("DUALUSDT")
        return

    got = resolve_klines_url_for_symbol("DUALUSDT")
    kind = "SPOT" if got == SPOT_KLINES_URL else "FUTURES"
    if old == expected:
        assert kind == expected, (
            f"spot={spot_status} futures={futures_status}: this route "
            f"EXISTED BEFORE and must be unchanged ({old}), got {kind}. "
            "A dual-listed symbol must not be silently re-pointed; a "
            "settling or halted market has thinner prices than a healthy "
            "one and the switch would be invisible."
        )
    else:
        assert kind == expected, (
            f"spot={spot_status} futures={futures_status}: the old rule "
            f"RAISED here and the intended new answer is {expected}, got "
            f"{kind}."
        )


@pytest.mark.asyncio
async def test_the_dual_listed_case_the_order_asked_about() -> None:
    """Spot TRADING + futures SETTLING must still route to SPOT.

    Called out explicitly rather than left inside the table, because it
    is the case the review asked about: a settling perp near expiry has
    thin prices, and routing a healthy spot pair to it would be a silent
    data-quality regression.

    It is unchanged because the set is futures-ONLY -- a difference --
    so a symbol present in both markets is in NEITHER futures-only set
    and falls through to spot. The subtraction is what preserves it.
    """
    spot, futures = _payload("TRADING", "SETTLING")
    await cohort_cache.refresh_futures_only_cache(_FakeHttp(spot, futures))
    assert resolve_klines_url_for_symbol("DUALUSDT") == SPOT_KLINES_URL


@pytest.mark.asyncio
async def test_the_mirror_case_that_the_first_fix_DID_break() -> None:
    """Spot BREAK + futures TRADING must route to FUTURES.

    THE REGRESSION THE REVIEW'S QUESTION SURFACED, kept as its own test
    because it is the one that actually broke. Resolving on the routable
    sets alone sent this to the halted spot pair: the symbol is in both
    routable sets, so it is in neither routable futures-ONLY set, and it
    fell through to routable spot -- which, unlike the tradable spot set,
    answers for halted pairs.

    Layering fixes it: the TRADABLE futures-only set still contains this
    symbol (futures TRADING, spot not TRADING) and is consulted first.
    """
    spot, futures = _payload("BREAK", "TRADING")
    await cohort_cache.refresh_futures_only_cache(_FakeHttp(spot, futures))
    got = resolve_klines_url_for_symbol("DUALUSDT")
    assert got == FUTURES_KLINES_URL, (
        "a symbol halted on spot but TRADING on futures must use the "
        "healthy futures contract, not the halted spot pair"
    )
