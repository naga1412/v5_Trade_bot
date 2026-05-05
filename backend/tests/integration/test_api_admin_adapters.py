"""Integration tests for /api/v1/admin/adapters/* (SP-3 Phase F)."""
from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa


@pytest.mark.asyncio
async def test_health_endpoint_returns_latest_per_exchange(
    admin_client: Any, auth_factory: Any,
) -> None:
    async with auth_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO adapter_health (exchange, checked_at, is_healthy, "
            "latency_ms, quota_used_pct) VALUES "
            "('binance', '2026-05-05 10:00:00', 1, 42, 0.12), "
            "('binance', '2026-05-05 11:00:00', 1, 38, 0.18), "
            "('bybit',   '2026-05-05 10:30:00', 0, 999, 0.99)"
        ))
        await session.commit()
    r = await admin_client.get("/api/v1/admin/adapters/health")
    assert r.status_code == 200, r.text
    body = {item["exchange"]: item for item in r.json()}
    assert body["binance"]["latency_ms"] == 38   # latest row wins
    assert body["bybit"]["is_healthy"] is False


@pytest.mark.asyncio
async def test_universe_endpoint_lists_with_filters(
    admin_client: Any, auth_factory: Any,
) -> None:
    async with auth_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO universe_history "
            "(exchange, symbol, asset_class, listed_at, last_synced_at, "
            "delisted_at) VALUES "
            "('binance', 'BTC/USDT', 'crypto', '2017-08-17', '2026-05-01', "
            "NULL), "
            "('binance', 'LUNA/USDT', 'crypto', '2020-08-01', '2026-05-01', "
            "'2022-05-12'), "
            "('yahoo',   'AAPL',     'stock',  '2000-01-01', '2026-05-01', "
            "NULL)"
        ))
        await session.commit()
    r = await admin_client.get(
        "/api/v1/admin/universe?exchange=binance&active=true",
    )
    assert r.status_code == 200, r.text
    syms = [u["symbol"] for u in r.json()]
    assert "BTC/USDT" in syms
    assert "LUNA/USDT" not in syms
    assert "AAPL" not in syms


@pytest.mark.asyncio
async def test_sync_endpoint_triggers_sync_and_returns_counts(
    admin_client: Any, monkeypatch: Any,
) -> None:
    """POST /admin/adapters/binance/sync invokes sync_universe + returns counts."""
    from app.data import adapters
    from app.data.adapters._base import SymbolInfo

    class FakeAdapter:
        name = "binance"

        async def list_symbols(self) -> list[SymbolInfo]:
            return [SymbolInfo(
                canonical="BTC/USDT", native="BTCUSDT",
                base="BTC", quote="USDT",
                listed_at=None, delisted_at=None, asset_class="crypto",
            )]

        async def fetch_klines(self, **kwargs: Any) -> list[Any]:
            return []

    fake = FakeAdapter()
    monkeypatch.setitem(adapters._INSTANCES, "binance", fake)

    r = await admin_client.post("/api/v1/admin/adapters/binance/sync")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exchange"] == "binance"
    assert body["added"] == 1


@pytest.mark.asyncio
async def test_health_endpoint_403_for_non_admin(friend_client: Any) -> None:
    r = await friend_client.get("/api/v1/admin/adapters/health")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_sync_unknown_exchange_returns_404(admin_client: Any) -> None:
    r = await admin_client.post("/api/v1/admin/adapters/kraken/sync")
    assert r.status_code == 404
