"""End-to-end test of the universe sync flow with a mocked Binance API."""
from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
import sqlalchemy as sa


_EXCHANGE_INFO = {
    "symbols": [
        {"symbol": "BTCUSDT", "status": "TRADING",
         "baseAsset": "BTC", "quoteAsset": "USDT",
         "isSpotTradingAllowed": True},
        {"symbol": "ETHUSDT", "status": "TRADING",
         "baseAsset": "ETH", "quoteAsset": "USDT",
         "isSpotTradingAllowed": True},
        {"symbol": "DEAD", "status": "BREAK",
         "baseAsset": "X", "quoteAsset": "USDT",
         "isSpotTradingAllowed": False},
    ],
}


@pytest.mark.asyncio
async def test_full_sync_flow_inserts_then_marks_delisted(
    auth_factory: Any,
) -> None:
    """First sync inserts symbols; second sync flips disappeared ones to delisted."""
    from app.data.adapters.binance import BinanceAdapter
    from app.data.universe_sync import sync_universe

    # First sync: 2 spot-tradable symbols inserted (DEAD is non-TRADING).
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com",
    ) as router:
        router.get("/api/v3/exchangeInfo").mock(
            return_value=httpx.Response(200, json=_EXCHANGE_INFO),
        )
        adapter = BinanceAdapter(http=http)
        async with auth_factory() as session:
            r1 = await sync_universe(adapter, session)
            await session.commit()
    assert r1.added == 2
    assert r1.still_active == 0
    assert r1.newly_delisted == 0

    # Second sync: ETH disappears -> newly_delisted=1.
    eth_gone = {"symbols": [
        {"symbol": "BTCUSDT", "status": "TRADING",
         "baseAsset": "BTC", "quoteAsset": "USDT",
         "isSpotTradingAllowed": True},
    ]}
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com",
    ) as router:
        router.get("/api/v3/exchangeInfo").mock(
            return_value=httpx.Response(200, json=eth_gone),
        )
        adapter = BinanceAdapter(http=http)
        async with auth_factory() as session:
            r2 = await sync_universe(adapter, session)
            await session.commit()
    assert r2.newly_delisted == 1

    # Verify ETH delisted_at is now set.
    async with auth_factory() as session:
        row = (await session.execute(sa.text(
            "SELECT delisted_at FROM universe_history "
            "WHERE exchange='binance' AND symbol='ETH/USDT'"
        ))).first()
    assert row is not None
    assert row.delisted_at is not None
