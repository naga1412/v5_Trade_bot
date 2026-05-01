# trading-radar — Meta-Plan & Gap-Fix Design

**Date:** 2026-05-01
**Status:** Approved structure, awaiting user review of full doc
**Supersedes:** `files/MASTER_PLAN.md` (kept as historical reference)
**Audience:** Claude Code agents executing the build, plus the human reviewer

---

## 1. Purpose & Relationship to MASTER_PLAN.md

`MASTER_PLAN.md` is a strong product vision but is too coarse to execute as one Claude Code project. It contains 42 modules across 6 phases over 7–9 months, with several internal contradictions (Freqtrade vs custom paper engine, L7 vs L8 overlap, fabricated "solo accuracy" numbers, vague reward function), and is missing critical policies (look-ahead bias, survivorship bias, training-serving skew, catastrophic forgetting, cold-start, audit integrity, free-tier rate-limit accounting).

This document is the **single source of truth** for the build going forward. It:

1. **Resolves ambiguities** in MASTER_PLAN with explicit decisions (Section 2).
2. **Decomposes** the work into 8 independently-buildable sub-projects, each with its own brainstorm → spec → plan → implementation cycle (Section 3).
3. **Fully specifies the first sub-project (SP-0 Tracer Bullet)** so implementation can start immediately (Section 4).
4. **Codifies cross-cutting policies** that every sub-project must comply with (Sections 5 and 6).
5. **Defines the Claude Code execution model** — which skills to use, when to spawn parallel subagents, worktree strategy, phase gates (Section 7).
6. **Updates the tech stack** with deltas from MASTER_PLAN (Section 8).

Rule of use: **MASTER_PLAN.md is the *what*; this doc is the *how*.** When they conflict, this doc wins.

---

## 2. Decision Log — Ambiguities Resolved Upfront

### 2.1 Freqtrade kept as a LIBRARY, not a runtime service

**Why:** MASTER_PLAN has both M15 (Freqtrade order execution) and M32 (custom paper engine) doing the same job. Freqtrade is built for autonomous trading; this platform is explicitly a research/coach tool that may flip to autonomous later. Freqtrade's `IStrategy` DSL is incompatible with our 10-layer/trap output, forcing a permanent translation layer. But Freqtrade has two genuinely hard components worth keeping: a battle-tested backtest engine with realistic slippage/fees, and `hyperopt` (Bayesian param search).

**Decision:**
- Freqtrade as a **Python library import** used only by `tools/backtest.py` and `tools/hyperopt.py`.
- **No** Freqtrade docker service. **No** `IStrategy` as live runtime. **No** `freqtrade_user/` directory in production paths.
- Custom paper engine (~500 LOC) is the runtime today and the autonomous engine tomorrow. Flipping live = same engine, real keys, additional safety gates (`EXECUTE_REAL=true` flag, 90-day paper-perf precondition, hardware confirm).

**Removed from scope vs MASTER_PLAN:** M15 module, `backend/freqtrade_user/` directory, `freqtrade` docker service, FreqAI integration.

### 2.2 L7 vs L8 — sharpen the boundary

**Why:** MASTER_PLAN says L7 is "LSTM + XGBoost ensemble" and L8 is "Conv-LSTM SOTA fusion". They overlap.

**Decision:**
- **L7 = XGBoost on engineered features** (gradient boost on indicator/pattern features, no neural).
- **L8 = Conv-LSTM end-to-end** on raw OHLCV+volume tensors (no engineered features).

Complementary, no overlap.

### 2.3 Layer weights and "solo accuracy" — TARGETS, not facts

**Why:** MASTER_PLAN section 5 presents L1 weight=18%, "solo accuracy 78%" as if measured. They are guesses.

**Decision:**
- All 9 static layers (L1–L9) start with **equal weight = 1/9 each**.
- L10 (PPO brain) learns the actual weights from paper-trade outcomes.
- Doc explicitly labels accuracy column as "target, not measured" wherever quoted.

### 2.4 Pattern voting scheme

**Why:** 158 patterns (82 candle + 76 chart) can fire on the same candle. No conflict resolution exists in MASTER_PLAN.

**Decision:** Each pattern emits `{direction ∈ {LONG, SHORT}, strength ∈ [0,1], confidence ∈ [0,1]}`. Layer score:

```
layer_score = Σ_patterns (strength × confidence × historical_accuracy_for_this_pattern_on_this_asset_TF)
```

Per-pattern accuracy tracked in a `pattern_stats(pattern_id, symbol, timeframe, n_samples, n_correct)` table, updated nightly. Patterns with `n_samples < 50` use prior `accuracy = 0.5`.

### 2.5 Brain architecture — global brain + per-asset adapter

**Why:** Per-asset brains (200+ checkpoints) means each brain trains on tiny data, and new assets cold-start with zero history.

**Decision:**
- **One global PPO brain** trained on all assets. Input: `[asset_embedding, market_state_features, layer_scores]`.
- **Per-asset LoRA-style adapter** (~10K params) fine-tunes on top.
- Storage: 1 large checkpoint (~50 MB) + 200 small adapters (~50 KB each = ~10 MB total).
- New asset → use global brain immediately, adapter starts at zero, warms up over 100 trades.

### 2.6 Auth & exposure model

**Why:** MASTER_PLAN exposes the platform via Cloudflare Tunnel with no auth.

**Decision:**
- **Cloudflare Access (Zero Trust)** — free for <50 users — gates all HTTP routes with Google SSO.
- **FastAPI dependency** validates `Cf-Access-Jwt-Assertion` header on every route.
- Treated as a public-internet-exposed service. No "trust the network" assumptions.

### 2.7 Hosting model — Oracle Cloud Free Tier (primary) + Laptop (dev + DR backup)

**Why:** User confirmed the laptop will sometimes switch off. A research platform whose value depends on continuous learning + continuous audit trail cannot tolerate this — the nightly PPO brain training, the 2-min scanner, the paper-trade engine, and the audit hash chain must all be continuous, otherwise data is corrupted by gaps. Oracle's Always Free Ampere A1 ARM instance gives 4 vCPU + 24 GB RAM matching the laptop budget, with 99.9% uptime, for $0.

**Decision:**

**Primary host = Oracle Cloud Always Free (Ampere A1 ARM, 4 vCPU, 24 GB RAM, 200 GB block storage).** Runs:
- Postgres + TimescaleDB
- Backend FastAPI
- Celery workers + scanner workers + paper-trade workers
- Brain inference (training is offloaded to Colab)
- Cloudflare Tunnel + Cloudflare Access
- Grafana + Prometheus + Loki + MLflow

**Dev + DR mirror = Windows Laptop.** Runs:
- VS Code with Remote-SSH plugin → edit Oracle code as if it were local
- Local docker stack for fast iteration and testing (separate Postgres data)
- Cron job: nightly `pg_dump | ssh laptop` mirror of Oracle DB
- Pulls training data for ML iteration (training itself on Google Colab GPU)
- Becomes the failover if Oracle ever suspends the account

**Common to both:**
- **Container runtime:** Docker Compose (Linux ARM on Oracle; Docker Desktop + WSL2 on laptop).
- **Public access:** Cloudflare Tunnel pointed at Oracle's internal IP — free HTTPS, hides Oracle IP, survives any IP change.
- **Auth:** Cloudflare Access (Zero Trust, free <50 users) gates all public routes with Google SSO.
- **Mobile:** Same public Cloudflare URL works 24/7. Responsive React UI is a **Phase-1 / SP-0 cross-cutting requirement**.
- **Local LAN access:** `http://<laptop-lan-ip>:5173` from any device on home Wi-Fi (dev stack only — no auth gate on LAN, fast iteration).
- **Backup chain:** Hourly `pg_dump` of changed tables on Oracle → Oracle local disk; nightly full `pg_basebackup` → encrypted Backblaze B2 (10 GB free tier, ~2 GB compressed) AND nightly mirror to laptop's external SSD; brain checkpoints replicated identically.
- **Postgres tear protection:** `synchronous_commit = on`.

**Provisioning the Oracle Ampere instance:**
- Free-tier Ampere capacity is genuinely free but often "out of capacity" in any given region. Use the open-source `oci-arm-host-capacity` polling script (GitHub) to keep retrying every 60s across allowed regions until provisioned.
- Pick the region nearest the user — Mumbai, Singapore, or Hyderabad (best for India latency).
- ARM architecture: most Python wheels work; TA-Lib needs `apt install python3-talib` or build-from-source (~30 min one-time). All docker base images use ARM64 variants.

**Failure-mode plan:**

| Failure | Recovery |
|---|---|
| Oracle out-of-capacity at signup | Run polling script, expect 1–14 days wait |
| Oracle suspends account post-signup | Restore latest backup to laptop's local stack, run from there until new Oracle account or paid alternative |
| Oracle has >2 weeks of unavailability | Acceptable fallback: pay €4.50/month for Hetzner CX22 (4 vCPU, 8 GB RAM, 99.99% uptime). Documented as a deliberate violation of the "$0 forever" rule. |
| Laptop disk failure | Oracle DB is unaffected; restore laptop dev mirror from B2 |
| Cloudflare Tunnel down | App still reachable on Oracle's public IP via SSH-tunnel; restore tunnel within minutes |
| Home internet down | App still up on Oracle, only LAN/dev access lost |
| Oracle network outage | App down for outage duration; nothing user can do; rare |

**Risks accepted:**
- Brain training nightly cron must succeed even if Colab session expires; reschedule with retry on the Oracle host.
- Network egress between Cloudflare ↔ Oracle ↔ user's mobile does add ~50–100 ms latency vs local LAN. Acceptable for this UI workload (1-second WebSocket cadence is fine).

---

## 3. Sub-Project Decomposition

8 sub-projects. Each gets its own brainstorm → spec → plan → implementation cycle later. Risk-first sequencing means SP-0 ships fast; SP-1 attacks the deepest unknown (ML data pipeline + ghost candles) before more UI is built; SP-2..SP-7 fan out where parallelism is safe.

### 3.1 Dependency graph

```
SP-0 Tracer Bullet (foundation + 1-asset slice)
       │
       ├──► SP-1 ML Data Pipeline + Ghost Candles  ◄── HIGHEST RISK, do early
       │            │
       │            ├──► SP-4 RL Brain (L10) ─────────────┐
       │            │                                     │
       └──► SP-2 Indicators+Patterns Library ──┬──► SP-5 Full Scoring Engine (L1-L9 + traps)
                                               │                    │
            SP-3 Data Adapters & Universe ─────┘                    │
            (Binance/Bybit/Yahoo/TwelveData)                        │
                                                                    ▼
                                                        SP-6 UI Completion
                                                        (3 tabs, all panels, mobile)
                                                                    │
                                                                    ▼
                                                        SP-7 Ops Hardening
                                                        (DR, audit chain, monitoring,
                                                         champion-challenger, hyperopt)
```

### 3.2 Sub-project sheet

| ID | Name | Weeks | Parallel? | Exit criteria (one sentence) |
|----|------|-------|-----------|------------------------------|
| **SP-0** | Tracer Bullet | 4 | No (foundation) | BTC/USDT 1h chart + 4 panels live, paper trades log to Postgres, Cloudflare URL works on phone |
| **SP-1** | ML Data Pipeline + Ghost Candles | 6 | No (depends SP-0) | Conv-LSTM predicts 1-step OHLC with ≤1.5% MAE on hold-out, ghost candle overlay renders |
| **SP-2** | Indicators + Patterns Library | 4 | Yes (3 subagents) | All 43 indicators + 158 patterns implemented, cross-validated within 0.1% of TradingView on 100 samples |
| **SP-3** | Data Adapters + Universe | 3 | Yes (4 subagents) | All 4 exchange adapters live, point-in-time universe table populated, rate-limit guards tested |
| **SP-4** | RL Brain (L10) | 6 | No (depends SP-1) | Global PPO brain + per-asset LoRA adapter trains nightly, beats equal-weight baseline by ≥10% Sharpe on backtest |
| **SP-5** | Full Scoring + Traps | 4 | Partial (depends SP-2, SP-3) | All 10 layers + 12 traps + asymmetric long/short thresholds wired, FINAL_SCORE matches by-hand calculation on 50 fixtures |
| **SP-6** | UI Completion | 6 | Yes (5 subagents per-panel) | All 3 tabs feature-complete, all 14 sidebar panels live, mobile responsive at 375px, Lighthouse ≥80 |
| **SP-7** | Ops Hardening | 4 | Partial | Audit hash chain verified, nightly DR backup tested, champion-challenger gate auto-promotes, latency p99 <500 ms |

**Total: 37 weeks (~9 months)** — matches MASTER_PLAN budget but with much clearer milestones. Parallel work where marked compresses calendar time.

### 3.3 Sequencing rationale (the risk-first part)

Conventional plans build SP-0 → SP-2 → SP-3 → SP-5 → SP-6 → SP-1 → SP-4 → SP-7 (UI first, ML last). That's where these projects die: ML training data turns out to be insufficient or biased after 5 months of UI work, forcing a re-architect.

Risk-first inverts. As soon as SP-0 proves the rails, **SP-1 attacks the ML data pipeline immediately** with only 3 layers of scoring and no fancy UI. If ghost candles can't reach the accuracy target on real data, you find out in week 10, not month 5, and you can re-scope honestly.

### 3.4 Per-sub-project blueprint format

Every sub-project's brainstorm produces a doc with these sections (Appendix A holds the template):

1. Goal + non-goals
2. Acceptance criteria (testable, binary pass/fail)
3. Module list with interfaces (Python protocols / TypeScript types)
4. Data contracts (DB tables touched, WS messages, REST endpoints)
5. TDD test plan (which tests are written first)
6. Validation procedure (how the human checks it works)
7. Risks + mitigations
8. Dependencies on other SPs (incoming + outgoing)

---

## 4. SP-0 Tracer Bullet (fully specified here)

**Goal:** Prove every architectural rail with the smallest end-to-end slice that runs on the Oracle Ampere host (with identical docker stack also runnable on the laptop dev mirror), is reachable on the phone over LTE, and trades paper money on real BTC/USDT data.

**Non-goals:** Multi-asset. Multi-timeframe. Ghost candles. RL brain. News. Tab 2. Tab 3. More than 3 layers.

### 4.1 Acceptance criteria (binary pass/fail)

- [ ] `docker compose up` brings the full stack online on the Oracle Ampere VM with one command (and identically on the laptop dev mirror).
- [ ] Cloudflare Tunnel + Cloudflare Access serve the app at `https://trading-radar.<yourdomain>` with Google SSO gate, pointing at the Oracle host.
- [ ] Same app loads correctly on iPhone Safari and Android Chrome (responsive, no horizontal scroll, touch targets ≥44px) — verified over LTE so the laptop being on/off does not affect the test.
- [ ] Tab 1 chart shows BTC/USDT 1h candles streaming live from Binance WS (last bar updates every tick).
- [ ] 4 sidebar panels render real values (not placeholders): Trade Status Bar, Master Bias Score, Momentum (RSI/MACD), Trade Setup.
- [ ] 3 layers compute live: L1 (HTF EMA trend), L3 (RSI+MACD momentum), L5 (volume confirmation). Other 7 layers return `null` and the aggregator handles it.
- [ ] Custom paper engine takes a signal, opens/closes a virtual trade, writes to `paper_trades` table.
- [ ] Audit row written to `predictions` with all layer scores + final score + inputs hash.
- [ ] WebSocket survives a forced disconnect (kill+restart Cloudflare Tunnel) and reconnects within 5s with no missing candles.
- [ ] Postgres data persists across `docker compose down/up` on Oracle.
- [ ] Hourly `pg_dump` and nightly `pg_basebackup` cron jobs configured on Oracle; nightly mirror lands on laptop's external SSD; nightly upload reaches Backblaze B2.
- [ ] Recovery rehearsal: restore from latest B2 backup to laptop dev stack and confirm the restored Postgres has identical row counts in `predictions` and `paper_trades`.

### 4.2 Module list (with interfaces)

| Module | Interface |
|--------|-----------|
| `data.adapters.binance` | `async def stream_candles(symbol, tf) -> AsyncIterator[Candle]` |
| `data.universe` | `def is_tradable(symbol, ts) -> bool`  *(scaffolding for SP-3)* |
| `core.indicators.ema` | `def ema(closes: np.ndarray, period: int) -> np.ndarray` |
| `core.indicators.rsi` | `def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray` |
| `core.indicators.macd` | `def macd(closes) -> tuple[np.ndarray, np.ndarray, np.ndarray]` |
| `core.scoring.layer1_macro` | `def score(bars: pd.DataFrame) -> LayerScore` |
| `core.scoring.layer3_momentum` | `def score(bars: pd.DataFrame) -> LayerScore` |
| `core.scoring.layer5_volume` | `def score(bars: pd.DataFrame) -> LayerScore` |
| `core.scoring.aggregator` | `def aggregate(scores: dict[int, LayerScore \| None]) -> FinalScore` |
| `core.execution.paper_engine` | `def on_signal(sig: Signal) -> Trade \| None` |
| `db.audit` | `def log_prediction(p: Prediction) -> None`  *(append-only, hash-chained from day 1)* |
| `api.routes.tab1` | `GET /api/v1/predict/{symbol}/{tf}` |
| `api.routes.ws` | `WS /ws/v1/{client_id}` with `live_prediction` channel |
| `frontend.tabs.Tab1LivePrediction` | Chart + 4 panels component |
| `frontend.hooks.useWebSocket` | Reconnect with backoff, gap-fill on resume |
| `frontend.layout.responsive` | Tailwind breakpoints: mobile (default), `md:` (≥768px), `lg:` (≥1024px) |

### 4.3 Data contracts

- DB tables created: `ohlcv`, `predictions`, `paper_trades`, `watchlist`. (`brain_checkpoints`, `ghost_predictions`, `news_items`, `scanner_snapshots` come in later SPs.)
- WS messages: `live_prediction_update` (subset of MASTER_PLAN section 12 — only the 4 panels' fields).
- REST: `GET /api/v1/predict/BTC-USDT/1h`, `GET /api/v1/health`.

### 4.4 TDD test plan (tests written first)

- Per-indicator: 10 hand-computed fixtures from a known dataset, assert match within 1e-6.
- Per-layer: 5 fixtures per layer (bull, bear, ranging, volatile, low-volume), assert score in expected band.
- Aggregator: 1 fixture per "missing layer" case (handles `None` correctly).
- Paper engine: open trade on LONG signal, close on stop hit, close on TP hit, no trade on SKIP — 4 tests.
- Audit: hash chain unbroken across 100 inserts (`hash(row_n) = sha256(hash(row_n-1) + canonical_row_n)`).
- WS: client reconnects after server-side disconnect, no candle gap > 1.
- Frontend: Vitest snapshot for each panel at mobile/tablet/desktop breakpoints.
- E2E (Playwright): load Cloudflare URL → SSO → see chart updating → kill WS → see reconnect → see new candle.

### 4.5 Validation procedure (manual gate)

The human reviewer:

1. Opens the production Cloudflare URL on laptop, phone (over LTE, not Wi-Fi — confirms Oracle host independence from laptop), and one other browser. All three show the same live data.
2. Picks 10 random closed candles. Computes RSI(14), MACD(12,26,9), and EMA(20/50/200) by hand or against TradingView. Verifies ≤0.1% deviation from app values.
3. Forces a paper trade by temporarily lowering the threshold. Verifies it appears in Oracle Postgres and in UI.
4. Simulates Oracle host crash: SSH in, `docker compose down` then `docker compose up -d`. Verifies the stack auto-restarts cleanly and no DB rows are corrupt (uses the audit hash chain to verify). Repeat with `sudo reboot` of the Oracle VM to test full host restart.
5. Closes the laptop entirely for 1 hour. Verifies the public URL still works, paper trades still write, audit log still grows — proves laptop independence.

### 4.6 Risks + mitigations specific to SP-0

| Risk | Mitigation |
|------|-----------|
| Oracle Ampere free instance is "out of capacity" at signup | Run `oci-arm-host-capacity` polling script across 3+ regions; expect 1–14 days. Develop on laptop in the meantime. |
| ARM build pain for TA-Lib / select PyTorch wheels | Pin to known-good ARM64 docker base images; one-time build of TA-Lib from source captured in `backend/Dockerfile` |
| Cloudflare Tunnel + Access setup is fiddly first time | Allocate a full week in the 4-week budget for ops, not just code |
| WSL2 + Docker Desktop on Windows (laptop dev mirror) can be slow | Use WSL2-internal volumes for Postgres data (not bind mounts to NTFS) |
| Binance WS occasionally drops mid-tick | Reconnect with backoff + on resume, REST-fetch missing closed candles |
| First mobile pass looks awful | Reserve last 3 days of SP-0 for mobile polish |

### 4.7 Dependencies

- **Incoming:** none (foundation).
- **Outgoing:** every other sub-project consumes SP-0's tables, audit log, WS infrastructure, and CSS theme.

---

## 5. Critical Gap Fixes (cross-cutting policies)

Every sub-project complies with these. Each sub-project's spec must explicitly reference which policies it touches.

### 5.1 Look-ahead bias / data leakage policy

**Rule:** No feature available at time `t` may peek at any data with timestamp `> t`. Includes the *current bar's close* during intra-bar inference (only use the last *closed* bar).

**Enforcement:**
- All indicator functions take a `pd.DataFrame` indexed by close timestamp; the latest row is the latest *closed* bar. Live tick data goes through a separate "current-bar partial" path that never touches training.
- Train/val/test split is **temporal, not random**. 70/15/15 by date, with a **2-week embargo** between val and test.
- Walk-forward validation only. K-fold CV is banned for time-series.
- Code review checklist: "Does this PR compute any feature using a future bar?"
- Test fixture: deliberately leaky version of one indicator + assertion that backtest perf drops by >20% when leak is removed (catches leak regressions).

### 5.2 Survivorship bias policy (point-in-time universe)

**Rule:** Backtests use the universe that existed at the backtest timestamp, not today's universe.

**Enforcement:**
- Table `universe_history(symbol, exchange, listed_at, delisted_at)`. Populated from Binance/Bybit symbol-status APIs + manually-curated historical delistings.
- `data.universe.is_tradable(symbol, ts)` returns False if `ts < listed_at` or `ts >= delisted_at`.
- All backtest queries `JOIN universe_history` to filter delisted symbols at the relevant timestamp.

### 5.3 Training data pipeline + feature store

**Rule:** One module computes features. Both training and serving call it. No copy-paste.

**Enforcement:**
- Single Python package `core.features` with versioned feature functions. Each feature has a string `feature_id` (e.g., `rsi_14_v1`).
- `core.features.compute(bars, feature_ids) -> pd.DataFrame` — used by both training scripts and live scoring.
- Feature metadata stored in `feature_registry` table.
- Training data versioned with **DVC** (Data Version Control). Each model checkpoint records the DVC hash of the training data it used.
- Tooling: `tools/verify_no_skew.py` runs the feature module on the same input in train mode and serve mode, asserts identical output.

### 5.4 Training-serving skew prevention

Already structurally covered by 5.3 (one feature module). Adds:
- **Inputs hash** column on `predictions` table — sha256 of canonical (symbol, ts, feature_ids, feature_values). Enables exact reproduction of any historical prediction.
- Nightly: re-run a random 100 historical predictions through current code, assert hash matches. Drift = bug.

### 5.5 PPO reward shaping (5-component, anti-hacking)

**Rule:** Single-component reward (`profit% − drawdown`) is reward-hackable. Use a **5-component bounded reward** with anti-trivial-strategy guards.

```
reward = w1·realized_pnl_pct
       + w2·(-1)·max_intra_trade_drawdown_pct
       + w3·(-1)·slippage_and_fee_cost_pct
       + w4·(holding_time_penalty if bars_held > target_max_hold else 0)
       + w5·(-action_inactivity_penalty if 0 trades in last N bars else 0)

clamped to [-2.0, +2.0] per trade
```

**Guards against common pathologies:**
- `w5` (inactivity penalty) prevents the brain from learning "always SKIP".
- `w4` (holding penalty) prevents "open and never close" cheating.
- Bounded reward prevents one lucky trade from dominating gradients.
- Reward computed only on **closed** trades. Open trades = 0 reward (no peeking at unrealized PnL).
- All weights versioned in `brain_config.yaml`; changing them = new model version.

### 5.6 Catastrophic forgetting policy

**Rule:** PPO updates must not erase knowledge of past regimes.

**Enforcement:**
- **Replay buffer** spans 18 months minimum, sampled with regime-balanced weighting (bull/bear/sideways/high-vol/low-vol — each ≥15% of training batch).
- **Elastic Weight Consolidation (EWC)** regularizer on top of PPO — penalizes large updates to weights that were important for old regimes.
- Nightly training: `0.7 × replay + 0.3 × recent` mixture, never 100% recent.
- Eval set = 5 fixed historical windows representing different regimes. Brain must not regress >5% Sharpe on any of them.

### 5.7 Cold-start policy

(Already foreshadowed by decision 2.5.)

- New asset → use global brain immediately (no training delay).
- Adapter starts at zero weights = pure global policy.
- After 100 paper trades for the asset, adapter starts training nightly.
- Until then, asset's signals are tagged `cold_start: true` in audit log; UI shows a small "warming" indicator.

### 5.8 WebSocket reliability

- Server: heartbeat every 15s. Client missing 3 heartbeats = reconnect.
- Reconnect: exponential backoff capped at 30s, infinite retries.
- On reconnect, client sends `last_received_ts`; server REST-fills gap from Postgres.
- Per-symbol message ordering guaranteed via per-symbol asyncio queue server-side.
- Out-of-order tick from exchange = drop with warning log.

### 5.9 Data quality gate

Every incoming candle passes through `core.dataquality.validate(candle) -> ValidationResult`:
- High ≥ Low? Open within [Low, High]? Close within [Low, High]?
- Volume ≥ 0?
- Timestamp = expected next bar boundary (no gaps, no dupes)?
- Price within ±20% of previous close (catches fat-finger ticks)?
- Volume within ±10× of 20-bar median (catches false volume spikes)?

Failures → row written to `data_quality_alerts` table, downstream layers skipped for that bar, UI panel shows a yellow "data quality warning" badge.

### 5.10 Champion-challenger gate (model promotion)

Replaces the vague "M28 A/B canary".

- **Champion** = currently-active brain (or scoring weights, or any model artifact).
- **Challenger** = newly-trained version, runs in **shadow mode** alongside champion for **14 days minimum**, generating predictions on the same data without affecting trades.
- **Promotion criteria (all required):**
  1. Challenger Sharpe ≥ champion Sharpe + 0.2.
  2. Challenger max drawdown ≤ champion max drawdown.
  3. Challenger win rate within ±5% of champion (no flukes).
  4. Challenger has ≥100 trades in shadow window.
  5. Manual sign-off via UI button (no auto-promote without human).
- Demotion: if promoted challenger underperforms 30 days post-promotion, auto-rollback to previous champion.

### 5.11 TimescaleDB chunk + compression + retention

- `ohlcv`: chunk interval = 7 days. Compress chunks older than 30 days (segment by symbol, order by ts). Expected ~10× compression.
- Retention: keep 5 years of OHLCV for 1m+TFs, indefinite for ≥1h.
- `predictions`: chunk 1 day. Compress >90 days. Retain 2 years.
- `news_items`: 20-day rolling delete (already in MASTER_PLAN, M35).
- `data_quality_alerts`: 90-day retention.

### 5.12 Memory & cost budget (24 GB nominal — Oracle Ampere A1 production host)

| Component | RAM target |
|-----------|-----------|
| Postgres + TimescaleDB | 4 GB |
| Redis (scanner cache + pub/sub) | 1.5 GB |
| Backend FastAPI + workers | 3 GB |
| Celery workers (×4 concurrency) | 2 GB |
| PyTorch inference (Conv-LSTM, FinBERT) | 4 GB |
| Per-asset paper-trade workers (max 30 concurrent) | 3 GB |
| Frontend dev server (dev only) | 1 GB |
| Grafana + Prometheus | 1 GB |
| MLflow tracking server (artifacts on disk, DB shares Postgres) | 0.3 GB |
| Loki + Promtail (log aggregation) | 0.5 GB |
| Docker engine overhead | 1 GB |
| **Total budgeted** | **21.3 GB** |
| Headroom for OS (Ubuntu ARM64) | 2.7 GB |

(On the laptop dev mirror, replace "Docker engine overhead" with "Docker Desktop + WSL2: 2 GB" and accept slightly tighter headroom; dev does not need to run all components simultaneously.)

**Hard caps enforced:**
- Postgres: `shared_buffers=2GB`, `work_mem=64MB`.
- Redis: `maxmemory 1500mb maxmemory-policy allkeys-lru`.
- Each Celery worker: `--max-memory-per-child=500000` (KB).
- Conv-LSTM: batch size capped to fit in 4 GB (training on Colab GPU, not local).

**Cost ceiling tracking:** Each free-tier API has a counter in Redis. Hitting 80% of daily quota → Telegram alert. Hitting 95% → automatic pause + Telegram alert.

### 5.13 Backup / DR

- **RPO target: 1 hour. RTO target: 4 hours.**
- **Hourly** on Oracle: `pg_dump` of changed tables → Oracle local disk.
- **Nightly** on Oracle: full `pg_basebackup` → encrypted Backblaze B2 (10 GB free tier; ~2 GB compressed) AND `rsync` over SSH to laptop's external SSD.
- **Brain checkpoints**: every checkpoint write also writes to B2 and to laptop SSD.
- **Recovery rehearsal:** quarterly. Restore latest backup to the laptop's local docker stack, verify it runs end-to-end, run validation script. If laptop ever needs to become primary (Oracle suspended), the rehearsal proves the path works.

### 5.14 Audit trail integrity (hash chain)

- `predictions` table has `prev_hash` and `row_hash` columns.
- `row_hash = sha256(prev_hash || canonical_json(this_row_excluding_hashes))`.
- Insertion via stored procedure that computes `row_hash` from previous row's hash.
- Nightly verification job walks the chain end-to-end; any break alerts Telegram and logs to `audit_violations`.
- Same chain applied to `paper_trades`.

### 5.15 Free-tier rate-limit accounting

| API | Free quota | Tracking |
|-----|-----------|----------|
| Binance public REST | weight 1200/min | Token bucket in Redis, refilled per Binance's `X-MBX-USED-WEIGHT-1M` header |
| Binance WS | unlimited | Monitor connection health only |
| TwelveData | 800 calls/day | Daily counter in Redis, resets 00:00 UTC |
| CryptoPanic | 500 calls/day | Daily counter |
| Glassnode | tight, varies by metric | Per-endpoint daily counter |
| Yahoo `yfinance` | unlimited but rate-limited unofficially | Self-throttle: max 1 req/sec per symbol |
| HuggingFace inference | n/a (FinBERT runs locally) | n/a |

Each adapter wraps `RateLimitedClient` middleware. Exceeded quota → request queued, retried at next reset, alert emitted.

---

## 6. Cross-Cutting Standards

### 6.1 TDD policy

- **Red-green-refactor for all non-ML code.** Every PR: failing test commit, then passing implementation commit.
- **ML code exception:** training loops can't be TDD'd strictly. Required instead:
  - Unit tests on data shape and gradient flow (forward + backward on a 2-sample minibatch returns finite gradients).
  - Unit tests on invariances (shifting input by 1 bar shifts output predictably).
  - Smoke test: loss decreases over 5 training steps on a tiny fixture.
  - Snapshot test: trained checkpoint produces deterministic output on a fixed input (catches non-determinism regressions).
- **Frontend:** Vitest unit tests + React Testing Library for components; Playwright for E2E flows.
- **Coverage target:** ≥85% on `core/scoring/`, `core/risk/`, `core/execution/`, `db/audit.py`. ≥60% elsewhere. ML training code excluded.

### 6.2 Validation policy (TradingView cross-check)

- **Tolerance: 0.1% absolute** for any indicator vs TradingView reference.
- **Last-bar problem:** TV's last bar is partial; ours is closed. Cross-check on closed bars only (second-to-last bar in TV).
- **Sample procedure:** for each indicator, pick 100 random `(symbol, timeframe, timestamp)` triples. Pull TV value. Pull our value. Assert within tolerance. Failures > 1% of samples = block merge.
- Automated via `tools/validate_indicators.py`.

### 6.3 Definition of Done (universal checklist)

A module is "done" when ALL of:
- [ ] Acceptance criteria from sub-project spec are checked.
- [ ] All tests pass locally + in CI.
- [ ] Coverage target met for the module's tier.
- [ ] Lint + type check pass (`ruff`, `mypy`, `tsc --strict`, `eslint`).
- [ ] No new entries in `data_quality_alerts` during 24h soak run.
- [ ] Audit log shows expected rows for the module's actions.
- [ ] Mobile-responsive (manual check at 375px width).
- [ ] PR description references which gap-fix policies (Section 5) the module complies with.
- [ ] Code-reviewer agent (per `superpowers:requesting-code-review`) returns no Sev-1 issues.
- [ ] Docs/README updated for the affected sub-project.

---

## 7. Claude Code Execution Model

### 7.1 Skill stack — canonical workflow per sub-project

For each sub-project from SP-0 through SP-7:

1. **`superpowers:brainstorming`** → produces sub-project spec under `docs/superpowers/specs/YYYY-MM-DD-SP-<id>-design.md`.
2. **`superpowers:writing-plans`** → produces `docs/superpowers/plans/YYYY-MM-DD-SP-<id>-plan.md` with task list.
3. **`superpowers:using-git-worktrees`** → creates isolated worktree `worktrees/sp-<id>/`.
4. **`superpowers:subagent-driven-development`** *(parallel SPs)* OR **`superpowers:executing-plans`** *(serial SPs)* → implements.
5. **`superpowers:test-driven-development`** → invoked inside step 4 for every implementation task.
6. **`superpowers:verification-before-completion`** → invoked before any "task complete" claim.
7. **`superpowers:requesting-code-review`** → invoked before merge.
8. **`superpowers:receiving-code-review`** → when responding to review feedback.
9. **`superpowers:finishing-a-development-branch`** → merges, tags, deploys.

### 7.2 Serial vs parallel — concrete rules

| SP | Mode | Why |
|----|------|-----|
| SP-0 Tracer Bullet | **Serial** | Foundation; everything depends on it; one human reviewer |
| SP-1 ML Data + Ghost | **Serial** | Highest risk; demands focused thought; cohesive pipeline |
| SP-2 Indicators+Patterns | **Parallel — 3 subagents** | Indicators (43), candle patterns (82), chart patterns (76) are mutually independent |
| SP-3 Data Adapters | **Parallel — 4 subagents** | One per exchange (Binance, Bybit, Yahoo, TwelveData) |
| SP-4 RL Brain | **Serial** | Single interconnected training pipeline |
| SP-5 Full Scoring + Traps | **Partial parallel** | Layers 2/4/6/9 in parallel (3 subagents); aggregator + traps serial after |
| SP-6 UI Completion | **Parallel — up to 5 subagents** | Per-panel work is independent (each panel = component + fixture + test file) |
| SP-7 Ops Hardening | **Partial parallel** | DR + monitoring + champion-challenger in parallel; audit hash-chain serial |

### 7.3 Worktree strategy

- One worktree per SP under `worktrees/sp-<id>/` from a branch `sp-<id>/main`.
- Subagents within a parallel SP each get their own sub-worktree `worktrees/sp-<id>/<subagent-name>/` from a branch `sp-<id>/<subagent-name>`.
- After all sub-branches merge into `sp-<id>/main`, merge to `main` via PR.
- Worktrees deleted after successful merge.

### 7.4 Phase gates (no SP starts until previous SP gates pass)

Each SP's PR to `main` gates on:
- All tests green in CI (GitHub Actions).
- Coverage target met.
- `tools/validate_indicators.py` passes (where applicable).
- `tools/verify_no_skew.py` passes (where applicable).
- Audit hash chain unbroken in current Postgres.
- 24-hour soak run on a feature-flag-gated deploy with no Sev-1 alerts.
- Code-reviewer agent approval.
- Human approval.

### 7.5 Branching + PR model

- `main` is always deployable (auto-deploys to Oracle production host on merge via GitHub Actions SSH-deploy step).
- Each SP merges via squash commit to `main` with the SP-id in the title.
- Module-level work inside an SP: regular commits on the SP branch, no separate PRs.
- Hotfixes: branch from `main`, fast PR.

### 7.6 Memory/state hygiene between sessions

- Every Claude Code session starts by reading `MEMORY.md`, `CLAUDE.md`, current SP's spec, current SP's plan.
- Save user feedback / project decisions to memory per the auto-memory rules in the harness.
- Each completed SP appends a one-line entry to `docs/superpowers/log.md` (date + SP-id + what shipped + what surprised us).
- No long-lived TODOs in memory — all work tracked in the SP plan file.

---

## 8. Updated Tech-Stack Notes (deltas from MASTER_PLAN section 2)

### Added

- **MLflow** — model registry. Tracks every Conv-LSTM and PPO checkpoint with hyperparameters, training data DVC hash, and eval metrics. Free, self-hostable, runs as a docker service.
- **DVC (Data Version Control)** — versions training data outside git (small metadata in git, blobs on external SSD or B2). Free, OSS.
- **Loki + Promtail** — log aggregation (paired with existing Grafana). Free, OSS.
- **Cloudflare Access** — auth gate (free <50 users). No new container.
- **Playwright** — E2E browser tests for the responsive UI. Free, OSS.

### Removed

- **Freqtrade as a docker service.** (Kept as a Python library import for backtest + hyperopt only — see decision 2.1.)
- (No host removal: Oracle Cloud Always Free is restored as **primary host** per revised decision 2.7. Laptop is now the dev environment + DR mirror, not the production host.)

### Pinned versions (lock these from day 1)

- Python 3.11.x
- PyTorch 2.3.x
- stable-baselines3 2.3.x
- HuggingFace transformers 4.42.x
- TA-Lib 0.4.32 (binary wheels for Windows/WSL2)
- pandas 2.2.x, numpy 1.26.x
- FastAPI 0.115.x, uvicorn 0.30.x
- React 18.3.x, Vite 5.x, TypeScript 5.5.x
- TailwindCSS 3.4.x

---

## 9. Open Questions (resolved before relevant SP starts, not before this doc ships)

These are intentionally not resolved yet. Each will be resolved in the brainstorm of the SP it affects.

| # | Question | Resolved during |
|---|----------|------------------|
| 1 | Exact mobile breakpoint behaviour for the dense 14-panel sidebar (collapse to drawer? swipe between panels? scrollable strip?) | SP-0 brainstorm |
| 2 | Conv-LSTM "5 prediction heads" — what are they exactly (open/high/low/close/pattern? or close/structure/pattern/confidence/regime?) | SP-1 brainstorm |
| 3 | Which 5 historical regime windows form the catastrophic-forgetting eval set? | SP-1 brainstorm |
| 4 | What's the exact `core.features` API — pandas DataFrame in/out, or polars, or numpy arrays? | SP-1 brainstorm |
| 5 | Per-asset adapter architecture — LoRA on which layer of the global brain? | SP-4 brainstorm |
| 6 | Hyperopt search space for layer weights — discrete grid or continuous? | SP-7 brainstorm |
| 7 | Is the Telegram bot read-only (alerts) or interactive (commands)? | SP-7 brainstorm |
| 8 | Mobile push notifications — yes/no, and via what channel? | SP-7 brainstorm |
| 9 | Backtesting tick-resolution — full L2 replay or candle-only? | SP-7 brainstorm |
| 10 | Hyperopt schedule — weekly, monthly, or only on regime change detection? | SP-7 brainstorm |

---

## 10. Appendix A — Per-Sub-Project Blueprint Template

Copy this block into every new SP brainstorm output.

```markdown
# SP-<id> <name> — Design

**Date:** YYYY-MM-DD
**Status:** Draft
**Depends on:** SP-<incoming ids>
**Feeds:** SP-<outgoing ids>
**Estimated weeks:** <n>
**Mode:** Serial | Parallel-N

## Goal
<one paragraph>

## Non-goals
- <bullet>
- <bullet>

## Acceptance criteria (binary)
- [ ] <criterion>

## Module list
| Module | Interface |
|--------|-----------|
| <path> | <signature> |

## Data contracts
- DB tables (created / modified): <list>
- WS messages: <list>
- REST endpoints: <list>

## TDD test plan
- <test fixture>: <assertion>

## Validation procedure (manual gate)
1. <step>

## Risks + mitigations
| Risk | Mitigation |
|------|-----------|

## Compliance with cross-cutting policies (Section 5)
- 5.1 Look-ahead: <how this SP complies>
- 5.2 Survivorship: <or N/A>
- ... (only list policies the SP touches)

## Open questions resolved here
- Q<n>: <answer>
```

---

**END OF META-PLAN**
