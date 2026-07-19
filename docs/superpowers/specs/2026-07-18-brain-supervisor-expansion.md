# Brain Supervisor Expansion — Design Spec

**Status:** Approved 2026-07-18. Implementation authorized to begin immediately.  
**Date:** 2026-07-18  
**Author:** Claude (autonomous mode) + naga1412 (vision + phase gate decisions)  
**Relates to:** `2026-05-07-SP-4-rl-brain-design.md` (parent), `2026-05-16-pr1-record-only-design.md` (persistence idiom)

---

## 1. Vision

The 9 scoring layers (L1–L9) are **workers**. The brain (L10) is the **supervisor** that learns per-worker reliability from real outcomes. Right now the supervisor sees only the 9 layer votes + 17 market features; it is blind to *how* each worker arrived at its vote, blind to the market microstructure a worker operates in, and trained on fewer than 2 months of live shadow data.

This expansion hires **5 missing workers** as record-only feature computers, extends the brain's observation to see them, and pre-trains the brain on years of backtested history so it enters live trading judging like a veteran — not a cold-start guesser.

**Brain authority grows ONLY on measured performance.** Wider adjust range, veto power, and sizing authority are separate future PRs each gated on operator approval + positive brain Sharpe in prod. This spec covers the infra build-out, not the authority escalation.

---

## 2. Phases at a Glance

| Phase | What | Gate |
|---|---|---|
| **0** | This spec doc | docs-only PR, 0h soak |
| **1** | 5 record-only worker PRs (W1–W5) | One PR each, 24-48h recording-only soak |
| **2** | Brain obs extension: OBS_DIM 58 → 70 | After all 5 workers have ≥48h of live data |
| **3** | Veteran pretraining (parallel track) | Starts after Phase 1 PRs merge |
| **4** | Worker report card (nightly permutation importance) | After Phase 2 ships |

Standing queue (#319 flip, #322 promotion, #320 probes) takes precedence on scheduling conflicts.

---

## 3. Phase 1 — Five Record-Only Workers

### 3.1 Common rules

All five PRs follow the same pattern:

**New module**: `backend/app/core/features/<worker>.py`  
- Pure functions on `pd.DataFrame` bars and scalar inputs.  
- No async, no DB, no side effects. Fully unit-testable without a database.  
- Returns a `dict[str, float | None]` with documented keys.  
- Returns `None` values gracefully on insufficient data (< lookback bars).

**Persistence — two sites, same PR**:

1. **`backend/app/shadow/observation.py`** — extend `obs_components` with a new top-level `"features"` dict. Bump `SCHEMA_VERSION` from `1` to `2`. The loader that consumes v1 rows must zero-fill the missing keys — tested explicitly.  
2. **`backend/app/core/predictor.py`** — in `build_prediction`, compute the same feature values and stash them in `prediction_extras["features"]` as a sub-dict so the `predictions.layer_scores` JSONB carries them for offline analysis.

**Train/serve-skew rule is absolute**: capture and inference ship in the same PR, using the same underlying pure function. If training and serving call different code paths, the PR is not complete.

**No behavior changes**: zero impact on gates, dispatcher, aggregator, final scores, or existing brain behavior. Values are recorded; the brain reads them only after Phase 2 ships.

**Soak**: 24-48h recording-only dev soak before prod-promotion.

---

### 3.2 PR-W1 — `mean_reversion.py`

**Purpose**: capture mean-reversion context so the brain can discount breakout signals that arrive at extended price extremes.

**Features** (3 dims in Phase 2):

| Key | Formula | Range | Notes |
|---|---|---|---|
| `z_ext` | `(close − EMA20) / ATR14` | [-∞, +∞], clamp [-5, 5] | Normalised distance from 20-bar EMA. Positive = above mean. |
| `bollinger_pct_b` | `(close − lower_band) / (upper_band − lower_band)` where bands = EMA20 ± 2×std20 | [0, 1], clamp [-0.5, 1.5] | %B: 0=lower band, 1=upper band, >1=extended above. |
| `dist_7d_high_pct` | `(high_7d − close) / close` | [0, +∞] | Distance below the 7-day rolling high. 0 = at the high. |

**Lookback**: 21 bars minimum (EMA20 needs warmup). Returns all-None dict below that.  
**Tests**: fixture with known EMA/std values asserting exact float output; test None path; test clamp boundaries.

---

### 3.3 PR-W2 — `volatility_state.py`

**Purpose**: tell the brain whether we are in a high- or low-volatility regime at the instrument level (as opposed to the market-wide regime detector which uses BTC daily bars).

**Features** (3 dims in Phase 2):

| Key | Formula | Notes |
|---|---|---|
| `realized_vol_24bar` | `annualized σ(log_returns over last 24 bars)` ×√(8760/1) | Annualized 1h realized vol. |
| `vol_percentile_30d` | rank of `realized_vol_24bar` in a trailing 720-bar (30d×24h) window | [0, 1]. None when < 720 bars available. |
| `atr_expansion_ratio` | `ATR14_now / ATR14_20bars_ago` | > 1 = volatility expanding; < 1 = contracting. None when < 34 bars. |

**Lookback**: 720 bars for `vol_percentile_30d`; 34 bars for `atr_expansion_ratio`; 24 bars for `realized_vol_24bar`.  
**Tests**: assert annualized vol formula on known returns; assert percentile is monotone; assert ATR ratio > 1 on bars with rising ATR.

---

### 3.4 PR-W3 — `btc_spread.py`

**Purpose**: measure whether the alt is running ahead of or lagging BTC, since L1 already computes each asset's trend independently without cross-asset context.

**Features** (1 dim in Phase 2):

| Key | Formula | Notes |
|---|---|---|
| `alt_btc_log_zscore` | z-score of `log(alt_close / btc_close)` over trailing 720 bars | Positive = alt outperforming BTC, negative = underperforming. None when < 720 bars or BTC unavailable. |

**BTC bar access**: `get_cached_market_regime` in `app.core.regime.market_regime` fetches BTC *daily* bars for the regime classifier; that cache is not suitable for 1h ratio computation. Use a new `get_cached_btc_close() -> float | None` thin wrapper in `btc_spread.py` itself that reads from a module-level cache updated by the shadow worker at each candle. Document the staleness bound explicitly: the BTC close is from the most recent BTC kline the shadow worker processed, which may lag by up to one 1h candle. This staleness is immaterial for a 30-day z-score.  

**PRE-READ required**: before implementing, read `backend/app/ws/live_prediction.py` to confirm how BTC bars reach the predictor (or don't). If BTC bars are available at the per-symbol scoring site, use them directly. If not, the module-level cache is the correct pattern.  
**Tests**: assert z-score is 0 when alt_close == btc_close for all bars; assert positive when alt consistently outperforms; assert None on short history.

---

### 3.5 PR-W4 — `flow_features.py`

**Purpose**: expose Binance Futures order-flow context that the brain cannot infer from price action alone.

**Features** (3 dims in Phase 2):

| Key | Binance endpoint | Notes |
|---|---|---|
| `ls_account_ratio` | `GET /futures/data/globalLongShortAccountRatio` | Long/(Long+Short) accounts. [0, 1]. |
| `taker_buy_sell_ratio` | `GET /futures/data/takerbuyvolume` + `GET /futures/data/takersellvolume` | `buy/(buy+sell)`. [0, 1]. |
| `oi_4h_delta` | `GET /futures/data/openInterestHist` | `(oi_now − oi_4h_ago) / oi_4h_ago`. Fractional delta. |

**PRE-READ required**: read `backend/app/data/binance_ticker.py` and `backend/app/core/scoring/intermarket_lookup.py` for the existing rate-limit and caching pattern. Reuse the same HTTP client and caching idiom — do not open new persistent HTTP connections.  
**Graceful failure**: any API error (timeout, 429, geoblock) returns `None` for that key. The shadow worker continues normally.  
**Tests**: mock Binance HTTP responses; assert None on HTTP error; assert ratio math is correct.

---

### 3.6 PR-W5 — `structure_location.py`

**Purpose**: tell the brain where within a market structure the entry lands — top of a leg (bad), middle of retracement (good), at a swing extreme (ambiguous).

**Features** (2 dims in Phase 2):

| Key | Formula | Notes |
|---|---|---|
| `dist_swing_atr` | `min(abs(close − nearest_swing_high), abs(close − nearest_swing_low)) / ATR14` | Distance to the nearest 20-bar swing high or low, in ATR units. |
| `retracement_fraction` | `(close − leg_start) / (leg_end − leg_start)` | Fraction along the most recent impulse leg (swing low → swing high for LONG context, reversed for SHORT). Clamped [0, 1]. None if no clear impulse. |

**PRE-READ required**: read `backend/app/core/patterns/chart/_helpers.py` for `find_swing_highs` / `find_swing_lows`; also read `backend/app/core/scoring/layer4_smc.py` for how it uses those helpers. Reuse the same helpers — do not reimplement swing detection.  
**Lookback**: 21 bars (swing detection needs lookback). Returns all-None below that.  
**Tests**: assert `dist_swing_atr` ≈ 0 when close == swing level; assert `retracement_fraction` = 0.5 at exact midpoint; assert None path.

---

### 3.7 `observation.py` schema v2 shape

After all 5 workers, the stored `obs_components` JSON gains a `"features"` key:

```json
{
    "schema_version": 2,
    "captured_at": "...",
    "symbol": "ETHUSDT",
    "atr": 22.5,
    "last_close": 2245.41,
    "layer_scores": [...],
    "market": {...},
    "position": {...},
    "macro": {...},
    "features": {
        "z_ext": 1.23,
        "bollinger_pct_b": 0.87,
        "dist_7d_high_pct": 0.012,
        "realized_vol_24bar": 0.65,
        "vol_percentile_30d": 0.72,
        "atr_expansion_ratio": 1.18,
        "alt_btc_log_zscore": -0.34,
        "ls_account_ratio": 0.52,
        "taker_buy_sell_ratio": 0.48,
        "oi_4h_delta": 0.031,
        "dist_swing_atr": 0.8,
        "retracement_fraction": 0.45
    }
}
```

Each worker PR adds its own keys to this dict. Workers that fail gracefully write `null` for their keys — the downstream loader zero-fills nulls at observation assembly time.

**Schema version loader rule**: any loader consuming `shadow_observations` rows must check `schema_version`. Version 1 rows are missing `"features"` entirely; treat as all-null for those 12 keys. Tested explicitly in each worker PR and comprehensively in the Phase 2 PR.

---

## 4. Phase 2 — Brain Observation Extension

**Gate**: all 5 workers must have ≥48h of live production data before this PR opens.

### 4.1 Dimension layout

| Segment | Dims | Source |
|---|---|---|
| asset_embedding | 32 | unchanged |
| layer_scores L1..L9 | 9 | unchanged |
| market_state (ATR%, funding, OI_Δ24h, DXY corr, gold corr, regime one-hot[5]) | 10 | unchanged |
| position_state (cur_pos, unrealized_pnl_R, bars_in_position) | 3 | unchanged |
| macro_calendar (hours_to_HI, FOMC, weekend, asia_open) | 4 | unchanged |
| **W1 mean_reversion** (z_ext, bollinger_pct_b, dist_7d_high_pct) | **3** | new |
| **W2 volatility_state** (realized_vol_24bar, vol_percentile_30d, atr_expansion_ratio) | **3** | new |
| **W3 btc_spread** (alt_btc_log_zscore) | **1** | new |
| **W4 flow_features** (ls_account_ratio, taker_buy_sell_ratio, oi_4h_delta) | **3** | new |
| **W5 structure_location** (dist_swing_atr, retracement_fraction) | **2** | new |
| **Total** | **70** | OBS_DIM: 58 → 70 |

### 4.2 Changes

**`backend/app/rl/obs.py`**:
- Add `WorkerFeatures` dataclass with the 12 fields above.
- Add `worker_features: WorkerFeatures` parameter to `build_observation`.
- Append the 12 floats to `parts` in the same fixed order as the table above.
- Update `OBS_DIM = 70`.
- Update the shape invariant check.

**`backend/app/rl/replay_buffer.py`** (training path):
- Load `shadow_observations.obs_components["features"]` and populate `WorkerFeatures`.
- v1 rows (schema_version=1, missing `"features"`) → all-zero `WorkerFeatures`. **Explicitly tested**: a buffer loaded from mixed v1/v2 rows must not raise.

**`backend/app/core/predictor.py`** (inference path):
- Read from `prediction_extras["features"]` to populate `WorkerFeatures` for the brain's `build_observation` call.
- Values were written there by the same Phase 1 pure functions → no train/serve skew.

**`backend/app/ml/inference_path.py`** or wherever the live brain inference runs:
- Pass `WorkerFeatures` through to `build_observation`. None → 0.0 for any null field.

**Checkpoint metadata**: bump `obs_dim` field in `rl_checkpoints` from 58 to 70. The checkpoint loader must reject a 58-dim policy fed a 70-dim observation with a clear error (not a silent shape mismatch). Tested: loading an old checkpoint raises `ValueError` with a diagnostic message.

**Regression tests** (mirror Phase 3A/B/C style):
- Bit-identical observation assembly: training path and inference path called with the same inputs produce `np.array_equal` results.
- Mixed v1/v2 buffer load: no exception; v1 rows produce zeros for worker dims.
- OBS_DIM constant matches `build_observation` output shape.

---

## 5. Phase 3 — Veteran Pretraining (Parallel Track)

Starts after Phase 1 PRs merge. Runs entirely offline. Nothing touches prod until a pretrained challenger wins the nightly Sharpe gate.

### 5a — Historical OHLCV fetch

**Tool**: extend or reuse `tools/ml/fetch_ohlcv.py` lineage.  
**Source**: `data.binance.vision` Binance archive (same source SP-1.1 used).  
**Scope**: 2 years of 1h bars for the current top-30 SPOT universe.  
**Output**: parquet files per symbol in `tools/ml/data/historical_ohlcv/`.  
**Binance archive limits**: respect rate limits; no concurrent > 5 symbols; checkpoint progress so restarts are incremental.

### 5b — Backtest experience generation

**New script**: `tools/ml/backtest_generate_experience.py`

Replays the **full current stack** over the historical bars:
- All 9 layers at their current implementation (imports from `backend/app/core/scoring/`).
- All current traps at current thresholds (imports from `backend/app/core/scoring/traps/`).
- Entry gates at current `MIN_ENTRY_SCORE_LONG=0.36` and `DISABLE_SHORT_SIGNALS` settings.
- ATR-based SL/TP geometry (same `_build_trade_setup` as production — post-fix).
- Simulated closed trades with full v2 observations (all 12 worker features computed from the historical bars using the same Phase 1 pure functions).

**Output**: a pretrain replay buffer, serialized as a separate file (not the `shadow_trades` table). Each entry must carry a `source: "backtest"` flag. **Never mixed into the live shadow replay buffer without explicit source filtering** — the two datasets must never silently blend.

**Explicit non-goal**: no look-ahead in the backtest generator. Each candle only sees bars up to and including itself. This is validated by a unit test that asserts no future bar index is accessed.

### 5c — Pretraining extension to `train_brain.py`

**New flag**: `--pretrain-buffer <path>` on `tools/ml/train_brain.py`.

Behavior when flag is set:
1. Load pretrain buffer (backtest experience).
2. Load live buffer (real `shadow_trades` rows, as currently).
3. Pretrain on backtest buffer for N epochs (configurable, default 10).
4. Fine-tune on live buffer for standard epoch count.
5. Register checkpoint in `rl_checkpoints` as usual.

The champion gate (`evaluate_challenger` → Sharpe comparison) still runs unchanged on the result. No special treatment or bypass for pretrained checkpoints.

### 5d — Safety rule

Pretrain checkpoints are evaluated on the same live holdout window as normal checkpoints. A pretrained model that performs worse than the current champion on live data is rejected identically to a standard challenger.

---

## 6. Phase 4 — Worker Report Card

**Gate**: Phase 2 must be shipped and the brain must have been running at OBS_DIM=70 for ≥7 days (to accumulate enough holdout data with full worker features).

### 6.1 Nightly eval addition

Extend the brain's nightly evaluation (`tools/ml/train_brain.py` eval section) to compute **permutation importance** on the holdout window:

For each obs dimension `i` in the 12 worker dimensions:
1. Shuffle the values of dim `i` across all holdout observations.
2. Re-run the trained policy on the shuffled observations.
3. Record the Sharpe drop vs unshuffled.

Aggregate by worker (W1 = dims 58-60, W2 = 61-63, W3 = 64, W4 = 65-67, W5 = 68-69) as `mean Sharpe drop`.

**Output**: a `"worker_report_card"` key in the eval JSON written to `rl_checkpoints.eval_metadata`.

```json
"worker_report_card": {
    "W1_mean_reversion":   { "sharpe_drop": 0.12, "dims": [58, 59, 60] },
    "W2_volatility_state": { "sharpe_drop": 0.08, "dims": [61, 62, 63] },
    "W3_btc_spread":       { "sharpe_drop": 0.31, "dims": [64] },
    "W4_flow_features":    { "sharpe_drop": -0.02, "dims": [65, 66, 67] },
    "W5_structure":        { "sharpe_drop": 0.19, "dims": [68, 69] }
}
```

Negative `sharpe_drop` = permuting that worker *improved* the policy (the brain was likely being confused by this worker's signal and is better off ignoring it). This is useful signal, not a bug.

### 6.2 `brain-report` ops-debug probe

New probe `brain-report` in `.github/workflows/ops-debug.yml`:

```bash
docker compose exec -T postgres psql -U postgres -d trading_radar -c \
  "SELECT id, captured_at, is_active,
          (eval_metadata->'worker_report_card') AS report_card
   FROM rl_checkpoints
   ORDER BY captured_at DESC LIMIT 3;"
```

Prints the last 3 checkpoint eval JSONs filtered to the report card. The operator can see at a glance which workers the brain is relying on and which are noise.

### 6.3 Authority escalation — explicitly deferred

The following are NOT in this spec and require separate operator approval + measured positive brain Sharpe before a future PR may implement them:
- Wider `brain_adjust` range beyond current (0, 2)
- Brain veto of entry-quality gate denials
- Brain-controlled position sizing beyond the current scoring multiplier
- Brain-controlled SL/TP adjustments

Each of these is a separate PR with its own promotion gate.

---

## 7. Constraints and Invariants

1. **No env changes** in any Phase 1-4 PR.
2. **No behavior changes** to gates, dispatcher, aggregator, or final scores.
3. **Train/serve skew rule**: the same pure function is called at capture and at inference. If a function is renamed, both sites are updated in the same commit.
4. **Graceful failure everywhere**: any worker that can't compute (missing bars, API error, insufficient lookback) returns `None` for its keys. `None` → 0.0 at obs assembly. Never raises.
5. **Schema backward compatibility**: v1 `shadow_observations` rows remain readable after Phase 2; the loader zero-fills missing worker dims.
6. **Separate pretrain buffer**: backtest-generated experience is never silently mixed into the live shadow replay buffer. Source flag is required.
7. **Champion gate is unchanged**: pretrained challengers face the same Sharpe gate as normally trained ones. No force-promote bypass.
8. **CI-not-triggering diagnostic rule (STANDING)**: any "CI not triggering" diagnosis MUST begin with `gh pr view N --json mergeable,mergeStateStatus`. `mergeStateStatus: UNKNOWN` or `DIRTY` means the branch has diverged (can't test-merge). Root cause: a later dev commit touched the same file. Fix: `git merge origin/dev` into the PR branch, resolve conflicts, push. Do not investigate GitHub webhooks, Actions configuration, or anything else before running this command. *Violated twice (PR #322): 2026-07-17 session and 2026-07-19 session.*

---

## 8. Implementation Order

```
Phase 0  (today)           spec doc → docs-only PR → immediate merge
Phase 1  (sequential)      PR-W1 → PR-W2 → PR-W3 → PR-W4 → PR-W5
                           Each 24-48h recording-only soak on dev
                           Batch prod-promotions where sensible (W1+W2 together, etc.)
Phase 3  (parallel)        start after PR-W1 merges, offline only
Phase 2  (after Phase 1)   ≥48h of live worker data required
Phase 4  (after Phase 2)   ≥7d of 70-dim brain data required
```

Phase 3 runs on the developer machine / Colab, not on the Hetzner host, and has no prod footprint until a pretrained challenger wins the gate. It can proceed in parallel with any Phase 1 or 2 work without conflict.
