# tools/

Manual scripts the human runs at validation gates.

## validate_indicators.py

Per meta-plan §6.2: tolerance 0.1% absolute against TradingView.

```bash
cd backend
python ../tools/validate_indicators.py BTCUSDT 1h 200 > /tmp/check.csv
```

Then open `/tmp/check.csv` in a spreadsheet:
1. Pick 10 random rows where second-to-last bar (NOT the last partial bar).
2. Open TradingView with same symbol, timeframe, RSI(14), EMA(20/50/200), MACD(12,26,9).
3. Fill `tv_value` column with TV's value at the same timestamp.
4. Compute `pct_diff = abs(ours - tv) / tv * 100`.
5. PASS if all rows ≤ 0.1%. FAIL if any row > 0.1%.
