"""Phase 4 (2026-08-17 redraft) -- live_fleet_universe liquidity-floor
selector, hysteresis, cold-start seeding, and the open-position override.

Fixture pattern mirrors tests/healer/test_detectors.py: per-suite SQLite
in-memory schema stand-ins (Postgres-only types swapped for SQLite
equivalents), seeded per test with exactly the row shape needed to
trigger (or not trigger) the behavior under test.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import patch

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.ratelimit import RateLimitedClient, TokenBucket
from app.shadow import live_fleet_universe as lfu_mod
from app.shadow.live_fleet_universe import (
    get_open_position_symbols,
    has_open_position,
    load_live_fleet_universe,
    refresh_live_fleet_universe,
)
from app.ws import keepalive

_BookSide = list[tuple[str, str]]
_DepthProvider = Callable[[str], tuple[_BookSide, _BookSide]]


@pytest.fixture(autouse=True)
def _no_sample_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production samples check_liquidity N_SAMPLES=5 times per candidate
    with a ~10s gap between samples. SAMPLE_GAP_SECONDS is read as a bare
    module-global inside refresh_live_fleet_universe's sampling loop, so
    it is resolved from app.shadow.live_fleet_universe's namespace at
    call time (not captured at def time) -- monkeypatching the module
    attribute here is sufficient to collapse it to 0 for every test in
    this file without touching production code."""
    monkeypatch.setattr(lfu_mod, "SAMPLE_GAP_SECONDS", 0.0)


@pytest.fixture
async def session_factory():
    """In-memory SQLite standing in for live_fleet_universe, live_trades,
    shadow_open_positions, and asset_universe (the last only needed by
    the cold-start-seed tests). Types are SQLite equivalents of the
    Postgres migration's real column types -- tests only exercise the
    columns the code actually reads/writes."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE live_fleet_universe ("
            "symbol TEXT NOT NULL, cohort TEXT NOT NULL, "
            "qvol_24h REAL NOT NULL, spread_bps REAL NOT NULL, "
            "depth_0_5pct_usdt REAL NOT NULL, "
            "snapshot_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (symbol, snapshot_at))"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE live_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'closed')"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE shadow_open_positions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL UNIQUE)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE asset_universe ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "quote_volume_usd_24h REAL NOT NULL, "
            "rank INTEGER NOT NULL, "
            "snapshot_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        # Migration 0041 (2026-08-30): the frozen pure-classifier input.
        # Empty by default -- tests that need a symbol classified
        # established_top20 seed it explicitly via _seed_baseline below,
        # matching production's real "not in this table -> not
        # established_top20" behavior for anything left unseeded.
        await conn.execute(sa.text(
            "CREATE TABLE cohort_baseline_symbols ("
            "symbol TEXT PRIMARY KEY, "
            "pred_distinct_days INTEGER NOT NULL DEFAULT 0, "
            "pred_n INTEGER NOT NULL DEFAULT 0, "
            "frozen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_baseline(session_factory, symbols: list[str]) -> None:
    """Populate cohort_baseline_symbols with exactly the given symbols --
    the pure classifier's sole input. Mirrors production's real seeded-
    migration shape (see migration 0041) without pulling in all 73 real
    symbols for tests that only care about one or two."""
    async with session_factory() as session:
        for sym in symbols:
            await session.execute(sa.text(
                "INSERT INTO cohort_baseline_symbols (symbol) VALUES (:s)"
            ), {"s": sym})
        await session.commit()


def _rate_client(transport: httpx.MockTransport) -> RateLimitedClient:
    http = httpx.AsyncClient(transport=transport)
    return RateLimitedClient(
        exchange="binance_futures", http=http,
        buckets={"default": TokenBucket(capacity=2400.0, refill_per_sec=40.0)},
    )


def _mock_transport(
    *,
    futures_symbols: list[str],
    spot_symbols: list[str],
    tickers: list[dict],
    depth_provider: _DepthProvider,
) -> httpx.MockTransport:
    """Serves exchangeInfo (futures + spot), 24h ticker, and depth.

    The ticker endpoint is hit TWO different ways by production code and
    must respond with two different shapes -- this is the fix for a
    genuine ambiguity in the plan doc's own illustrative test handler,
    which returned the same list-shaped body regardless of caller:

      - refresh_live_fleet_universe's own bulk prefilter call has NO
        `symbol` query param and expects a LIST of per-symbol ticker
        dicts (`[{"symbol": ..., "quoteVolume": ...}, ...]`).
      - check_liquidity's per-candidate call (via rate_client, one call
        per sample) passes `params={"symbol": sym}` and expects a SINGLE
        object (`{"symbol": ..., "quoteVolume": ...}`) -- calling
        `.json()["quoteVolume"]` on a list raises TypeError.

    Branch on presence of the `symbol` query param to serve the right
    shape to each caller.
    """
    ticker_by_symbol = {t["symbol"]: t for t in tickers}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/fapi/v1/exchangeInfo":
            return httpx.Response(200, json={"symbols": [
                {"symbol": s, "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"}
                for s in futures_symbols
            ]})
        if path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json={"symbols": [
                {"symbol": s, "quoteAsset": "USDT", "status": "TRADING"} for s in spot_symbols
            ]})
        if path == "/fapi/v1/ticker/24hr":
            sym = req.url.params.get("symbol")
            if sym:
                return httpx.Response(200, json=ticker_by_symbol[sym])
            return httpx.Response(200, json=tickers)
        if path == "/fapi/v1/depth":
            sym = req.url.params["symbol"]
            bids, asks = depth_provider(sym)
            return httpx.Response(200, json={"bids": bids, "asks": asks})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def _deep_book(_sym: str) -> tuple[_BookSide, _BookSide]:
    """Always passes spread (2bps) and depth ($20M-scale notional)."""
    return ([("99.99", "10000")], [("100.01", "10000")])


def _thin_book(_sym: str) -> tuple[_BookSide, _BookSide]:
    """Always fails depth SEVERELY -- ~$2 notional, far under both the
    $50k floor and the 2026-08-20 fast-exit severity trigger (50% of
    floor = $25k). Tests written before that trigger existed use this
    for "any kind of failure" generically; several of them are
    currently-a-member (exit-path) tests where that now also means
    "triggers fast-exit on the first sample" -- verify that's still
    the INTENDED outcome for a given test (immediate exit is exactly
    what a severe failure should do) rather than assuming unchanged
    5-sample-loop behavior. Use _marginal_thin_book below for tests
    that specifically want a near-miss that should NOT fast-exit."""
    return ([("99.99", "0.01")], [("100.01", "0.01")])


def _marginal_thin_book(_sym: str) -> tuple[_BookSide, _BookSide]:
    """Fails depth (below the $50k floor) but NOT severely -- ~$39k,
    78% of the floor, comfortably above the fast-exit severity trigger
    (50% of floor = $25k). Spread stays well within its own floor (2bps).
    For tests exercising the plain unanimous-5-of-5 marginal-miss
    hysteresis in isolation from the 2026-08-20 severity dimension."""
    return ([("99.99", "195")], [("100.01", "195")])


def _pattern_depth_provider(
    patterns: dict[str, list[bool]], *, fail_book: _DepthProvider = _thin_book,
) -> _DepthProvider:
    """Returns a depth_provider that replays a per-symbol pass/fail
    sequence across successive calls (one call per sample) -- lets tests
    hit an exact N-of-5 pass count deterministically. ``fail_book``
    defaults to the severe _thin_book (pre-2026-08-20 behavior); pass
    _marginal_thin_book for tests that need failures that do NOT trigger
    the fast-exit severity path."""
    call_counts: dict[str, int] = dict.fromkeys(patterns, 0)

    def provider(sym: str) -> tuple[_BookSide, _BookSide]:
        idx = call_counts[sym]
        call_counts[sym] += 1
        should_pass = patterns[sym][idx]
        return _deep_book(sym) if should_pass else fail_book(sym)

    provider.call_counts = call_counts  # type: ignore[attr-defined]
    return provider


# ---------------------------------------------------------------------
# 1. Cohort classification -- pure function of the frozen baseline
# (2026-08-30 rewrite; see module docstring's "Cohort classification").
# Replaces the old legacy_top20/asset_universe cold-start seed AND the
# open-position-rescue path's hardcoded established_top20 default, both
# of which were found to fabricate lineage on real staging data.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_established_top20_comes_from_frozen_baseline_not_legacy_fleet(
    session_factory,
) -> None:
    """A symbol in cohort_baseline_symbols gets established_top20 --
    regardless of whether asset_universe (the OLD, now-irrelevant,
    signal) says anything about it at all. A symbol passing the floor
    but absent from the frozen baseline gets liquidity_added_spot, even
    if asset_universe would have ranked it top-20 by volume today --
    asset_universe.rank is the selector's INPUT ranking, not evidence of
    real fleet membership (2026-08-30 investigation: ranks 21-30 were
    ranked but never actually streamed)."""
    await _seed_baseline(session_factory, ["BTCUSDT"])
    async with session_factory() as session:
        # NEWUSDT ranks #1 by volume in today's asset_universe -- this
        # must NOT matter; it's not in the frozen baseline.
        await session.execute(sa.text(
            "INSERT INTO asset_universe (symbol, quote_volume_usd_24h, rank, snapshot_at) "
            "VALUES ('NEWUSDT', 2000000000, 1, '2026-08-16T00:00:00')"
        ))
        await session.commit()

    tickers = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000000000"},
        {"symbol": "NEWUSDT", "quoteVolume": "25000000"},
    ]
    transport = _mock_transport(
        futures_symbols=["BTCUSDT", "NEWUSDT"],
        spot_symbols=["BTCUSDT", "NEWUSDT"],
        tickers=tickers,
        depth_provider=_deep_book,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert by_symbol["BTCUSDT"].cohort == "established_top20"
    assert by_symbol["NEWUSDT"].cohort == "liquidity_added_spot"


@pytest.mark.asyncio
async def test_cohort_recomputed_fresh_every_refresh_not_sticky(session_factory) -> None:
    """The old code inherited cohort from `prior` for any already-a-member
    symbol ("sticky"), which is exactly what let real churn corrupt tags
    (a symbol exiting and re-entering permanently lost its lineage,
    since the legacy-seed branch was cold-start-only). The new
    classifier has no such branch: it recomputes from the baseline table
    on every single call. Proven here by DIRECTLY EDITING a persisted
    prior row's cohort to something the classifier would never itself
    produce, then confirming the next refresh overwrites it back to
    what the frozen baseline actually says -- if any sticky-inheritance
    path remained, the corrupted value would survive unchanged."""
    await _seed_baseline(session_factory, ["BTCUSDT"])
    async with session_factory() as session:
        # A prior row exists, but with an cohort the classifier itself
        # would never assign to BTCUSDT (it's in the baseline, so the
        # classifier always says established_top20) -- simulates
        # corruption from the old buggy code, or simply a stale row.
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('BTCUSDT', 'liquidity_added_spot', 1000000000, 1.0, 100000, "
            "'2026-08-16T00:00:00')"
        ))
        await session.commit()

    tickers = [{"symbol": "BTCUSDT", "quoteVolume": "1000000000"}]
    transport = _mock_transport(
        futures_symbols=["BTCUSDT"], spot_symbols=["BTCUSDT"],
        tickers=tickers, depth_provider=_deep_book,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    # NOT sticky -- the classifier overwrites the corrupted prior value
    # with what the frozen baseline actually says, every single call.
    assert by_symbol["BTCUSDT"].cohort == "established_top20"


# ---------------------------------------------------------------------
# 1b. Cold-start single-sample fast path (Task 5e, ratified 2026-08-19,
# correcting Task 5c the same day -- see
# docs/superpowers/decisions/2026-08-19-live-fleet-universe-never-
# scheduled-incident.md's Implementation note). Task 5c's original fix
# only lowered the pass-count threshold (3-of-5 -> 1-of-5) while leaving
# the 5-sample, ~10s-apart sleep loop itself unconditional, so a
# cold-start sweep still took the full ~50-60 minutes across the
# qualifying market. These tests now prove the SPEED property directly
# (exactly 1 check_liquidity call per candidate, zero sleeps), not just
# the pass/fail outcome -- a test that only checked "the symbol got
# admitted" would not have caught Task 5c's regression.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cold_start_admits_on_single_pass(session_factory) -> None:
    """No prior live_fleet_universe snapshot at all -- a candidate that
    passes its ONE cold-start sample is admitted, taking exactly 1
    check_liquidity call (not 5). The normal entry bar (>=3 of 5, see
    test_entry_requires_three_of_five_passes below) does not apply on
    the very first-ever refresh: there is no hysteresis history to
    require 3-of-5 against yet, and no 5-sample loop to run at all."""
    tickers = [{"symbol": "COLDUSDT", "quoteVolume": "25000000"}]
    provider = _pattern_depth_provider({
        "COLDUSDT": [True],  # single sample, passes -> admitted (cold start)
    })
    transport = _mock_transport(
        futures_symbols=["COLDUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=provider,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "COLDUSDT" in by_symbol
    # The speed property: exactly 1 sample, not 5. This is the assertion
    # that actually catches a regression back to Task 5c's mistake.
    assert provider.call_counts["COLDUSDT"] == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cold_start_rejects_on_single_fail(session_factory) -> None:
    """Companion to the admit case above: a candidate that fails its ONE
    cold-start sample is rejected outright -- admission is that single
    sample's pass/fail, full stop, with no threshold to weigh it against.
    Still exactly 1 check_liquidity call, proving the speed property
    holds on the rejection path too, not just the admission path."""
    tickers = [{"symbol": "COLDUSDT", "quoteVolume": "25000000"}]
    provider = _pattern_depth_provider({
        "COLDUSDT": [False],  # single sample, fails -> rejected (cold start)
    })
    transport = _mock_transport(
        futures_symbols=["COLDUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=provider,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "COLDUSDT" not in by_symbol
    assert provider.call_counts["COLDUSDT"] == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cold_start_single_sample_does_not_apply_once_prior_exists(session_factory) -> None:
    """Regression guard proving the single-sample fast path is scoped to
    cold start only: with a non-empty table (any single prior row makes
    `prior` truthy), a candidate goes back through the full 5-sample
    loop and the unchanged >=3-of-5 entry bar -- a lone pass (1 of 5) is
    correctly rejected, and it takes all 5 samples to determine that
    (contrast with the cold-start case above, which takes exactly 1)."""
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('ANCHORUSDT', 'established_top20', 500000000, 1.0, 100000, "
            "'2026-08-16T00:00:00')"
        ))
        await session.commit()

    tickers = [{"symbol": "COLDUSDT", "quoteVolume": "25000000"}]
    provider = _pattern_depth_provider({
        "COLDUSDT": [True, False, False, False, False],  # 1 of 5 -> rejected (warm)
    })
    transport = _mock_transport(
        futures_symbols=["COLDUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=provider,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "COLDUSDT" not in by_symbol
    # Warm refresh: the full 5-sample loop runs (unaffected by Task 5e --
    # only the cold-start branch changed), unlike the 1-call cold-start
    # case above.
    assert provider.call_counts["COLDUSDT"] == 5  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cold_start_takes_exactly_one_sample_per_candidate_no_sleep(session_factory) -> None:
    """Proves the SPEED property directly and exhaustively across an
    entire cold-start sweep (multiple candidates, not just one symbol):
    every candidate gets exactly 1 check_liquidity call, and
    asyncio.sleep(SAMPLE_GAP_SECONDS) is never invoked at all during the
    cold-start branch -- a single sample never needs the inter-sample
    gap. This is the direct regression test for Task 5c's actual defect:
    it lowered the pass-count threshold but left the 5-sample,
    ~10s-apart sleep loop unconditional, so a cold-start sweep still
    took ~50-60 minutes across the ~70+ qualifying candidates instead of
    the "minutes" the operator's ruling required. Monkeypatching
    asyncio.sleep to fail loudly if called is a stronger guarantee than
    counting calls: it holds even if some future change reintroduces a
    sleep this test's call-count assertions didn't anticipate."""
    tickers = [
        {"symbol": "COLDAUSDT", "quoteVolume": "25000000"},
        {"symbol": "COLDBUSDT", "quoteVolume": "30000000"},
        {"symbol": "COLDCUSDT", "quoteVolume": "40000000"},
    ]
    provider = _pattern_depth_provider({
        "COLDAUSDT": [True],
        "COLDBUSDT": [False],
        "COLDCUSDT": [True],
    })
    transport = _mock_transport(
        futures_symbols=["COLDAUSDT", "COLDBUSDT", "COLDCUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=provider,
    )
    http = httpx.AsyncClient(transport=transport)

    async def _fail_if_called(_seconds: float) -> None:
        raise AssertionError(
            "asyncio.sleep must never be called during the cold-start "
            "single-sample branch -- a single sample never needs the "
            "inter-sample gap (Task 5e regression guard for Task 5c's "
            "actual defect: threshold was relaxed but the 5-sample "
            "sleep loop was left unconditional)."
        )

    with patch.object(asyncio, "sleep", _fail_if_called):
        entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "COLDAUSDT" in by_symbol
    assert "COLDBUSDT" not in by_symbol
    assert "COLDCUSDT" in by_symbol
    assert provider.call_counts["COLDAUSDT"] == 1  # type: ignore[attr-defined]
    assert provider.call_counts["COLDBUSDT"] == 1  # type: ignore[attr-defined]
    assert provider.call_counts["COLDCUSDT"] == 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------
# 2. Entry hysteresis: >=3 of 5
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_requires_three_of_five_passes(session_factory) -> None:
    """A symbol passing exactly 3/5 samples enters; a symbol passing only
    2/5 does not. Neither symbol is a prior member -- an unrelated prior
    row (ANCHORUSDT, not part of this refresh's candidate set) is seeded
    purely so `prior` is non-empty and cold-start seeding logic (which is
    orthogonal to the entry threshold) doesn't fire, isolating the
    entry-threshold behavior specifically."""
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('ANCHORUSDT', 'established_top20', 500000000, 1.0, 100000, "
            "'2026-08-16T00:00:00')"
        ))
        await session.commit()

    tickers = [
        {"symbol": "THREEUSDT", "quoteVolume": "25000000"},
        {"symbol": "TWOUSDT", "quoteVolume": "25000000"},
    ]
    provider = _pattern_depth_provider({
        "THREEUSDT": [True, True, False, True, False],   # 3 of 5 -> enters
        "TWOUSDT": [True, False, True, False, False],     # 2 of 5 -> stays out
    })
    transport = _mock_transport(
        futures_symbols=["THREEUSDT", "TWOUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=provider,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "THREEUSDT" in by_symbol
    assert "TWOUSDT" not in by_symbol
    assert provider.call_counts["THREEUSDT"] == 5  # type: ignore[attr-defined]
    assert provider.call_counts["TWOUSDT"] == 5  # type: ignore[attr-defined]


# ---------------------------------------------------------------------
# 3. Exit hysteresis: unanimous 5 of 5 fails
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_requires_unanimous_five_of_five_fails(session_factory) -> None:
    """A symbol already IN the universe (seeded via a prior snapshot row)
    that fails 4/5 samples is RETAINED (not unanimous); the same-shaped
    symbol failing 5/5 EXITS. Both start as existing members so this
    isolates the exit-side threshold specifically (contrast with the
    entry-side test above). Uses _marginal_thin_book (not _thin_book) for
    the failing samples -- a near-miss, not a collapse -- to isolate the
    unanimous-hysteresis threshold from the 2026-08-20 fast-exit severity
    dimension (see the dedicated fast-exit tests below for that path)."""
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) VALUES "
            "('FOURFAILUSDT', 'liquidity_added_spot', 25000000, 2.0, 60000, '2026-08-16T00:00:00'), "
            "('FIVEFAILUSDT', 'liquidity_added_spot', 25000000, 2.0, 60000, '2026-08-16T00:00:00')"
        ))
        await session.commit()

    tickers = [
        {"symbol": "FOURFAILUSDT", "quoteVolume": "25000000"},
        {"symbol": "FIVEFAILUSDT", "quoteVolume": "25000000"},
    ]
    provider = _pattern_depth_provider({
        "FOURFAILUSDT": [True, False, False, False, False],  # 1 pass -> retained
        "FIVEFAILUSDT": [False, False, False, False, False],  # 0 pass -> exits
    }, fail_book=_marginal_thin_book)
    transport = _mock_transport(
        futures_symbols=["FOURFAILUSDT", "FIVEFAILUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=provider,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "FOURFAILUSDT" in by_symbol
    assert "FIVEFAILUSDT" not in by_symbol
    # A retained member goes through the main loop, so its cohort is
    # freshly recomputed (2026-08-30: no longer sticky) -- FOURFAILUSDT
    # is futures-only here (spot_symbols=[]) and not in the frozen
    # baseline, so the pure classifier says futures_poll, regardless of
    # what its seeded prior row said (the prior seed's cohort value is
    # irrelevant to this test's actual purpose: exit-threshold counting).
    assert by_symbol["FOURFAILUSDT"].cohort == "futures_poll"
    assert provider.call_counts["FOURFAILUSDT"] == 5  # type: ignore[attr-defined]
    assert provider.call_counts["FIVEFAILUSDT"] == 5  # type: ignore[attr-defined]


# ---------------------------------------------------------------------
# 3b. Fast-exit severity trigger (2026-08-20, operator ruling following
# the 73-vs-42 universe-discrepancy investigation): a SEVERE single-sample
# failure (depth < 50% of floor, or spread > 2x max) exits immediately
# without waiting for the other N_SAMPLES-1 -- caps worst-case severe-
# failure exit lag to one refresh cycle instead of ~5 cycles (~30h).
# ---------------------------------------------------------------------


def _severe_depth_book(_sym: str) -> tuple[_BookSide, _BookSide]:
    """Depth ~$10k -- 20% of the $50k floor, well under the 50% severe
    trigger. Spread stays tight (2bps) so ONLY depth is severe."""
    return ([("99.99", "50")], [("100.01", "50")])


def _severe_spread_book(_sym: str) -> tuple[_BookSide, _BookSide]:
    """Spread ~20bps -- 4x the 5bps max, well over the 2x severe trigger.
    Depth stays deep ($1M-scale) so ONLY spread is severe."""
    return ([("99.90", "10000")], [("100.10", "10000")])


@pytest.mark.asyncio
async def test_fast_exit_on_severe_depth_failure_single_sample(session_factory) -> None:
    """A currently-a-member symbol whose FIRST sample shows severe depth
    collapse exits immediately -- exactly 1 check_liquidity call, not the
    full 5, and it's excluded from the result regardless of what the
    remaining (unfetched) samples would have shown."""
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('COLLAPSEUSDT', 'liquidity_added_spot', 25000000, 2.0, 60000, "
            "'2026-08-16T00:00:00')"
        ))
        await session.commit()

    tickers = [{"symbol": "COLLAPSEUSDT", "quoteVolume": "25000000"}]
    provider = _pattern_depth_provider({
        "COLLAPSEUSDT": [False, False, False, False, False],
    }, fail_book=_severe_depth_book)
    transport = _mock_transport(
        futures_symbols=["COLLAPSEUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=provider,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "COLLAPSEUSDT" not in by_symbol
    # The speed property: fast-exit fires on sample 1, never reaching
    # samples 2-5 -- this is the assertion that actually distinguishes
    # fast-exit from "it happened to fail all 5 anyway".
    assert provider.call_counts["COLLAPSEUSDT"] == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_fast_exit_on_severe_spread_failure_single_sample(session_factory) -> None:
    """Same as the depth case, but severity comes from spread blowing out
    past 2x the max instead of depth collapsing."""
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('BLOWNOUTUSDT', 'liquidity_added_spot', 25000000, 2.0, 60000, "
            "'2026-08-16T00:00:00')"
        ))
        await session.commit()

    tickers = [{"symbol": "BLOWNOUTUSDT", "quoteVolume": "25000000"}]
    provider = _pattern_depth_provider({
        "BLOWNOUTUSDT": [False, False, False, False, False],
    }, fail_book=_severe_spread_book)
    transport = _mock_transport(
        futures_symbols=["BLOWNOUTUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=provider,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "BLOWNOUTUSDT" not in by_symbol
    assert provider.call_counts["BLOWNOUTUSDT"] == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_marginal_failure_does_not_fast_exit(session_factory) -> None:
    """Regression guard distinguishing the two failure classes: a symbol
    failing marginally (depth ~78% of floor, same fixture as the
    unanimous-hysteresis test above) must run the FULL 5-sample loop,
    not fast-exit on the first miss -- the severity trigger must not
    fire for near-misses, only genuine collapses."""
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('NEARMISSUSDT', 'liquidity_added_spot', 25000000, 2.0, 60000, "
            "'2026-08-16T00:00:00')"
        ))
        await session.commit()

    tickers = [{"symbol": "NEARMISSUSDT", "quoteVolume": "25000000"}]
    provider = _pattern_depth_provider({
        # 1 pass, 4 marginal (not severe) fails -> retained, all 5 sampled.
        "NEARMISSUSDT": [True, False, False, False, False],
    }, fail_book=_marginal_thin_book)
    transport = _mock_transport(
        futures_symbols=["NEARMISSUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=provider,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "NEARMISSUSDT" in by_symbol
    assert provider.call_counts["NEARMISSUSDT"] == 5  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_fast_exit_does_not_apply_to_entry_candidates(session_factory) -> None:
    """A brand-new candidate (not currently a member) whose first sample
    is severely bad must NOT be fast-rejected -- fast-exit is an EXIT-
    only concept (it protects an existing, presumably-open-position-
    eligible member from a slow removal). A non-member has nothing to
    protect by exiting fast; it just runs the normal entry evaluation."""
    async with session_factory() as session:
        # Unrelated prior row so `prior` is non-empty -- this is a warm
        # refresh, not the cold-start single-sample path.
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('ANCHORUSDT', 'established_top20', 500000000, 1.0, 100000, "
            "'2026-08-16T00:00:00')"
        ))
        await session.commit()

    tickers = [{"symbol": "NEWCANDIDATEUSDT", "quoteVolume": "25000000"}]
    provider = _pattern_depth_provider({
        # Severe on sample 1, then passes 3 of the remaining 4 -- if
        # fast-exit incorrectly applied to entry candidates, this would
        # reject after 1 call; the correct behavior runs all 5 and
        # admits on the real 3-of-5 entry bar.
        "NEWCANDIDATEUSDT": [False, True, True, True, False],
    }, fail_book=_severe_depth_book)
    # ANCHORUSDT deliberately NOT in futures_symbols -- it exists only as
    # a stale prior row so `prior` is non-empty (warm refresh), mirroring
    # test_entry_requires_three_of_five_passes's exact convention; it is
    # not itself a candidate this cycle and is never re-sampled.
    transport = _mock_transport(
        futures_symbols=["NEWCANDIDATEUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=provider,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "NEWCANDIDATEUSDT" in by_symbol
    assert provider.call_counts["NEWCANDIDATEUSDT"] == 5  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_open_position_override_wins_over_fast_exit_with_loud_log(
    session_factory, caplog,
) -> None:
    """Operator ruling: the open-position override still wins over
    fast-exit -- never strand a live position -- but it must be LOGGED
    LOUDLY (ERROR), not the routine INFO-level retention message every
    other override case gets, so a severe failure held open is visible
    rather than silent."""
    import logging

    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('STRANDEDUSDT', 'liquidity_added_spot', 25000000, 2.0, 60000, "
            "'2026-08-16T00:00:00')"
        ))
        await session.execute(sa.text(
            "INSERT INTO live_trades (symbol, status) VALUES ('STRANDED/USDT', 'open')"
        ))
        await session.commit()

    tickers = [{"symbol": "STRANDEDUSDT", "quoteVolume": "25000000"}]
    provider = _pattern_depth_provider({
        "STRANDEDUSDT": [False, False, False, False, False],
    }, fail_book=_severe_depth_book)
    transport = _mock_transport(
        futures_symbols=["STRANDEDUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=provider,
    )
    http = httpx.AsyncClient(transport=transport)

    with caplog.at_level(logging.ERROR, logger="app.shadow.live_fleet_universe"):
        entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    # Never stranded -- the override wins.
    assert "STRANDEDUSDT" in by_symbol
    # But it's visible: an ERROR-level record mentioning the symbol and
    # the severity, not a silent/routine retention.
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("STRANDEDUSDT" in r.message for r in error_records)
    assert any("SEVERELY FAILED" in r.message for r in error_records)


# ---------------------------------------------------------------------
# 4. Open-position override
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_position_overrides_unanimous_exit(session_factory) -> None:
    """Symbols failing 5/5 samples but holding an open position are
    retained anyway -- has_open_position short-circuits the exit, per
    addendum (a) point 4's hard override. Covers BOTH open-position
    sources, which store the symbol in genuinely DIFFERENT formats:
      - live_trades.symbol: BASE/QUOTE slash form ("HELD/USDT").
      - shadow_open_positions.symbol: Binance no-slash form
        ("SHADOWHELDUSDT").
    A naive has_open_position that queries both tables with the same
    literal string would silently never match the shadow_open_positions
    leg -- this test would catch that regression via SHADOWHELDUSDT
    incorrectly exiting. A third symbol with NO open position anywhere
    is the control and must exit on schedule."""
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) VALUES "
            "('HELDUSDT', 'futures_poll', 25000000, 2.0, 60000, '2026-08-16T00:00:00'), "
            "('SHADOWHELDUSDT', 'liquidity_added_spot', 22000000, 3.0, 55000, '2026-08-16T00:00:00'), "
            "('NOHOLDUSDT', 'futures_poll', 21000000, 4.0, 51000, '2026-08-16T00:00:00')"
        ))
        await session.execute(sa.text(
            "INSERT INTO live_trades (symbol, status) VALUES ('HELD/USDT', 'open')"
        ))
        await session.execute(sa.text(
            "INSERT INTO shadow_open_positions (symbol) VALUES ('SHADOWHELDUSDT')"
        ))
        await session.commit()

    tickers = [
        {"symbol": "HELDUSDT", "quoteVolume": "25000000"},
        {"symbol": "SHADOWHELDUSDT", "quoteVolume": "22000000"},
        {"symbol": "NOHOLDUSDT", "quoteVolume": "21000000"},
    ]
    transport = _mock_transport(
        futures_symbols=["HELDUSDT", "SHADOWHELDUSDT", "NOHOLDUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=_thin_book,  # every sample fails, every symbol
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "HELDUSDT" in by_symbol
    assert "SHADOWHELDUSDT" in by_symbol
    assert "NOHOLDUSDT" not in by_symbol

    # Carried forward verbatim from the prior snapshot, not re-sampled --
    # the freshly-sampled (failing/thin) numbers must NOT appear.
    assert by_symbol["HELDUSDT"].qvol_24h == pytest.approx(25_000_000)
    assert by_symbol["HELDUSDT"].spread_bps == pytest.approx(2.0)
    assert by_symbol["HELDUSDT"].depth_0_5pct_usdt == pytest.approx(60_000)
    assert by_symbol["SHADOWHELDUSDT"].depth_0_5pct_usdt == pytest.approx(55_000)

    # Confirm it was actually persisted (not just returned in-memory).
    async with session_factory() as session2:
        persisted = (await session2.execute(sa.text(
            "SELECT symbol, cohort FROM live_fleet_universe "
            "WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM live_fleet_universe)"
        ))).all()
    assert {r.symbol for r in persisted} == {"HELDUSDT", "SHADOWHELDUSDT"}


@pytest.mark.asyncio
async def test_has_open_position_checks_correct_symbol_format_per_table(session_factory) -> None:
    """Direct, isolated unit coverage of has_open_position itself (not
    just via the end-to-end refresh above) -- proves each table is
    queried in ITS OWN native symbol format, not a shared literal."""
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_trades (symbol, status) VALUES ('ETH/USDT', 'open')"
        ))
        await session.execute(sa.text(
            "INSERT INTO shadow_open_positions (symbol) VALUES ('SOLUSDT')"
        ))
        await session.commit()

        assert await has_open_position(session, "ETH/USDT") is True
        assert await has_open_position(session, "SOL/USDT") is True
        assert await has_open_position(session, "ADA/USDT") is False

        # A closed live_trades row must not count as open.
        await session.execute(sa.text(
            "INSERT INTO live_trades (symbol, status) VALUES ('ADA/USDT', 'closed')"
        ))
        await session.commit()
        assert await has_open_position(session, "ADA/USDT") is False


@pytest.mark.asyncio
async def test_get_open_position_symbols_unions_both_tables_normalized(session_factory) -> None:
    """Direct, isolated unit coverage of get_open_position_symbols itself
    (companion to test_has_open_position_checks_correct_symbol_format_per_table
    above, mirroring it for the new bulk helper) -- proves the union spans
    BOTH tables and every symbol comes back normalized to no-slash form
    (live_fleet_universe's own convention), regardless of which table's
    native format it started in."""
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_trades (symbol, status) VALUES ('ETH/USDT', 'open')"
        ))
        await session.execute(sa.text(
            "INSERT INTO live_trades (symbol, status) VALUES ('ADA/USDT', 'closed')"
        ))
        await session.execute(sa.text(
            "INSERT INTO shadow_open_positions (symbol) VALUES ('SOLUSDT')"
        ))
        await session.commit()

        assert await get_open_position_symbols(session) == {"ETHUSDT", "SOLUSDT"}


# ---------------------------------------------------------------------
# 4b. Task 5d -- open-position override queries live positions directly,
# not just prior snapshot membership (fixes the cold-start / restart gap
# from docs/superpowers/decisions/2026-08-19-live-fleet-universe-never-
# scheduled-incident.md, ruling 4).
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_position_no_prior_entry_rescued_with_fresh_sample(session_factory) -> None:
    """The core Task 5d gap, reproduced directly: an open live_trades
    position on a symbol that has NO entry in `prior` at all (the table
    is completely empty here -- simulating the very first-ever refresh,
    e.g. right after Task 5c's scheduler runs for the first time, or
    after the table gets wiped) and that fails every check_liquidity
    sample this cycle (thin book -- genuinely off the liquidity floor).

    Before Task 5d, the override loop only iterated `prior.items()` --
    with `prior` empty, it would iterate zero times and this symbol
    would be silently dropped, losing candle coverage on an open
    position. After the fix, `get_open_position_symbols` finds it
    directly, and since it has no prior entry to inherit numbers from,
    the rescue branch takes one fresh check_liquidity sample and
    classifies it via the SAME pure `_classify_cohort` call as every
    other path (2026-08-30 rewrite -- this used to hardcode
    established_top20 "the safe default with no lineage to inherit,"
    which is exactly the fabrication that gave XPLUSDT/TRUMPUSDT/
    REDUSDT lineage they never had). RESCUEUSDT is futures-only here
    (no spot listing) and NOT in the frozen baseline, so the pure
    classifier says futures_poll -- proving the rescue path no longer
    invents established_top20 out of nothing.
    """
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_trades (symbol, status) VALUES ('RESCUE/USDT', 'open')"
        ))
        await session.commit()

    tickers = [{"symbol": "RESCUEUSDT", "quoteVolume": "30000000"}]
    transport = _mock_transport(
        futures_symbols=["RESCUEUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=_thin_book,  # fails every sample, main loop AND rescue
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "RESCUEUSDT" in by_symbol
    assert by_symbol["RESCUEUSDT"].cohort == "futures_poll"
    # Real numbers from the rescue's fresh check_liquidity sample -- NOT a
    # fabricated/placeholder zero. _thin_book's fixed geometry (bid
    # 99.99x0.01, ask 100.01x0.01) yields deterministic non-zero values.
    assert by_symbol["RESCUEUSDT"].qvol_24h == pytest.approx(30_000_000)
    assert by_symbol["RESCUEUSDT"].spread_bps == pytest.approx(2.0)
    assert by_symbol["RESCUEUSDT"].depth_0_5pct_usdt == pytest.approx(2.0, abs=0.01)
    assert by_symbol["RESCUEUSDT"].depth_0_5pct_usdt != 0.0

    # Confirm it was actually persisted (not just returned in-memory) --
    # this is what a subsequent _load_keepalive_symbols read (test below)
    # depends on.
    async with session_factory() as session2:
        persisted = (await session2.execute(sa.text(
            "SELECT symbol, cohort FROM live_fleet_universe "
            "WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM live_fleet_universe)"
        ))).all()
    assert {r.symbol for r in persisted} == {"RESCUEUSDT"}


@pytest.mark.asyncio
async def test_open_position_with_prior_entry_uses_prior_not_resampled(session_factory) -> None:
    """Same shape as the rescue test above (open position, off-floor,
    fails every sample this cycle) but this time a `prior` entry DOES
    exist for the symbol -- steady-state operation, not first-ever
    refresh. Proves the `if sym in prior` branch still takes precedence
    over the fresh-sample rescue fallback: the persisted numbers must be
    the PRIOR snapshot's distinctive values (qvol=77,000,000 / spread=9.0
    / depth=123,456 / cohort=liquidity_added_spot) unchanged, NOT the
    fresh thin-book sample's numbers (qvol=30,000,000 / spread=2.0 /
    depth=~2.0) and NOT a fresh rescue-path classifier call at all."""
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('PRIORHELDUSDT', 'liquidity_added_spot', 77000000, 9.0, 123456, "
            "'2026-08-16T00:00:00')"
        ))
        await session.execute(sa.text(
            "INSERT INTO live_trades (symbol, status) VALUES ('PRIORHELD/USDT', 'open')"
        ))
        await session.commit()

    tickers = [{"symbol": "PRIORHELDUSDT", "quoteVolume": "30000000"}]
    transport = _mock_transport(
        futures_symbols=["PRIORHELDUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=_thin_book,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "PRIORHELDUSDT" in by_symbol
    assert by_symbol["PRIORHELDUSDT"].cohort == "liquidity_added_spot"
    assert by_symbol["PRIORHELDUSDT"].qvol_24h == pytest.approx(77_000_000)
    assert by_symbol["PRIORHELDUSDT"].spread_bps == pytest.approx(9.0)
    assert by_symbol["PRIORHELDUSDT"].depth_0_5pct_usdt == pytest.approx(123456)


@pytest.mark.asyncio
async def test_open_position_survives_simulated_fleet_supervisor_restart(session_factory) -> None:
    """The operator's actual framing (flagged during Task 17's review),
    proven end-to-end rather than just at the persistence layer: an
    off-floor open-position symbol with no prior snapshot entry --
    identical setup to test_open_position_no_prior_entry_rescued_with_fresh_sample
    above -- must still be in the desired-symbol set a FRESH
    fleet-supervisor boot computes on restart.

    ``ws_keepalive_task``'s own ``run_keepalive`` calls
    ``_load_keepalive_symbols`` on startup ("Initial population" in
    keepalive.py) to decide which children to spawn -- it does NOT call
    ``refresh_live_fleet_universe`` itself or share any in-memory state
    with it. So the real guarantee the operator asked for ("a restart
    doesn't lose candle coverage for an open position") lives entirely in
    what ``_load_keepalive_symbols`` reads back from the persisted
    ``live_fleet_universe`` table -- if the rescued row didn't make it
    into that read, the persistence-layer fix alone would NOT have
    closed the incident's gap. This test calls
    ``_load_keepalive_symbols`` completely independently of the refresh
    call above it, reusing only the row it left behind in the DB, to
    prove that chain end-to-end."""
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_trades (symbol, status) VALUES ('RESTARTHELD/USDT', 'open')"
        ))
        await session.commit()

    tickers = [{"symbol": "RESTARTHELDUSDT", "quoteVolume": "30000000"}]
    transport = _mock_transport(
        # Dual-listed (spot-backed), unlike the sibling RESCUEUSDT test --
        # this test specifically exercises _load_keepalive_symbols, which
        # intentionally EXCLUDES the futures_poll cohort (that cohort is
        # served by the separate futures REST-poll supervisor, not
        # ws_keepalive_task -- see keepalive.py's own docstring). A
        # futures-only rescued symbol would correctly never appear in
        # `desired` below regardless of the persistence fix, so this test
        # needs a spot-backed symbol to actually prove the round-trip.
        futures_symbols=["RESTARTHELDUSDT"], spot_symbols=["RESTARTHELDUSDT"],
        tickers=tickers, depth_provider=_thin_book,  # off-floor -- fails every sample
    )
    http = httpx.AsyncClient(transport=transport)

    # Step 1: a refresh runs (e.g. Task 5c's scheduler's very first tick,
    # or any tick after the table was wiped) and rescues the open-position
    # symbol per Task 5d -- see the dedicated test above for the detailed
    # assertions on this step; here it's just the setup for step 2.
    # RESTARTHELDUSDT is spot-backed and not in the frozen baseline, so
    # the pure classifier says liquidity_added_spot.
    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}
    assert "RESTARTHELDUSDT" in by_symbol
    assert by_symbol["RESTARTHELDUSDT"].cohort == "liquidity_added_spot"

    # Step 2: simulate a fleet-supervisor restart -- a completely fresh,
    # independent call to _load_keepalive_symbols, exactly what
    # ws_keepalive_task's own run_keepalive does on boot. No in-memory
    # state from step 1 is reused -- only the row it persisted.
    desired = await keepalive._load_keepalive_symbols(
        session_factory, exclude=frozenset(), timeframe="1h",
    )

    # Promotion note (Stage 1, 2026-08-31; also applies to #527's
    # promotion, 2026-09-01): this assertion was TEMPORARILY downgraded to
    # a 2-tuple check while cohort-threading (Phase 4 Task 9, Epic B) was
    # still a later promotion stage -- #527 alone never touches
    # keepalive.py's _load_keepalive_symbols return shape. Epic B (#474)
    # is now applied on this branch (Stage 2), which threads cohort
    # through _load_keepalive_symbols as a 3rd tuple element -- restored
    # to the original 3-tuple assertion below, proving both the rescued
    # position survives a fresh keepalive load AND its cohort tag
    # (liquidity_added_spot, from the pure classifier) survives with it.
    assert ("RESTARTHELD/USDT", "1h", "liquidity_added_spot") in desired


# ---------------------------------------------------------------------
# 5. established_top20 depends ONLY on frozen-baseline membership, not
# on prior-snapshot history (2026-08-30 rewrite -- this section used to
# test the OPPOSITE: that a re-entrant could never regain
# established_top20. That was the bug. NEARUSDT/ADAUSDT lost real
# established_top20 lineage on genuine re-entry under the old code;
# these tests now prove the fix directly.)
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_entrant_in_baseline_gets_established_top20(session_factory) -> None:
    """A symbol newly entering the universe on a WARM refresh (absent
    from `prior` -- this is NOT cold start) DOES get established_top20
    if it's in the frozen baseline -- this is the direct fix for the
    real NEARUSDT/ADAUSDT bug (both exited the fleet and later
    re-entered; the old sticky/legacy_top20 code permanently lost their
    established_top20 tag on that re-entry since the legacy-seed branch
    only ever fires once, at cold start). asset_universe involvement is
    irrelevant now (see test_established_top20_comes_from_frozen_
    baseline_not_legacy_fleet above) -- omitted here for isolation."""
    await _seed_baseline(session_factory, ["OLDUSDT", "REENTRANTUSDT"])
    async with session_factory() as session:
        # A prior snapshot exists -- this run is NOT cold start.
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('OLDUSDT', 'established_top20', 900000000, 1.0, 200000, "
            "'2026-08-16T00:00:00')"
        ))
        await session.commit()

    tickers = [
        {"symbol": "OLDUSDT", "quoteVolume": "900000000"},
        # REENTRANTUSDT is absent from `prior` (simulating a symbol that
        # exited on some earlier refresh) but IS in the frozen baseline.
        {"symbol": "REENTRANTUSDT", "quoteVolume": "800000000"},
    ]
    transport = _mock_transport(
        futures_symbols=["OLDUSDT", "REENTRANTUSDT"],
        spot_symbols=["OLDUSDT", "REENTRANTUSDT"],
        tickers=tickers, depth_provider=_deep_book,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert by_symbol["REENTRANTUSDT"].cohort == "established_top20"


@pytest.mark.asyncio
async def test_new_entrant_not_in_baseline_never_gets_established_top20(
    session_factory,
) -> None:
    """Companion negative case: a symbol newly entering the universe on a
    WARM refresh that is NOT in the frozen baseline gets
    liquidity_added_spot or futures_poll, never established_top20 --
    even if it's a spot-listed top-volume symbol today. Being ranked
    highly right now is not the same as having been part of the
    pre-cutover fleet (see the module docstring's rationale for why
    asset_universe.rank was rejected as the baseline source)."""
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('OLDUSDT', 'established_top20', 900000000, 1.0, 200000, "
            "'2026-08-16T00:00:00')"
        ))
        await session.commit()
    await _seed_baseline(session_factory, ["OLDUSDT"])  # NEWENTRANTUSDT deliberately excluded

    tickers = [
        {"symbol": "OLDUSDT", "quoteVolume": "900000000"},
        {"symbol": "NEWENTRANTUSDT", "quoteVolume": "800000000"},
    ]
    transport = _mock_transport(
        futures_symbols=["OLDUSDT", "NEWENTRANTUSDT"],
        spot_symbols=["OLDUSDT", "NEWENTRANTUSDT"],
        tickers=tickers, depth_provider=_deep_book,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert by_symbol["OLDUSDT"].cohort == "established_top20"  # in the frozen baseline
    assert by_symbol["NEWENTRANTUSDT"].cohort != "established_top20"
    assert by_symbol["NEWENTRANTUSDT"].cohort == "liquidity_added_spot"


# ---------------------------------------------------------------------
# 6. Churn-cycle identity (operator's explicit unit-test requirement,
# 2026-08-30 ruling): admit -> exit -> re-admit must produce an
# IDENTICAL cohort across all three states, for one symbol from each
# of the three cohorts. This is the direct proof that the pure
# classifier fixes the real defect -- the old sticky/legacy_top20 code
# would have silently reclassified the established_top20 symbol below
# to liquidity_added_spot on its re-admission (exactly what happened to
# the real NEARUSDT/ADAUSDT on staging).
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cohort_identical_across_admit_exit_readmit_cycle(session_factory) -> None:
    """Three symbols, one per cohort, each cycled through three
    sequential refreshes: admit (3-of-5 pass), exit (unanimous 5-of-5
    marginal fail -- _marginal_thin_book, not _thin_book, to isolate
    plain hysteresis from the fast-exit severity dimension), then
    re-admit (3-of-5 pass again). Asserts the cohort recorded at
    admission equals the cohort recorded at re-admission, for all
    three -- proving the classifier is genuinely decidable by identity
    alone, independent of the fleet-membership history in between."""
    await _seed_baseline(session_factory, ["CHURNBASEUSDT"])
    async with session_factory() as session:
        # Unrelated anchor row so `prior` is non-empty from the start --
        # isolates this test from the cold-start single-sample fast
        # path, which is a sampling-count concern orthogonal to what
        # this test verifies (see module docstring).
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('ANCHORUSDT', 'established_top20', 500000000, 1.0, 100000, "
            "'2026-08-16T00:00:00')"
        ))
        await session.commit()

    churn_symbols = ["CHURNBASEUSDT", "CHURNFUTUSDT", "CHURNSPOTUSDT"]
    # ANCHORUSDT is also a real candidate in every phase below, always
    # passing (_deep_book) -- keeps `results` non-empty even during
    # phase 2 (all 3 churn symbols exit simultaneously). Without this, a
    # phase-2 refresh would persist ZERO rows, so `load_live_fleet_
    # universe`'s "latest snapshot" read would keep returning phase 1's
    # STALE snapshot into phase 3 -- an artifact of this test's minimal
    # candidate set, not a real production concern (with 70+ real
    # candidates, a simultaneous all-exit is not a scenario this module
    # needs to handle specially).
    all_symbols = churn_symbols + ["ANCHORUSDT"]
    tickers = [{"symbol": s, "quoteVolume": "25000000"} for s in all_symbols]
    # CHURNFUTUSDT has no spot listing (futures_poll); the other two
    # (plus ANCHORUSDT) are dual-listed. CHURNBASEUSDT is in the frozen
    # baseline (established_top20); CHURNSPOTUSDT is not
    # (liquidity_added_spot).
    futures_symbols = all_symbols
    spot_symbols = ["CHURNBASEUSDT", "CHURNSPOTUSDT", "ANCHORUSDT"]
    expected_cohort = {
        "CHURNBASEUSDT": "established_top20",
        "CHURNFUTUSDT": "futures_poll",
        "CHURNSPOTUSDT": "liquidity_added_spot",
    }

    def _provider_with_anchor(patterns: dict[str, list[bool]], **kw):
        full = dict(patterns)
        full["ANCHORUSDT"] = [True, True, True, True, True]  # always passes
        return _pattern_depth_provider(full, **kw)

    # --- Phase 1: admit (3-of-5 pass, not currently members) ---
    admit_provider = _provider_with_anchor({
        s: [True, True, False, True, False] for s in churn_symbols  # 3 of 5 -> enters
    })
    transport1 = _mock_transport(
        futures_symbols=futures_symbols, spot_symbols=spot_symbols,
        tickers=tickers, depth_provider=admit_provider,
    )
    http1 = httpx.AsyncClient(transport=transport1)
    admitted = await refresh_live_fleet_universe(session_factory, http1, _rate_client(transport1))
    admitted_by_symbol = {e.symbol: e for e in admitted}
    for sym in churn_symbols:
        assert sym in admitted_by_symbol, f"{sym} failed to be admitted in phase 1"
        assert admitted_by_symbol[sym].cohort == expected_cohort[sym]

    # --- Phase 2: exit (unanimous 5-of-5 marginal fail, now members) ---
    exit_provider = _provider_with_anchor(
        {s: [False, False, False, False, False] for s in churn_symbols},  # 0 of 5 -> exits
        fail_book=_marginal_thin_book,
    )
    transport2 = _mock_transport(
        futures_symbols=futures_symbols, spot_symbols=spot_symbols,
        tickers=tickers, depth_provider=exit_provider,
    )
    http2 = httpx.AsyncClient(transport=transport2)
    after_exit = await refresh_live_fleet_universe(session_factory, http2, _rate_client(transport2))
    after_exit_by_symbol = {e.symbol: e for e in after_exit}
    for sym in churn_symbols:
        assert sym not in after_exit_by_symbol, f"{sym} did not exit in phase 2"
    assert "ANCHORUSDT" in after_exit_by_symbol  # keeps the snapshot non-empty

    # --- Phase 3: re-admit (3-of-5 pass again, no longer members) ---
    readmit_provider = _provider_with_anchor({
        s: [True, True, False, True, False] for s in churn_symbols
    })
    transport3 = _mock_transport(
        futures_symbols=futures_symbols, spot_symbols=spot_symbols,
        tickers=tickers, depth_provider=readmit_provider,
    )
    http3 = httpx.AsyncClient(transport=transport3)
    readmitted = await refresh_live_fleet_universe(session_factory, http3, _rate_client(transport3))
    readmitted_by_symbol = {e.symbol: e for e in readmitted}
    for sym in churn_symbols:
        assert sym in readmitted_by_symbol, f"{sym} failed to be re-admitted in phase 3"
        # THE assertion: cohort at re-admission is IDENTICAL to cohort
        # at first admission -- churn history left no trace.
        assert readmitted_by_symbol[sym].cohort == admitted_by_symbol[sym].cohort
        assert readmitted_by_symbol[sym].cohort == expected_cohort[sym]


# ---------------------------------------------------------------------
# Bonus: load_live_fleet_universe's cohort filter (public interface)
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_live_fleet_universe_filters_by_cohort(session_factory) -> None:
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) VALUES "
            "('AUSDT', 'established_top20', 1, 1, 1, '2026-08-17T00:00:00'), "
            "('BUSDT', 'liquidity_added_spot', 1, 1, 1, '2026-08-17T00:00:00'), "
            "('CUSDT', 'futures_poll', 1, 1, 1, '2026-08-17T00:00:00')"
        ))
        await session.commit()

        all_entries = await load_live_fleet_universe(session)
        assert {e.symbol for e in all_entries} == {"AUSDT", "BUSDT", "CUSDT"}

        futures_only = await load_live_fleet_universe(session, cohort="futures_poll")
        assert {e.symbol for e in futures_only} == {"CUSDT"}
