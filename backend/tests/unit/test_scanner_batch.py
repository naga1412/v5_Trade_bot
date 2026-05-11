"""Unit tests for app/scanner/batch.py — 60s multi-asset batch scanner.

Drives ``run_one_pass`` with stub adapters so we can deterministically
verify the cache is filled, falling back to the default watchlist when
asset_universe is empty, and skipping individual symbols when fetches
fail (without breaking the rest of the batch).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.scanner import batch
from app.scanner.fast_scan import (
    FastScanResult,
    ModuleScore,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def universe_factory():
    """In-memory SQLite mirror of the asset_universe table."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE asset_universe ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "quote_volume_usd_24h REAL NOT NULL, "
            "rank INTEGER NOT NULL, "
            "snapshot_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "UNIQUE (symbol, snapshot_at))"
        ))
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with an empty cache."""
    batch._clear_cache_for_tests()
    yield
    batch._clear_cache_for_tests()


def _fake_scan_result(symbol: str, score: float = 30.0) -> FastScanResult:
    return FastScanResult(
        symbol=symbol,
        timeframe="1h",
        final_score=score,
        direction="LONG" if score > 5 else "SHORT" if score < -5 else "NEUTRAL",
        confidence=0.7,
        tier="probable",
        phase="markup",
        modules=[
            ModuleScore("trend", score, 0.30),
            ModuleScore("momentum", score * 0.8, 0.30),
            ModuleScore("volatility", 20, 0.10),
            ModuleScore("volume", 10, 0.10),
            ModuleScore("structure", score * 0.5, 0.20),
        ],
        last_close=100.0,
        expected_move_pct=2.5,
        rr_estimate=2.0,
    )


# ---------------------------------------------------------------------------
# _to_pair
# ---------------------------------------------------------------------------


def test_to_pair_strips_usdt_suffix() -> None:
    assert batch._to_pair("BTCUSDT") == "BTC/USDT"
    assert batch._to_pair("ethusdt".upper()) == "ETH/USDT"


def test_to_pair_handles_unknown_suffix() -> None:
    # Unknown quote currency — pass through unchanged.
    assert batch._to_pair("FOOBAR") == "FOOBAR"


# ---------------------------------------------------------------------------
# _load_watchlist — DB-backed + fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_watchlist_uses_default_when_table_empty(universe_factory) -> None:
    async with universe_factory() as session:
        result = await batch._load_watchlist(session)
    # No rows in asset_universe → fallback list.
    assert "BTC/USDT" in result or "BTCUSDT" in result or len(result) > 0
    # The default watchlist contains slash-form pairs.
    assert any("/" in s for s in result)


@pytest.mark.asyncio
async def test_load_watchlist_reads_latest_snapshot(universe_factory) -> None:
    async with universe_factory() as session:
        for i, sym in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT")):
            await session.execute(sa.text(
                "INSERT INTO asset_universe (symbol, quote_volume_usd_24h, rank) "
                "VALUES (:s, :v, :r)"
            ), {"s": sym, "v": 1e9 - i * 1e8, "r": i + 1})
        await session.commit()
        result = await batch._load_watchlist(session)
    # All 3 symbols come back, in rank order.
    assert result[:3] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


# ---------------------------------------------------------------------------
# run_one_pass — happy path + per-symbol failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_one_pass_fills_cache_for_each_symbol(universe_factory) -> None:
    factory = universe_factory
    async with factory() as session:
        for i, sym in enumerate(("BTCUSDT", "ETHUSDT")):
            await session.execute(sa.text(
                "INSERT INTO asset_universe (symbol, quote_volume_usd_24h, rank) "
                "VALUES (:s, :v, :r)"
            ), {"s": sym, "v": 1e9, "r": i + 1})
        await session.commit()

    async def _fake_scan(_http: Any, pair: str) -> FastScanResult:
        return _fake_scan_result(pair, score=42.0)

    with patch.object(batch, "_scan_one_symbol", _fake_scan):
        n = await batch.run_one_pass(factory, http=object(), stagger_ms=0)  # type: ignore[arg-type]
    assert n == 2
    snap = batch.get_cache_snapshot()
    assert ("BTC/USDT", "1h") in snap
    assert ("ETH/USDT", "1h") in snap
    assert snap[("BTC/USDT", "1h")].result.final_score == 42.0


@pytest.mark.asyncio
async def test_run_one_pass_skips_symbols_that_fail(universe_factory) -> None:
    factory = universe_factory
    async with factory() as session:
        for i, sym in enumerate(("BTCUSDT", "FAILUSDT", "ETHUSDT")):
            await session.execute(sa.text(
                "INSERT INTO asset_universe (symbol, quote_volume_usd_24h, rank) "
                "VALUES (:s, :v, :r)"
            ), {"s": sym, "v": 1e9, "r": i + 1})
        await session.commit()

    async def _flaky_scan(_http: Any, pair: str) -> FastScanResult | None:
        if pair == "FAIL/USDT":
            raise httpx.HTTPError("boom")
        return _fake_scan_result(pair)

    with patch.object(batch, "_scan_one_symbol", _flaky_scan):
        n = await batch.run_one_pass(factory, http=object(), stagger_ms=0)  # type: ignore[arg-type]
    # BTC + ETH succeed; FAIL is skipped.
    assert n == 2
    snap = batch.get_cache_snapshot()
    assert ("BTC/USDT", "1h") in snap
    assert ("ETH/USDT", "1h") in snap
    assert ("FAIL/USDT", "1h") not in snap


@pytest.mark.asyncio
async def test_run_one_pass_returns_zero_when_fast_scan_returns_none(
    universe_factory,
) -> None:
    """fast_scan returns None for sub-60-bar symbols — must not blow up."""
    factory = universe_factory
    async with factory() as session:
        await session.execute(sa.text(
            "INSERT INTO asset_universe (symbol, quote_volume_usd_24h, rank) "
            "VALUES ('NEWUSDT', 1e6, 1)"
        ))
        await session.commit()

    async def _too_short(_http: Any, _pair: str) -> FastScanResult | None:
        return None

    with patch.object(batch, "_scan_one_symbol", _too_short):
        n = await batch.run_one_pass(factory, http=object(), stagger_ms=0)  # type: ignore[arg-type]
    assert n == 0
    assert batch.cache_size() == 0


# ---------------------------------------------------------------------------
# get_cache_snapshot returns a copy
# ---------------------------------------------------------------------------


def test_get_cache_snapshot_returns_a_copy() -> None:
    batch._CACHE[("BTC/USDT", "1h")] = batch.CachedScan(
        result=_fake_scan_result("BTC/USDT"),
        scanned_at=datetime(2026, 5, 12, 12, 0),
    )
    snap = batch.get_cache_snapshot()
    assert ("BTC/USDT", "1h") in snap
    # Mutating the snapshot should not affect the real cache.
    snap.clear()
    assert ("BTC/USDT", "1h") in batch._CACHE
