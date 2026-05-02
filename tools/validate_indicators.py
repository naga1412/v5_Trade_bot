"""Cross-check our indicators vs TradingView.

Usage:
    python tools/validate_indicators.py BTCUSDT 1h 200

Outputs a CSV to stdout:
    ts,close,our_rsi14,our_ema20,our_ema50,our_ema200,our_macd_line,our_macd_signal

Open this CSV in a spreadsheet, manually fill TV values for 100 random rows,
compute pct diff, fail any row outside 0.1% tolerance.
"""
import asyncio
import csv
import sys

import httpx
import numpy as np

from app.core.indicators.ema import ema
from app.core.indicators.macd import macd
from app.core.indicators.rsi import rsi
from app.data.adapters.binance import BinanceClient


async def main(symbol: str, timeframe: str, limit: int) -> None:
    async with httpx.AsyncClient() as http:
        client = BinanceClient(http=http)
        candles = await client.fetch_klines(symbol, timeframe, limit=limit)

    closes = np.array([c.close for c in candles], dtype=np.float64)
    rsi14 = rsi(closes, 14)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    macd_line, macd_signal, _ = macd(closes, 12, 26, 9)

    writer = csv.writer(sys.stdout)
    writer.writerow([
        "ts","close","our_rsi14","our_ema20","our_ema50","our_ema200",
        "our_macd_line","our_macd_signal","tv_value (FILL MANUALLY)","pct_diff",
    ])
    for i, c in enumerate(candles):
        writer.writerow([
            c.ts.isoformat(), f"{c.close:.4f}",
            f"{rsi14[i]:.4f}" if not np.isnan(rsi14[i]) else "",
            f"{ema20[i]:.4f}" if not np.isnan(ema20[i]) else "",
            f"{ema50[i]:.4f}" if not np.isnan(ema50[i]) else "",
            f"{ema200[i]:.4f}" if not np.isnan(ema200[i]) else "",
            f"{macd_line[i]:.6f}" if not np.isnan(macd_line[i]) else "",
            f"{macd_signal[i]:.6f}" if not np.isnan(macd_signal[i]) else "",
            "", "",
        ])


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    tf = sys.argv[2] if len(sys.argv) > 2 else "1h"
    lim = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    asyncio.run(main(sym, tf, lim))
