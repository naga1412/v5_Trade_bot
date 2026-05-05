"""Manual one-shot historical OHLCV import from Binance (SP-3 Phase F).

Pulls daily klines for the symbols currently active in ``universe_history``
between START and END (or for the lifetime of each symbol). Writes to the
existing ``ohlcv`` table. Skips symbols with no universe_history row.

Cron scheduling is deferred to SP-7. Run manually:

    docker compose exec backend python -m tools.data.bulk_import_binance \\
        --start 2024-01-01 --end 2026-01-01

This script is INTENTIONALLY a single-shot CLI — see meta-plan §5.15.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from app.data.adapters import get_adapter
from app.db.session import get_session_factory


async def main(start: datetime, end: datetime) -> None:
    factory = get_session_factory()
    adapter = get_adapter("binance")

    async with factory() as session:
        rows = (await session.execute(sa.text(
            "SELECT symbol FROM universe_history "
            "WHERE exchange='binance' AND delisted_at IS NULL"
        ))).all()
    symbols = [r.symbol for r in rows]
    print(f"importing {len(symbols)} symbols from {start} to {end}")

    for sym in symbols:
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=500), end)
            bars = await adapter.fetch_klines(
                symbol=sym, timeframe="1d", limit=500,
                start=cursor, end=chunk_end,
            )
            print(
                f"{sym}: fetched {len(bars)} bars "
                f"[{cursor.date()}..{chunk_end.date()}]",
            )
            # Insert into ohlcv table (existing schema from SP-0).
            async with factory() as session:
                for c in bars:
                    await session.execute(sa.text(
                        "INSERT INTO ohlcv (symbol, timeframe, ts, "
                        "open, high, low, close, volume) VALUES "
                        "(:s, '1d', :ts, :o, :h, :l, :c, :v) "
                        "ON CONFLICT (symbol, timeframe, ts) DO NOTHING"
                    ), {
                        "s": sym, "ts": c.ts,
                        "o": c.open, "h": c.high, "l": c.low,
                        "c": c.close, "v": c.volume,
                    })
                await session.commit()
            cursor = chunk_end


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="2024-01-01")
    parser.add_argument("--end", type=str, default="2026-01-01")
    args = parser.parse_args()
    s = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    e = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    asyncio.run(main(s, e))
