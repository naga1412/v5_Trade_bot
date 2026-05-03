from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class AssetUniverseEntry:
    symbol: str
    quote_volume_usd_24h: float
    rank: int


async def fetch_top_n_usdt_futures(
    http: httpx.AsyncClient,
    *,
    base_url: str = "https://fapi.binance.com",
    n: int = 30,
) -> list[AssetUniverseEntry]:
    """Fetch top N USDT-quoted Binance Futures perpetuals by 24h quote volume."""
    response = await http.get(f"{base_url}/fapi/v1/ticker/24hr", timeout=15.0)
    response.raise_for_status()

    tickers = response.json()
    usdt_only = [
        t for t in tickers
        if t.get("symbol", "").endswith("USDT")
    ]
    usdt_only.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
    top_n = usdt_only[:n]

    return [
        AssetUniverseEntry(
            symbol=t["symbol"],
            quote_volume_usd_24h=float(t["quoteVolume"]),
            rank=i + 1,
        )
        for i, t in enumerate(top_n)
    ]


async def save_universe_snapshot(
    session: AsyncSession, entries: list[AssetUniverseEntry]
) -> datetime:
    """Insert the snapshot. All entries share the same snapshot_at for grouping."""
    now = datetime.now(timezone.utc)
    for entry in entries:
        await session.execute(
            sa.text(
                "INSERT INTO asset_universe "
                "(symbol, quote_volume_usd_24h, rank, snapshot_at) "
                "VALUES (:s, :v, :r, :ts)"
            ),
            {"s": entry.symbol, "v": entry.quote_volume_usd_24h,
             "r": entry.rank, "ts": now.isoformat()},
        )
    return now


async def load_current_universe(
    session: AsyncSession,
) -> list[AssetUniverseEntry]:
    """Load the most recent snapshot (all entries with the latest snapshot_at)."""
    result = await session.execute(
        sa.text(
            "SELECT symbol, quote_volume_usd_24h, rank "
            "FROM asset_universe "
            "WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM asset_universe) "
            "ORDER BY rank ASC"
        )
    )
    return [
        AssetUniverseEntry(symbol=row.symbol,
                           quote_volume_usd_24h=row.quote_volume_usd_24h,
                           rank=row.rank)
        for row in result
    ]
