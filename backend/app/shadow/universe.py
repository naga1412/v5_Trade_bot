import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssetUniverseEntry:
    symbol: str
    quote_volume_usd_24h: float
    rank: int


async def fetch_top_n_usdt_spot(
    http: httpx.AsyncClient,
    *,
    base_url: str = "https://api.binance.com",
    n: int = 30,
) -> list[AssetUniverseEntry]:
    """Fetch top N USDT-quoted Binance SPOT pairs by 24h quote volume.

    Historically this function pulled from Binance **Futures**
    (``fapi.binance.com/fapi/v1/ticker/24hr``), which returns top
    perpetual contracts by volume. Many of those (1000PEPEUSDT,
    SKYAIUSDT, MUUSDT, leveraged tokens, etc.) only exist on Futures
    — the SPOT exchange has no listing for them.

    But the shadow worker subscribes via the SPOT WebSocket
    (``stream.binance.com:9443``) because the Hetzner-Helsinki host
    is geoblocked from Futures WS (see MEMORY → binance_futures_ws_geoblock).
    Net result: 15 of 30 universe symbols 400-Bad-Requested every hour
    in the scanner, and only 15 contributed any scoring activity at all.

    Fix: source the universe from the SPOT 24h ticker endpoint. Every
    selected symbol is guaranteed to resolve on SPOT — the scanner now
    operates on the full 30 instead of the previous 15.

    Volumes ranked across the SPOT market differ from Futures rankings
    (typically more conservative — major coins dominate), but the
    selection is still top-N-by-liquidity which is exactly what the
    universe wants.
    """
    response = await http.get(f"{base_url}/api/v3/ticker/24hr", timeout=15.0)
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


# Backward-compatibility alias — keep the old name resolvable so any
# external script or operator note that still references
# ``fetch_top_n_usdt_futures`` keeps working (it just now fetches from
# SPOT internally). Safe to remove after one release cycle.
fetch_top_n_usdt_futures = fetch_top_n_usdt_spot


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
             "r": entry.rank, "ts": now},
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


async def load_shadow_universe(
    session: AsyncSession, narrow: list[str],
) -> list[str]:
    """Load the active universe, intersected with SHADOW_NARROW_UNIVERSE if non-empty.

    Per PR3 spec §4.4:
      - Empty list → use full top-30 universe (PR1 behavior preserved).
      - Non-empty + has overlap → return intersection ordered by universe rank.
      - Non-empty + zero overlap → log WARNING and fall back to full universe
        (fail-loud-then-fail-open). A typo'd ``SHADOW_NARROW_UNIVERSE=["BTCUSD"]``
        (wrong suffix) should NOT silently produce an empty worker.

    Empty top-30 (no snapshot yet) falls back to a single-symbol BTCUSDT list
    so the worker doesn't bail on cold-start; matches PR1 behavior.
    """
    rows = await load_current_universe(session)
    full = [e.symbol for e in rows]
    if not full:
        log.warning("shadow universe empty; using BTCUSDT fallback")
        return ["BTCUSDT"]

    if not narrow:
        return full

    narrow_set = set(narrow)
    intersection = [s for s in full if s in narrow_set]
    if not intersection:
        log.warning(
            "SHADOW_NARROW_UNIVERSE=%r has no overlap with current universe "
            "(first 5 of %d: %r); falling back to full universe to avoid "
            "an empty worker",
            narrow, len(full), full[:5],
        )
        return full
    return intersection
