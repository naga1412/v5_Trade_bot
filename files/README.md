# trading-radar

Zero-cost retail trading research platform with self-learning AI brain.

> **⚠️ Important:** This is a research and analysis platform, NOT an autonomous trading bot. Freqtrade runs in dry-run mode only. Real trading decisions are made by the human user based on the platform's analysis.

## What It Does

Three-tab analytical platform for crypto, forex, and commodities trading:

1. **Tab 1 — Live Prediction:** Single-asset deep analysis with chart + 14 sidebar panels + ghost candle overlay
2. **Tab 2 — Paper Trading Lab:** Per-asset paper trade performance + AI brain learning curves
3. **Tab 3 — Scanner Radar:** Multi-asset opportunity scanner (200+ assets, refresh every 2 min)

## Key Features

- 10-layer confluence prediction system
- 158 candlestick + chart pattern detection library
- 12 trap filters (long + short asymmetric)
- Ghost candle prediction (1–50 candles ahead)
- Self-learning RL brain (PPO algorithm)
- News intelligence (FinBERT-LSTM)
- Multi-market: crypto futures + spot + commodities + forex
- Real-time scanner across 200+ assets

## Tech Stack

- **Backend:** Python 3.11, FastAPI, Freqtrade (forked), CCXT, Celery, Redis
- **AI:** PyTorch, XGBoost, Stable-Baselines3, FinBERT (HuggingFace)
- **Frontend:** React 18 + TypeScript, Vite, TailwindCSS, TradingView Lightweight Charts
- **Infrastructure:** PostgreSQL + TimescaleDB, Redis, Docker Compose, Oracle Cloud Free Tier

## Quick Start

```bash
# 1. Clone repo
git clone https://github.com/<username>/trading-radar.git
cd trading-radar

# 2. Configure environment
cp .env.example .env
nano .env  # Fill in API keys

# 3. Start services
docker compose up -d

# 4. Open dashboard
# Frontend: http://localhost:5173
# Backend:  http://localhost:8000/docs
# Grafana:  http://localhost:3000
```

See [`MASTER_PLAN.md`](./MASTER_PLAN.md) for complete architecture.
See [`CLAUDE.md`](./CLAUDE.md) for build rules.

## Project Status

Active development — Phase 1 (Foundation).

## License

Private — for personal use only. Forked from Freqtrade (GPL-3.0).

## Disclaimer

Automated trading is risky and can result in total loss of capital. This platform is for research purposes. Backtested results never guarantee future performance. Always trade with money you can afford to lose. Verify with a qualified professional before deploying real capital.
