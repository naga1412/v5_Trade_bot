"""Seed `universe_history` with hand-picked Yahoo symbols (SP-3 Phase D).

Yahoo has no list-all endpoint — operators run this script once to bootstrap
the universe with the macro / equity instruments the bot is allowed to query.
Idempotent: ON CONFLICT DO NOTHING on (exchange, symbol).

Usage:
    docker compose exec backend python -m tools.data.seed_yahoo_symbols
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
    {"symbol": "DXY", "asset_class": "index"},
    {"symbol": "SPX", "asset_class": "index"},
    {"symbol": "NDX", "asset_class": "index"},
    {"symbol": "VIX", "asset_class": "index"},
    {"symbol": "GOLD", "asset_class": "commodity"},
    {"symbol": "OIL", "asset_class": "commodity"},
    {"symbol": "SPY", "asset_class": "stock"},
    {"symbol": "QQQ", "asset_class": "stock"},
    {"symbol": "GLD", "asset_class": "commodity"},
    {"symbol": "AAPL", "asset_class": "stock"},
    {"symbol": "EUR/USD", "asset_class": "fx"},
    {"symbol": "USD/JPY", "asset_class": "fx"},
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
                    "VALUES ('yahoo', :s, :cls, :listed, :md) "
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
    print(f"seeded {len(SEEDS)} yahoo symbols")


if __name__ == "__main__":
    asyncio.run(main())
