# SP-1 — ML Data Pipeline + Ghost Candles Design Spec

**Date:** 2026-05-05
**Status:** Approved by user
**Implementation target:** Sub-project SP-1 (after SP-0.7 multi-user wrapper; before SP-2..SP-4)
**Depends on:** SP-0.5 (shadow trading data accumulation), SP-0.7 (per-user data isolation)
**Companion specs:** `2026-05-01-trading-radar-meta-plan-design.md`, `2026-05-03-multi-user-design.md`
**Why first ML sub-project:** highest-risk technical unknown — must surface failure mode in week 6, not month 5

---

## 1. Purpose

Add machine-learning-driven 1-step OHLC prediction to trading-radar. Train a small Conv-LSTM on historical Binance OHLCV data, evaluate on five fixed historical regime windows, deploy as inference on the existing FastAPI backend, render the predicted "ghost" candle on Tab 1's chart with an uncertainty band.

This is the load-bearing test of whether ML can add value over the existing rule-based scoring (L1 + L3 + L5 from SP-0). If the model fails to hit the 1.5% MAE acceptance bar, the platform still works — ML predictions are upside, not infrastructure.

### Non-goals

- **No multi-step forecasting in v1.** Single bar ahead only. Multi-step is SP-1 v2.
- **No L7 XGBoost ensemble.** Deferred to SP-1.5.
- **No RL brain (L10).** Deferred to SP-4.
- **No per-user adapters.** Global model only. Per-user LoRA adapters are SP-4.
- **No live retraining loop in v1.** Manual monthly retraining cycle on Colab; automation is SP-4.
- **No per-asset model.** Single multi-asset model trained on all 30 USDT pairs simultaneously (improves generalization).

---

## 2. Locked decisions

| # | Decision | Value |
|---|---|---|
| 1 | Model architecture | **Conv-LSTM** (1D conv layers + LSTM + dense head, ~500K params) |
| 2 | Input | Last **256 bars** of OHLCV (10 days of 1h candles), normalized as % change from last close |
| 3 | Output | **4 floats** — next bar's open / high / low / close (also as % change) |
| 4 | Uncertainty | **Monte Carlo dropout** — 32 forward passes at inference, P5/P50/P95 percentiles |
| 5 | Loss | **MAE** (Mean Absolute Error) on the 4 OHLC outputs |
| 6 | Optimizer | Adam + cosine LR decay, batch size 64, ~30 epochs with early stopping |
| 7 | Train split | **Time-based** — train (2017→2023), val (2024), test (2025→today) + 5 regime windows held out |
| 8 | Training infra | **Google Colab T4** (free tier), checkpoint shipped to Oracle as `.pt` file |
| 9 | Inference infra | **Oracle backend CPU** (4 vCPU sufficient — ~10ms per asset per forward pass × 32 MC samples = ~320ms per asset per closed candle) |
| 10 | Retrain cadence | **Monthly v0 → quarterly when stable**; manual trigger via Colab notebook |
| 11 | Multi-asset | **Single global model** trained on all 30 top USDT pairs (~1.5M training pairs) |
| 12 | Per-user | **No per-user adapters** in SP-1; predictions are global (one row per closed candle, same for all users — but stored per-user to enable adapter divergence in SP-4) |
| 13 | Acceptance bar | **≤1.5% MAE on ALL 5 regime windows** — single window failure rejects the model |
| 14 | UI render | **Always visible**, dim opacity (50%), uncertainty wicks at P5/P95 |
| 15 | Failure fallback | **L1+L3+L5 rule-based scoring continues** unchanged; ML failure = no ghost candle, no L8 score |

---

## 3. Architecture

### 3.1 Conv-LSTM model

```python
class ConvLSTMPredictor(nn.Module):
    def __init__(self):
        super().__init__()
        # Conv stack: extract local patterns
        self.conv1 = nn.Conv1d(in_channels=5, out_channels=32, kernel_size=3, padding=1)
        self.conv2 = nn.Conv1d(in_channels=32, out_channels=64, kernel_size=3, padding=1)
        self.conv_drop = nn.Dropout(0.2)

        # LSTM stack: capture temporal dependencies
        self.lstm = nn.LSTM(input_size=64, hidden_size=128, num_layers=2,
                            dropout=0.2, batch_first=True)

        # Dense head: predict 4 OHLC % changes
        self.fc = nn.Linear(128, 4)

    def forward(self, x):
        # x shape: (batch, 256, 5)  — 256 bars, 5 features
        x = x.transpose(1, 2)            # (batch, 5, 256) for Conv1d
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = self.conv_drop(x)
        x = x.transpose(1, 2)            # (batch, 256, 64) for LSTM
        h, _ = self.lstm(x)
        return self.fc(h[:, -1, :])      # (batch, 4) — predicted next OHLC % changes
```

Total params: ~480K. Fits comfortably in Colab T4's 16 GB during training and Oracle's 4 vCPU during inference.

### 3.2 Input normalization

Raw OHLCV cannot be fed directly — BTC was $30k in 2022 and is $80k now; the model must learn *relative* movements, not absolute levels.

```python
def normalize_window(bars: pd.DataFrame) -> torch.Tensor:
    """bars: DataFrame with columns [open, high, low, close, volume], 256 rows.
    Returns tensor shape (256, 5) with O/H/L/C as % change from last close,
    volume as z-score over the window.
    """
    last_close = bars["close"].iloc[-1]
    pct = bars[["open", "high", "low", "close"]].div(last_close).sub(1.0)
    vol_z = (bars["volume"] - bars["volume"].mean()) / bars["volume"].std()
    return torch.tensor(np.column_stack([pct.values, vol_z.values]), dtype=torch.float32)
```

At inference, the model output is denormalized back to absolute prices before persisting:

```python
def denormalize_prediction(pred_pct: torch.Tensor, last_close: float) -> dict[str, float]:
    """pred_pct: (4,) tensor of % changes. Returns {open, high, low, close} in price units."""
    return {
        "open":  last_close * (1.0 + pred_pct[0].item()),
        "high":  last_close * (1.0 + pred_pct[1].item()),
        "low":   last_close * (1.0 + pred_pct[2].item()),
        "close": last_close * (1.0 + pred_pct[3].item()),
    }
```

### 3.3 Uncertainty quantification

`model.train()` mode is enabled at inference time so dropout fires. Run 32 forward passes per prediction, compute statistics:

```python
def predict_with_uncertainty(model, x, n_samples=32):
    model.train()  # dropout active
    samples = torch.stack([model(x) for _ in range(n_samples)])  # (32, batch, 4)
    return {
        "mean":  samples.mean(dim=0),
        "p5":    samples.quantile(0.05, dim=0),
        "p95":   samples.quantile(0.95, dim=0),
        "std":   samples.std(dim=0),
    }
```

The `p5` and `p95` percentiles render as the ghost candle's uncertainty wicks; `mean` renders as the candle body.

---

## 4. Data model

### 4.1 New table: `feature_registry`

```sql
CREATE TABLE feature_registry (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    version INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL,
    dtype TEXT NOT NULL CHECK (dtype IN ('float', 'int', 'bool', 'category')),
    layer INTEGER,                       -- which scoring layer uses this (1-10) or NULL for raw
    computation TEXT NOT NULL,           -- Python expression or function name
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Documents which features exist, their semantics, and which scoring layer consumes them. Seeded by an alembic data migration with the initial set (RSI, MACD, ATR, etc. + the 5 raw OHLCV columns).

### 4.2 New table: `ml_checkpoints`

```sql
CREATE TABLE ml_checkpoints (
    id BIGSERIAL PRIMARY KEY,
    model_name TEXT NOT NULL,            -- 'conv_lstm_predictor'
    version TEXT NOT NULL,               -- semver: '0.1.0', '0.2.0'
    checkpoint_uri TEXT NOT NULL,        -- 'b2://trading-radar-models/conv_lstm_v0.1.0.pt'
    sha256 TEXT NOT NULL,                -- integrity check on download
    trained_at TIMESTAMPTZ NOT NULL,
    train_data_window TEXT NOT NULL,     -- '2017-01 to 2023-12'
    eval_results JSONB NOT NULL,         -- per-regime MAE: {bull: 0.012, bear: 0.014, ...}
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    activated_at TIMESTAMPTZ,
    deactivated_at TIMESTAMPTZ,
    notes TEXT,
    UNIQUE (model_name, version)
);
CREATE INDEX ml_checkpoints_active_idx ON ml_checkpoints (model_name, is_active) WHERE is_active = TRUE;
```

Backend loads `is_active=true` checkpoint at startup. Activating a new checkpoint deactivates the previous one (atomic via transaction). Audit-trail visible in admin UI.

### 4.3 New table: `pattern_stats`

```sql
CREATE TABLE pattern_stats (
    id BIGSERIAL PRIMARY KEY,
    pattern_id TEXT NOT NULL,            -- 'hammer', 'engulfing_bull', etc.
    symbol TEXT NOT NULL,                -- 'BTC/USDT' or 'GLOBAL' for cross-asset
    timeframe TEXT NOT NULL,             -- '1h', '4h', etc.
    n_samples INTEGER NOT NULL DEFAULT 0,
    n_correct INTEGER NOT NULL DEFAULT 0,
    accuracy DOUBLE PRECISION GENERATED ALWAYS AS
        (CASE WHEN n_samples = 0 THEN 0.5 ELSE n_correct::float / n_samples END) STORED,
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pattern_id, symbol, timeframe)
);
```

Per meta-plan §2.4. Updated by a nightly job that joins `predictions` rows (which patterns fired) against `shadow_trades` outcomes (did they win?). Patterns with `n_samples < 50` use prior `accuracy = 0.5` (handled in the GENERATED column).

### 4.4 Extensions to `predictions` table

Add columns to the existing `predictions` table (alembic migration 0007):

```sql
ALTER TABLE predictions ADD COLUMN ghost_open DOUBLE PRECISION;
ALTER TABLE predictions ADD COLUMN ghost_high DOUBLE PRECISION;
ALTER TABLE predictions ADD COLUMN ghost_low DOUBLE PRECISION;
ALTER TABLE predictions ADD COLUMN ghost_close DOUBLE PRECISION;
ALTER TABLE predictions ADD COLUMN ghost_p5_low DOUBLE PRECISION;
ALTER TABLE predictions ADD COLUMN ghost_p95_high DOUBLE PRECISION;
ALTER TABLE predictions ADD COLUMN ghost_uncertainty DOUBLE PRECISION;
ALTER TABLE predictions ADD COLUMN model_checkpoint_id BIGINT REFERENCES ml_checkpoints(id);
```

All nullable — predictions written before SP-1 ships have NULL ghost data, predictions written after the model is loaded include the ghost candle. Audit chain canonical row hash includes the new columns (so cross-version tampering is detectable).

---

## 5. Training pipeline

### 5.1 Data export (backend → B2)

A new nightly cron job in the backend exports the last 30 days of OHLCV + predictions + shadow_trades + pattern fires as Parquet files to the existing B2 bucket:

```
b2://trading-radar-backups/ml-exports/2026-05-05/
├── ohlcv_1h_30d.parquet           (~10 MB)
├── predictions_30d.parquet        (~5 MB)
├── shadow_trades_30d.parquet      (~1 MB)
└── manifest.json                   (export metadata + integrity hashes)
```

For the first training run (one-time bulk), a separate script `tools/ml/bulk_export.py` exports all historical data (~5 GB compressed) to B2. Subsequent runs are incremental.

### 5.2 Colab training notebook

`tools/ml/colab/train_conv_lstm.ipynb` — committed to the repo, opened directly from GitHub. Workflow:

1. Mount Google Drive, install torch + duckdb + boto3
2. `boto3.client("s3", endpoint_url=B2).download_file(...)` to pull the bulk Parquet from B2
3. `duckdb.sql("SELECT ... FROM parquet_scan(...)")` to construct training pairs (sliding window over OHLCV)
4. Build PyTorch DataLoader with batch_size=64, shuffle=True (within train split only)
5. Train ~30 epochs with early stopping (patience=5)
6. Evaluate on val set + 5 regime windows
7. If all 5 regime windows pass: save checkpoint, upload to B2, register in `ml_checkpoints` via the admin API
8. Manual trigger to set `is_active=true`

Estimated wall-clock: ~3 hours per full training run on Colab T4 free tier.

### 5.3 Eval harness — 5 regime windows

```python
REGIME_WINDOWS = [
    ("bull_breakout",   "2020-10-01", "2021-04-30"),  # post-COVID rally
    ("bear_crash",      "2022-04-01", "2022-12-31"),  # LUNA + FTX
    ("sideways_grind",  "2023-04-01", "2023-09-30"),  # range-bound
    ("high_volatility", "2020-03-01", "2020-04-15"),  # COVID crash
    ("low_volatility",  "2024-04-01", "2024-07-31"),  # post-halving compression
]

def evaluate_on_regime(model, regime_name, start, end) -> dict:
    """Returns {mae: 0.013, samples: 4380, passes_acceptance: True}."""
    bars = load_ohlcv("BTC/USDT", "1h", start, end)
    predictions = []
    actuals = []
    for window_end in range(256, len(bars) - 1):
        x = normalize_window(bars.iloc[window_end-256:window_end])
        pred = model(x.unsqueeze(0))
        actual = bars.iloc[window_end+1][["open","high","low","close"]]
        predictions.append(denormalize(pred, bars.iloc[window_end]["close"]))
        actuals.append(actual)
    mae = compute_mae(predictions, actuals)
    return {"mae": mae, "samples": len(predictions), "passes": mae <= 0.015}
```

ALL 5 windows must show `passes=True` for the checkpoint to be eligible for activation.

### 5.4 Model registry

The notebook calls `POST /api/v1/admin/ml-checkpoints` (new admin endpoint, see §6.4) to register a freshly trained checkpoint. Admin sees it in the UI, reviews the eval JSON, clicks "Activate" to flip `is_active=true` (deactivating the previous). All transactions; rollback is one click.

---

## 6. Inference pipeline

### 6.1 Checkpoint loading

Backend startup (in `app/main.py:lifespan`):

```python
async def load_active_checkpoint() -> tuple[ConvLSTMPredictor, MlCheckpoint] | None:
    """Load the active checkpoint from B2. Returns None if none active (dev startup)."""
    async with session_factory() as session:
        active = await session.scalar(
            select(MlCheckpoint).where(
                MlCheckpoint.model_name == "conv_lstm_predictor",
                MlCheckpoint.is_active == True,
            )
        )
        if active is None:
            log.warning("no active ML checkpoint; ghost candles disabled")
            return None
        local_path = await download_with_sha_check(active.checkpoint_uri, active.sha256)
        model = ConvLSTMPredictor()
        model.load_state_dict(torch.load(local_path, map_location="cpu"))
        return model, active
```

Held in module-scope `_active_model` and `_active_checkpoint` references. Accessed by the live prediction worker.

### 6.2 Per-asset prediction loop

The existing `app/ws/live_prediction.py:run_live_prediction` (BTC/USDT 1h) and `app/shadow/worker.py` (30 assets, 1h) get extended:

```python
def predict_ghost_candle(model, bars: pd.DataFrame, last_close: float) -> dict:
    """Returns ghost candle + uncertainty band for the next bar."""
    x = normalize_window(bars.iloc[-256:]).unsqueeze(0)
    with torch.no_grad():
        samples = torch.stack([model(x) for _ in range(32)])  # (32, 1, 4)
    mean = samples.mean(dim=0).squeeze(0)
    p5 = samples.quantile(0.05, dim=0).squeeze(0)
    p95 = samples.quantile(0.95, dim=0).squeeze(0)
    return {
        "ghost_open":  last_close * (1.0 + mean[0].item()),
        "ghost_high":  last_close * (1.0 + mean[1].item()),
        "ghost_low":   last_close * (1.0 + mean[2].item()),
        "ghost_close": last_close * (1.0 + mean[3].item()),
        "ghost_p5_low":  last_close * (1.0 + p5[2].item()),
        "ghost_p95_high": last_close * (1.0 + p95[1].item()),
        "ghost_uncertainty": float(samples.std(dim=0).mean().item()),
    }
```

Called once per closed candle, after `build_prediction(...)`, before `persist_prediction(...)`. Adds ~320ms per asset (32 forward passes × 10ms) — fits comfortably in the 1-hour candle budget.

If `_active_model` is None (no checkpoint loaded), ghost fields are NULL and the prediction is persisted without them. Live prediction continues — ghost is additive.

### 6.3 WebSocket payload

Existing `live_prediction` channel payload extended with ghost fields (all nullable):

```typescript
interface LivePrediction {
  // ... existing fields ...
  ghost?: {
    open: number;
    high: number;
    low: number;
    close: number;
    p5_low: number;
    p95_high: number;
    uncertainty: number;       // [0, ∞), lower = more confident
  };
}
```

Frontend extends `useLivePrediction` to expose `data.ghost` to the chart component.

### 6.4 Admin endpoints

```
POST   /api/v1/admin/ml-checkpoints           — register new checkpoint (called by Colab notebook)
GET    /api/v1/admin/ml-checkpoints           — list all (active + historical)
PATCH  /api/v1/admin/ml-checkpoints/{id}      — activate / deactivate
DELETE /api/v1/admin/ml-checkpoints/{id}      — soft delete (sets deactivated_at)
```

All admin-gated via `Depends(require_admin)` from SP-0.7.

---

## 7. Frontend — ghost candle on chart

### 7.1 TVChart extension

`src/components/chart/TVChart.tsx` accepts a new `ghost` prop:

```tsx
interface Props {
  symbol: string;
  timeframe: string;
  livePrice?: number;
  liveTs?: string;
  signalMarkers?: SignalMarkers | null;
  ghost?: GhostCandle | null;            // NEW
}

interface GhostCandle {
  open: number;
  high: number;
  low: number;
  close: number;
  p5_low: number;
  p95_high: number;
  uncertainty: number;
  ts: string;            // when the prediction was made (last closed bar's ts)
}
```

When `ghost` is non-null, the chart appends a single candle to the right of the latest real bar at `ts + 1 timeframe`:
- Body: `ghost.open` → `ghost.close` (greenish if up, reddish if down)
- Wicks: `ghost.high` and `ghost.low`
- Uncertainty band: thin lines extending to `p5_low` and `p95_high` (different style)
- Opacity: 50% (so it's visually distinguishable from real candles)
- Always visible (no toggle in v1)

If model is unavailable (`ghost === null`) the chart renders normally — no ghost, no error message, no UI flag. The user just doesn't see a prediction.

### 7.2 Confidence indicator

Existing `MasterBiasScore` panel gets a new sub-row showing model confidence:

```
MASTER BIAS SCORE
+25.5                                                              BULL
████████████████████████░░░░░░░░░░░░░░░

GHOST CANDLE                                                  ±$420 / 0.5%
Open  $79808.77 → Close  $80120.45  (+0.39%)
```

The `±$420 / 0.5%` is the uncertainty band width converted from `ghost.uncertainty`. Lower = more confident.

### 7.3 No new tab

Ghost candles are part of Tab 1 (Live Prediction). No new tab in v1. A "Model Performance" tab showing per-regime MAE charts is a follow-up.

---

## 8. Cross-cutting policy compliance (per meta-plan §5)

| Policy | How SP-1 satisfies it |
|---|---|
| §5.14 audit hash chain | `predictions` row hash now includes ghost_* columns; `ml_checkpoints` is append-mostly with audit fields |
| §5.13 backups | Existing pg_dump covers new tables; checkpoint files are in B2 (already replicated) |
| §5.15 rate limits | Inference is internal-only (no public API for ML predictions); existing rate limits unchanged |
| §2.6 Cloudflare Access | New admin endpoints inherit `Depends(require_admin)` from SP-0.7 |
| §2.7 hosting | Inference fits in Oracle's 4 vCPU + 24 GB RAM budget; training is offloaded to free Colab |
| Per-user (SP-0.7 §7.3) | `predictions.user_id` already exists; ghost fields are global (same prediction for all users in v1) but stored per-user to enable per-user adapters in SP-4 |

---

## 9. Acceptance criteria

- [ ] Conv-LSTM trained on 2017→2023 BTC/USDT 1h data converges (val MAE plateaus < 1.8%)
- [ ] All **5 regime windows** show test MAE ≤ 1.5%
- [ ] Checkpoint successfully uploads to B2 + registers in `ml_checkpoints` table
- [ ] Backend loads active checkpoint at startup, logs warning if none active
- [ ] Ghost candle persists to `predictions.ghost_*` columns on every closed BTC/USDT 1h candle
- [ ] WebSocket payload carries the ghost field
- [ ] Tab 1 chart renders ghost candle with uncertainty band, dimmed to 50% opacity
- [ ] If model is unavailable, chart renders normally (no error, no ghost)
- [ ] `pattern_stats` nightly job populates accuracy from `predictions` × `shadow_trades` join
- [ ] `feature_registry` seeded with at least the existing indicator set (RSI, MACD, ATR, EMA20, EMA50)
- [ ] Admin can list/activate/deactivate checkpoints via `/api/v1/admin/ml-checkpoints`
- [ ] Audit chain integrity verified after migration 0007 (existing rows still verify)

---

## 10. Risk + fallback plan

| Failure mode | Detection | Fallback |
|---|---|---|
| Model can't beat random walk (~0.5% MAE) | Eval harness flags it; refuse to register checkpoint | Don't deploy; revisit feature engineering or try L7 XGBoost (SP-1.5) |
| 4 of 5 regime windows pass | Eval harness rejects checkpoint registration | Re-tune model; do not deploy partial-pass models |
| Live MAE drifts above 2% after deploy | Nightly job compares predictions vs actuals; alerts admin if > threshold | Auto-rollback to previous active checkpoint (admin click) |
| Checkpoint download from B2 fails at startup | Backend logs warning, ghost fields stay NULL | Service stays up; bot still trades on rule-based scoring |
| Conv-LSTM fundamentally doesn't work for crypto | Even after re-tuning, no checkpoint passes acceptance | Pivot to L7 XGBoost as the primary ML signal (SP-1.5 reorders) |
| Colab free tier becomes restricted | Training notebook fails | Fall back to Colab Pro ($10/mo) or local training on laptop GPU if available |
| Inference latency exceeds 1-second budget per asset | Logged at startup; per-prediction timing in worker | Reduce MC sample count from 32 to 16 (halves latency, slightly wider uncertainty bands) |

**Crucially: SP-1 failure does NOT brick the bot.** The existing rule-based scoring (L1 + L3 + L5) and shadow trading worker continue unchanged. ML is upside; failure is recoverable.

---

## 11. Sub-project sequencing

This spec is implemented as **SP-1**, between SP-0.7 (multi-user wrapper, just shipped) and SP-2..SP-4.

After SP-1 ships, the natural next sub-projects:

- **SP-1.5** — L7 XGBoost on engineered features (parallel ML signal, ensemble candidate)
- **SP-2** — Pattern detection layer (the 158-pattern voting scheme from meta-plan §2.4)
- **SP-3** — News + sentiment layer (FinBERT)
- **SP-4** — RL brain (L10) + per-user LoRA adapters (depends on SP-1's pipeline)

---

## 12. Implementation cost estimate

- Sub-project size: **~30 tasks across 6 phases** (smaller than SP-0.7's 49 tasks; mostly because the frontend lift is much smaller — single component extension, no new tab)
- Wall-clock: **~6 weeks of subagent-driven work** (per meta-plan §3 §175)
- Phase ordering (risk-first):
  - **Phase A (week 1)** — Data export pipeline + B2 upload + bulk export script
  - **Phase B (week 2)** — Eval harness + 5 regime windows + baseline (random walk) measurement
  - **Phase C (week 3)** — Conv-LSTM v0 trains + ships a checkpoint + at-least-1-window passes
  - **Phase D (week 4)** — Ghost candle UI overlay (works against v0 model output)
  - **Phase E (weeks 5-6)** — Iterate model to ≤1.5% MAE on ALL 5 windows; tune hyperparameters
  - **Phase F (week 6 tail)** — Pattern stats nightly job, admin endpoints, ship
- New backend modules: `app/ml/{model.py,inference.py,export.py,patterns.py}`, `app/api/routes/admin_ml.py`
- New frontend: minimal — extend `TVChart` + `useLivePrediction`
- Database migrations: 1 large (0007_ml_tables_and_predictions_extension)
- Test coverage: per-regime MAE assertions are the critical gate; unit tests for normalization, denormalization, MC sampling

---

## 13. Open questions (to be resolved during implementation)

| # | Question | Resolved during |
|---|---|---|
| 1 | Should ghost candles also persist for shadow trading (multi-asset 1h) or just live BTC/USDT? | Phase D — likely both; same code path, just more rows |
| 2 | What's the exact threshold for "model degradation alert"? (live MAE > X for Y bars in a row) | Phase F — start with `live_mae > 2% for 24h` as a soft alert |
| 3 | Should the Colab notebook auto-register checkpoints, or always require admin manual review? | Phase F — manual review for v0 ship; auto-register can come later |
| 4 | Multi-step forecasting — should the spec leave hooks for it (e.g., output shape `(n_steps, 4)`)? | Phase C — keep output `(4,)` for v1; multi-step is a different model |
| 5 | What happens to a checkpoint that was active when its rows are queried for backfill? Audit chain implications? | Phase A — `model_checkpoint_id` FK on predictions makes this provenance-tracked; no chain break |

---

## 14. Reference

- Meta-plan: `docs/superpowers/specs/2026-05-01-trading-radar-meta-plan-design.md` §3 §2.2 §2.5
- Multi-user spec: `docs/superpowers/specs/2026-05-03-multi-user-design.md` §7.1 (user_id on predictions)
- SP-0.7 plan: `docs/superpowers/plans/2026-05-04-SP-0.7-multi-user-plan.md`
- Conv-LSTM reference: Shi et al. 2015 ("Convolutional LSTM Network: A Machine Learning Approach for Precipitation Nowcasting") — adapted from weather prediction to OHLC time series

---

**END OF SP-1 ML DATA + GHOST CANDLES DESIGN SPEC**
