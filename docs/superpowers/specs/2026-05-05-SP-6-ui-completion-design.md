# SP-6 — UI Completion Design Spec

**Date:** 2026-05-05
**Status:** Approved (autonomous-mode default; user can redirect)
**Implementation target:** Sub-project SP-6 (after SP-2/3/5 ship; all done)
**Depends on:** SP-2 (158 patterns + L2), SP-3 (4 adapters), SP-5 (10 layers + 17 traps + tier classification)
**Companion specs:** `MASTER_PLAN.md` §9 (UI specs), `2026-05-01-trading-radar-meta-plan-design.md` §3 §180

---

## 1. Purpose

Complete the user interface to the bar set by MASTER_PLAN §9: **Tab 1 with all 14 sidebar panels populated** with real data from the 10 scoring layers, **Tab 3 Scanner Radar** with bullish/bearish columns + signal cards + 2-min refresh, **3 admin sub-pages** (Patterns / Adapters / Traps) deferred from SP-2/3/5, **mobile responsive at 375px**, and **Lighthouse score ≥80** on the deployed app.

After SP-6 ships, the bot has feature-complete UI exposing every backend capability built across SP-0 → SP-5. The remaining SP-7 (Ops hardening) handles backtesting + monitoring; SP-8 (Autonomous Trading) handles real-money execution.

### Non-goals

- **No new scoring/trading logic** — SP-6 is UI only. Any data missing in the backend stays missing in the UI (panel shows "—").
- **No new backend endpoints** beyond the 3 admin sub-pages already gated through SP-2/3/5 backends. Tab 3 Scanner Radar needs ONE new backend endpoint (`GET /api/v1/scanner/radar`) that aggregates per-asset prediction state — defined in §3.6.
- **No mobile-native app.** Responsive web at 375px width is the target.
- **No charting library beyond lightweight-charts.** Existing dependency.
- **No new state management library** (no Redux, no Zustand). React hooks + the existing `useCurrentUser`/`useShadowUpdates` patterns.

---

## 2. Locked decisions

| # | Decision | Value |
|---|---|---|
| 1 | Tab 1 panel count | **14 panels** per MASTER_PLAN §9 (current: 4 shipped) |
| 2 | Tab 3 Scanner | **NEW tab** with bullish/bearish columns, refresh every 2 min (configurable) |
| 3 | Admin sub-pages | **3 NEW** — Patterns (SP-2), Adapters (SP-3), Traps (SP-5) |
| 4 | Mobile breakpoint | **375px** (iPhone SE / mini); panels stack vertically below `md:` (768px) |
| 5 | Lighthouse target | **≥80** for Performance, Accessibility, Best Practices, SEO |
| 6 | Charting library | **lightweight-charts** (already installed; SP-1 added ghost candle support) |
| 7 | Sparkline rendering | **Pure SVG** (already used in SP-0.5 EquityCurve component) |
| 8 | Per-panel data source | Each panel calls ONE existing `api.*()` method or subscribes to ONE WS channel |
| 9 | Empty-state convention | Each panel renders "—" or "no data" placeholder when its data source returns null/empty (matches SP-0.5 Bot Status pattern) |
| 10 | Auto-refresh interval | Tab 1 panels react to WS `live_prediction` events; Tab 3 polls every 2 min OR subscribes to new `scanner_updates` WS channel |

---

## 3. Architecture

### 3.1 Module layout

```
frontend/src/
├── App.tsx                                — extend tab nav with "Scanner" tab
├── lib/api.ts                              — extend with scanner.radar() + admin.{patterns,adapters,traps}()
├── tabs/
│   ├── Tab1LivePrediction/
│   │   ├── index.tsx                       — extend sidebar to render 14 panels
│   │   └── panels/
│   │       ├── TradeStatusBar.tsx          ✓ exists
│   │       ├── MasterBiasScore.tsx         ✓ exists
│   │       ├── MomentumIndicators.tsx      ✓ exists
│   │       ├── TradeSetup.tsx              ✓ exists
│   │       ├── FinalValue.tsx              NEW
│   │       ├── LongShortRatio.tsx          NEW
│   │       ├── DeepLearningSupervisor.tsx  NEW (Tab 1 panel #5; renders L8 Conv-LSTM warning)
│   │       ├── HtfBiasStructure.tsx        NEW (L1 macro layer score + Wyckoff)
│   │       ├── VolumeProfile.tsx           NEW
│   │       ├── MarketMicrostructure.tsx    NEW
│   │       ├── LiquiditySweep.tsx          NEW (reads L4 SMC fires)
│   │       ├── OiFundingRate.tsx           NEW
│   │       ├── IntermarketAnalysis.tsx     NEW
│   │       ├── SentimentFearGreed.tsx      NEW
│   │       ├── GhostCandlePrediction.tsx   NEW (reads predictions.ghost_*)
│   │       ├── KeyLevels.tsx               NEW (EMA 20/50/200)
│   │       └── NewsMacroImpact.tsx         NEW
│   ├── Tab3Scanner/                        NEW (entirely)
│   │   ├── index.tsx                       — toolbar + bullish/bearish columns
│   │   ├── ScannerToolbar.tsx              — search, market, TF, refresh interval
│   │   ├── SignalCard.tsx                  — individual asset card (5 rows per spec)
│   │   ├── BullishColumn.tsx               — green-titled list
│   │   ├── BearishColumn.tsx               — red-titled list
│   │   ├── HybridSupervisorBar.tsx         — cyan progress bar "X/8 done"
│   │   ├── FilterPills.tsx                 — All/Confirmed/Probable/Weak/Diverging filters
│   │   └── hooks/
│   │       └── useScannerRadar.ts          — REST poll + optional WS subscription
│   └── Settings/                           — existing
│       └── ...
└── components/
    ├── Admin/
    │   ├── PatternsAdmin.tsx               NEW (SP-2 deferred)
    │   ├── AdaptersAdmin.tsx               NEW (SP-3 deferred)
    │   ├── TrapsAdmin.tsx                  NEW (SP-5 deferred)
    │   └── ...
```

### 3.2 Tab 1 — 14 sidebar panels mapping to backend data

| # | Panel | Backend source | Empty state |
|---|---|---|---|
| 1 | Trade Status Bar | `data.trade_setup.direction` + `final.tier` | "NEUTRAL" |
| 2 | Master Bias Score | `data.final.score` (-100 to +100 scale) | "—" |
| 3 | Final Value | `data.final.score` + `data.trade_setup.{risk_reward,...}` | "—" |
| 4 | Long/Short Ratio | Computed from `data.layer_scores` (count LONG vs SHORT scores) | "50/50" |
| 5 | Deep Learning Supervisor | `data.layer_scores["8"]` (Conv-LSTM score) — red alert if SHORT + confidence > 0.75 | hidden when no L8 data |
| 6 | HTF Bias & Structure | `data.layer_scores["1"]` (macro) + Wyckoff phase from L4 SMC notes | "—" |
| 7 | Volume Profile | `data.layer_scores["5"]` notes (POC/VAH/VAL — needs backend extension OR derive from indicators) | "—" |
| 8 | Momentum Indicators | `data.momentum.{rsi, macd_*}` | "—" |
| 9 | Market Microstructure | `data.layer_scores["4"]` (SMC notes) | "—" |
| 10 | Liquidity Sweep | `data.layer_scores["4"]` notes (sweep field) + `data.traps_fired` for liquidity_sweep | "no sweep" |
| 11 | OI & Funding Rate | Live data from Binance/Bybit adapters (NOT YET WIRED — defer to SP-6.5 or show "no data") | "no data" |
| 12 | Intermarket Analysis | DXY corr from Yahoo adapter; gold corr — need new computation (defer or stub) | "no data" |
| 13 | Sentiment & Fear/Greed | NEW endpoint reading external F&G API (defer to SP-9) | "no data" |
| 14 | Ghost Candle Prediction | `data.ghost.{open, high, low, close, uncertainty}` (already exists from SP-1) | "no model" |
| 15 | Trade Setup | `data.trade_setup.{entry, stop_loss, take_profit, risk_reward}` | "—" |
| 16 | Key Levels | Compute from bars: EMA20, EMA50, EMA200 (existing indicators from SP-2) | "—" |
| 17 | News & Macro Impact | NEW — no backend yet (defer to SP-9) | "no events" |

**Note: 17 panels listed but spec says 14.** Per MASTER_PLAN §9, panels 1-14 are the canonical list; 15-17 are "additional" panels that may be combined/dropped. SP-6 ships **all 17 as separate components** for forward-compat; the user can hide panels via a future preference.

### 3.3 Mobile responsiveness (≤375px)

Per spec §1: each panel stacks vertically below `md:` (768px) breakpoint. The chart area collapses to a smaller height; sidebar becomes a swipeable bottom sheet OR stacks below the chart.

**Decision: stack below chart** (simpler than bottom sheet; matches SP-0.5 Bot Status mobile pattern).

### 3.4 Tab 3 Scanner Radar

Per MASTER_PLAN §9 (lines 304-332):

**Toolbar:** search, watchlist add, "★ Add" pill, market dropdown ("Crypto 200+"), timeframe dropdown ("1h"), asset count input (200), refresh interval input ("2 min"), sort dropdown ("AI Score"), filter pills (All / Confirmed / Probable / Weak / Diverging / Hybrid / Analyzing).

**Hybrid Supervisor cyan progress bar** below toolbar.

**Two columns:**
- **Bullish (green title)** — assets with `final.direction == LONG` and `tier >= PAPER`, sorted by `final.score`
- **Bearish (red title)** — same for SHORT

**Each signal card (5 rows):**
- Row 1: `★ favorite + symbol + full_name | sparkline + ±points badge`
- Row 2 (tags): `LONG/SHORT solid + 4h LONG/SHORT outlined + Confirmed/Probable badge + Hybrid dot + AI ±score + Wyckoff phase`
- Row 3: `±%change` right-aligned
- Row 4: `confidence bar + "Conf X%"`
- Row 5: `score tags (SMC ±N, Wyckoff ±N, Microstructure ±N, Momentum ±N)`

**Footer:** `"Scanning N assets • {tf}" left | "Auto-refresh every X min • Click card to view chart" right`

**Click handler:** opens Tab 1 with `?symbol=X&tf=Y` deeplink (signal_id deeplink already exists from SP-0.5).

### 3.5 Admin sub-pages (deferred from SP-2/3/5)

Each sub-page mirrors the existing SP-0.7 `Admin/Users.tsx` structure: table of items, per-row actions, "Add" button.

**PatternsAdmin** — calls `api.adminListPatterns()` (need to add to api.ts), shows all 158 patterns + per-(symbol, tf) enabled state, toggle button.

**AdaptersAdmin** — calls `api.adminAdapterHealth()`, shows 4-row table with health status + last-sync time + manual sync trigger button.

**TrapsAdmin** — calls `api.adminListTraps()`, shows all 17 traps + enabled state, toggle button.

### 3.6 NEW backend endpoint for Tab 3

`GET /api/v1/scanner/radar?market=crypto&tf=1h&limit=200`

Returns:
```json
{
  "scanned_at": "2026-05-05T...",
  "scanned_count": 186,
  "filter_counts": {"all": 186, "confirmed": 38, "probable": 34, "weak": 65},
  "bullish": [{symbol, full_name, points, pct_change, direction, signal_tier, ai_score, confidence, scores, sparkline, ...}],
  "bearish": [...]
}
```

Implementation:
- Query `predictions` table for latest row per symbol in the universe (use SP-3's `universe_history`)
- Filter by `final.direction` LONG/SHORT
- Sort by `final.score` desc (bullish) / asc (bearish)
- Compute sparkline from last 20 bars (cached via Redis with 2-min TTL)
- Auth-gated via `Depends(require_user)` (per-user filter on `user_id` from SP-0.7)

Latency budget: <500ms for 200 assets. Achievable with bulk SQL query + Redis cache for sparklines.

---

## 4. Data model

**No new tables.** Tab 3 Scanner reads from existing `predictions` table (filtered by latest row per symbol).

---

## 5. Lighthouse target — Performance ≥80

Existing patterns (SP-0.5/0.7/1) already pass Lighthouse Performance ≥80 due to:
- Vite production build with code-splitting
- React 18 concurrent rendering
- Tailwind purge

SP-6 risk areas:
- 200 signal cards in Tab 3 Scanner — needs virtualization or pagination
- 14 panels making 14 separate API calls — needs to be reduced to 1 (already is, since they all read from `data.*` returned by `useLivePrediction`)

**Decision:** Tab 3 paginates 50 cards per direction (configurable); Lighthouse audit run in CI as a check.

---

## 6. Sub-project sequencing

SP-6 implementation order (~6 weeks, parallel-friendly):

- **Phase A — Worktree + scaffolding + Tab 3 backend endpoint + 3 admin sub-pages mounted as stubs** (~6 tasks)
- **Phase B — Tab 1 panels 5-13 (9 new panels)** — parallel-safe, ~9 tasks (one per panel)
- **Phase C — Tab 1 panels 14-17 (4 new panels)** — parallel-safe, ~4 tasks
- **Phase D — Tab 3 Scanner Radar full implementation** (~8 tasks: toolbar, columns, signal card, hooks, integration)
- **Phase E — Admin sub-pages: PatternsAdmin + AdaptersAdmin + TrapsAdmin** (~6 tasks)
- **Phase F — Mobile responsive pass + Lighthouse audit + ship** (~5 tasks)

After SP-6 ships:
- **SP-7** — Ops hardening (backtest framework, monitoring, hyperopt for layer weights)
- **SP-8** — Autonomous trading (already specced; gated on SP-1.1 + SP-4)
- **SP-9** — News + sentiment (FinBERT for L9; populates news panels)

---

## 7. Cross-cutting policy compliance

| Policy | How SP-6 satisfies it |
|---|---|
| §2.6 Cloudflare Access | All admin sub-pages render conditionally on `useCurrentUser().isAdmin` |
| Per-user (SP-0.7) | `useScannerRadar` calls `/api/v1/scanner/radar` which uses `current_user.id` for filtering |
| Audit chain | No new write paths in SP-6 (UI is read-only of audit-chained data) |
| Lighthouse | Runs in CI via `npx lhci autorun` (new dep) — fails CI if Performance/Accessibility < 80 |

---

## 8. Risk + fallback plan

| Failure mode | Detection | Fallback |
|---|---|---|
| Panels 11-13 (OI, Intermarket, Sentiment) have no backend data source | Manual inspection — panels show "no data" | OK — empty state by design; SP-9 fills sentiment, SP-3.5 fills OI/funding |
| Tab 3 query latency > 500ms with 200 assets | API timing logs | Add Redis cache; reduce default limit to 100; add pagination |
| Lighthouse Performance < 80 | CI fails | Lazy-load less-frequently-used panels; defer non-critical JS |
| Mobile layout breaks at 375px | Manual check + Playwright mobile spec | Each panel must have a mobile-fallback CSS class; explicit test |
| 158 pattern toggle list overwhelms PatternsAdmin UI | UX feedback | Add filter + pagination (50 per page) |

---

## 9. Acceptance criteria

- [ ] Tab 1 sidebar shows all 17 panel slots (some may show "no data" if backend lacks the source)
- [ ] Tab 3 Scanner Radar tab visible in nav; renders bullish + bearish columns; refresh button works
- [ ] Admin → Patterns sub-page lists 158 patterns with toggle
- [ ] Admin → Adapters sub-page shows 4 adapters' health + manual sync button
- [ ] Admin → Traps sub-page lists 17 traps with toggle
- [ ] Mobile (375px): panels stack vertically; no horizontal scroll on any tab
- [ ] Lighthouse audit: Performance ≥80, Accessibility ≥80, Best Practices ≥80
- [ ] Click signal card on Tab 3 → opens Tab 1 with that symbol + tf
- [ ] No regression in existing 1175+ backend tests
- [ ] Frontend Vitest test count: existing ~187 + ~80-100 new = ~270
- [ ] Playwright E2E: at least 3 new specs (tab3-scanner, admin-patterns, mobile-responsive)

---

## 10. Implementation cost estimate

- Sub-project size: **~38 tasks across 6 phases**
- Wall-clock: **~6 weeks of subagent-driven work** (per meta-plan §3 §180 with 5-subagent parallelism)
- New backend modules: 1 (`app/api/routes/scanner.py`)
- New frontend modules: ~25 (panels + scanner components + admin sub-pages)
- New tests: ~80-100 frontend + ~10 backend
- Database migrations: 0 (no schema changes)
- Lighthouse target enforced in CI

---

## 11. Reference

- MASTER_PLAN: `files/MASTER_PLAN.md` §9 (UI specs)
- Meta-plan: `docs/superpowers/specs/2026-05-01-trading-radar-meta-plan-design.md` §3 §180
- SP-0.5 spec (Bot Status panels): `docs/superpowers/specs/2026-05-03-SP-0.5-bot-status-tab-design.md`
- SP-2 spec (patterns): `docs/superpowers/specs/2026-05-05-SP-2-indicators-patterns-design.md`
- SP-3 spec (adapters): `docs/superpowers/specs/2026-05-05-SP-3-data-adapters-universe-design.md`
- SP-5 spec (traps): `docs/superpowers/specs/2026-05-05-SP-5-full-scoring-traps-design.md`

---

**END OF SP-6 UI COMPLETION DESIGN SPEC**
