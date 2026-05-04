"""Integration tests for GET /api/v1/bot-status/asset-universe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa


async def _insert_snapshot(
    factory: Any, *, snapshot_at: datetime, entries: list[tuple[str, float, int]],
) -> None:
    async with factory() as session:
        for symbol, qv, rank in entries:
            await session.execute(sa.text(
                "INSERT INTO asset_universe "
                "(symbol, quote_volume_usd_24h, rank, snapshot_at) "
                "VALUES (:s, :v, :r, :ts)"
            ), {"s": symbol, "v": qv, "r": rank, "ts": snapshot_at.isoformat()})
        await session.commit()


@pytest.mark.asyncio
async def test_asset_universe_returns_latest_snapshot(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    older = datetime(2026, 5, 1, tzinfo=UTC)
    newer = older + timedelta(hours=2)
    await _insert_snapshot(bot_status_factory, snapshot_at=older,
                           entries=[("OLD1USDT", 1.0, 1), ("OLD2USDT", 0.5, 2)])
    await _insert_snapshot(bot_status_factory, snapshot_at=newer,
                           entries=[
                               ("BTCUSDT", 9_000_000.0, 1),
                               ("ETHUSDT", 5_000_000.0, 2),
                               ("SOLUSDT", 2_000_000.0, 3),
                           ])

    r = await bot_status_client.get("/api/v1/bot-status/asset-universe")
    assert r.status_code == 200
    body = r.json()
    assert body["snapshot_at"].startswith("2026-05-01T02:00:00")
    assert len(body["entries"]) == 3
    syms = [e["symbol"] for e in body["entries"]]
    assert syms == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    ranks = [e["rank"] for e in body["entries"]]
    assert ranks == [1, 2, 3]
    assert body["entries"][0]["quote_volume_24h_usdt"] == pytest.approx(9_000_000.0)


@pytest.mark.asyncio
async def test_asset_universe_empty_returns_empty_response(
    bot_status_client: Any,
) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/asset-universe")
    assert r.status_code == 200
    body = r.json()
    assert body["entries"] == []
