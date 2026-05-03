
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

---

## 2026-05-03 — SP-0.5 Bot Status tab + multi-asset shadow trading: SHIPPED

**Scope:** added a second tab (Bot Status) to the app and a multi-asset shadow-trading subsystem behind it. Top-30 USDT-quoted Binance Futures (refreshed daily at 00:00 UTC), 1h closed-candle scoring, max 30 concurrent paper positions, $30 fixed size, 30-min per-asset cooldown, 24-bar timeout, ATR-based SL/TP. Hash-chained `shadow_trades` table per the audit policy.

**Delivered (43 commits on branch `sp-0.5/main`):**

| Phase | Sub-system | Commits |
|---|---|---|
| A   | Worktree | 1 |
| B   | Migration 0003 (4 tables) | 1 |
| C   | Asset universe fetcher + persistence | 2 |
| D   | Shadow signal engine (entry rule, position gate) | 3 |
| E   | Exit monitor (SL/TP/timeout, pessimistic SL-first) | 1 |
| F   | Persistence (open positions + closed trades hash-chained) | 1 |
| G   | Multi-stream Binance combined-stream reader | 2 |
| H   | Stats (win rate, profit factor, RR, Sharpe, max DD) | 2 |
| I   | Worker orchestrator + lifespan + universe refresh job | 3 |
| J   | 8 REST endpoints under `/api/v1/bot-status/*` + `/predict?signal=` | 10 |
| K   | WS `shadow_updates` channel (typed publishers) | 1 |
| L   | Frontend tab nav + hash-route hook | 2 |
| M   | Frontend API client + `useShadowUpdates` hook | 2 |
| N   | 7 Bot Status sections (Overview, PromotionGate, OpenPositions, PerAsset, LongShort, Equity, RecentTrades) + assembly | 8 |
| O   | Tab1 `?signal=` deeplink + TVChart entry/SL/TP markers | 2 |
| P   | Multi-asset E2E worker test + Playwright Bot Status spec | 2 |
| —   | Maintenance (vitest exclude e2e dir) | 1 |

**Test counts at ship:**
- Backend: **199 passing** (was 47 at SP-0; +152 new)
- Frontend Vitest: **88 passing** (was 10 at SP-0; +78 new)
- Frontend Playwright: 4 specs × 2 device projects = 8 cases listed clean

**Surprises / decisions worth flagging:**

- **Promotion gate computation** uses spec §4.1 thresholds (Telegram-approve mode): 30d window, ≥30 days continuous trading, ≥100 trades, Sharpe ≥1.0, max DD ≤12%, win rate ≥40%, PF ≥1.5. Fully-auto gate (§4.2) deferred to SP-8.
- **Profit factor `inf` JSON encoding:** capped at `999.0` (`_PROFIT_FACTOR_INF_CAP`) when no losses present. Frontend should treat `≥999` as "no losses yet".
- **`/bot-status/open-positions` returns `current_price=null`** intentionally — live price comes through WS `shadow_pnl_tick` instead of a Binance round-trip on every cold load.
- **Hash deeplink:** chose `#/live-prediction?signal=xyz` (URL-hash + query) instead of adding `react-router-dom`. Custom `useHashRoute` with `URLSearchParams` parser keeps deps minimal.
- **Worker DI:** `ShadowWorker` accepts `session_factory`, `reader`, `seed_history` injection points. Made E2E testing tractable — full multi-asset scenario with 30 candles across 3 symbols runs in ~0.5s with monkeypatched `build_prediction`.
- **Audit chain integrity confirmed in P1:** `verify_chain` on `shadow_trades` returns ok with valid `prev_hash`/`row_hash` linkage starting from `GENESIS_HASH`. Same chain policy as SP-0 `predictions` table.
- **Pre-existing predict tests fixed:** the J10 work (`/predict?signal=`) inadvertently repaired 2 pre-existing `test_api_predict.py` tests that were broken under `ENV=test` due to unset Cloudflare Access settings. They now run green via the new `bot_status_client` fixture which overrides `require_cf_user`.

**Manual P3 checklist (for the human after deploy):**
- [ ] All 7 sections render correctly on mobile (375px width)
- [ ] No horizontal scroll on Bot Status tab
- [ ] Touch targets ≥44px on tab nav and section controls
- [ ] Signal deeplink from RecentTrades opens Tab1 with markers visible
- [ ] WS reconnects on Cloudflare Tunnel restart (kill tunnel, observe reconnect)
- [ ] DB row counts match closed trades shown in UI

**Next:** ship Q1 (PR sp-0.5/main → main) and Q2 (tag `sp-0.5`). Then begin SP-0.7 (multi-user wrapper) per the meta-plan.

