import pytest
import respx
import httpx

from app.shadow.universe import fetch_top_n_usdt_futures, AssetUniverseEntry


SAMPLE_24H_RESPONSE = [
    {"symbol": "BTCUSDT", "quoteVolume": "1234567890.0"},
    {"symbol": "ETHUSDT", "quoteVolume": "987654321.0"},
    {"symbol": "SOLUSDT", "quoteVolume": "543210987.0"},
    {"symbol": "BNBUSDT", "quoteVolume": "345678901.0"},
    {"symbol": "XRPUSDT", "quoteVolume": "234567890.0"},
    {"symbol": "BTCUSDC", "quoteVolume": "999999999.0"},  # not USDT, must be excluded
    {"symbol": "DOGEUSDT", "quoteVolume": "123456789.0"},
]


@pytest.mark.asyncio
async def test_fetch_top_n_usdt_futures_returns_sorted_usdt_only() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://fapi.binance.com"
    ) as router:
        router.get("/fapi/v1/ticker/24hr").mock(
            return_value=httpx.Response(200, json=SAMPLE_24H_RESPONSE)
        )
        entries = await fetch_top_n_usdt_futures(
            http=http, base_url="https://fapi.binance.com", n=3
        )

    assert len(entries) == 3
    assert all(isinstance(e, AssetUniverseEntry) for e in entries)
    # Sorted by volume desc, USDT only
    assert entries[0].symbol == "BTCUSDT"
    assert entries[0].rank == 1
    assert entries[0].quote_volume_usd_24h == 1234567890.0
    assert entries[1].symbol == "ETHUSDT"
    assert entries[2].symbol == "SOLUSDT"
    # USDC excluded
    assert all(e.symbol.endswith("USDT") for e in entries)


import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.shadow.universe import save_universe_snapshot, load_current_universe


@pytest.mark.asyncio
async def test_save_and_load_universe_snapshot() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE asset_universe ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "quote_volume_usd_24h REAL NOT NULL, "
            "rank INTEGER NOT NULL, "
            "snapshot_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "UNIQUE (symbol, snapshot_at))"
        ))

    entries = [
        AssetUniverseEntry("BTCUSDT", 1.2e9, 1),
        AssetUniverseEntry("ETHUSDT", 9.8e8, 2),
        AssetUniverseEntry("SOLUSDT", 5.4e8, 3),
    ]
    async with AsyncSession(engine) as session:
        snapshot_at = await save_universe_snapshot(session, entries)
        await session.commit()

        current = await load_current_universe(session)

    assert snapshot_at is not None
    assert len(current) == 3
    assert current[0].symbol == "BTCUSDT"
    assert current[0].rank == 1
