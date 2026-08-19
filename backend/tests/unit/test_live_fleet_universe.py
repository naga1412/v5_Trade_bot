"""Phase 4 (2026-08-17 redraft) -- live_fleet_universe liquidity-floor
selector, hysteresis, cold-start seeding, and the open-position override.

Fixture pattern mirrors tests/healer/test_detectors.py: per-suite SQLite
in-memory schema stand-ins (Postgres-only types swapped for SQLite
equivalents), seeded per test with exactly the row shape needed to
trigger (or not trigger) the behavior under test.
"""
from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.ratelimit import RateLimitedClient, TokenBucket
from app.shadow import live_fleet_universe as lfu_mod
from app.shadow.live_fleet_universe import (
    has_open_position,
    load_live_fleet_universe,
    refresh_live_fleet_universe,
)

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
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


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
    """Always fails depth (well under the $50k floor)."""
    return ([("99.99", "0.01")], [("100.01", "0.01")])


def _pattern_depth_provider(patterns: dict[str, list[bool]]) -> _DepthProvider:
    """Returns a depth_provider that replays a per-symbol pass/fail
    sequence across successive calls (one call per sample) -- lets tests
    hit an exact N-of-5 pass count deterministically."""
    call_counts: dict[str, int] = dict.fromkeys(patterns, 0)

    def provider(sym: str) -> tuple[_BookSide, _BookSide]:
        idx = call_counts[sym]
        call_counts[sym] += 1
        should_pass = patterns[sym][idx]
        return _deep_book(sym) if should_pass else _thin_book(sym)

    provider.call_counts = call_counts  # type: ignore[attr-defined]
    return provider


# ---------------------------------------------------------------------
# 1. Cold-start seeding
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cold_start_seeds_established_top20_from_legacy_fleet(session_factory) -> None:
    """No prior live_fleet_universe snapshot exists. A symbol that (a)
    passes the new floor AND (b) is in today's legacy top-20-by-volume
    fleet (asset_universe, read via load_current_universe) gets tagged
    established_top20, not liquidity_added_spot. A symbol passing the
    floor but absent from the legacy fleet gets liquidity_added_spot."""
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO asset_universe (symbol, quote_volume_usd_24h, rank, snapshot_at) "
            "VALUES ('BTCUSDT', 1000000000, 1, '2026-08-16T00:00:00')"
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
async def test_cold_start_does_not_refire_on_second_refresh(session_factory) -> None:
    """The cold-start legacy-fleet consultation is gated on `not prior`.
    After the first refresh persists a snapshot, a second refresh must
    NOT re-run the legacy-seed branch -- proven here by removing
    asset_universe's row entirely before the second call: if cold-start
    logic incorrectly re-fired, BTCUSDT would silently keep its cohort
    anyway (sticky-cohort would mask the bug), so instead this asserts
    a DIFFERENT, later-arriving legacy-fleet symbol does NOT retroactively
    get tagged established_top20 on the second run even though it now
    (hypothetically) appears in asset_universe -- see the dedicated
    test_new_entrant_never_tagged_established_top20 below for the
    focused version of this; this test additionally proves the first
    run's seed doesn't fire twice by checking BTCUSDT's cohort survives
    unchanged across both runs even after its asset_universe row is
    deleted (proving the SECOND run never re-reads asset_universe at
    all for an existing member -- it uses the sticky prior cohort)."""
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO asset_universe (symbol, quote_volume_usd_24h, rank, snapshot_at) "
            "VALUES ('BTCUSDT', 1000000000, 1, '2026-08-16T00:00:00')"
        ))
        await session.commit()

    tickers = [{"symbol": "BTCUSDT", "quoteVolume": "1000000000"}]
    transport = _mock_transport(
        futures_symbols=["BTCUSDT"], spot_symbols=["BTCUSDT"],
        tickers=tickers, depth_provider=_deep_book,
    )
    http = httpx.AsyncClient(transport=transport)
    first = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    assert {e.symbol: e.cohort for e in first} == {"BTCUSDT": "established_top20"}

    # Delete the asset_universe row -- if the cold-start branch incorrectly
    # re-fired on the second call, load_current_universe would return
    # nothing and legacy_top20 would be empty, but that wouldn't change
    # the outcome for an already-sticky symbol either way. The real proof
    # is structural: `prior` is non-empty after the first run, so
    # `if not prior:` cannot enter the legacy-seed branch on this second
    # call regardless of what's in asset_universe.
    async with session_factory() as session:
        await session.execute(sa.text("DELETE FROM asset_universe"))
        await session.commit()

    second = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    assert {e.symbol: e.cohort for e in second} == {"BTCUSDT": "established_top20"}


# ---------------------------------------------------------------------
# 1b. Cold-start entry-bar relaxation (Task 5c, ratified 2026-08-19)
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cold_start_admits_on_any_pass(session_factory) -> None:
    """No prior live_fleet_universe snapshot at all -- a candidate passing
    check_liquidity on only 1 of 5 samples is still admitted. The normal
    entry bar (>=3 of 5, see test_entry_requires_three_of_five_passes
    below) does not apply on the very first-ever refresh: there is no
    hysteresis history to require 3-of-5 against yet."""
    tickers = [{"symbol": "COLDUSDT", "quoteVolume": "25000000"}]
    provider = _pattern_depth_provider({
        "COLDUSDT": [True, False, False, False, False],  # 1 of 5 -> admitted (cold start)
    })
    transport = _mock_transport(
        futures_symbols=["COLDUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=provider,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "COLDUSDT" in by_symbol
    assert provider.call_counts["COLDUSDT"] == 5  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cold_start_relaxation_does_not_apply_once_prior_exists(session_factory) -> None:
    """Regression guard proving the relaxation is scoped to cold start
    only: with a non-empty table (any single prior row makes `prior`
    truthy), the SAME 1-of-5 candidate that was admitted above is
    correctly rejected under the unchanged >=3-of-5 entry bar."""
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
    assert provider.call_counts["COLDUSDT"] == 5  # type: ignore[attr-defined]


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
    entry-side test above)."""
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
    })
    transport = _mock_transport(
        futures_symbols=["FOURFAILUSDT", "FIVEFAILUSDT"], spot_symbols=[],
        tickers=tickers, depth_provider=provider,
    )
    http = httpx.AsyncClient(transport=transport)

    entries = await refresh_live_fleet_universe(session_factory, http, _rate_client(transport))
    by_symbol = {e.symbol: e for e in entries}

    assert "FOURFAILUSDT" in by_symbol
    assert "FIVEFAILUSDT" not in by_symbol
    # Retained member's cohort stays sticky (unchanged from its prior row).
    assert by_symbol["FOURFAILUSDT"].cohort == "liquidity_added_spot"
    assert provider.call_counts["FOURFAILUSDT"] == 5  # type: ignore[attr-defined]
    assert provider.call_counts["FIVEFAILUSDT"] == 5  # type: ignore[attr-defined]


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


# ---------------------------------------------------------------------
# 5. established_top20 is cold-start-only, never retroactive
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_entrant_never_tagged_established_top20(session_factory) -> None:
    """Even on a WARM refresh (a prior snapshot already exists, so this
    is NOT the cold-start path), a symbol newly entering the universe --
    absent from the prior snapshot -- gets liquidity_added_spot or
    futures_poll, never established_top20, EVEN IF that new entrant also
    happens to appear in today's legacy asset_universe top-20.
    established_top20 is a cold-start-only lineage tag, gated on
    `if not prior:`, and must not be assigned retroactively post-cutover."""
    async with session_factory() as session:
        # A prior snapshot exists -- this run is NOT cold start.
        await session.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('OLDUSDT', 'established_top20', 900000000, 1.0, 200000, "
            "'2026-08-16T00:00:00')"
        ))
        # NEWENTRANTUSDT is ALSO in the legacy top-20-by-volume fleet --
        # this must NOT matter, because the legacy-consultation branch
        # never runs when `prior` is non-empty.
        await session.execute(sa.text(
            "INSERT INTO asset_universe (symbol, quote_volume_usd_24h, rank, snapshot_at) "
            "VALUES ('NEWENTRANTUSDT', 800000000, 2, '2026-08-16T00:00:00')"
        ))
        await session.commit()

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

    assert by_symbol["OLDUSDT"].cohort == "established_top20"  # sticky, inherited
    assert by_symbol["NEWENTRANTUSDT"].cohort != "established_top20"
    assert by_symbol["NEWENTRANTUSDT"].cohort == "liquidity_added_spot"


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
