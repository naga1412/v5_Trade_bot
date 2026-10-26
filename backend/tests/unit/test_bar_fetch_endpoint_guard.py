"""FU-53 guard: no bar-fetch path may reach SPOT klines for a futures-only symbol.

WHY THIS EXISTS, stated here because a test whose failure explains nothing
gets deleted by whoever hits it next instead of understood.

A ``futures_poll`` symbol is a USDT perpetual with **no spot pair at all**.
``https://api.binance.com/api/v3/klines?symbol=XYZUSDT`` for such a symbol is
not a degraded answer, it is a permanent 400/empty -- every call, forever. So a
bar-fetch path that picks the spot endpoint without consulting the cohort does
not under-perform for that cohort, it returns nothing for it, silently, for as
long as the code lives.

WHAT BROKE WHEN IT DID (#539, root-caused 2026-09-01):
``run_live_prediction`` seeded its history from spot for every symbol. The
entire ``futures_poll`` cohort therefore produced **zero predictions for its
whole existence** and nobody noticed, because the failure path returned an empty
list rather than raising. See
``docs/superpowers/decisions/2026-09-01-futures-poll-seed-was-spot-only.md``.

It then happened twice more in two other files -- ``app.ml.validator`` and
``backend/scripts/mfe_mae_curve``'s range fetcher (which cost the A0 escalation
60 of its 279 symbols). Three incidents in three separate files is not three
mistakes, it is a missing constraint: there is no single bar-fetch entry point,
so every new caller re-makes the choice and the spot endpoint is the one that
looks like the default.

THE GUARD IS THE LOAD-BEARING HALF, NOT THE HELPER. A helper that callers may
bypass is a convention, and the convention has already failed three times. The
fourth instance will land in a file that does not exist yet, so the guard is
written as an **exact** allowlist: a new file that fetches bars without
consulting the cohort fails :func:`test_no_new_file_may_hardcode_a_spot_klines_url`
the moment it is added, whether or not its author ever reads this docstring.

EXEMPTIONS CARRY THEIR ARGUMENT. ``KNOWN_UNMIGRATED`` below is not a blind spot
-- each entry says why it is not migrated and whether it is live-affecting. The
set is asserted EXACTLY in both directions, so removing an entry needs a
deliberate edit here, and two of them are open live findings rather than
accepted behaviour.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


# Resolve the tree from this file, so the guard works from any cwd.
BACKEND = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (BACKEND / "app", BACKEND / "scripts")

# The literal that marks a hand-rolled spot bar fetch. Matched on the path,
# not the host, so a base-url split across two lines is still caught.
SPOT_KLINES_RE = re.compile(r"/api/v3/klines")

# The one cohort with no spot pair. Authoritative definition:
# app/shadow/live_fleet_universe.py:290 -- Cohort = Literal[...].
FUTURES_ONLY_COHORT = "futures_poll"


# --- files that may legitimately name the spot klines endpoint -------------
#
# Each entry states WHY. "It already does" is not a reason.
ALLOWED_SPOT_URL = {
    # OWNS THE COHORT -> ENDPOINT MAPPING. resolve_klines_url is the one
    # place the choice is made, so the two URLs have to be written down
    # here. Every other entry in this set is an exception; this one is the
    # rule.
    "app/data/bar_fetch.py",
    # Owns the spot endpoint. The futures sibling (BinanceFuturesAdapter,
    # base_url https://fapi.binance.com) owns /fapi/v1/klines. A cohort
    # selector has to choose between two concrete adapters somewhere, and
    # this is where they live.
    "app/data/adapters/binance.py",
    # BTC/USDT ONLY, by construction -- it classifies the market regime from
    # BTC daily bars and takes no symbol argument. BTC has a spot pair, so
    # the cohort question does not arise.
    "app/core/regime/market_regime.py",
    # Health-checks the endpoint ITSELF. Naming the URL is the entire point;
    # it fetches no bars for any application purpose.
    "app/healer/system_truth_detector.py",
    "scripts/probe_external_endpoint_healthcheck.py",
    "scripts/probe_system_truth.py",
    # BTC-only probes, same reason as market_regime: no symbol parameter.
    "scripts/probe_btc_trend_filter_test.py",
    "scripts/probe_gate_counterfactual.py",
    "scripts/probe_gate_regime_window.py",
}

# --- offenders NOT migrated by this change, each with its argument ---------
#
# These are real instances of the defect. They are listed rather than fixed
# because each one changes behaviour that needs its own authorization, and a
# silent fix to a live scoring path is exactly the kind of change this project
# requires an operator decision for.
KNOWN_UNMIGRATED = {
    # LIVE-AFFECTING, OPEN FINDING. Fetches spot klines per symbol for the
    # MTF confluence vote and fails OPEN (returns None -> zero-vote
    # degradation), so the whole futures_poll cohort scores as if no
    # higher-timeframe agreed. 26 futures_poll symbols were live in prod on
    # 2026-10-26 with 960 predictions in a 3-day window, so this is active,
    # not latent. NOT fixed here: routing it to fapi would start producing
    # real MTF votes for 26 live symbols, which changes live dispatch and
    # needs an operator decision, not a drive-by edit.
    "app/core/scoring/mtf_confluence.py",
    # MEASUREMENT-INTEGRITY, OPEN FINDING. The variant-lane rolling report
    # fetches spot klines per symbol, so futures_poll trades silently drop
    # out of variant measurement -- the same mechanism that cost the A0
    # escalation 60 of 279 symbols, applied to a report we make decisions
    # from. Not migrated here because it changes reported variant numbers,
    # which is a measurement change and wants to be made deliberately.
    "scripts/shadow_variant_rolling_report.py",
    # Retrospective analysis probes, cohort-blind. Same defect, no
    # production effect: they bias the population they sample by silently
    # dropping futures-only symbols. Migrating them is mechanical and is
    # the obvious follow-up; it is out of this change's named scope.
    "scripts/probe_own_trade_entry_timing.py",
    "scripts/probe_part3_entry_timing_all_trades.py",
}


# ===========================================================================
# G0 -- THE ALLOWLIST IS CLOSED.
# ===========================================================================
# An allowlist that can GROW is a cannot-fail check in waiting: the fifth
# instance of the defect does not fail the guard, it gets appended to the
# guard. The exemption then arrives as an inherited default rather than as a
# decision, which is how every instance of this family has entered the
# codebase so far.
#
# So the contents are pinned here as literals. Adding a sixth exempt file is
# then a deliberate edit to THIS test, visible as a red diff line in review,
# and the reviewer sees the count change rather than one more line in a
# 15-line set they would skim.
CLOSED_SPOT_URL = frozenset({
    "app/data/bar_fetch.py",
    "app/data/adapters/binance.py",
    "app/core/regime/market_regime.py",
    "app/healer/system_truth_detector.py",
    "scripts/probe_external_endpoint_healthcheck.py",
    "scripts/probe_system_truth.py",
    "scripts/probe_btc_trend_filter_test.py",
    "scripts/probe_gate_counterfactual.py",
    "scripts/probe_gate_regime_window.py",
})
CLOSED_UNMIGRATED_SPOT_URL = frozenset({
    "app/core/scoring/mtf_confluence.py",
    "scripts/shadow_variant_rolling_report.py",
    "scripts/probe_own_trade_entry_timing.py",
    "scripts/probe_part3_entry_timing_all_trades.py",
})
CLOSED_FETCH_KLINES = frozenset({
    "app/api/routes/tab1.py",
    "app/data/intermarket_correlations.py",
})
CLOSED_UNMIGRATED_FETCH_KLINES = frozenset({
    "app/shadow/worker.py",
    "app/scanner/batch.py",
})

_CLOSED_MSG = (
    "\n\nTHE ALLOWLIST IS CLOSED ON PURPOSE. An allowlist that can grow is a "
    "check that cannot fail: the next instance of the defect would not fail "
    "this guard, it would be appended to it, and the exemption would arrive "
    "as an inherited default instead of as a decision. That is how all four "
    "known instances of this defect entered the tree.\n\n"
    "If you genuinely need another exemption: change BOTH the working set and "
    "the CLOSED_* literal above, in one commit, with the reason written next "
    "to the entry. The duplication is the point -- it makes the change show up "
    "as a diff a reviewer cannot skim past."
)


def test_the_allowlists_are_closed() -> None:
    """The working sets must equal the pinned literals, exactly.

    This does not check the codebase. It checks the GUARD -- that nobody
    widened the exemptions without saying so. Deliberately separate from the
    scans below, so a widened allowlist is reported as "the guard was
    weakened" rather than silently making the scans pass.
    """
    for name, working, closed in (
        ("ALLOWED_SPOT_URL", ALLOWED_SPOT_URL, CLOSED_SPOT_URL),
        ("KNOWN_UNMIGRATED", KNOWN_UNMIGRATED, CLOSED_UNMIGRATED_SPOT_URL),
        ("ALLOWED_FETCH_KLINES_CALLERS",
         ALLOWED_FETCH_KLINES_CALLERS, CLOSED_FETCH_KLINES),
        ("KNOWN_UNMIGRATED_FETCH_KLINES",
         KNOWN_UNMIGRATED_FETCH_KLINES, CLOSED_UNMIGRATED_FETCH_KLINES),
    ):
        added = sorted(set(working) - set(closed))
        removed = sorted(set(closed) - set(working))
        assert not added, f"{name} GREW by {added}" + _CLOSED_MSG
        assert not removed, (
            f"{name} SHRANK by {removed} -- an exemption was retired but its "
            f"CLOSED_* literal was not updated. Delete it there too, so the "
            f"pinned count keeps meaning something." + _CLOSED_MSG
        )


def test_the_closed_counts_are_what_review_signed_off() -> None:
    """Exact counts, hand-written, not derived from the sets they check.

    ``len(CLOSED_SPOT_URL) == len(CLOSED_SPOT_URL)`` would be a tautology.
    These integers were counted by hand at review time, so they fail if the
    pinned sets themselves are edited -- which is the only remaining way to
    widen the exemptions in a single quiet commit.
    """
    assert len(CLOSED_SPOT_URL) == 9, len(CLOSED_SPOT_URL)
    assert len(CLOSED_UNMIGRATED_SPOT_URL) == 4, len(CLOSED_UNMIGRATED_SPOT_URL)
    assert len(CLOSED_FETCH_KLINES) == 2, len(CLOSED_FETCH_KLINES)
    assert len(CLOSED_UNMIGRATED_FETCH_KLINES) == 2, (
        len(CLOSED_UNMIGRATED_FETCH_KLINES)
    )
    # 4 unmigrated URL sites + 2 unmigrated adapter callers = 6 known-open
    # instances of the defect. A fix that migrates one MUST lower this.
    assert len(CLOSED_UNMIGRATED_SPOT_URL) + len(CLOSED_UNMIGRATED_FETCH_KLINES) == 6


def _py_files() -> list[Path]:
    out: list[Path] = []
    for root in SCAN_ROOTS:
        if root.exists():
            out.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(out)


def _rel(p: Path) -> str:
    return p.relative_to(BACKEND).as_posix()


def _docstring_node_ids(tree: ast.Module) -> set[int]:
    """ids of Constant nodes that are docstrings, which make no requests.

    ``app/shadow/worker.py`` documents the kline row schema by citing
    ``/api/v3/klines`` in prose. A raw text scan flags that, and a guard
    that cries wolf on a comment is a guard people learn to override --
    so the scan reads string literals that could reach an HTTP call and
    skips the ones that document.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(id(body[0].value))
    return out


def _files_naming_spot_klines() -> set[str]:
    """Files with a non-docstring string literal naming the spot path.

    Comments are invisible to ``ast`` and so are skipped for free.
    """
    hits: set[str] = set()
    for path in _py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - not expected in this tree
            continue
        skip = _docstring_node_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
                and SPOT_KLINES_RE.search(node.value)
            ):
                hits.add(_rel(path))
                break
    return hits


# ===========================================================================
# G1 -- the structural guard. This is the one that catches the FOURTH file.
# ===========================================================================
def test_no_new_file_may_hardcode_a_spot_klines_url() -> None:
    """The set of files naming /api/v3/klines is EXACTLY the declared set.

    Asserted in both directions on purpose. "No unexpected file" alone would
    let the list rot: an entry whose file stopped naming the URL would sit
    here forever, and the next reader could not tell a live exemption from a
    stale one.
    """
    found = _files_naming_spot_klines()
    declared = ALLOWED_SPOT_URL | KNOWN_UNMIGRATED

    unexpected = sorted(found - declared)
    stale = sorted(declared - found)

    assert not unexpected, (
        "A BAR-FETCH PATH NAMES THE SPOT KLINES ENDPOINT DIRECTLY:\n  "
        + "\n  ".join(unexpected)
        + "\n\nA futures_poll symbol has NO spot pair, so /api/v3/klines for it "
        "returns nothing -- every call, forever, silently. That is how #539 gave "
        "the entire futures-only cohort zero predictions for its whole existence, "
        "and it has since recurred in two more files.\n\n"
        "Fetch bars through app.data.bar_fetch.fetch_bars(..., symbol_source=...), "
        "which picks the endpoint from the cohort. If this file genuinely must name "
        "the spot URL (it health-checks the endpoint, or it is BTC-only and takes no "
        "symbol argument), add it to ALLOWED_SPOT_URL *with that reason written out*."
    )
    assert not stale, (
        "These files no longer name the spot klines endpoint, so their entry in "
        "ALLOWED_SPOT_URL / KNOWN_UNMIGRATED is stale and must be deleted:\n  "
        + "\n  ".join(stale)
        + "\n\nThe list is asserted exactly so that a migrated file cannot keep "
        "an exemption it no longer needs -- otherwise a future reader cannot tell "
        "which exemptions are still load-bearing."
    )


# ===========================================================================
# G2 -- the call-graph guard. Catches a caller with no URL literal at all.
# ===========================================================================
# app.ml.validator was instance #2 and contains no URL at all: it just called
# an adapter method. A URL scan cannot see that, so the two guards are not
# redundant.
#
# THE METHOD NAME DOES NOT IDENTIFY THE MARKET, and this is itself part of why
# the defect recurred. BOTH adapters expose a method called `fetch_klines`:
#
#   BinanceAdapter.fetch_klines         -> api.binance.com/api/v3/klines   (SPOT)
#   BinanceFuturesAdapter.fetch_klines  -> fapi.binance.com/fapi/v1/klines (FUTURES)
#
# So `x.fetch_klines(sym, tf)` is not reviewable by reading it -- the market
# depends entirely on what `x` is, often bound many lines earlier. This guard
# therefore does NOT try to infer the receiver's type (which would need real
# type inference and would quietly mis-resolve). It flags EVERY call site and
# requires each to be declared below with its market stated. The ambiguity is
# the hazard; naming every call site is the mitigation.
ALLOWED_FETCH_KLINES_CALLERS = {
    # Selects spot vs futures per request, explicitly, with both calls
    # present rather than defaulting to one.
    "app/api/routes/tab1.py",
    # SPOT, correctly: a fixed set of macro majors on 1d, all spot-listed.
    # Takes no cohort and never sees a futures_poll symbol.
    "app/data/intermarket_correlations.py",
}

KNOWN_UNMIGRATED_FETCH_KLINES = {
    # SPOT, cohort-blind. LIVE-AFFECTING, OPEN FINDING. The shadow worker's
    # history seed, and the shadow universe includes futures_poll symbols.
    # Not fixed here: routing it changes which symbols can open shadow
    # trades, i.e. the measurement population, which needs an operator
    # decision rather than a drive-by edit.
    "app/shadow/worker.py",
    # SPOT, cohort-blind. Scanner card path -- lower blast radius than the
    # above (it feeds a UI list, not scoring or dispatch) but same defect.
    "app/scanner/batch.py",
}


def _files_calling_fetch_klines() -> set[str]:
    """Files containing any ``<receiver>.fetch_klines(...)`` call.

    ``ast`` rather than grep so that a mention in a docstring or comment
    cannot match, and so a same-named local function is not confused with a
    method call on an adapter.
    """
    hits: set[str] = set()
    for path in _py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - not expected in this tree
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "fetch_klines"
            ):
                hits.add(_rel(path))
                break
    return hits


def test_every_fetch_klines_call_site_is_declared_with_its_market() -> None:
    """Every adapter bar fetch is declared, because the call cannot be read.

    Instance #2 (app.ml.validator) had no URL literal -- it just called an
    adapter -- so G1 would have missed it entirely. And since both adapters
    name the method ``fetch_klines``, no amount of reading the call line tells
    you which market it reaches. Hence: declare them all.
    """
    found = _files_calling_fetch_klines()
    declared = ALLOWED_FETCH_KLINES_CALLERS | KNOWN_UNMIGRATED_FETCH_KLINES

    unexpected = sorted(found - declared)
    stale = sorted(declared - found)

    assert not unexpected, (
        "UNDECLARED ADAPTER BAR FETCH in:\n  "
        + "\n  ".join(unexpected)
        + "\n\nBoth BinanceAdapter and BinanceFuturesAdapter expose a method "
        "called `fetch_klines`, on SPOT and FUTURES respectively, so this call "
        "line does not say which market it reaches -- that depends on the "
        "receiver. If it is the spot adapter and the symbol can belong to the "
        "futures_poll cohort, the fetch returns an empty list forever and reads "
        "like 'no data yet': that is #539, which cost the futures-only cohort "
        "every prediction it should have made.\n\n"
        "Prefer app.data.bar_fetch.fetch_bars(..., symbol_source=...), which "
        "picks the adapter from the cohort. If this site must call an adapter "
        "directly, declare it above WITH ITS MARKET and the reason the cohort "
        "question does not arise."
    )
    assert not stale, (
        "No longer calls an adapter's fetch_klines; delete the stale entry:\n  "
        + "\n  ".join(stale)
        + "\n\nThe list is exact in both directions so a migrated file cannot "
        "keep an exemption it no longer needs."
    )


# ===========================================================================
# G3/G4 -- the behavioural guards: the helper must route BOTH ways.
# ===========================================================================
def _routed_url(symbol_source: str) -> str:
    """The URL the helper would request for this cohort, with no I/O."""
    from app.data.bar_fetch import resolve_klines_url

    return resolve_klines_url(symbol_source)


def test_futures_only_cohort_routes_to_the_futures_endpoint() -> None:
    url = _routed_url(FUTURES_ONLY_COHORT)
    assert url == "https://fapi.binance.com/fapi/v1/klines", (
        f"cohort {FUTURES_ONLY_COHORT!r} routed to {url!r}. It has no spot pair, "
        "so anything but the fapi endpoint returns nothing for every symbol in it "
        "-- the #539 failure, reproduced."
    )


def test_spot_backed_cohorts_route_to_the_spot_endpoint() -> None:
    """The mirror case, and it is not redundant.

    A helper hardwired to fapi passes the test above while being just as
    wrong: ``established_top20`` symbols are selected by SPOT volume and are
    not all listed as perpetuals. Only asserting both directions pins the
    behaviour to "chooses" rather than "happens to be right once".
    """
    for cohort in ("established_top20", "liquidity_added_spot"):
        url = _routed_url(cohort)
        assert url == "https://api.binance.com/api/v3/klines", (
            f"cohort {cohort!r} routed to {url!r}, but it is spot-backed. A helper "
            "that sends everything to fapi passes the futures case and is still "
            "wrong -- which is why both directions are asserted."
        )


def test_an_unknown_cohort_raises_rather_than_defaulting_to_spot() -> None:
    """The degenerate case with a known answer, and the real lesson of #539.

    #539 was not a wrong endpoint so much as a SILENT default: the spot path
    was what you got when nobody decided. A new cohort added to
    ``live_fleet_universe.Cohort`` without being taught here must therefore
    fail loudly at the first fetch, not inherit spot and quietly return
    nothing.
    """
    with pytest.raises(ValueError, match="unknown cohort"):
        _routed_url("a_cohort_that_does_not_exist")


def test_symbol_routing_uses_the_futures_only_listing_cache() -> None:
    """The cohort-free entry point, for callers with no symbol_source column.

    ``app.ml.validator`` reads ``prediction_validations``, which has no
    cohort column, so it asks the futures-only listing cache instead.
    """
    from app.data import bar_fetch
    from app.shadow import cohort_cache

    cohort_cache._futures_only_cache = {"PERPONLYUSDT"}
    try:
        assert bar_fetch.resolve_klines_url_for_symbol("PERPONLYUSDT") == (
            bar_fetch.FUTURES_KLINES_URL
        )
        assert bar_fetch.resolve_klines_url_for_symbol("BTCUSDT") == (
            bar_fetch.SPOT_KLINES_URL
        )
    finally:
        cohort_cache._reset_for_tests()


@pytest.mark.asyncio
async def test_unclassifiable_symbol_returns_None_not_an_empty_list() -> None:
    """``None`` and ``[]`` must not be the same answer, and this is the crux.

    #539's whole invisibility came from the two being indistinguishable: a
    spot fetch for a futures-only symbol returned ``[]``, which every caller
    read as "no bars yet" rather than "you asked the wrong market". So when
    the market cannot be determined at all, the answer must be a value no
    caller can mistake for data.

    Asserted with ``is None`` rather than falsiness on purpose -- ``[]`` is
    falsy too, so ``assert not bars`` would pass on the exact bug this
    test exists to catch. That is a check that cannot fail.
    """
    from app.data.bar_fetch import fetch_bars_by_symbol
    from app.shadow import cohort_cache

    cohort_cache._reset_for_tests()
    try:
        assert cohort_cache.get_futures_only_cache() is None, (
            "precondition: the cache must be unpopulated for this test to mean "
            "anything -- a populated cache would route normally and the test "
            "would pass without exercising the unclassifiable path"
        )

        bars = await fetch_bars_by_symbol("ANYUSDT", "1h", limit=3)
        assert bars is None, (
            f"got {bars!r}. An unclassifiable symbol must return None, never "
            "[] -- [] is what a wrong-market fetch returns, and conflating "
            "the two is exactly how #539 stayed invisible for the "
            "futures_poll cohort's entire existence."
        )
    finally:
        # Leave the module-level cache in its pristine state even if an
        # assertion above fails. This test deliberately mutates process-wide
        # state, and a guard suite that makes OTHER tests order-dependent
        # would be trading one silent failure class for another.
        cohort_cache._reset_for_tests()


def test_every_declared_cohort_is_routed() -> None:
    """Enumerated from the Literal, not from a hand-copied list.

    A hand-copied list is a second source of truth that drifts; reading the
    annotation means adding a cohort upstream breaks this test here.
    """
    import typing

    from app.shadow.live_fleet_universe import Cohort

    cohorts = typing.get_args(Cohort)
    assert cohorts, "Cohort Literal resolved to no members -- the guard would be vacuous"
    for cohort in cohorts:
        url = _routed_url(cohort)
        assert url.endswith("/klines"), f"cohort {cohort!r} routed to {url!r}"
