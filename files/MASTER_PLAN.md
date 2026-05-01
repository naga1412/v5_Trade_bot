# Master Plan v5 FINAL — Trading Analysis Platform with Self-Learning AI Brain

**Project name:** `trading-radar` (working title)
**Build time:** 7–9 months (single developer)
**Monthly cost:** $0 (free-tier infrastructure)
**Stack:** Python (FastAPI + Freqtrade) + React + PostgreSQL + Redis + PyTorch
**License:** Private — for personal use

---

## 1. Project Overview

A zero-cost, institutional-grade retail trading research platform with three tabs:

1. **Tab 1 — Live Prediction Formation:** Single-asset deep analysis with chart + 14 sidebar panels + ghost candle prediction overlay (1–50 candles ahead)
2. **Tab 2 — Paper Trading Lab:** Per-asset paper trade performance, brain learning curves, version comparison
3. **Tab 3 — Scanner Radar:** Multi-asset opportunity scanner (200+ assets, refresh every 2 min)

The platform is a **manual trading coach**, NOT an autonomous trading bot. Freqtrade runs in dry-run mode only. The bot generates predictions and paper-trades silently to train its brain. The user manually executes real trades based on the bot's analysis.

**Core innovations:**
- Ghost candle prediction (1–50 candles ahead with confidence decay)
- Self-learning RL brain (PPO algorithm) that improves from every trade outcome
- 10-layer confluence scoring system
- 158-pattern detection library
- 12-trap filter system for both long and short
- News intelligence with FinBERT-LSTM
- Multi-market support: crypto futures + spot + commodities + forex

---

## 2. Technology Stack (All Free/Open-Source)

### Frontend
- **React 18** + Vite + TypeScript
- **TradingView Lightweight Charts** (free OSS) — chart engine
- **TailwindCSS** — styling
- **Zustand** — state management
- **Socket.io client** — WebSocket
- **Recharts** — sidebar mini-charts
- Fonts: **JetBrains Mono** (data) + **Inter** (UI)

### Backend
- **FastAPI** (Python 3.11+) — web framework
- **Freqtrade** (forked) — bot core in dry-run mode
- **CCXT** — exchange adapter (30+ exchanges)
- **Celery + Redis** — background workers
- **asyncio** — parallel scanner

### AI / Machine Learning
- **PyTorch 2.x** — Conv-LSTM ghost candle predictor
- **XGBoost** — Layer 7 gradient boosting
- **Stable-Baselines3** (PPO) — RL self-learning brain
- **HuggingFace Transformers** — FinBERT for news sentiment
- **TA-Lib + Pandas-TA** — technical indicators
- **scikit-learn** — feature engineering
- Training: **Google Colab free tier** (12 hr/day GPU)

### Data Sources (All Free Tier)
- **Binance USDS-M Futures + Spot API** — crypto (free, unlimited public)
- **Bybit API** — backup crypto (free, unlimited public)
- **Yahoo Finance** (yfinance lib) — gold, silver, oil, stocks (free unlimited)
- **TwelveData free tier** — forex 28 pairs (800 calls/day)
- **CryptoPanic free tier** — news aggregator (500 calls/day)
- **Forex Factory RSS** — economic calendar (free unlimited)
- **Glassnode free tier** — on-chain BTC/ETH (limited)
- **Alternative.me** — Fear & Greed Index (free)

### Infrastructure
- **Oracle Cloud Free Tier** — 24GB RAM ARM VM (forever free)
- **PostgreSQL 16 + TimescaleDB** — time-series + relational DB
- **Redis 7 OSS** — cache + pub/sub
- **Grafana + Prometheus** — monitoring (free OSS)
- **Cloudflare Tunnel** — free HTTPS + DDoS protection
- **GitHub** — private repo
- **GitHub Actions** — CI/CD (2,000 min/month free)
- **Docker + docker-compose** — containerization

---

## 3. UI Design System (Match Reference Screenshots)

### Color Palette
```css
:root {
  /* Backgrounds */
  --bg-base: #0a0d12;        /* App background — pure black */
  --bg-chart: #0d1018;       /* Chart panel */
  --bg-panel: #12161d;       /* Sidebar panels */
  --bg-elevated: #1a1f28;    /* Buttons, hover states */
  --border: #1f2530;         /* Default panel borders */
  --border-strong: #2a2d33;  /* Emphasized borders */

  /* Semantic colors — punchy, vibrant */
  --green: #00d68f;          /* Bull, profit, longs */
  --red: #ff3d71;            /* Bear, loss, shorts, alerts */
  --gold: #ffd700;           /* Volume profile, POC, key levels */
  --purple: #c084fc;         /* EMA, AI brain, predictions */
  --cyan: #22d3ee;           /* Secondary EMAs, info */
  --orange: #ffa500;         /* Resistance, warning, PROBABLE tag */
  --pink: #ff6b9d;           /* Hybrid supervisor LONG flag */

  /* Translucent overlays for ghost candles */
  --green-15: rgba(0,214,143,0.15);
  --red-15: rgba(255,61,113,0.15);
  --gold-15: rgba(255,215,0,0.15);
  --purple-15: rgba(192,132,252,0.15);

  /* Text */
  --text-primary: #c4c8d0;
  --text-secondary: #8c91a0;
  --text-tertiary: #6b7280;

  /* Typography */
  --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
  --font-sans: 'Inter', system-ui, sans-serif;

  /* Layout dimensions */
  --sidebar-width: 230px;
  --topnav-height: 32px;
  --tfrow-height: 28px;
  --panel-gap: 3px;
  --panel-radius: 4px;
  --panel-padding: 0.4rem 0.55rem;
}
```

### Typography Rules
- Sidebar panel titles: `7.5px` uppercase, letter-spacing `0.04em`, color `--text-tertiary`
- Sidebar panel values: `9px` monospace, weight 500
- Sidebar panel labels: `8.5px` monospace, color `--text-secondary`
- Tab 3 signal card symbols: `11.5px` Inter
- Tab 3 signal card tags: `9px` Inter, weight 500
- Chart labels: `7.5px` JetBrains Mono

---

## 4. Architecture — 42 Modules Total

### Backend Modules (M1–M30)
| ID | Module | Function |
|----|--------|----------|
| M1 | Data ingestion | CCXT + WebSocket multi-feed (Binance/Bybit/Yahoo/TwelveData) |
| M2 | 10-layer scoring engine | Aggregate confluence calculator |
| M3 | AI prediction (Conv-LSTM + XGBoost) | Layers 7 & 8 neural networks |
| M4 | 12-trap filter system | Dual-direction veto logic |
| M5 | Direction classifier | Long/short asymmetric thresholds |
| M6 | News aggregator | CryptoPanic + RSS deduplicated |
| M7 | FinBERT-LSTM NLP | Sentiment + impact scoring |
| M8 | Economic calendar | Forex Factory + macro events |
| M9 | Whale + on-chain | Glassnode + Etherscan integration |
| M10 | Position sizing | Half-Kelly + ATR-based stops |
| M11 | Circuit breaker | Auto kill-switch on drawdown |
| M12 | Correlation manager | BTC.D + asset coupling tracker |
| M13 | Slippage + fee model | Real cost prediction |
| M14 | Regime detector | Bull/bear/sideways/volatility classifier |
| M15 | Order execution | Smart routing (paper trades only) |
| M16 | Monitoring + alerts | Telegram + Grafana dashboards |
| M17 | Backtesting | Walk-forward + Monte Carlo |
| M18 | News-event simulator | Backtest news impact |
| M19 | Disaster recovery | Dead-man switch + multi-region failover |
| M20 | Order book microstructure | L2 depth + flow imbalance |
| M21 | Tick-level execution sim | Realistic backtest engine |
| M22 | Audit trail | Append-only logs for compliance |
| M23 | RL self-learning brain | PPO agent — Layer 10 |
| M24 | XAI explainability | SHAP per-trade reasoning |
| M25 | Multi-exchange routing | Failover + arbitrage detection |
| M26 | Advanced order types | Post-only, iceberg, TWAP, VWAP |
| M27 | Latency monitor | Auto-pause on >300ms spikes |
| M28 | A/B canary deploy | Champion vs challenger testing |
| M29 | Portfolio optimizer | Markowitz + risk parity |
| M30 | Anomaly detection | Pump-dump + manipulation filter |

### Platform Modules (M31–M35)
| ID | Module | Function |
|----|--------|----------|
| M31 | Ghost candle predictor | Predicts 1–50 candles ahead with patterns |
| M32 | Paper trading engine | Virtual capital, real market data |
| M33 | Real-time chart UI | TradingView Lightweight Charts wrapper |
| M34 | Self-correction tracker | Tracks accuracy + errors fixed |
| M35 | News feed UI cleanup | 20-day rolling auto-delete |

### UI Modules (M36–M42)
| ID | Module | Function |
|----|--------|----------|
| M36 | React 3-tab shell | Routing, layout, global state |
| M37 | TradingView wrapper | Charts + ghost overlay + key levels |
| M38 | 14 sidebar panel components | All Tab 1 sidebar widgets |
| M39 | Multi-market asset search | Crypto + forex + commodities |
| M40 | Scanner radar UI | Tab 3 cards + filters + sparklines |
| M41 | Paper trade lab UI | Tab 2 metrics + brain learning curves |
| M42 | WebSocket data layer | Real-time updates infrastructure |

---

## 5. The 10-Layer Confluence Prediction System

| Layer | Function | Weight | Solo Accuracy |
|-------|----------|--------|---------------|
| L1 | Macro bias — HTF trend (Weekly EMA, ADX, Ichimoku, Daily MACD) | 18% | 78% |
| L2 | Key zone identification (S/R, Volume Profile, Fibonacci, Pivots) | 13% | 72% |
| L3 | Momentum confirmation (RSI, MACD, Stochastic, CCI, Williams %R) | 9% | 68% |
| L4 | Macro pattern trigger (76 chart patterns library) | 8% | 65% |
| L5 | Volume + SMC validation (VWAP, OBV, Order Blocks, FVG, Liquidity) | 10% | 70% |
| L6 | Micro pattern (158 lib — 82 candle + 76 chart patterns) | 7% | 66% |
| L7 | LSTM + XGBoost AI ensemble | 7% | 82% |
| L8 | Conv-LSTM SOTA fusion (multivariate deep learning) | 7% | 86% |
| L9 | News + sentiment intelligence (FinBERT-LSTM + calendar) | 11% | 76% |
| L10 | RL self-learning meta-brain (PPO — adaptive overlay) | 10% | 85% |

**Master decision formula:**
```
STATIC_SCORE = (L1×0.18 + L2×0.13 + L3×0.09 + L4×0.08 + L5×0.10 + L6×0.07 + L7×0.07 + L8×0.07 + L9×0.11)
BRAIN_ADJUST = Layer10_PPO(market_state, layer_scores) → adaptive multiplier
FINAL_SCORE = STATIC_SCORE × BRAIN_ADJUST × (1 - SUM(traps × 0.15)) × news_multiplier × direction_penalty
EXECUTE_SIGNAL = FINAL_SCORE ≥ threshold AND traps_passed AND circuit_OK AND brain_confidence > 0.7
```

**Score thresholds:**
- `< 55%` → SKIP (no signal generated)
- `55–65%` → Paper-trade only, log for analysis
- `65–75%` → Small position suggestion (0.5%)
- `75–85%` → Standard suggestion (1%)
- `85%+` → A+ trade suggestion (1.5%)

---

## 6. The 12 Trap Filters (Dual Direction)

Each filter checks BOTH long and short variants. Shorts require +2 layer threshold higher than longs (asymmetric risk).

1. **Pre-news event confluence trap** (extreme severity)
2. **Liquidity sweep into setup zone** (extreme — short squeeze risk)
3. **Parabolic blow-off / capitulation move** (extreme)
4. **Friday close / weekend trap** (high — gap risk)
5. **Counter-trend against weekly bias** (high — shorting bull market = capital destruction)
6. **All-indicator extreme rally / decline** (high — oversold bounces faster than overbought drops)
7. **Altcoin during BTC indecision** (high — alt beta to BTC)
8. **Volume spike with no follow-through** (high)
9. **Pattern inside a pattern** (medium)
10. **Low-cap thin order book** (medium — borrow rate killer for shorts)
11. **Pattern at price extremes** (medium — ATH/ATL risk)
12. **Volatility regime change** (medium)

**Plus 5 short-only filters:** short squeeze cascade, funding rate decay, borrow rate, unlimited upside risk, regulatory short bans.

---

## 7. Pattern Libraries

### 82 Candlestick Patterns (Single + Multi-Candle)
Doji variants (4), Hammer/Hanging Man (2), Engulfing (2), Harami (2), Morning/Evening Star (2), Three Methods (4), Marubozu (2), Spinning Top (2), Three Soldiers/Crows (2), and 60+ others. Use TA-Lib's built-in pattern recognition functions: `talib.CDLDOJI`, `talib.CDLENGULFING`, etc.

### 76 Chart Patterns
Head & Shoulders (regular + inverse), Double/Triple Top/Bottom, Cup & Handle, Rounded Top/Bottom, Triangles (ascending, descending, symmetrical), Flags & Pennants, Wedges (rising, falling), Channels (ascending, descending, horizontal), Diamond, Broadening, Rectangles, etc. Implement using peak detection + slope regression on swing points.

---

## 8. Multi-Market Support

| Market | Source | Free Tier | Symbols |
|--------|--------|-----------|---------|
| Crypto Futures (USDS-M) | Binance API | Unlimited public | 200+ |
| Crypto Spot | Binance/Coinbase API | Unlimited public | 500+ |
| Commodities (Gold, Silver, Oil, Copper) | Yahoo Finance via `yfinance` | Unlimited | 20+ |
| Forex (28 major + minor pairs) | TwelveData API | 800 calls/day | 28 |
| Stocks (optional) | Yahoo Finance | Unlimited | All |

---

## 9. UI Specifications (Pixel-Precise)

### Tab 1 — Live Prediction Formation

**Layout:** Top nav (32px) → Timeframe pill row (28px) → Body (chart 85% / sidebar 230px fixed)

**14 Sidebar Panels (in order, top to bottom):**
1. **Trade Status Bar** — Current status (NEUTRAL / LONG / SHORT) with gold warning
2. **Master Bias Score** — -100 to +100 with progress bar + badge (BULL/BEAR/NEUTRAL)
3. **Final Value** — Risk-reward ratio, match strict %, max drawdown
4. **Long / Short Ratio** — Split bar showing 49.2% / 50.8%
5. **Deep Learning Supervisor** — Red alert panel when SHORT signal active (75% confidence)
6. **HTF Bias & Structure** — Wyckoff phase + confidence
7. **Volume Profile** — POC, VAH, VAL
8. **Momentum Indicators** — 2-col grid: RSI, MACD, Stoch, CCI
9. **Market Microstructure** — Order flow, imbalance ratio
10. **Liquidity Sweep** — Above PDH / Below PDL
11. **OI & Funding Rate** — OI delta, funding %
12. **Intermarket Analysis** — DXY corr, Gold corr
13. **Sentiment & Fear/Greed** — F&G index, news bias
14. **Ghost Candle Prediction** — Next pattern, size, confidence
15. **Trade Setup** — 2-col grid: ENTRY, SL, TP, R:R
16. **Key Levels** — EMA 20/50/200
17. **News & Macro Impact** — Red-bordered when HIGH impact

**Chart elements:**
- Candles with OHLC
- Ghost candles (1–50 ahead) — opacity decays from 80% (next candle) to 30% (50 ahead)
- Key levels: PDH (green dashed), PDL (red dashed), Resistance (orange solid), EMA 200 (cyan dashed)
- EMA curves: 20/50 (purple), 200 (cyan)
- Volume profile right edge (gold + cyan bars)
- Volume histogram bottom (green/red)

### Tab 3 — Scanner Radar

**Toolbar (left to right):**
- Search assets box (130px)
- Add to watchlist box (130px)
- Gold "★ Add" pill
- Market dropdown ("Crypto 200+")
- Timeframe dropdown ("1h")
- Cyan asset count icon
- Asset count input (200)
- "min" refresh interval input (2)
- Sort dropdown ("AI Score")
- Filter pills: All 186, ✓ Confirmed 38 (green), ~ Probable 34 (orange), ✗ Weak 65 (red), ⚡ Diverging 46 (purple), 🛡 Hybrid (orange), ⏱ Analyzing... (cyan)
- Right side: ✓ scanned count, DS X/8 pill, timestamp

**Hybrid Supervisor cyan progress bar** below toolbar showing "X/8 done"

**Two-column body:**
- Bullish column (green title, 83 assets) with MILD intensity label
- Bearish column (red title, 100 assets) with MILD intensity label

**Each signal card has:**
- Row 1: ★ favorite + symbol + full name | sparkline + ±points badge
- Row 2 (tags): solid LONG/SHORT, outlined 4h LONG/SHORT, ✓ CONFIRMED green or ~ PROBABLE orange, pink/purple dot Hybrid LONG/SHORT, AI ±score purple, Wyckoff phase text
- Row 3: ±%change at right
- Row 4: confidence bar + "Conf X%" text
- Row 5: score tags (SMC ±N, Wyckoff ±N, Microstructure ±N, Momentum ±N)

**Footer:** "Scanning 200+ crypto • 1h timeframe" left | "Auto-refresh every 2 min • Click card to view chart" right

---

## 10. Project Folder Structure

```
trading-radar/
├── docker-compose.yml           # Multi-container setup
├── .env.example                 # Environment variables template
├── README.md
├── backend/
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── app/
│   │   ├── main.py             # FastAPI entry point
│   │   ├── config.py
│   │   ├── api/                # REST + WebSocket endpoints
│   │   │   ├── routes/
│   │   │   │   ├── tab1.py     # Live prediction endpoints
│   │   │   │   ├── tab2.py     # Paper trade lab
│   │   │   │   ├── tab3.py     # Scanner radar
│   │   │   │   └── ws.py       # WebSocket handlers
│   │   ├── core/
│   │   │   ├── data_ingestion.py        # M1
│   │   │   ├── scoring/
│   │   │   │   ├── layer1_macro.py      # M2 — Layer 1
│   │   │   │   ├── layer2_zones.py
│   │   │   │   ├── layer3_momentum.py
│   │   │   │   ├── layer4_chart_patterns.py
│   │   │   │   ├── layer5_smc_volume.py
│   │   │   │   ├── layer6_candle_patterns.py
│   │   │   │   ├── layer7_xgboost.py
│   │   │   │   ├── layer8_conv_lstm.py
│   │   │   │   ├── layer9_news.py
│   │   │   │   ├── layer10_brain.py
│   │   │   │   └── aggregator.py        # Final score formula
│   │   │   ├── traps/
│   │   │   │   └── trap_filter.py       # M4 — 12 traps
│   │   │   ├── direction.py             # M5
│   │   │   ├── risk/
│   │   │   │   ├── position_sizing.py   # M10
│   │   │   │   ├── circuit_breaker.py   # M11
│   │   │   │   ├── correlation.py       # M12
│   │   │   │   └── slippage.py          # M13
│   │   │   └── execution/
│   │   │       └── paper_engine.py      # M32
│   │   ├── ai/
│   │   │   ├── models/
│   │   │   │   ├── conv_lstm.py         # Layer 8
│   │   │   │   ├── ghost_candle.py      # M31
│   │   │   │   ├── rl_brain.py          # M23
│   │   │   │   └── finbert_wrapper.py   # M7
│   │   │   ├── training/
│   │   │   │   ├── train_conv_lstm.py
│   │   │   │   ├── train_ghost.py
│   │   │   │   └── train_brain.py
│   │   │   └── checkpoints/             # Per-asset model files
│   │   ├── news/
│   │   │   ├── aggregator.py            # M6
│   │   │   ├── finbert_classifier.py    # M7
│   │   │   ├── calendar_scraper.py      # M8
│   │   │   └── cleanup_worker.py        # M35 — 20-day delete
│   │   ├── scanner/
│   │   │   ├── parallel_engine.py       # M40 backend
│   │   │   ├── classifier.py            # CONFIRMED/PROBABLE/WEAK
│   │   │   └── cache_layer.py           # Redis 110s TTL
│   │   ├── data/
│   │   │   ├── adapters/
│   │   │   │   ├── binance.py
│   │   │   │   ├── bybit.py
│   │   │   │   ├── yahoo_finance.py
│   │   │   │   ├── twelvedata.py
│   │   │   │   └── glassnode.py
│   │   │   └── universe.py              # M39 — asset list manager
│   │   ├── workers/
│   │   │   ├── celery_app.py
│   │   │   ├── paper_trade_worker.py    # Per-asset background worker
│   │   │   ├── scanner_worker.py        # 2-min scan loop
│   │   │   └── brain_trainer_worker.py  # Nightly RL training
│   │   ├── db/
│   │   │   ├── models.py                # SQLAlchemy models
│   │   │   ├── migrations/              # Alembic migrations
│   │   │   └── audit.py                 # M22 — append-only log
│   │   └── utils/
│   │       ├── ta_indicators.py         # All 43 indicators
│   │       ├── candle_patterns.py       # 82 patterns
│   │       ├── chart_patterns.py        # 76 patterns
│   │       └── time_utils.py
│   ├── freqtrade_user/                  # Forked Freqtrade strategies
│   │   ├── strategies/
│   │   │   └── MasterPlanV5.py          # Custom IStrategy
│   │   ├── freqaimodels/
│   │   │   └── ConvLSTMModel.py         # Custom IFreqaiModel
│   │   └── config.json
│   └── tests/
│       ├── test_layers.py
│       ├── test_traps.py
│       └── test_ghost_candles.py
│
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── Dockerfile
│   ├── index.html
│   ├── public/
│   │   └── fonts/
│   │       ├── JetBrainsMono-Regular.woff2
│   │       └── Inter-Regular.woff2
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── styles/
│       │   ├── globals.css              # Theme variables
│       │   └── tab1.css
│       ├── tabs/
│       │   ├── Tab1LivePrediction/
│       │   │   ├── index.tsx
│       │   │   ├── TopNav.tsx
│       │   │   ├── TimeframeRow.tsx
│       │   │   ├── ChartContainer.tsx
│       │   │   ├── Sidebar.tsx
│       │   │   └── panels/
│       │   │       ├── TradeStatusBar.tsx
│       │   │       ├── MasterBiasScore.tsx
│       │   │       ├── FinalValue.tsx
│       │   │       ├── LongShortRatio.tsx
│       │   │       ├── DeepLearningSupervisor.tsx
│       │   │       ├── HtfBiasStructure.tsx
│       │   │       ├── VolumeProfile.tsx
│       │   │       ├── MomentumIndicators.tsx
│       │   │       ├── MarketMicrostructure.tsx
│       │   │       ├── LiquiditySweep.tsx
│       │   │       ├── OiFundingRate.tsx
│       │   │       ├── IntermarketAnalysis.tsx
│       │   │       ├── SentimentFearGreed.tsx
│       │   │       ├── GhostCandlePrediction.tsx
│       │   │       ├── TradeSetup.tsx
│       │   │       ├── KeyLevels.tsx
│       │   │       └── NewsImpact.tsx
│       │   ├── Tab2PaperLab/
│       │   │   ├── index.tsx
│       │   │   ├── PortfolioMetrics.tsx
│       │   │   ├── PerAssetMetrics.tsx
│       │   │   ├── BrainLearningCurve.tsx
│       │   │   └── BrainVersionRollback.tsx
│       │   └── Tab3Scanner/
│       │       ├── index.tsx
│       │       ├── Toolbar.tsx
│       │       ├── FilterPills.tsx
│       │       ├── SupervisorBar.tsx
│       │       ├── SignalsColumn.tsx
│       │       ├── SignalCard.tsx
│       │       └── Footer.tsx
│       ├── components/
│       │   ├── ui/
│       │   │   ├── Panel.tsx
│       │   │   ├── Row.tsx
│       │   │   ├── Badge.tsx
│       │   │   ├── BarTrack.tsx
│       │   │   ├── Tag.tsx
│       │   │   ├── ScoreTag.tsx
│       │   │   ├── Sparkline.tsx
│       │   │   ├── Star.tsx
│       │   │   └── StatGrid.tsx
│       │   └── chart/
│       │       ├── TVChart.tsx
│       │       ├── GhostCandleOverlay.tsx
│       │       ├── KeyLevelsOverlay.tsx
│       │       └── VolumeProfile.tsx
│       ├── hooks/
│       │   ├── useWebSocket.ts
│       │   ├── useScanner.ts
│       │   └── usePaperTrade.ts
│       ├── stores/
│       │   ├── globalStore.ts           # Zustand
│       │   ├── tab1Store.ts
│       │   ├── tab2Store.ts
│       │   └── tab3Store.ts
│       └── utils/
│           ├── api.ts
│           └── formatters.ts
│
├── infra/
│   ├── docker/
│   │   ├── postgres.Dockerfile
│   │   └── redis.conf
│   ├── grafana/
│   │   └── dashboards/
│   ├── prometheus/
│   │   └── prometheus.yml
│   └── scripts/
│       ├── init_db.sql
│       ├── deploy.sh
│       └── backup.sh
│
└── docs/
    ├── ARCHITECTURE.md
    ├── API_REFERENCE.md
    └── DEPLOYMENT.md
```

---

## 11. Database Schema (PostgreSQL + TimescaleDB)

```sql
-- Time-series: OHLCV data (TimescaleDB hypertable)
CREATE TABLE ohlcv (
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  ts TIMESTAMPTZ NOT NULL,
  open DOUBLE PRECISION NOT NULL,
  high DOUBLE PRECISION NOT NULL,
  low DOUBLE PRECISION NOT NULL,
  close DOUBLE PRECISION NOT NULL,
  volume DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (symbol, timeframe, ts)
);
SELECT create_hypertable('ohlcv', 'ts');

-- Predictions log (audit trail M22)
CREATE TABLE predictions (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  layer_scores JSONB NOT NULL,
  final_score DOUBLE PRECISION NOT NULL,
  direction TEXT,
  confidence DOUBLE PRECISION,
  brain_action TEXT,
  brain_confidence DOUBLE PRECISION,
  trap_filters JSONB,
  ghost_candles JSONB,
  model_version TEXT,
  inputs_hash TEXT,
  is_paper_trade BOOLEAN DEFAULT FALSE
);

-- Ghost candle predictions (for accuracy tracking)
CREATE TABLE ghost_predictions (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  prediction_ts TIMESTAMPTZ NOT NULL,
  step INT NOT NULL,                    -- 1..50
  predicted_open DOUBLE PRECISION,
  predicted_high DOUBLE PRECISION,
  predicted_low DOUBLE PRECISION,
  predicted_close DOUBLE PRECISION,
  predicted_pattern TEXT,
  predicted_chart_pattern TEXT,
  predicted_structure TEXT,
  confidence DOUBLE PRECISION,
  actual_ohlc JSONB,                    -- Filled when actual candle closes
  ohlc_error_pct DOUBLE PRECISION,
  pattern_correct BOOLEAN,
  resolved_at TIMESTAMPTZ
);

-- Paper trades
CREATE TABLE paper_trades (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  direction TEXT NOT NULL,              -- LONG / SHORT
  entry_price DOUBLE PRECISION NOT NULL,
  exit_price DOUBLE PRECISION,
  stop_loss DOUBLE PRECISION,
  take_profit DOUBLE PRECISION,
  position_size DOUBLE PRECISION,
  opened_at TIMESTAMPTZ NOT NULL,
  closed_at TIMESTAMPTZ,
  pnl_pct DOUBLE PRECISION,
  max_drawdown_during DOUBLE PRECISION,
  bars_held INT,
  reward_signal DOUBLE PRECISION,       -- Fed to RL brain
  reasoning JSONB,                       -- WHY this trade was taken
  brain_version TEXT
);

-- News items
CREATE TABLE news_items (
  id BIGSERIAL PRIMARY KEY,
  source TEXT NOT NULL,
  url TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  body TEXT,
  published_at TIMESTAMPTZ NOT NULL,
  fetched_at TIMESTAMPTZ DEFAULT NOW(),
  sentiment_score DOUBLE PRECISION,
  sentiment_label TEXT,                 -- bullish/bearish/neutral
  impact_score DOUBLE PRECISION,
  category TEXT,                        -- regulatory/exchange/macro/whale/project/social
  affected_assets TEXT[]
);
CREATE INDEX idx_news_published_at ON news_items(published_at DESC);
-- Worker auto-deletes rows older than 20 days

-- Brain checkpoints (per asset)
CREATE TABLE brain_checkpoints (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  version TEXT NOT NULL,
  saved_at TIMESTAMPTZ DEFAULT NOW(),
  weights_path TEXT NOT NULL,           -- File path to .pt file
  total_trades INT DEFAULT 0,
  win_rate DOUBLE PRECISION,
  sharpe_ratio DOUBLE PRECISION,
  max_drawdown DOUBLE PRECISION,
  is_active BOOLEAN DEFAULT FALSE,
  notes TEXT
);

-- Scanner results cache
CREATE TABLE scanner_snapshots (
  id BIGSERIAL PRIMARY KEY,
  scanned_at TIMESTAMPTZ DEFAULT NOW(),
  market TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  total_scanned INT,
  results JSONB NOT NULL                -- Full bullish/bearish lists
);

-- User watchlist & favorites
CREATE TABLE watchlist (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  is_favorite BOOLEAN DEFAULT FALSE,
  paper_trade_active BOOLEAN DEFAULT FALSE,
  added_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 12. WebSocket API Contract

### Connection
```
ws://localhost:8000/ws/{client_id}
```

### Subscribe to Tab 1 live prediction
```json
{
  "action": "subscribe",
  "channel": "live_prediction",
  "params": {"symbol": "BTC/USDT", "timeframe": "1h"}
}
```

### Server message: live_prediction_update (every 1s)
```json
{
  "channel": "live_prediction",
  "symbol": "BTC/USDT",
  "timeframe": "1h",
  "ts": "2026-04-01T12:00:00Z",
  "price": 77189.0,
  "change_pct": -0.27,
  "trade_status": "NEUTRAL",
  "master_bias": {"score": 12, "label": "NEUTRAL", "color": "purple"},
  "final": {"rr": "5/100", "match_strict": 79.7, "max_dd": 2227.5},
  "long_short": {"long": 49.2, "short": 50.8},
  "deep_learning": {"signal": "SHORT", "confidence": 75, "watch_close_above": "..."},
  "htf": {"trend": "Markup", "confidence": 68.4, "label": "BULL"},
  "volume_profile": {"poc": 76750, "vah": 77400, "val": 75900},
  "momentum": {"rsi": 58.2, "macd": 0.7, "stoch": 62, "cci": -15},
  "microstructure": {"flow": 0.42, "imbalance": 2.4, "label": "BUY DOM"},
  "liquidity": {"above_pdh": "+$2.4M", "below_pdl": "-$1.1M"},
  "oi": {"delta": 2.4, "funding": 0.0078},
  "intermarket": {"dxy": -0.62, "gold": 0.18},
  "sentiment": {"fgi": 38, "label": "FEAR", "news": "Negative"},
  "ghost": {"pattern": "Bull engulf", "size": "Small body", "conf": 71.2},
  "trade_setup": {"entry": 76420, "sl": 75890, "tp": 77800, "rr": "1:2.6"},
  "emas": {"e20": 76180, "e50": 75920, "e200": 74800},
  "key_levels": {"pdh": 79200, "pdl": 76000, "resistance": 77500},
  "news": [{"id": 1, "event": "CPI release", "time_until": "4h", "impact": "HIGH"}]
}
```

### Server message: ghost_candles_update (per closed candle)
```json
{
  "channel": "ghost_candles",
  "symbol": "BTC/USDT",
  "timeframe": "1h",
  "candles": [
    {"step": 1, "open": 77200, "high": 77450, "low": 77150, "close": 77380,
     "pattern": "Bullish Engulfing", "chart_pattern": "Flag", "structure": "W",
     "confidence": 0.71},
    {"step": 2, "open": 77380, "high": 77600, "low": 77320, "close": 77550,
     "pattern": "Doji", "chart_pattern": null, "structure": null, "confidence": 0.62},
    // ... up to step 50
  ]
}
```

### Server message: scanner_update (every 2 min)
```json
{
  "channel": "scanner",
  "scanned_at": "2026-04-01T12:00:00Z",
  "market": "Crypto USDS-M",
  "timeframe": "1h",
  "scanned_count": 186,
  "filter_counts": {
    "all": 186, "confirmed": 38, "probable": 34, "weak": 65, "diverging": 46
  },
  "supervisor_progress": {"done": 3, "total": 8},
  "bullish": [
    {
      "symbol": "IO/USDT",
      "full_name": "io.net",
      "is_favorite": true,
      "points": 23,
      "pct_change": 2.97,
      "direction": "LONG",
      "htf_direction": "LONG",
      "signal_tier": "PROBABLE",
      "hybrid_flag": "LONG",
      "ai_score": 36,
      "wyckoff_phase": "Accumulation",
      "confidence": 60,
      "scores": {"smc": 100, "wyckoff": 15, "microstructure": 20, "momentum": 0},
      "sparkline": [12, 13, 11, 14, 12, 15, 13, 16, 14, 17, 18, 16, 19, 20, 18, 21, 22, 20, 23, 23]
    }
    // ... more cards
  ],
  "bearish": [/* same shape */]
}
```

---

## 13. Phase-by-Phase Build Roadmap

### Phase 1 — Foundation (3 weeks)
- [ ] Sign up Oracle Cloud Free Tier, provision 4-vCPU ARM VM (24GB RAM)
- [ ] SSH setup, install Docker + docker-compose
- [ ] Create GitHub private repo: `trading-radar`
- [ ] Write `docker-compose.yml` with services: postgres, redis, freqtrade, backend, frontend, grafana, prometheus
- [ ] Install Freqtrade in dry-run mode, connect Binance testnet
- [ ] Initialize FastAPI project with WebSocket support
- [ ] Initialize React + Vite + TypeScript + TailwindCSS
- [ ] Implement theme system (CSS variables matching reference)
- [ ] Create 3-tab routing skeleton (empty tabs)
- [ ] Set up GitHub Actions CI/CD workflow
- [ ] **Validation:** Default Freqtrade strategy runs in dry-run for 7 days, no errors

### Phase 2 — Tab 1 Foundation (10 weeks)
- [ ] Implement M1 data ingestion (Binance, Bybit, Yahoo, TwelveData adapters)
- [ ] PostgreSQL + TimescaleDB schema and migrations
- [ ] All 43 technical indicators (TA-Lib + custom)
- [ ] 82 candlestick patterns (TA-Lib `CDL*` functions)
- [ ] 76 chart patterns (peak detection + slope regression)
- [ ] 10-layer scoring engine (all layers L1-L9 first; L10 in Phase 3)
- [ ] 12 trap filter system (all filters with dual-direction)
- [ ] Multi-market asset search backend
- [ ] WebSocket pipeline backend → frontend
- [ ] React Tab 1 layout: top nav + timeframe row + chart + sidebar
- [ ] TradingView Lightweight Charts integration
- [ ] All 14 sidebar panel components matching reference
- [ ] Multi-market asset search frontend with autocomplete
- [ ] **Validation:** Tab 1 shows real-time data for BTC/USDT, all 14 panels updating, indicators match TradingView for 100 random points

### Phase 3 — Ghost Candles + AI Brain (8 weeks)
- [ ] Build M31 ghost candle predictor (PyTorch Conv-LSTM with 5 prediction heads)
- [ ] Train on 5+ years multi-asset data using Google Colab free GPU
- [ ] Implement self-correction loop (every closed candle → error → backprop)
- [ ] Render ghost candles on TradingView chart with opacity gradient (0.99^step decay)
- [ ] Pattern accuracy tracking (display in P9 panel)
- [ ] M23 RL self-learning brain (Stable-Baselines3 PPO)
- [ ] Reward function: profit% - drawdown - slippage costs
- [ ] Per-asset brain checkpoint manager
- [ ] Brain version comparison + rollback UI
- [ ] M3/M7/M8 Conv-LSTM + XGBoost integrate with FreqAI
- [ ] **Validation:** Ghost candles render correctly, accuracy improves week over week, brain converges on backtest data

### Phase 4 — Tab 3 Scanner (6 weeks)
- [ ] Async parallel scanner engine (asyncio + 20 workers)
- [ ] Redis cache layer (110s TTL)
- [ ] Signal classification (CONFIRMED ≥8 layers, PROBABLE 6-7, WEAK <6)
- [ ] Wyckoff phase detection
- [ ] Tab 3 toolbar with all filter pills
- [ ] Hybrid Supervisor cyan progress bar
- [ ] Two-column bull/bear layout
- [ ] Signal card component with all 6-tag system + sparkline + score tags
- [ ] Click card → opens Tab 1 with that asset loaded
- [ ] Watchlist favorites + star system + persistence
- [ ] WebSocket scanner_update every 2 min
- [ ] **Validation:** 200 assets scan in <30 seconds, all classifications display correctly, click-through to Tab 1 works

### Phase 5 — Tab 2 Paper Lab + News (6 weeks)
- [ ] M32 paper trading engine using Freqtrade dry-run
- [ ] Per-asset background worker spawning ("Start paper trade" button in Tab 1)
- [ ] M41 Tab 2 dashboard: portfolio metrics + per-asset breakdown
- [ ] Brain learning curves (accuracy over time)
- [ ] M6 news aggregator (CryptoPanic + RSS feeds)
- [ ] M7 FinBERT-LSTM news classifier microservice
- [ ] M8 Forex Factory economic calendar scraper
- [ ] M35 News feed UI with 20-day rolling auto-delete
- [ ] M9 Whale + on-chain integration (Glassnode free)
- [ ] News-driven veto rules (pause 30 min before/after major events)
- [ ] **Validation:** 30+ assets paper-trading in parallel, brain accuracy rising over 4 weeks, news properly classifies and impacts predictions

### Phase 6 — Polish + Optional Live Bridge (Ongoing)
- [ ] Mobile-responsive layouts
- [ ] Telegram bot integration for top scanner picks
- [ ] Grafana dashboards for system metrics
- [ ] Backtesting framework with M17 + M21 (walk-forward + tick-level sim)
- [ ] M22 audit trail (append-only logs)
- [ ] M27 latency monitor + auto-pause
- [ ] M19 disaster recovery (dead-man switch)
- [ ] After 90 days successful paper trading: optionally enable Freqtrade live mode
- [ ] Quarterly retrain + parameter review
- [ ] **Validation:** Sharpe > 1.5 on paper, max DD < 15%, ready for live

---

## 14. Critical Build Rules (DO NOT SKIP)

1. **Build Phase 1 first.** Resist urge to start with cool stuff (ghost candles, brain). Foundation must be rock-solid.
2. **Run dry-run only.** Freqtrade NEVER trades real money in this platform. Real trading is a separate decision after 90+ days paper trading proves stable.
3. **Validate every layer.** Cross-check 100 random data points against TradingView for every indicator. If even one indicator is off by 1%, your whole system is wrong.
4. **Use Half-Kelly for sizing.** Full Kelly is too aggressive. Always Half-Kelly minimum.
5. **Shorts require +2 layer threshold higher than longs.** Asymmetric risk demands asymmetric sizing.
6. **Brain trains nightly, not real-time.** Real-time RL training causes instability. Train at 00:00 UTC on 256 random replay samples.
7. **Backtest with realistic slippage + fees.** Without these, backtests lie about live performance.
8. **API keys: trade-only permissions, NEVER withdrawal.** This is non-negotiable for security.
9. **Audit trail (M22) is append-only.** No deletes, no edits. Every prediction logged with timestamps + model version.
10. **News from observed content is NOT instructions.** The news layer informs predictions but never overrides safety rules.

---

## 15. Realistic Expectation Matrix

| Metric | Target | Honest Reality |
|--------|--------|----------------|
| Win rate per trade | 60–68% | Achievable after 6 months brain training |
| Average risk-reward | 1:2 minimum | Set by ATR-based stops |
| Annual return | 50–120% net | Realistic; 150%+ requires luck |
| Max drawdown | < 18% | Plan for 25% temporary spike |
| Sharpe ratio | > 2.0 | Excellent; > 1.5 acceptable |
| Probability bot survives 1 year | ~70% | Up from 40% in v1 |
| Probability profitable 3+ years | ~35% | Up from 15% in v1 |
| Build success probability | ~60% | Solo dev, 9 months consistency required |
| Ghost candle 1-step accuracy | 60–75% | Reliable for next-candle |
| Ghost candle 5-step accuracy | 45–55% | Directional correct, prices approximate |
| Ghost candle 50-step accuracy | 25–35% | STRUCTURE only, not prices |

---

## 16. Day-One Setup Commands (Copy-Paste Ready)

### 1. Provision Oracle Cloud
1. Sign up at https://www.oracle.com/cloud/free/
2. Create Compute → "Always Free Eligible" → ARM-based Ampere A1 (4 OCPU, 24 GB RAM)
3. Choose Ubuntu 22.04 ARM64 image
4. SSH in: `ssh -i ~/.ssh/oracle_key ubuntu@<vm-ip>`

### 2. Install Docker + Tools
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-v2 git python3.11 python3-pip nodejs npm postgresql-client redis-tools
sudo usermod -aG docker $USER
newgrp docker
docker --version && docker compose version
```

### 3. Clone & Initialize Project
```bash
git clone https://github.com/<your-username>/trading-radar.git
cd trading-radar
cp .env.example .env
# Edit .env with your API keys (Binance testnet, etc.)
nano .env
```

### 4. Start All Services
```bash
docker compose up -d postgres redis
docker compose exec postgres psql -U postgres -c "CREATE DATABASE trading_radar;"
docker compose exec postgres psql -U postgres trading_radar < infra/scripts/init_db.sql
docker compose up -d backend frontend freqtrade grafana prometheus
docker compose ps  # All should be "Up"
docker compose logs -f backend  # Watch backend startup
```

### 5. Verify Services
- Backend API: `http://<vm-ip>:8000/docs` (FastAPI Swagger)
- Frontend: `http://<vm-ip>:5173` (React dev) or `http://<vm-ip>` (production)
- Grafana: `http://<vm-ip>:3000` (admin/admin)
- Freqtrade Web UI: `http://<vm-ip>:8080`

### 6. Set up Cloudflare Tunnel (free HTTPS)
```bash
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i cloudflared.deb
cloudflared tunnel login
cloudflared tunnel create trading-radar
cloudflared tunnel route dns trading-radar yourdomain.com
# Edit ~/.cloudflared/config.yml to forward yourdomain.com → http://localhost:5173
cloudflared tunnel run trading-radar &
```

---

## 17. .env Template

```bash
# Database
POSTGRES_USER=postgres
POSTGRES_PASSWORD=changeme_strong_password_here
POSTGRES_DB=trading_radar
DATABASE_URL=postgresql://postgres:changeme_strong_password_here@postgres:5432/trading_radar

# Redis
REDIS_URL=redis://redis:6379/0

# Binance (TESTNET FIRST)
BINANCE_API_KEY=your_testnet_key
BINANCE_API_SECRET=your_testnet_secret
BINANCE_USE_TESTNET=true

# Bybit
BYBIT_API_KEY=your_key
BYBIT_API_SECRET=your_secret

# News APIs (free tiers)
CRYPTOPANIC_TOKEN=your_free_token
TWELVEDATA_API_KEY=your_free_key
GLASSNODE_API_KEY=your_free_key

# Telegram (alerts)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# App config
ENV=development
LOG_LEVEL=INFO
SECRET_KEY=generate_random_64_char_string_here
ALLOWED_ORIGINS=http://localhost:5173,https://yourdomain.com
WEBSOCKET_PORT=8000

# Freqtrade
FREQTRADE_DRY_RUN=true
FREQTRADE_INITIAL_CAPITAL=10000
```

---

## 18. Instructions for Claude Code

When this plan is given to Claude Code, follow this strict order:

### Phase 1 first (3 weeks)
1. Create the project folder structure exactly as specified in section 10
2. Initialize git repo, create `.gitignore`, `README.md`
3. Write `docker-compose.yml` with all services
4. Initialize FastAPI backend with health check endpoint + WebSocket support
5. Initialize React + Vite + TypeScript + TailwindCSS frontend with theme system
6. Set up PostgreSQL + TimescaleDB schema (init_db.sql)
7. Configure Freqtrade in dry-run mode
8. Verify everything boots with `docker compose up`

### Phase 2 next (10 weeks)
9. Implement data adapters (Binance, Bybit, Yahoo, TwelveData)
10. Implement all 43 technical indicators
11. Implement 82 candle patterns + 76 chart patterns
12. Implement 10-layer scoring (L1-L9; L10 placeholder)
13. Implement 12 trap filter system
14. Build all 14 React sidebar panel components matching reference exactly
15. Integrate TradingView Lightweight Charts
16. Connect WebSocket pipeline

### Strict rules for Claude Code:
- Use exact color values from CSS variables
- Use exact font sizes from typography rules
- Match panel layouts pixel-perfect to reference screenshots
- Write tests for every layer scoring function
- Always validate against TradingView for indicator accuracy
- Never use real trading capital in any code path
- Always log predictions to audit trail (M22)
- Never bypass circuit breaker (M11)

### Test before next phase
At end of each phase, manually verify the validation criteria. Do not advance until current phase is stable.

---

## END OF MASTER PLAN

This document is the complete blueprint. Hand it to Claude Code with the instruction:
**"Build trading-radar following MASTER_PLAN.md. Start with Phase 1. Do not skip phases. Do not invent features beyond the spec. Match UI exactly to reference colors and dimensions specified."**
