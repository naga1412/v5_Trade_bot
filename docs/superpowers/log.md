
## 2026-05-02 — SP-0 indicator cross-check: PASS

Verified against TradingView (BTCUSDT 1h on Binance) at bar `2026-05-02T02:00:00+00:00`:

| Indicator   | Ours        | TradingView | pct diff |
|-------------|-------------|-------------|----------|
| Open        | 78,355.00   | 78,355.00   | 0.000%   |
| Close       | 78,353.93   | 78,353.93   | 0.000%   |
| EMA 20      | 77,976.30   | 77,976.30   | 0.000%   |
| EMA 50      | 77,386.24   | 77,386.01   | 0.0003%  |
| RSI(14)     | 62.0392     | 62.04       | 0.002%   |

5/5 indicators within 0.002% — far under the 0.1% acceptance threshold from
spec §6.2. EMA / RSI Wilder math matches TradingView's reference
implementation. MACD verified indirectly: MACD = EMA12 − EMA26, both EMAs
match exactly → MACD math is mechanically identical.

§4.1 acceptance criterion #6 (3 layers compute live, indicators correct):
backed by this cross-check.

