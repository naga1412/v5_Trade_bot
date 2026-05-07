"""Bulk-fetch BTC/USDT 1h history from Binance.

SP-1.1 — produces the training dataset for the first Conv-LSTM checkpoint.

Two data sources, picked by ``--source``:

* ``archive`` (default): pulls monthly ZIP files from Binance's public
  archive CDN ``data.binance.vision``. **This works from Google Colab
  and any cloud datacenter** — the archive is geographically unrestricted
  unlike the live API. Trade-off: the most recent ~1 day may be missing
  (archive is updated daily). Fine for training a 8-year history model.

* ``api``: pulls from ``api.binance.com/api/v3/klines``. Faster + always
  current, but **HTTP 451 from Google Cloud / AWS / Azure datacenter IPs**
  due to Binance's regional compliance. Use only from your own machine
  in a non-restricted region.

Output: a single Parquet file with columns
    timestamp (UTC datetime, index), open, high, low, close, volume
sorted ascending, deduped, no gaps.

Usage:
    python -m tools.ml.fetch_ohlcv \\
        --symbol BTCUSDT --interval 1h \\
        --start 2017-08-17 --end 2026-05-07 \\
        --out data/ml/btcusdt_1h.parquet \\
        --source archive
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests


log = logging.getLogger(__name__)

BINANCE_BASE = "https://api.binance.com"
KLINES_PATH = "/api/v3/klines"
MAX_BARS_PER_REQUEST = 1000

# Public CDN — never geo-restricted. URL pattern:
#   https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2024-04.zip
ARCHIVE_BASE = "https://data.binance.vision/data/spot/monthly/klines"

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


def _month_iter(start: datetime, end: datetime) -> list[tuple[int, int]]:
    """Yield (year, month) pairs covering [start, end] inclusive of months."""
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def fetch_archive_month(
    *, symbol: str, interval: str, year: int, month: int,
    session: requests.Session,
) -> pd.DataFrame | None:
    """Download one monthly ZIP from data.binance.vision and return its bars.

    Returns ``None`` for months that don't exist (e.g., before the symbol
    listed, or current month not yet published). Surfaces network errors.
    """
    url = (
        f"{ARCHIVE_BASE}/{symbol}/{interval}/"
        f"{symbol}-{interval}-{year:04d}-{month:02d}.zip"
    )
    resp = session.get(url, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()

    # ZIP contains one CSV with 12 cols matching the API's kline schema.
    df = pd.read_csv(
        BytesIO(resp.content),
        compression="zip",
        header=None,
        names=[
            "open_ms", "open", "high", "low", "close", "volume",
            "close_ms", "quote_vol", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ],
    )
    return df


def fetch_all_archive(
    *, symbol: str, interval: str, start: datetime, end: datetime,
) -> pd.DataFrame:
    """Pull monthly archive ZIPs from data.binance.vision for the date range.

    Works from any IP (Colab / cloud / personal). Resolution is monthly
    so the partial start/end months are pulled in full and trimmed at
    the end. Most recent ~24h may be missing (archive lag).
    """
    months = _month_iter(start, end)
    sess = requests.Session()
    sess.headers["User-Agent"] = "trading-radar-ml-fetch/1.1-archive"

    frames: list[pd.DataFrame] = []
    fetched_count = 0
    for i, (y, m) in enumerate(months, start=1):
        try:
            df_m = fetch_archive_month(
                symbol=symbol, interval=interval,
                year=y, month=m, session=sess,
            )
        except requests.RequestException as e:
            log.warning("archive fetch %s-%02d failed: %s", y, m, e)
            continue
        if df_m is None:
            log.debug("archive %s-%02d not present (404), skipping", y, m)
            continue
        frames.append(df_m)
        fetched_count += 1
        if i % 12 == 0:
            log.info(
                "archive: %d/%d months fetched (%d total months had data)",
                i, len(months), fetched_count,
            )
        time.sleep(0.05)  # gentle pacing on the CDN

    if not frames:
        raise RuntimeError(
            f"no archive data for {symbol} {interval} {start.date()}..{end.date()}"
        )

    df = pd.concat(frames, ignore_index=True)

    # Binance flipped the archive's timestamp unit from milliseconds to
    # MICROSECONDS at the 2024-12 → 2025-01 boundary. Older months have
    # 13-digit values (ms), newer have 16-digit (μs). We auto-detect
    # per-row by magnitude rather than per-month so a mixed-range pull
    # (the common SP-1.1 case) works without per-file branching.
    open_ms_int = pd.to_numeric(df["open_ms"], errors="coerce")
    # Anything > 1e14 is microseconds (year > 5138 in millis = absurd);
    # anything ≤ 1e14 is milliseconds. The cutoff sits between any
    # plausible ms timestamp (≤ 9.5e12 in 2270) and any plausible μs
    # timestamp (≥ 1.5e15 from 2017 onward).
    is_micro = open_ms_int > 1e14
    df["open_ms"] = open_ms_int

    # Convert each subset to a datetime Series, then merge by index so
    # the output column is a single tz-aware datetime64[ns, UTC] dtype
    # (avoids pandas' object-vs-datetime mixed-dtype FutureWarning).
    ms_idx = df.index[~is_micro]
    us_idx = df.index[is_micro]
    ts_ms = pd.to_datetime(df.loc[ms_idx, "open_ms"], unit="ms", utc=True)
    ts_us = pd.to_datetime(df.loc[us_idx, "open_ms"], unit="us", utc=True)
    df["timestamp"] = pd.concat([ts_ms, ts_us]).reindex(df.index)
    # Drop any rows that didn't parse (malformed or stale header rows).
    df = df.dropna(subset=["timestamp"])
    df = df.set_index("timestamp")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    # Trim to the requested window (the partial first/last months may
    # extend beyond it).
    df = df.loc[start:end]
    log.info(
        "archive: %d bars across %d months, range %s..%s",
        len(df), fetched_count, df.index[0], df.index[-1],
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
    p.add_argument(
        "--source",
        default="archive",
        choices=("archive", "api"),
        help=(
            "archive = data.binance.vision monthly ZIPs (works everywhere, "
            "including Colab; default). api = api.binance.com/api/v3/klines "
            "(faster + current, but HTTP 451 from cloud datacenter IPs)."
        ),
    )
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    if args.source == "archive":
        df = fetch_all_archive(
            symbol=args.symbol, interval=args.interval, start=start, end=end,
        )
    else:
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
