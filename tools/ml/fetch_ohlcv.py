"""Bulk-fetch BTC/USDT 1h history from Binance public klines API.

SP-1.1 — produces the training dataset for the first Conv-LSTM checkpoint.

The original SP-1 design assumed bulk export from production Postgres into
B2; the Hetzner deploy dropped B2, so this script pulls straight from
Binance's public REST endpoint (no auth, no DB needed). 2017-08-17 (BTC
launch on Binance) through "today" at 1h timeframe is ~70-80k bars across
~70-80 paginated requests at 1000 bars/request. Wall time: ~30 seconds.

Output: a single Parquet file with columns
    timestamp (UTC datetime, index), open, high, low, close, volume
sorted ascending, deduped, no gaps.

Usage:
    python -m tools.ml.fetch_ohlcv \\
        --symbol BTCUSDT --interval 1h \\
        --start 2017-08-17 --end 2026-05-07 \\
        --out data/ml/btcusdt_1h.parquet

The Binance klines endpoint is rate-limited at 1200 req/min for public
IP, well above what this script needs even at full historical pull.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests


log = logging.getLogger(__name__)

BINANCE_BASE = "https://api.binance.com"
KLINES_PATH = "/api/v3/klines"
MAX_BARS_PER_REQUEST = 1000

INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def _to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_klines_page(
    *, symbol: str, interval: str, start_ms: int, end_ms: int,
    session: requests.Session,
) -> list[list]:
    """Fetch up to MAX_BARS_PER_REQUEST klines for [start_ms, end_ms]."""
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": MAX_BARS_PER_REQUEST,
    }
    resp = session.get(BINANCE_BASE + KLINES_PATH, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def fetch_all(
    *, symbol: str, interval: str, start: datetime, end: datetime,
) -> pd.DataFrame:
    """Paginate Binance klines from start to end. Returns OHLCV DataFrame."""
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval {interval!r}; pick from {list(INTERVAL_MS)}")
    step_ms = INTERVAL_MS[interval] * MAX_BARS_PER_REQUEST
    cursor_ms = _to_ms(start)
    final_ms = _to_ms(end)

    rows: list[list] = []
    sess = requests.Session()
    sess.headers["User-Agent"] = "trading-radar-ml-fetch/1.0"

    page_count = 0
    while cursor_ms < final_ms:
        page_end = min(cursor_ms + step_ms, final_ms)
        page = fetch_klines_page(
            symbol=symbol, interval=interval,
            start_ms=cursor_ms, end_ms=page_end, session=sess,
        )
        page_count += 1
        if not page:
            cursor_ms = page_end + 1
            continue
        rows.extend(page)
        last_open_ms = int(page[-1][0])
        # Advance cursor to one millisecond past the last bar's open time so
        # the next page starts cleanly (Binance includes startTime, so adding
        # 1 ms here is what avoids the duplicate at the page boundary).
        cursor_ms = last_open_ms + INTERVAL_MS[interval]
        if page_count % 20 == 0:
            log.info(
                "fetched page %d, %d total bars so far, cursor=%s",
                page_count, len(rows),
                datetime.fromtimestamp(cursor_ms / 1000, tz=timezone.utc),
            )
        # Trivial pacing — 50ms between pages keeps us well under the
        # 1200/min weight budget while staying fast on a full pull.
        time.sleep(0.05)

    if not rows:
        raise RuntimeError(
            f"no bars returned for {symbol} {interval} {start}..{end}"
        )

    # Binance kline schema (12 fields):
    # [open_ms, open, high, low, close, volume, close_ms, quote_vol,
    #  trades, taker_buy_base, taker_buy_quote, ignore]
    df = pd.DataFrame(rows, columns=[
        "open_ms", "open", "high", "low", "close", "volume",
        "close_ms", "quote_vol", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    df["timestamp"] = pd.to_datetime(df["open_ms"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    log.info(
        "fetched %d bars across %d pages, range %s..%s",
        len(df), page_count, df.index[0], df.index[-1],
    )
    return df


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tools.ml.fetch_ohlcv")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h", choices=sorted(INTERVAL_MS))
    p.add_argument("--start", required=True, help="YYYY-MM-DD UTC")
    p.add_argument(
        "--end",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="YYYY-MM-DD UTC (defaults to today)",
    )
    p.add_argument("--out", required=True, help="output Parquet path")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    df = fetch_all(
        symbol=args.symbol, interval=args.interval, start=start, end=end,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, compression="snappy")
    log.info("wrote %s (%d rows, %d MB)",
             out_path, len(df), out_path.stat().st_size // (1024 * 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
