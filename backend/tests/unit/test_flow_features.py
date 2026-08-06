"""Tests for app.core.features.flow_features (W4 brain supervisor)."""
from __future__ import annotations

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.features.flow_features as flow_mod
from app.core.features.flow_features import (
    compute,
    get_cached,
    update_flow_cache,
    update_flow_cache_and_persist,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Isolate tests by clearing the module-level cache between runs."""
    flow_mod._clear_cache_for_tests()
    yield
    flow_mod._clear_cache_for_tests()


# ---------------------------------------------------------------------------
# compute() — sync read from cache
# ---------------------------------------------------------------------------

def test_compute_returns_all_none_when_no_cache() -> None:
    result = compute("BTCUSDT")
    assert result == {"ls_account_ratio": None, "taker_buy_sell_ratio": None, "oi_4h_delta": None}


def test_compute_returns_copy_not_reference() -> None:
    flow_mod._FLOW_CACHE["BTCUSDT"] = {"ls_account_ratio": 0.5, "taker_buy_sell_ratio": 0.6, "oi_4h_delta": 0.01}
    result = compute("BTCUSDT")
    result["ls_account_ratio"] = 99.9
    assert flow_mod._FLOW_CACHE["BTCUSDT"]["ls_account_ratio"] == 0.5  # cache unchanged


def test_compute_returns_null_for_unknown_symbol() -> None:
    flow_mod._FLOW_CACHE["ETHUSDT"] = {"ls_account_ratio": 0.4, "taker_buy_sell_ratio": 0.55, "oi_4h_delta": -0.02}
    result = compute("SOLUSDT")
    assert result == {"ls_account_ratio": None, "taker_buy_sell_ratio": None, "oi_4h_delta": None}


# ---------------------------------------------------------------------------
# update_flow_cache() — async fetch + update
# ---------------------------------------------------------------------------

def _make_mock_transport(
    ls_rows: list | None = None,
    buy_rows: list | None = None,
    sell_rows: list | None = None,
    oi_rows: list | None = None,
    *,
    raise_on: str | None = None,
) -> httpx.MockTransport:
    """Build a mock transport that returns configured responses per endpoint."""

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if raise_on and raise_on in path:
            raise httpx.NetworkError("mock network error")
        if "globalLongShortAccountRatio" in path:
            data = ls_rows if ls_rows is not None else [{"longAccount": "0.52"}]
            return httpx.Response(200, json=data)
        if "takerbuyvolume" in path:
            data = buy_rows if buy_rows is not None else [{"buyVol": "600.0"}]
            return httpx.Response(200, json=data)
        if "takersellvolume" in path:
            data = sell_rows if sell_rows is not None else [{"sellVol": "400.0"}]
            return httpx.Response(200, json=data)
        if "openInterestHist" in path:
            data = oi_rows if oi_rows is not None else [
                {"sumOpenInterest": "100.0"},
                {"sumOpenInterest": "102.0"},
                {"sumOpenInterest": "104.0"},
                {"sumOpenInterest": "106.0"},
                {"sumOpenInterest": "110.0"},
            ]
            return httpx.Response(200, json=data)
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
        buy_rows=[{"buyVol": "60.0"}],
        sell_rows=[{"sellVol": "40.0"}],
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
        buy_rows=[{"buyVol": "70.0"}],
        sell_rows=[{"sellVol": "30.0"}],
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
        if "globalLongShortAccountRatio" in req.url.path:
            return httpx.Response(200, json=[{"longAccount": "0.50"}])
        if "takerbuyvolume" in req.url.path:
            return httpx.Response(200, json=[{"buyVol": "50.0"}])
        if "takersellvolume" in req.url.path:
            return httpx.Response(200, json=[{"sellVol": "50.0"}])
        if "openInterestHist" in req.url.path:
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
        buy_rows=[{"buyVol": "0.0"}],
        sell_rows=[{"sellVol": "0.0"}],
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


# ---------------------------------------------------------------------------
# update_flow_cache_and_persist() — item 3, continuous time series storage
# ---------------------------------------------------------------------------

_CREATE_FLOW_SNAPSHOTS_TABLE = (
    "CREATE TABLE flow_feature_snapshots ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, "
    "captured_at TEXT NOT NULL DEFAULT (datetime('now')), "
    "ls_account_ratio REAL, taker_buy_sell_ratio REAL, oi_4h_delta REAL, "
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
    transport = _make_mock_transport(
        ls_rows=[{"longAccount": "0.42"}],
        buy_rows=[{"buyVol": "70.0"}],
        sell_rows=[{"sellVol": "30.0"}],
    )
    async with httpx.AsyncClient(transport=transport) as http:
        await update_flow_cache_and_persist(
            "ADAUSDT", http=http, base_url="https://fapi.binance.com",
        )
    async with _session_factory() as session:
        row = (await session.execute(sa.text(
            "SELECT symbol, ls_account_ratio, taker_buy_sell_ratio, oi_4h_delta "
            "FROM flow_feature_snapshots"
        ))).one()
    assert row.symbol == "ADAUSDT"
    assert row.ls_account_ratio == pytest.approx(0.42)
    assert row.taker_buy_sell_ratio == pytest.approx(0.70)
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
