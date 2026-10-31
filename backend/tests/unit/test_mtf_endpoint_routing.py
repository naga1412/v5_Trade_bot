"""Defect (a): the MTF endpoint is RESOLVED per symbol, never defaulted.

This module used to build ``https://api.binance.com/api/v3/klines`` for
every symbol it scored. For a futures-only contract that request can
only ever 400, and the prod counter measured exactly that:

    47 scoring cycles, 2026-10-27 -> 2026-10-29
    HTTPStatusError_400   3,387 occurrences   96.50%   23 symbols, 6/6 tfs
    transport exceptions    123 occurrences    3.50%
    429 / 418                 0 occurrences    0.00%

Each 400 became a ``(0, 0.0)`` vote through the fail-open path, which is
indistinguishable from genuine timeframe disagreement in every
downstream consumer including the dispatch gate's veto.

Pre-registered in ``2026-10-27-mtf-status-code-rule.md``, read in
``2026-10-29-mtf-status-code-read.md`` (verdict: MIGRATE), authorised by
the operator 2026-10-29.

THE TEST THAT MATTERS MOST HERE is
:func:`test_a_1000x_contract_routes_to_FUTURES_and_is_not_rewritten`.
Every other test in this file would still pass if someone "helpfully"
added a symbol-name mapping; that one is the reason not to.
"""
from __future__ import annotations

import inspect
from typing import Any

import httpx
import pytest

from app.core.scoring import mtf_confluence as mc
from app.core.scoring.types import Direction
from app.data.bar_fetch import FUTURES_KLINES_URL, SPOT_KLINES_URL


class _RecordingClient:
    """Records every URL it is asked for and returns one usable bar set."""

    def __init__(self, bars: int = 200) -> None:
        self.urls: list[str] = []
        self._bars = bars

    async def get(self, url: str, **_: Any) -> Any:
        self.urls.append(url)
        # A monotonic ramp: enough rows for _vote_for_tf, and the values
        # are irrelevant here because routing is what is under test.
        rows = [
            [i * 60_000, "1", "2", "0.5", str(100 + i), "10",
             0, "0", 0, "0", "0", "0"]
            for i in range(self._bars)
        ]
        # THE `request=` IS LOAD-BEARING, and leaving it off is how this
        # stub was first written. `_fetch_one_tf` calls
        # `resp.raise_for_status()`, and httpx raises RuntimeError -- not
        # an HTTPError -- when the response has no request attached. That
        # escapes the `except (httpx.HTTPError, httpx.TimeoutException)`
        # and lands in gather(return_exceptions=True), so all six
        # timeframes read as failures and compute returns None. The test
        # caught it, which is the only reason it is written down here.
        return httpx.Response(
            200, json=rows, request=httpx.Request("GET", url),
        )


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    mc._reset_fetch_failures_for_tests()
    mc._KLINE_CACHE.clear()
    yield
    mc._reset_fetch_failures_for_tests()
    mc._KLINE_CACHE.clear()


@pytest.fixture
def listings(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Seed the four listing caches the resolver consults, by name.

    Seeded EXPLICITLY rather than through a helper so each test's
    premise is readable at the call site: the resolver's whole contract
    is that it answers only from positive membership, and a test that
    hid which set a symbol was in would not be testing that.
    """
    from app.shadow import cohort_cache

    def seed(
        *,
        futures_only: set[str] | None = None,
        spot: set[str] | None = None,
        routable_futures_only: set[str] | None = None,
        routable_spot: set[str] | None = None,
    ) -> None:
        monkeypatch.setattr(cohort_cache, "_futures_only_cache", futures_only)
        monkeypatch.setattr(cohort_cache, "_spot_listed_cache", spot)
        monkeypatch.setattr(
            cohort_cache, "_routable_futures_only_cache", routable_futures_only,
        )
        monkeypatch.setattr(
            cohort_cache, "_routable_spot_cache", routable_spot,
        )

    return seed


# ===========================================================================
# The two markets
# ===========================================================================

def test_a_futures_only_contract_resolves_to_FAPI(listings: Any) -> None:
    """The whole point of the migration: no more 400 for these."""
    listings(futures_only={"PERPONLYUSDT"}, spot={"BTCUSDT"})
    assert mc._resolve_symbol_url("PERPONLYUSDT") == FUTURES_KLINES_URL


def test_a_spot_listed_symbol_resolves_to_SPOT(listings: Any) -> None:
    """Behaviour-preserving for everything that already worked."""
    listings(futures_only={"PERPONLYUSDT"}, spot={"BTCUSDT"})
    assert mc._resolve_symbol_url("BTCUSDT") == SPOT_KLINES_URL


def test_a_routable_only_symbol_still_resolves(listings: Any) -> None:
    """BREAK / SETTLING symbols have no tradable entry but do have bars.

    The resolver's layered precedence (tradable sets first, routable
    sets only for symbols that would otherwise raise) must reach MTF
    too, or a halted symbol loses its votes for the opposite reason to
    defect (a).
    """
    listings(
        futures_only=set(), spot=set(),
        routable_futures_only={"HALTEDUSDT"}, routable_spot=set(),
    )
    assert mc._resolve_symbol_url("HALTEDUSDT") == FUTURES_KLINES_URL


# ===========================================================================
# THE 1000x TICKER CLASS -- the test that forbids a name mapping
# ===========================================================================

def test_a_1000x_contract_routes_to_FUTURES_and_is_not_rewritten(
    listings: Any,
) -> None:
    """``1000PEPEUSDT`` -> fapi, even though ``PEPEUSDT`` is on spot.

    Binance lists some perpetuals at 1000x the underlying, so the
    CONTRACT is ``1000PEPEUSDT`` while the SPOT PAIR is ``PEPEUSDT``.
    "Has no spot pair" is therefore true OF THE CONTRACT STRING and
    false of the asset.

    ROUTING TO FUTURES IS CORRECT AND NO MAPPING MAY BE ADDED. The
    position is in the 1000x contract; its price, bars and ADX are the
    contract's. Rewriting the symbol to ``PEPEUSDT`` to reach spot would
    fetch a different, 1000x-rescaled instrument on a market we are not
    trading -- numerically offset bars feeding a real scoring vote.

    This test exists because the set difference that produces the
    futures-only cache is a STRING difference, so a future reader who
    notices ``PEPEUSDT`` sitting in the spot set is one short step from
    "fixing" the apparent inconsistency.
    """
    listings(
        futures_only={"1000PEPEUSDT", "1000SHIBUSDT", "1000BONKUSDT"},
        spot={"PEPEUSDT", "SHIBUSDT", "BONKUSDT", "BTCUSDT"},
    )
    for contract in ("1000PEPEUSDT", "1000SHIBUSDT", "1000BONKUSDT"):
        assert mc._resolve_symbol_url(contract) == FUTURES_KLINES_URL, contract
    # ...and the unprefixed asset still goes to spot, which is what makes
    # the pair genuinely ambiguous to a careless reader.
    for asset in ("PEPEUSDT", "SHIBUSDT", "BONKUSDT"):
        assert mc._resolve_symbol_url(asset) == SPOT_KLINES_URL, asset


@pytest.mark.asyncio
async def test_the_1000x_contract_is_FETCHED_from_fapi(listings: Any) -> None:
    """End to end: the request actually leaves for fapi, not just the URL.

    Asserting on ``_resolve_symbol_url`` alone would pass even if the
    resolved value were then discarded at the call site, which is
    precisely the "executes but goes nowhere" shape this repo has now
    confirmed five times.
    """
    listings(futures_only={"1000PEPEUSDT"}, spot={"PEPEUSDT"})
    client = _RecordingClient()

    result = await mc.compute_mtf_confluence(
        "1000PEPEUSDT", Direction.LONG, _http=client,
    )

    assert result is not None, "a resolvable symbol must produce a vote"
    assert len(client.urls) == 6, client.urls
    assert set(client.urls) == {FUTURES_KLINES_URL}, client.urls
    assert SPOT_KLINES_URL not in client.urls


# ===========================================================================
# The unresolvable case: fail open, but COUNTED, and never to spot
# ===========================================================================

def test_an_unresolvable_symbol_returns_None_not_a_spot_default(
    listings: Any,
) -> None:
    listings(futures_only=set(), spot=set())
    assert mc._resolve_symbol_url("NEVERHEARDUSDT") is None


def test_an_unresolvable_symbol_is_counted_once_per_timeframe(
    listings: Any,
) -> None:
    """Six timeframes, six counts, under the kind ``UnresolvedMarket``.

    Hand-derived rather than asserted as an inequality: TIMEFRAMES has
    exactly 6 members, the resolver loops over all of them, so the
    symbol-level counter must read exactly 6 and the detail counter must
    hold exactly 6 keys each at exactly 1.
    """
    listings(futures_only=set(), spot=set())

    mc._resolve_symbol_url("NEVERHEARDUSDT")

    assert mc.get_fetch_failure_counts() == {"NEVERHEARDUSDT": 6}
    detail = mc.get_fetch_failure_detail()
    assert len(detail) == 6, detail
    assert set(detail.values()) == {1}, detail
    assert {tf for _, tf, _ in detail} == set(mc.TIMEFRAMES)
    assert {kind for _, _, kind in detail} == {"UnresolvedMarket"}, detail


@pytest.mark.asyncio
async def test_an_unresolvable_symbol_issues_ZERO_requests(
    listings: Any,
) -> None:
    """Not merely "does not reach spot" -- reaches nothing at all.

    Six requests that cannot succeed are six requests' worth of weight
    and latency spent to learn what the listing cache already knew.
    """
    listings(futures_only=set(), spot=set())
    client = _RecordingClient()

    result = await mc.compute_mtf_confluence(
        "NEVERHEARDUSDT", Direction.LONG, _http=client,
    )

    assert result is None
    assert client.urls == [], client.urls


@pytest.mark.asyncio
async def test_prewarm_skips_an_unresolvable_symbol_and_CONTINUES(
    listings: Any,
) -> None:
    """One unplaceable symbol must not end the sweep for the rest.

    `continue`, not `break` -- the ordering matters, so the unplaceable
    symbol is deliberately FIRST in the list. If the call site broke out
    of the loop this test would see zero requests instead of twelve.
    """
    listings(futures_only=set(), spot={"BTCUSDT", "ETHUSDT"})
    client = _RecordingClient()

    await mc.prewarm_cache(
        ["NEVERHEARDUSDT", "BTCUSDT", "ETHUSDT"],
        deadline_seconds=30.0,
        _http=client,
    )

    # 2 resolvable symbols x 6 timeframes, and nothing for the first.
    assert len(client.urls) == 2 * len(mc.TIMEFRAMES), client.urls
    assert set(client.urls) == {SPOT_KLINES_URL}


# ===========================================================================
# The silent default cannot come back
# ===========================================================================

def test_url_is_required_and_has_no_default() -> None:
    """A default value for `url` would BE defect (a), reintroduced.

    Checked on the signature rather than by behaviour because the
    failure mode is a future edit adding `url: str = SPOT_KLINES_URL`,
    which no behavioural test would notice: every existing caller passes
    it explicitly, so the default would simply sit there unexercised
    until the next caller forgot.
    """
    sig = inspect.signature(mc._fetch_one_tf)
    url = sig.parameters["url"]
    assert url.default is inspect.Parameter.empty, (
        f"`url` acquired a default ({url.default!r}). Any default is a "
        "silent endpoint choice, which is the defect itself."
    )
    assert url.kind is inspect.Parameter.KEYWORD_ONLY, (
        "`url` must stay keyword-only so a positional call cannot pass "
        "a timeframe into it by accident."
    )


# ===========================================================================
# The refresh loop's url map -- one resolve per SYMBOL, not per pair
# ===========================================================================

def test_due_urls_resolve_once_per_SYMBOL_not_per_pair(
    listings: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`due` carries up to 6 pairs per symbol; the resolver sees it once.

    This matters because `_resolve_symbol_url` records one failure PER
    TIMEFRAME on an unresolvable symbol. Resolving per pair would record
    6 x 6 = 36 for a symbol that is merely newer than the last listing
    refresh, which would make the counter unreadable exactly when it is
    being used to tell a routing failure from a transport one.

    Hand-derived: 2 distinct symbols across 7 pairs -> exactly 2 calls.
    """
    listings(futures_only=set(), spot={"BTCUSDT"})
    calls: list[str] = []
    real = mc._resolve_symbol_url

    def counting(symbol: str, *a: Any, **kw: Any) -> Any:
        calls.append(symbol)
        return real(symbol, *a, **kw)

    monkeypatch.setattr(mc, "_resolve_symbol_url", counting)

    due = [
        ("BTCUSDT", "5m"), ("BTCUSDT", "15m"), ("BTCUSDT", "1h"),
        ("BTCUSDT", "4h"), ("BTCUSDT", "1d"), ("BTCUSDT", "1w"),
        ("NEVERHEARDUSDT", "1h"),
    ]
    resolved = mc._resolve_urls_for_due(due)

    assert calls == ["BTCUSDT", "NEVERHEARDUSDT"], calls
    assert resolved == {"BTCUSDT": SPOT_KLINES_URL}, resolved
    # The unresolvable symbol is DROPPED, not mapped to None -- the return
    # type is dict[str, str] and the call site subscripts it directly.
    assert "NEVERHEARDUSDT" not in resolved
    # ...and it was counted once per timeframe by the resolver, exactly once.
    assert mc.get_fetch_failure_counts() == {"NEVERHEARDUSDT": 6}
