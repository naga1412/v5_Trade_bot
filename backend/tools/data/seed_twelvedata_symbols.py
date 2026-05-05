"""Seed `universe_history` with hand-picked TwelveData symbols (SP-3 Phase E).

TwelveData's symbol-list endpoints (`/stocks`, `/forex_pairs`, etc.) are paid;
the free tier requires a manual seed. Operators run this script once to
bootstrap the universe with the stock + FX instruments the bot is allowed to
query via TwelveData. Idempotent: ON CONFLICT DO NOTHING on (exchange, symbol).

Usage:
    docker compose exec backend python -m tools.data.seed_twelvedata_symbols
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TypedDict

from app.db.session import get_session_factory


class _Seed(TypedDict):
    symbol: str
    asset_class: str


SEEDS: list[_Seed] = [
    {"symbol": "AAPL", "asset_class": "stock"},
    {"symbol": "MSFT", "asset_class": "stock"},
    {"symbol": "GOOG", "asset_class": "stock"},
    {"symbol": "TSLA", "asset_class": "stock"},
    {"symbol": "EUR/USD", "asset_class": "fx"},
    {"symbol": "USD/JPY", "asset_class": "fx"},
    {"symbol": "GBP/USD", "asset_class": "fx"},
    {"symbol": "USD/CHF", "asset_class": "fx"},
    {"symbol": "AUD/USD", "asset_class": "fx"},
    {"symbol": "NZD/USD", "asset_class": "fx"},
]


async def main() -> None:
    import sqlalchemy as sa

    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        for entry in SEEDS:
            await session.execute(
                sa.text(
                    "INSERT INTO universe_history "
                    "(exchange, symbol, asset_class, listed_at, metadata) "
                    "VALUES ('twelvedata', :s, :cls, :listed, :md) "
                    "ON CONFLICT (exchange, symbol) DO NOTHING"
                ),
                {
                    "s": entry["symbol"],
                    "cls": entry["asset_class"],
                    "listed": now,
                    "md": json.dumps({"seeded": True}),
                },
            )
        await session.commit()
    print(f"seeded {len(SEEDS)} twelvedata symbols")


if __name__ == "__main__":
    asyncio.run(main())
