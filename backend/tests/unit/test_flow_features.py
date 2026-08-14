"""Tests for app.core.features.flow_features (W4 brain supervisor)."""
from __future__ import annotations

import logging

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.core.features.flow_features as flow_mod
from app.data.adapters import get_intermarket_adapter
from app.core.features.flow_features import (
    compute,
    get_cached,
    update_flow_cache,
    update_flow_cache_and_persist,
)
from app.data.ratelimit import RateLimitedClient


@pytest.fixture(autouse=True)
def _reset_cache():
    """Isolate tests by clearing the module-level cache between runs."""
    flow_mod._clear_cache_for_tests()
    flow_mod._clear_failure_streaks_for_tests()
    yield
    flow_mod._clear_cache_for_tests()
    flow_mod._clear_failure_streaks_for_tests()


# ---------------------------------------------------------------------------
# compute() — sync read from cache
# ---------------------------------------------------------------------------

def test_compute_returns_all_none_when_no_cache() -> None:
    result = compute("BTCUSDT")
    assert result == {
        "ls_account_ratio": None, "taker_buy_sell_ratio": None,
        "oi_4h_delta": None, "oi_24h_delta": None,
    }


def test_compute_returns_copy_not_reference() -> None:
    flow_mod._FLOW_CACHE["BTCUSDT"] = {
        "ls_account_ratio": 0.5, "taker_buy_sell_ratio": 0.6,
        "oi_4h_delta": 0.01, "oi_24h_delta": 0.03,
    }
    result = compute("BTCUSDT")
    result["ls_account_ratio"] = 99.9
    assert flow_mod._FLOW_CACHE["BTCUSDT"]["ls_account_ratio"] == 0.5  # cache unchanged


def test_compute_returns_null_for_unknown_symbol() -> None:
    flow_mod._FLOW_CACHE["ETHUSDT"] = {
        "ls_account_ratio": 0.4, "taker_buy_sell_ratio": 0.55,
        "oi_4h_delta": -0.02, "oi_24h_delta": -0.05,
    }
    result = compute("SOLUSDT")
    assert result == {
        "ls_account_ratio": None, "taker_buy_sell_ratio": None,
        "oi_4h_delta": None, "oi_24h_delta": None,
    }


# ---------------------------------------------------------------------------
# update_flow_cache() — async fetch + update
# ---------------------------------------------------------------------------

def _make_mock_transport(
    ls_rows: list | None = None,
    taker_rows: list | None = None,
    oi_rows: list | None = None,
    *,
    raise_on: str | None = None,
) -> httpx.MockTransport:
    """Build a mock transport that returns configured responses per endpoint.

    Matches on EXACT path, not substring. A substring match (e.g.
    ``"openInterestHist" in path``) would return 200 for *both* the
    correct path (``/futures/data/openInterestHist``) and the wrong one
    that 404s in production (``/fapi/v1/openInterestHist``) — that blind
    spot is exactly why this mock never caught the real bug (found
    2026-08-06 via the item-3 endpoint audit) despite the endpoint being
    100% broken since inception. Exact-path matching makes a future
    wrong-path regression fail here instead of shipping silently.
    """

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if raise_on and raise_on in path:
            raise httpx.NetworkError("mock network error")
        if path == "/futures/data/globalLongShortAccountRatio":
            data = ls_rows if ls_rows is not None else [{"longAccount": "0.52"}]
            return httpx.Response(200, json=data)
        if path == "/futures/data/takerlongshortRatio":
            data = taker_rows if taker_rows is not None else [
                {"buyVol": "600.0", "sellVol": "400.0"}
            ]
            return httpx.Response(200, json=data)
        if path == "/futures/data/openInterestHist":
            data = oi_rows if oi_rows is not None else [
                {"sumOpenInterest": "100.0"},
                {"sumOpenInterest": "102.0"},
                {"sumOpenInterest": "104.0"},
                {"sumOpenInterest": "106.0"},
                {"sumOpenInterest": "110.0"},
            ]
            return httpx.Response(200, json=data)
        # Any other path (including the old, wrong /fapi/v1/openInterestHist
        # or /futures/data/takerbuyvolume) 404s, exactly like real Binance.
        return httpx.Response(404, json={"msg": "unknown"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_ls_account_ratio_computed_correctly() -> None:
    transport = _make_mock_transport(ls_rows=[{"longAccount": "0.45"}])
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache("ETHUSDT", http=http, base_url="https://fapi.binance.com")
    assert get_cached("ETHUSDT")["ls_account_ratio"] == pytest.approx(0.45)


@pytest.mark.asyncio
async def test_taker_buy_sell_ratio_math() -> None:
    transport = _make_mock_transport(
        taker_rows=[{"buyVol": "60.0", "sellVol": "40.0"}],
    )
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache("BTCUSDT", http=http, base_url="https://fapi.binance.com")
    assert get_cached("BTCUSDT")["taker_buy_sell_ratio"] == pytest.approx(0.60)


@pytest.mark.asyncio
async def test_oi_4h_delta_math() -> None:
    transport = _make_mock_transport(oi_rows=[
        {"sumOpenInterest": "100.0"},
        {"sumOpenInterest": "102.0"},
        {"sumOpenInterest": "104.0"},
        {"sumOpenInterest": "106.0"},
        {"sumOpenInterest": "110.0"},
    ])
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache("SOLUSDT", http=http, base_url="https://fapi.binance.com")
    # (110 - 100) / 100 = 0.10
    assert get_cached("SOLUSDT")["oi_4h_delta"] == pytest.approx(0.10)


@pytest.mark.asyncio
async def test_oi_4h_delta_none_when_fewer_than_5_rows() -> None:
    transport = _make_mock_transport(oi_rows=[
        {"sumOpenInterest": "100.0"},
        {"sumOpenInterest": "102.0"},
    ])
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache("BNBUSDT", http=http, base_url="https://fapi.binance.com")
    assert get_cached("BNBUSDT")["oi_4h_delta"] is None


@pytest.mark.asyncio
async def test_ls_error_returns_none_for_that_key_others_still_computed() -> None:
    """Network error on one endpoint → that key is None; others succeed."""
    transport = _make_mock_transport(
        raise_on="globalLongShortAccountRatio",
        taker_rows=[{"buyVol": "70.0", "sellVol": "30.0"}],
    )
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache("XRPUSDT", http=http, base_url="https://fapi.binance.com")
    result = get_cached("XRPUSDT")
    assert result["ls_account_ratio"] is None
    assert result["taker_buy_sell_ratio"] == pytest.approx(0.70)
    # oi_4h_delta uses default mock rows → 0.10
    assert result["oi_4h_delta"] == pytest.approx(0.10)


@pytest.mark.asyncio
async def test_update_caches_per_symbol_independently() -> None:
    transport = _make_mock_transport(ls_rows=[{"longAccount": "0.55"}])
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache("BTCUSDT", http=http, base_url="https://fapi.binance.com")
    assert get_cached("ETHUSDT")["ls_account_ratio"] is None  # not updated
    assert get_cached("BTCUSDT")["ls_account_ratio"] == pytest.approx(0.55)


@pytest.mark.asyncio
async def test_slash_symbol_normalised() -> None:
    """BTC/USDT should be normalised to BTCUSDT for the Binance request."""
    seen_symbols: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sym = req.url.params.get("symbol", "")
        seen_symbols.append(sym)
        if req.url.path == "/futures/data/globalLongShortAccountRatio":
            return httpx.Response(200, json=[{"longAccount": "0.50"}])
        if req.url.path == "/futures/data/takerlongshortRatio":
            return httpx.Response(200, json=[{"buyVol": "50.0", "sellVol": "50.0"}])
        if req.url.path == "/futures/data/openInterestHist":
            return httpx.Response(200, json=[
                {"sumOpenInterest": "100.0"},
                {"sumOpenInterest": "102.0"},
                {"sumOpenInterest": "104.0"},
                {"sumOpenInterest": "106.0"},
                {"sumOpenInterest": "110.0"},
            ])
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache("BTC/USDT", http=http, base_url="https://fapi.binance.com")
    assert all(sym == "BTCUSDT" for sym in seen_symbols), f"got {seen_symbols}"


@pytest.mark.asyncio
async def test_taker_ratio_none_when_total_volume_zero() -> None:
    transport = _make_mock_transport(
        taker_rows=[{"buyVol": "0.0", "sellVol": "0.0"}],
    )
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache("AVAXUSDT", http=http, base_url="https://fapi.binance.com")
    assert get_cached("AVAXUSDT")["taker_buy_sell_ratio"] is None


@pytest.mark.asyncio
async def test_oi_base_zero_returns_none() -> None:
    transport = _make_mock_transport(oi_rows=[
        {"sumOpenInterest": "0.0"},
        {"sumOpenInterest": "1.0"},
        {"sumOpenInterest": "2.0"},
        {"sumOpenInterest": "3.0"},
        {"sumOpenInterest": "4.0"},
    ])
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache("DOTUSDT", http=http, base_url="https://fapi.binance.com")
    assert get_cached("DOTUSDT")["oi_4h_delta"] is None


@pytest.mark.asyncio
async def test_oi_4h_and_24h_delta_computed_from_one_25row_response() -> None:
    """25 buckets covers a 24h span; 4h delta reuses the last 5 of them --
    both windows come from a single openInterestHist call, zero extra cost.
    """
    rows = [{"sumOpenInterest": f"{100.0 + i}"} for i in range(25)]
    # index 0 = 100.0 (24h ago), index 20 = 120.0 (4h ago), index 24 = 124.0 (now)
    transport = _make_mock_transport(oi_rows=rows)
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache("INJUSDT", http=http, base_url="https://fapi.binance.com")
    result = get_cached("INJUSDT")
    # 4h: (124 - 120) / 120
    assert result["oi_4h_delta"] == pytest.approx((124.0 - 120.0) / 120.0)
    # 24h: (124 - 100) / 100
    assert result["oi_24h_delta"] == pytest.approx((124.0 - 100.0) / 100.0)


@pytest.mark.asyncio
async def test_oi_24h_delta_none_when_fewer_than_25_rows() -> None:
    """Fewer than 25 buckets: 4h delta still computes (needs only 5), 24h stays None."""
    transport = _make_mock_transport(oi_rows=[
        {"sumOpenInterest": "100.0"},
        {"sumOpenInterest": "102.0"},
        {"sumOpenInterest": "104.0"},
        {"sumOpenInterest": "106.0"},
        {"sumOpenInterest": "110.0"},
    ])
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache("PEPEUSDT", http=http, base_url="https://fapi.binance.com")
    result = get_cached("PEPEUSDT")
    assert result["oi_4h_delta"] == pytest.approx(0.10)
    assert result["oi_24h_delta"] is None


@pytest.mark.asyncio
async def test_oi_24h_base_zero_returns_none() -> None:
    rows = [{"sumOpenInterest": "0.0"}] + [
        {"sumOpenInterest": f"{i}"} for i in range(1, 25)
    ]
    transport = _make_mock_transport(oi_rows=rows)
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache("FLOKIUSDT", http=http, base_url="https://fapi.binance.com")
    assert get_cached("FLOKIUSDT")["oi_24h_delta"] is None


# ---------------------------------------------------------------------------
# update_flow_cache_and_persist() — item 3, continuous time series storage
# ---------------------------------------------------------------------------

_CREATE_FLOW_SNAPSHOTS_TABLE = (
    "CREATE TABLE flow_feature_snapshots ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, "
    "captured_at TEXT NOT NULL DEFAULT (datetime('now')), "
    "ls_account_ratio REAL, taker_buy_sell_ratio REAL, oi_4h_delta REAL, "
    "oi_24h_delta REAL, "
    "source TEXT NOT NULL DEFAULT 'binance_futures')"
)


@pytest.fixture
async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_CREATE_FLOW_SNAPSHOTS_TABLE))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_update_and_persist_writes_a_row(monkeypatch, _session_factory) -> None:
    monkeypatch.setattr(flow_mod, "get_session_factory", lambda: _session_factory)
    oi_rows = [{"sumOpenInterest": f"{100.0 + i}"} for i in range(25)]
    transport = _make_mock_transport(
        ls_rows=[{"longAccount": "0.42"}],
        taker_rows=[{"buyVol": "70.0", "sellVol": "30.0"}],
        oi_rows=oi_rows,
    )
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache_and_persist(
            "ADAUSDT", http=http, base_url="https://fapi.binance.com",
        )
    async with _session_factory() as session:
        row = (await session.execute(sa.text(
            "SELECT symbol, ls_account_ratio, taker_buy_sell_ratio, "
            "oi_4h_delta, oi_24h_delta FROM flow_feature_snapshots"
        ))).one()
    assert row.symbol == "ADAUSDT"
    assert row.ls_account_ratio == pytest.approx(0.42)
    assert row.taker_buy_sell_ratio == pytest.approx(0.70)
    assert row.oi_4h_delta == pytest.approx((124.0 - 120.0) / 120.0)
    assert row.oi_24h_delta == pytest.approx((124.0 - 100.0) / 100.0)
    # cache still updated exactly as update_flow_cache alone would do
    assert get_cached("ADAUSDT")["ls_account_ratio"] == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_update_and_persist_skips_row_when_everything_failed(monkeypatch, _session_factory) -> None:
    monkeypatch.setattr(flow_mod, "get_session_factory", lambda: _session_factory)
    transport = httpx.MockTransport(lambda req: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache_and_persist(
            "LTCUSDT", http=http, base_url="https://fapi.binance.com",
        )
    async with _session_factory() as session:
        count = (await session.execute(
            sa.text("SELECT COUNT(*) AS n FROM flow_feature_snapshots")
        )).one()
    assert count.n == 0


@pytest.mark.asyncio
async def test_update_and_persist_swallows_db_errors(monkeypatch) -> None:
    """A broken session factory must not propagate — fire-and-forget contract."""
    transport = _make_mock_transport()

    def _broken_factory():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(flow_mod, "get_session_factory", lambda: _broken_factory)
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache_and_persist(
            "MATICUSDT", http=http, base_url="https://fapi.binance.com",
        )
    # No exception raised — and the in-memory cache still updated normally.
    assert get_cached("MATICUSDT")["ls_account_ratio"] is not None


@pytest.mark.asyncio
async def test_update_and_persist_uses_global_factory_not_injected(monkeypatch) -> None:
    """Regression guard: must call app.db.session.get_session_factory(), not
    accept one from the caller — a fire-and-forget task must never become a
    second concurrent consumer of whatever pool the caller was built with
    (see worker.py W4 call site comment for the corruption this caused).
    """
    calls: list[str] = []

    class _FakeSession:
        async def __aenter__(self):
            calls.append("opened")
            return self

        async def __aexit__(self, *exc):
            calls.append("closed")

        async def execute(self, *a, **kw):
            calls.append("execute")

        async def commit(self):
            calls.append("commit")

    monkeypatch.setattr(flow_mod, "get_session_factory", lambda: (lambda: _FakeSession()))
    transport = _make_mock_transport()
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache_and_persist(
            "OPUSDT", http=http, base_url="https://fapi.binance.com",
        )
    assert calls == ["opened", "execute", "commit", "closed"]


# ---------------------------------------------------------------------------
# TIER 2a (defect sweep 2026-08-06): the broad-except+DEBUG-log pattern that
# hid the taker-ratio and OI-delta wrong-endpoint bugs for months is still
# live in this file. A single symbol failing (delisted, thin market) is a
# legitimate quirk and must stay quiet; the SAME endpoint failing for every
# symbol in a row is exactly what those two incidents looked like and must
# now be loud. These tests assert the escalation threshold, not the fetch
# logic (already covered above).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_isolated_single_failure_does_not_escalate(caplog) -> None:
    """One bad symbol amid otherwise-successful calls must stay quiet."""
    caplog.set_level(logging.DEBUG, logger="app.core.features.flow_features")
    ok_transport = _make_mock_transport()
    fail_transport = _make_mock_transport(raise_on="globalLongShortAccountRatio")
    async with httpx.AsyncClient(transport=fail_transport) as http:
        await update_flow_cache("BADSYMUSDT", http=http, base_url="https://fapi.binance.com")
    async with httpx.AsyncClient(transport=ok_transport) as http:
        await update_flow_cache("GOODSYMUSDT", http=http, base_url="https://fapi.binance.com")
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_endpoint_100pct_failure_escalates_to_error(caplog) -> None:
    """The same endpoint failing for every symbol in a row (the exact shape
    of the taker-ratio and OI-delta incidents) must escalate to ERROR
    before the streak reaches a full universe cycle."""
    caplog.set_level(logging.DEBUG, logger="app.core.features.flow_features")
    transport = _make_mock_transport(raise_on="globalLongShortAccountRatio")
    async with httpx.AsyncClient(transport=transport) as http:
        for i in range(flow_mod._CONSECUTIVE_FAILURE_ALERT_THRESHOLD):
            await update_flow_cache(f"SYM{i}USDT", http=http, base_url="https://fapi.binance.com")
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "expected an ERROR-level escalation once the streak hit threshold"
    assert "ls_account_ratio" in error_records[-1].getMessage()
    assert "consecutive" in error_records[-1].getMessage().lower()


@pytest.mark.asyncio
async def test_success_resets_the_failure_streak(caplog) -> None:
    """A single success between failure runs must reset the streak, so two
    separate short runs of failures (below threshold each) never combine
    into a false escalation."""
    caplog.set_level(logging.DEBUG, logger="app.core.features.flow_features")
    fail_transport = _make_mock_transport(raise_on="globalLongShortAccountRatio")
    ok_transport = _make_mock_transport()
    half = flow_mod._CONSECUTIVE_FAILURE_ALERT_THRESHOLD - 1

    async with httpx.AsyncClient(transport=fail_transport) as http:
        for i in range(half):
            await update_flow_cache(f"A{i}USDT", http=http, base_url="https://fapi.binance.com")
    async with httpx.AsyncClient(transport=ok_transport) as http:
        await update_flow_cache("RESETUSDT", http=http, base_url="https://fapi.binance.com")
    async with httpx.AsyncClient(transport=fail_transport) as http:
        for i in range(half):
            await update_flow_cache(f"B{i}USDT", http=http, base_url="https://fapi.binance.com")

    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_each_endpoint_tracked_independently(caplog) -> None:
    """oi_delta failing repeatedly must not mask/trigger on ls_account_ratio's
    counter, and vice versa — three endpoints, three independent streaks."""
    caplog.set_level(logging.DEBUG, logger="app.core.features.flow_features")
    transport = _make_mock_transport(raise_on="openInterestHist")
    async with httpx.AsyncClient(transport=transport) as http:
        for i in range(flow_mod._CONSECUTIVE_FAILURE_ALERT_THRESHOLD):
            await update_flow_cache(f"OI{i}USDT", http=http, base_url="https://fapi.binance.com")
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records
    assert "oi_delta" in error_records[-1].getMessage()
    assert "ls_account_ratio" not in error_records[-1].getMessage()


# ---------------------------------------------------------------------------
# FU-40 (remediation work order B4, 2026-08-14): production calls must route
# through the shared Binance Futures RateLimitedClient singleton instead of
# a fresh, unthrottled httpx.AsyncClient per call.
# ---------------------------------------------------------------------------


def _fake_intermarket_singleton(monkeypatch) -> RateLimitedClient:
    """Install a fake intermarket-adapter singleton with an inert transport.

    Deliberately never touches the real ``_shared_http()``/``_HTTP``
    globals — those back a real, live ``httpx.AsyncClient`` with no mock
    transport. Leaking a reference to that real client into a test (or
    leaving it un-closed) pollutes module-global state for the rest of
    the process and breaks unrelated tests (e.g.
    ``test_adapter_registry.py``'s ``aclose_all()``) depending on run
    order — this was caught the hard way while writing these tests.
    """
    import app.data.adapters as adapters_mod

    fake_http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(404, json={"msg": "unused in these tests"})
    ))
    fake_rate_client = RateLimitedClient(
        exchange="binance_futures",
        http=fake_http,
        buckets={"default": flow_mod.TokenBucket(capacity=2400.0, refill_per_sec=40.0)},
    )

    class _FakeAdapter:
        rate_client = fake_rate_client

    monkeypatch.setattr(adapters_mod, "_INTERMARKET_INSTANCE", _FakeAdapter())
    return fake_rate_client


def test_resolve_rate_client_reuses_shared_singleton_across_calls(monkeypatch) -> None:
    """Two no-``http=`` calls must return the exact same RateLimitedClient —
    proof the fix stops spinning up (and discarding) a fresh client on
    every 1h-candle tick across the universe."""
    fake_rate_client = _fake_intermarket_singleton(monkeypatch)
    rc1 = flow_mod._resolve_rate_client(None)
    rc2 = flow_mod._resolve_rate_client(None)
    assert rc1 is rc2
    assert rc1 is fake_rate_client
    assert rc1 is get_intermarket_adapter().rate_client


def test_resolve_rate_client_shares_binance_futures_exchange_bucket(monkeypatch) -> None:
    """The no-``http=`` path must be the *same* RateLimitedClient the
    intermarket adapter (premiumIndex/openInterestHist) already uses — not
    a second, independently-tracked bucket that still stacks on top of the
    real Binance IP-level weight limit."""
    _fake_intermarket_singleton(monkeypatch)
    rate_client = flow_mod._resolve_rate_client(None)
    assert rate_client.exchange == "binance_futures"


@pytest.mark.asyncio
async def test_default_path_uses_shared_rate_client_for_all_three_calls(monkeypatch) -> None:
    """End-to-end: with no ``http=`` override, all three fetches must be
    issued through the injected shared rate client (proving the module no
    longer builds its own client), tagged with distinct endpoint_keys."""
    transport = _make_mock_transport(ls_rows=[{"longAccount": "0.61"}])
    shared_http = httpx.AsyncClient(transport=transport)
    shared_rate_client = RateLimitedClient(
        exchange="binance_futures",
        http=shared_http,
        buckets={"default": flow_mod.TokenBucket(capacity=2400.0, refill_per_sec=40.0)},
    )
    seen_endpoint_keys: list[str] = []
    real_request = shared_rate_client.request

    async def _tracking_request(method, url, *, endpoint_key="default", **kw):
        seen_endpoint_keys.append(endpoint_key)
        return await real_request(method, url, endpoint_key=endpoint_key, **kw)

    monkeypatch.setattr(shared_rate_client, "request", _tracking_request)

    class _FakeAdapter:
        rate_client = shared_rate_client

    import app.data.adapters as adapters_mod
    monkeypatch.setattr(adapters_mod, "_INTERMARKET_INSTANCE", _FakeAdapter())

    try:
        await update_flow_cache("SHAREDUSDT")  # no http= -> production path
    finally:
        await shared_http.aclose()

    assert get_cached("SHAREDUSDT")["ls_account_ratio"] == pytest.approx(0.61)
    assert seen_endpoint_keys == [
        "globalLongShortAccountRatio", "takerlongshortRatio", "openInterestHist",
    ]


@pytest.mark.asyncio
async def test_explicit_http_override_still_bypasses_shared_client(monkeypatch) -> None:
    """Regression guard for tests/standalone callers: passing ``http=``
    explicitly must NOT touch the shared intermarket singleton at all."""
    import app.data.adapters as adapters_mod

    def _boom():
        raise AssertionError("get_intermarket_adapter() must not be called when http= is given")

    monkeypatch.setattr(adapters_mod, "get_intermarket_adapter", _boom)
    transport = _make_mock_transport(ls_rows=[{"longAccount": "0.33"}])
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache("EXPLICITUSDT", http=http, base_url="https://fapi.binance.com")
    assert get_cached("EXPLICITUSDT")["ls_account_ratio"] == pytest.approx(0.33)
