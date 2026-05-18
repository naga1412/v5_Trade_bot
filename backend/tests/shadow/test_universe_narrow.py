"""PR3 spec §4.4: SHADOW_NARROW_UNIVERSE filter semantics.

Covers the 3 cases the spec mandates:
  - Empty narrow list → use full top-30 universe (PR1 behavior).
  - Non-empty narrow + at least one overlap → intersection ordered by rank.
  - Non-empty narrow + zero overlap → log WARN + fall back to full universe.

The empty-universe fallback (cold-start with no asset_universe snapshot)
returns ['BTCUSDT'] — verified separately.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.shadow.universe import load_shadow_universe


async def _mk_engine(symbols: list[str]) -> Any:
    """Seed asset_universe with the given symbols at rank 1..N."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE asset_universe ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "quote_volume_usd_24h REAL NOT NULL, "
            "rank INTEGER NOT NULL, "
            "snapshot_at TEXT NOT NULL DEFAULT (datetime('now')))"
        ))
        for i, sym in enumerate(symbols, start=1):
            await conn.execute(sa.text(
                "INSERT INTO asset_universe (symbol, quote_volume_usd_24h, rank) "
                "VALUES (:s, :v, :r)"
            ), {"s": sym, "v": 1e9 / i, "r": i})
    return engine


@pytest.mark.asyncio
async def test_empty_narrow_returns_full_universe() -> None:
    """Empty narrow list = PR1 behavior preserved."""
    engine = await _mk_engine(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    async with AsyncSession(engine) as s:
        out = await load_shadow_universe(s, narrow=[])
    assert out == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


@pytest.mark.asyncio
async def test_narrow_intersects_with_universe() -> None:
    """Non-empty narrow + overlap → intersection, ordered by universe rank."""
    engine = await _mk_engine([
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT",
    ])
    async with AsyncSession(engine) as s:
        # narrow includes a symbol NOT in universe (XRPUSDT) — should be ignored
        out = await load_shadow_universe(
            s, narrow=["SOLUSDT", "BTCUSDT", "XRPUSDT"],
        )
    # Result preserves universe rank order: BTC(rank1) before SOL(rank3)
    assert out == ["BTCUSDT", "SOLUSDT"]


@pytest.mark.asyncio
async def test_narrow_zero_overlap_falls_back_to_full_universe(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Spec §4.4: typo'd narrow (e.g. ['BTCUSD'] missing T) must NOT silently
    kill the worker. Log a WARNING and fall back to the full universe."""
    caplog.set_level(logging.WARNING, logger="app.shadow.universe")
    engine = await _mk_engine(["BTCUSDT", "ETHUSDT"])
    async with AsyncSession(engine) as s:
        out = await load_shadow_universe(s, narrow=["BTCUSD", "ETHUSD"])
    assert out == ["BTCUSDT", "ETHUSDT"]  # full fallback
    assert any("no overlap" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_empty_universe_returns_btc_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cold-start (no asset_universe snapshot) returns ['BTCUSDT']."""
    caplog.set_level(logging.WARNING, logger="app.shadow.universe")
    engine = await _mk_engine([])  # no symbols inserted
    async with AsyncSession(engine) as s:
        out = await load_shadow_universe(s, narrow=[])
    assert out == ["BTCUSDT"]


@pytest.mark.asyncio
async def test_empty_universe_with_narrow_still_returns_btc_fallback() -> None:
    """Cold-start with a narrow set → still falls back to BTC (no source to
    intersect with). The narrow filter is silently inert because the
    fail-loud-on-empty-universe branch runs first."""
    engine = await _mk_engine([])
    async with AsyncSession(engine) as s:
        out = await load_shadow_universe(s, narrow=["SOLUSDT"])
    assert out == ["BTCUSDT"]
