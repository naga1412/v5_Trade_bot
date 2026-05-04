"""One-time historical bulk export for the first Conv-LSTM training run.

Pulls all-time 1h OHLCV for the top-N USDT-quoted Binance Futures perpetuals
via the Binance REST API, writes ~5 GB of Parquet to the local ``--out``
directory, then upload to B2 manually with rclone or ``aws s3 cp``.

Usage::

    docker compose exec backend python -m tools.ml.bulk_export \\
        --out /app/data/ml-bulk-export \\
        --start 2017-08-01 --end 2026-05-04 \\
        --top-n 30

Not committed to a cron — operator runs once. Subsequent training runs use the
nightly incremental from ``app/ml/export.py`` (last 30 days).

KNOWN GAP — B2 upload (Phase A scope):
    Same as ``app/ml/export.py``: this script writes to LOCAL DISK only. Upload
    to B2 is a manual ``rclone copy`` step until the proper backup pipeline
    lands (Phase C or a tiny SP-0.8 backup sub-project, whichever comes first).

KNOWN GAP — start_ms support on BinanceClient:
    The existing ``app.data.adapters.binance.BinanceClient.fetch_klines()``
    does not accept ``startTime``; it only fetches the most-recent ``limit``
    bars. For historical pagination we hit the REST endpoint directly via
    ``httpx`` and walk forwards by start time. If/when ``BinanceClient`` is
    extended to support ``startTime``/``endTime``, this module should be
    refactored to use the adapter for rate-limit consistency.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.shadow.universe import fetch_top_n_usdt_futures

log = logging.getLogger(__name__)

_BINANCE_FUTURES_KLINES = "https://fapi.binance.com/fapi/v1/klines"


async def fetch_history_for_symbol(
    http: httpx.AsyncClient,
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    interval: str = "1h",
    limit: int = 1000,
) -> pd.DataFrame:
    """Page through Binance 1h klines from ``start`` to ``end`` (1000 per request).

    ``symbol`` is in Binance Futures form (e.g. ``BTCUSDT``).
    """
    all_rows: list[dict] = []
    cursor_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    while cursor_ms < end_ms:
        params: dict[str, str | int] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
            "startTime": cursor_ms,
            "endTime": end_ms,
        }
        response = await http.get(_BINANCE_FUTURES_KLINES, params=params, timeout=15.0)
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        for row in batch:
            all_rows.append({
                "ts": row[0],            # open time (ms)
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            })
        # advance cursor past the last open-time we just received
        last_ts = int(batch[-1][0])
        next_cursor = last_ts + 1
        if next_cursor <= cursor_ms:
            break
        cursor_ms = next_cursor
        # Light politeness sleep to stay under the 2400 weight/min REST limit
        await asyncio.sleep(0.05)

    if not all_rows:
        return pd.DataFrame(
            columns=["ts", "open", "high", "low", "close", "volume"]
        )
    df = pd.DataFrame(all_rows)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--top-n", type=int, default=30,
        help="Number of top-volume USDT perpetuals to export (default 30)",
    )
    args = parser.parse_args()

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = (
        datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
        + timedelta(hours=1)  # inclusive
    )

    manifest: dict = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "files": {},
    }

    async with httpx.AsyncClient(timeout=60.0) as http:
        universe = await fetch_top_n_usdt_futures(http, n=args.top_n)
        log.info("fetched %d symbols", len(universe))
        for entry in universe:
            log.info("exporting %s (rank %d)", entry.symbol, entry.rank)
            df = await fetch_history_for_symbol(http, entry.symbol, start, end)
            target = out / f"ohlcv_1h_{entry.symbol}.parquet"
            pq.write_table(
                pa.Table.from_pandas(df),
                target,
                compression="snappy",
            )
            manifest["files"][target.name] = {"rows": len(df)}
            log.info("  wrote %d rows -> %s", len(df), target.name)

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"bulk export complete: {out}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
