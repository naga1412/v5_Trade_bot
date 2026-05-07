# SP-4 RL Brain (L10) — Design Spec

**Status:** Draft, awaiting user spec-review pass.
**Date:** 2026-05-07
**Author:** Claude (autonomous mode) + naga1412 (Q1, Q2 decisions)
**Supersedes:** `2026-05-07-SP-4-rl-brain-primer.md` (the primer becomes obsolete once this is approved)

---

## 1. Purpose & non-goals

**Purpose.** Replace the current rule-based equal-weight aggregation of L1–L9 layer scores with a **trainable policy** (L10) that:
- Learns the right *combination weights* of L1–L9 from real paper-trade outcomes
- Adapts per-asset via a learned embedding (one per universe symbol)
- Decides ENTER / HOLD / EXIT at each closed candle, with discretized position sizing
- Retrains nightly on accumulated `shadow_trades` outcomes
- Beats the equal-weight baseline by ≥10% Sharpe on a 6-month backtest window

**Non-goals (explicitly out of scope for SP-4 v1):**
- Continuous online learning (we batch-retrain daily, not per-trade)
- Brain-controlled stop-loss / take-profit (L5 ATR-based exits stay; brain only decides entry/exit/size — see §2.Q3)
- Real-money trading (gated to SP-8; this is paper-trades + simulation only)
- Cross-asset portfolio allocation (each asset's brain decision is independent — portfolio-level Kelly cap stays in L7)
- Multi-timeframe brains (1h only; multi-TF is an SP-4.x follow-up)
- Pre-launch HFT-style features (orderbook L2 imbalance, microsecond signal latency — not relevant for 1h candles)

---

## 2. Locked decisions

| # | Question | Decision | Rationale |
|---|---|---|---|
| Q1 | When to start? | **Build infra now, gate live promotion on SP-1.1 + 100 trades of seasoning** | Lets SP-4 implementation parallelize with the SP-1.1 Colab run; brain v1 ships with usable training data. |
| Q2 | Asset universe | **Top-60 USDT perpetuals (current top-30 + next 30 by 30d-volume)** | Covers ~95% of Binance USDT-paired liquidity; bottom-quartile by liquidity has slippage that breaks paper→live transfer; 60 fits free Colab T4 12-hour disconnect budget. Configurable; expand to ~400 in a follow-up SP after brain quality is proven. |
| Q3 | Brain controls SL/TP? | **No — entry/exit/size only; L5 ATR-based SL/TP stays** | Adds 2 action dimensions and 3-5 weeks of training time without clear research evidence the brain learns better SL/TP than ATR-based exits at the 1h horizon. v2 explores brain-controlled exits. |
| Q4 | Promotion mode | **Telegram-mode for first 90 days post-launch, auto-mode after** | RL agents fail in surprising ways. SP-7's existing `evaluate_challenger` returns the 5% Sharpe improvement bar; for SP-4 the operator gets a Telegram with the eval result and a "approve/reject" inline button before the swap goes live. After 90 days of clean swaps with no surprises, flip to SP-7 auto-mode. |
| Q5 | Cold-start adapter for new asset | **Blend `median(known_embeddings)` → asset's learned embedding linearly over first 100 trades** (`α = min(1.0, n_trades/100)`) | New asset gets the "average" brain immediately; learned per-asset specialization phases in as evidence accumulates. Matches the meta-plan §2.5 cold-start spec. |
| D1 | Observation space | **57 floats:** asset_embedding(32) + L1..L9(9) + market_state(9: ATR%, funding, OI_Δ24h, DXY_corr, gold_corr, regime_one_hot[5]) + position_state(3: cur_pos∈{-1,0,+1}, unrealized_pnl_R, bars_in_pos) + macro_calendar(4: hours_to_next_HI, FOMC_window, weekend, asia_open) | Matches what L1–L9 + SP-3.5 + SP-7 already produce. No new feature engineering. |
| D2 | Per-asset adapter | **Learned 32-dim embedding per asset, fed into 1st MLP layer** (NOT strict LoRA) | True LoRA on a small MLP saves only 5-10x params per asset; simple embedding saves 100x at much lower complexity. 60 assets × 32 floats × 4 bytes = 7.5 KB total. |
| D3 | Action space | **5 discrete actions:** `LONG_FULL, LONG_HALF, FLAT, SHORT_HALF, SHORT_FULL` | PPO converges much faster discrete vs continuous; broker has discrete tick sizes anyway; SP-7's Kelly cap handles fractional sizing within "_FULL". |
| D4 | Reward | **Per-trade risk-adjusted: `(realized_R − 0.5 × σ_20_R)`**, normalized to ~[-3, +3] | Trains on what the exit criterion measures (Sharpe-like). Per-trade (not per-bar) makes credit assignment clean; R-multiples normalize across assets. |
| D5 | Training cadence | **Daily 03:30 UTC retrain on Colab T4 GPU; trailing 365-day window of `shadow_trades` outcomes** | Fits free Colab budget; daily lets brain track regime shifts; 365-day window balances signal vs noise. |
| D6 | Promotion gate | **Reuses SP-7 `evaluate_challenger` with metric flipped from MAE → Sharpe**; same 5% improvement bar; same `?force=true` first-checkpoint bypass | One promotion surface for the whole platform. SP-7 already has the hook for "future RL agent metric." |
| D7 | Safety guards | **Mandatory:** (i) turnover cap ≤12 trades/asset/day during training (penalty), (ii) drawdown circuit-breaker auto-pauses via SP-PAUSE on >15% 7d portfolio DD, (iii) exponential action smoothing α=0.3 over 3 ticks in production, (iv) no leverage (size ≤ allocated capital) | RL agents reward-hack. These four guards block the four highest-blast-radius failure modes. |
| D8 | Phasing | **5 phases A → E** (replay buffer / PPO trainer / inference integration / promotion gate / Telegram mode) — see §11 | Risk-first: Phase A (offline data plumbing) is tested in isolation before any GPU training runs. |

---

## 3. Architecture

### 3.1 Module layout

```
backend/app/rl/
├── __init__.py
├── obs.py              # build_observation(asset, layer_scores, market, position, macro) -> np.ndarray(57,)
├── reward.py           # compute_reward(trade) -> float; uses trailing 20-trade vol per asset
├── replay_buffer.py    # ReplayBuffer: load_from_shadow_trades(window=365d) -> list[Transition]
├── policy.py           # PolicyNetwork: nn.Module mapping obs(57,) -> action logits(5,) + value
├── adapter.py          # AssetEmbedding: nn.Embedding(N_assets, 32); cold-start blend logic
├── ppo.py              # PPO trainer: rollout → advantages → policy loss + value loss + entropy
├── inference.py        # decide_action(obs, smoothing_state) -> Action; loaded checkpoint state
├── safety.py           # turnover_cap, drawdown_breaker, action_smoothing, kelly_cap
└── checkpoints.py      # mirror of app/ml/checkpoints.py but for L10 (rl_checkpoints table)

backend/migrations/versions/
└── 2026_05_07_0015_rl_checkpoints_and_brain_decisions.py  # new tables

tools/ml/                          # extend existing
├── train_brain.py        # PPO training script (mirrors tools/ml/train.py)
├── colab/train_brain.ipynb        # Colab T4 wrapper
└── register_brain.py     # registers checkpoint in rl_checkpoints

backend/app/predictor.py           # MODIFIED: brain inference replaces equal-weight in §6.4
backend/app/api/routes/admin_rl.py # NEW: /api/v1/admin/rl-checkpoints (mirrors admin_ml.py)
backend/app/api/routes/tab1.py     # MODIFIED: surface brain action + confidence in /predict response
```

### 3.2 Two new tables (migration 0015)

```sql
-- The trained PPO policy + per-asset adapter weights as a single .pt file.
CREATE TABLE rl_checkpoints (
  id              SERIAL PRIMARY KEY,
  model_name      TEXT NOT NULL,        -- 'ppo_policy_v1'
  version         TEXT NOT NULL,        -- 'v1-20260514-033000'
  checkpoint_uri  TEXT NOT NULL,        -- 'file:///app/data/rl-cache/ppo_policy_v1-20260514-033000.pt'
  sha256          TEXT NOT NULL,
  trained_at      TIMESTAMPTZ NOT NULL,
  train_data_window TEXT NOT NULL,
  eval_results    JSONB NOT NULL,        -- {sharpe, sortino, max_dd, n_trades, vs_baseline_pct, ...}
  is_active       BOOLEAN NOT NULL DEFAULT FALSE,
  activated_at    TIMESTAMPTZ,
  deactivated_at  TIMESTAMPTZ,
  notes           TEXT,
  UNIQUE (model_name, version)
);
-- Single-active-per-model invariant via partial unique index.
CREATE UNIQUE INDEX rl_checkpoints_one_active
  ON rl_checkpoints (model_name) WHERE is_active = TRUE;

-- Append-only log of every brain decision in production. Used for diagnosing
-- why a given trade fired (or didn't) and as the source-of-truth join target
-- when the next training run reconstructs (obs, action, reward) tuples.
CREATE TABLE brain_decisions (
  id            BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL,
  symbol        TEXT NOT NULL,
  checkpoint_id INTEGER NOT NULL REFERENCES rl_checkpoints(id),
  observation   JSONB NOT NULL,         -- the 57-float vector serialized as named keys
  action        TEXT NOT NULL,          -- 'LONG_FULL'|'LONG_HALF'|'FLAT'|'SHORT_HALF'|'SHORT_FULL'
  action_logits JSONB NOT NULL,         -- raw logits for replay
  value_estimate DOUBLE PRECISION,      -- critic's V(s) prediction
  smoothed_action TEXT NOT NULL,        -- after the α=0.3 smoothing in §safety
  prev_hash     TEXT NOT NULL,
  row_hash      TEXT NOT NULL
);
CREATE INDEX brain_decisions_symbol_ts ON brain_decisions (symbol, ts DESC);
```

`brain_decisions` carries the same hash-chain audit columns as `predictions` and `shadow_trades` (per meta-plan §6 audit policy). The hash chain is verified by the existing SP-7 `verify_chain` worker — we just register the new table.

### 3.3 Policy network

```python
class PolicyNetwork(nn.Module):
    def __init__(self, n_assets: int, obs_dim: int = 57, n_actions: int = 5):
        super().__init__()
        self.asset_emb = nn.Embedding(n_assets, 32)         # learned per-asset
        self.shared = nn.Sequential(
            nn.Linear(obs_dim, 128), nn.ReLU(),
            nn.Linear(128, 128), nn.ReLU(),
            nn.Dropout(0.1),
        )
        self.policy_head = nn.Linear(128, n_actions)        # action logits
        self.value_head = nn.Linear(128, 1)                  # critic V(s)

    def forward(self, asset_id: torch.Tensor, market_obs: torch.Tensor):
        emb = self.asset_emb(asset_id)                       # (B, 32)
        x = torch.cat([emb, market_obs], dim=-1)             # (B, 57)
        h = self.shared(x)
        return self.policy_head(h), self.value_head(h).squeeze(-1)
```

Param count: ~50K total. Trains in 30-60 min on Colab T4 for 60 assets × 365 days of trades.

### 3.4 Inference path in production

```
closed candle on BTC/USDT 1h
  → app.predictor.build_prediction(...)              [existing]
    → L1..L9 layer scores                            [existing SP-2/3/3.5/5/9]
    → L8 ghost candle                                [SP-1.1, NULL until activated]
    → market_state, position_state, macro_calendar   [existing aggregators]
    → app.rl.inference.decide_action(...)            [NEW]
      → load active rl_checkpoint                    [from app.rl.checkpoints]
      → build_observation(...)                       [from app.rl.obs]
      → policy_net.forward(...) → logits, value
      → sample action (argmax in production, sampled during training)
      → smooth via app.rl.safety.smooth_action(...)
      → INSERT brain_decisions row (hash-chained)
      → return Action
    → if Action != current_position: emit shadow_trade open/close
  → persist_prediction(...)                          [existing]
```

Same lifespan-loaded module-state pattern as `app.ml.checkpoints` — single in-process reference, no DB round-trip on every tick.

---

## 4. Reward function

```python
def compute_reward(trade: ShadowTrade, recent_trades_on_asset: list[ShadowTrade]) -> float:
    # R-multiple = pnl / (initial_risk_R = ATR-based stop distance)
    realized_R = trade.pnl_quote / trade.initial_risk_quote

    # Trailing 20-trade std-dev of R for this asset (asymmetric — early trades
    # use prior of σ=1.0 until 5+ trades available)
    if len(recent_trades_on_asset) >= 5:
        sigma = float(np.std([t.pnl_quote / t.initial_risk_quote for t in recent_trades_on_asset[-20:]]))
    else:
        sigma = 1.0

    # Risk-aversion-weighted return; clipped to ±3 to keep PPO advantages stable
    raw = realized_R - 0.5 * sigma
    return float(np.clip(raw, -3.0, +3.0))
```

The 0.5 risk-aversion weight is tunable; we lock it for v1 and revisit if the brain over-trades during early rollouts.

Episode boundary = trade close. The brain sees the reward only when the trade exits (via L5 SL/TP or timeout), not on every bar — this matches how PPO is normally applied to game playing where a "move" is the unit of decision.

---

## 5. Training pipeline

### 5.1 Replay buffer construction

`tools/ml/train_brain.py --window 365d` does:

1. `SELECT * FROM shadow_trades WHERE closed_at >= now() - 365d ORDER BY symbol, opened_at`
2. For each trade, reconstruct its observation at open-time by joining `predictions`, `intermarket_snapshots`, news/sentiment, regime markers as-of `opened_at` (point-in-time correctness — same approach SP-3 used for `universe_history`).
3. Compute the reward via §4.
4. Bundle into `Transition(obs, action_taken, reward, next_obs, done)` — `done=True` for trade-close transitions. Cross-trade transitions on the same asset chain together as `done=False`.

For the **first** training run (no real RL trades yet), the "action_taken" is whatever the equal-weight aggregation produced, i.e., the offline replay treats the existing rule-based decisions as the behavioral policy. PPO's importance-sampling correction handles this off-policy data correctly for the first few epochs; after the brain is live, on-policy data accumulates and dominates.

### 5.2 PPO training loop

Standard PPO-clip per Schulman et al 2017:
- Rollout collected from replay buffer (offline) + (later) simulator rollouts
- Clipped surrogate objective with ε=0.2
- Value-function loss with c1=0.5
- Entropy bonus c2=0.01 (encourages exploration during training)
- 4 epochs per batch
- Adam lr=3e-4, batch=256

### 5.3 Eval harness

After training, the candidate checkpoint runs through a 6-month backtest (2024-04 → 2024-09 BTC/USDT 1h + 2024 backtest data for the rest of the top-60). Outputs:

```python
{
  "sharpe":         float,   # per-asset, then portfolio-weighted
  "sortino":        float,
  "max_drawdown":   float,
  "n_trades":       int,
  "win_rate":       float,
  "profit_factor":  float,
  "vs_baseline_sharpe_pct": float,  # = (challenger_sharpe / baseline_sharpe - 1) * 100
  "per_asset":      dict,     # same metrics broken out per symbol
}
```

**SP-4 ship gate:** `vs_baseline_sharpe_pct >= 10` AND `max_drawdown <= 25%`. Below either threshold → checkpoint saved with eval JSON for diagnosis but NOT promoted.

### 5.4 Telegram-mode promotion (Q4)

When training completes:
1. Compute `evaluate_challenger` against the active champion (SP-7's existing function, metric=Sharpe).
2. If challenger wins by ≥5% Sharpe → send Telegram message:
   ```
   🧠 RL brain candidate v1-20260514-033000 ready
   Sharpe: 1.87 (champion: 1.62, +15.4%)
   Max DD: 11.2%   Trades: 412   Win-rate: 47%

   [Approve & Activate]   [Reject]   [View Details]
   ```
3. Operator clicks button → backend's `/api/v1/admin/rl-checkpoints/{id}` PATCH endpoint flips active row.
4. After 90 days of clean Telegram-approved swaps with no manual rejects, flip the env var `RL_PROMOTION_MODE=auto` to enable SP-7 auto-promotion.

The Telegram bot integration reuses the existing token + chat ID from SP-PAUSE alerts.

---

## 6. Inference integration

### 6.1 `predictor.py` modification

The existing `app/predictor.py:build_prediction` ends with an equal-weight aggregation:

```python
# OLD (current)
final_score = sum(layer.score * (1/9) for layer in (l1, l2, ..., l9))
direction = LONG if final_score > +0.5 else SHORT if final_score < -0.5 else FLAT
size = kelly_cap(final_score)
```

After SP-4 lands:

```python
# NEW (SP-4)
brain_action, brain_value = await app.rl.inference.decide_action(
    asset_id=asset.id,
    layer_scores=[l1.score, ..., l9.score],
    market=market_features,
    position=current_position,
    macro=macro_features,
)
direction, size = action_to_direction_size(brain_action)  # 5-action map
```

If no active rl_checkpoint exists, falls back to the equal-weight path (graceful degradation, same pattern as ghost candles in SP-1).

### 6.2 Brain → `shadow_trades` wiring

Each non-FLAT brain action against the current position's direction triggers a `shadow_trade` open/close in the existing `shadow.worker` flow. The brain's `action_logits` + `value_estimate` are persisted to `brain_decisions` with the hash-chain so we can replay any past decision verbatim.

---

## 7. Safety system

### 7.1 Turnover cap (training-time only)

During PPO training, episodes that exceed 12 trades per asset per day get a `-1.0` reward penalty added to every transition in that episode. This nudges the policy away from learning a hyperactive trading strategy that would amplify slippage in production.

### 7.2 Drawdown circuit-breaker (production)

```python
# Background worker, ticks every 5 min
async def drawdown_breaker_worker():
    portfolio_dd = await compute_7d_drawdown(now)
    if portfolio_dd > 0.15:
        await pause_state.set_paused(
            paused=True,
            by_email="system",
            reason=f"rl_drawdown_breaker: 7d DD {portfolio_dd:.1%}",
            session=session,
        )
        await send_telegram(f"⚠️ RL brain auto-paused: 7d portfolio DD {portfolio_dd:.1%}")
```

Manual operator action required to resume (via Settings → System → Resume). This is intentional — a 15% DD week needs eyes on it.

### 7.3 Action smoothing (production)

Exponential smoothing α=0.3 over the last 3 ticks. Smooths "flicker" between borderline actions when the brain's logits are close.

```python
# In app.rl.inference
def smooth_action(raw_action: int, history: deque[int]) -> int:
    history.append(raw_action)
    history = history[-3:]
    # Pick the most-frequent action in the last 3, ties broken toward FLAT
    return mode(history, default=FLAT)
```

### 7.4 No leverage (production)

`size ≤ 100% of allocated capital` enforced in `app.rl.safety.kelly_cap`. SP-8 (autonomous trading with real money) is the place to revisit leverage; v1 stays conservative.

---

## 8. Cross-cutting policy compliance

Per meta-plan §5 / §6:

- **Look-ahead bias.** Replay buffer construction uses point-in-time joins on `predictions` / `intermarket_snapshots` / `news` / `regime_markers` as-of `opened_at`. No future data leaks into the observation.
- **Survivorship bias.** Universe is `universe_history` (point-in-time top-60), not "today's top-60." A coin that delisted in 2023 is in the training data for the period it was active.
- **Training-serving skew.** The exact same `build_observation()` function runs at training-time (replay buffer) and inference-time (production). No separate feature pipelines.
- **Catastrophic forgetting.** Daily retrain uses 365-day window — old data stays in the buffer. Nightly fine-tunes from the previous day's checkpoint, not from scratch (warm-start preserves general knowledge).
- **Audit integrity.** `brain_decisions` is hash-chained alongside `predictions` and `shadow_trades`. SP-7 `verify_chain` extended to cover the new table.
- **Free-tier rate-limit accounting.** Training runs offline on Colab; production inference is local CPU on Hetzner (no external API calls per tick — all features come from the existing data adapter cache).

---

## 9. Acceptance criteria

- [ ] All migrations apply cleanly forward + reverse on dev DB
- [ ] `app.rl.replay_buffer.load_from_shadow_trades(window=365d)` returns ≥1 transition per existing closed shadow trade in the dev DB; raises if `shadow_trades` table is empty
- [ ] `app.rl.obs.build_observation` produces a 57-float vector with stable shape + dtype across 100+ random fixtures
- [ ] `app.rl.reward.compute_reward` matches by-hand calc on 20 fixtures (positive R, negative R, zero-trades-history asset cold-start, post-20-trade variance regime)
- [ ] `tools/ml/train_brain.py` completes a 1-epoch run on synthetic data without crashing (smoke test in tools/ml/tests/)
- [ ] First Colab training run produces a checkpoint with `vs_baseline_sharpe_pct >= 10` on the 6-month BTC/USDT 2024 backtest
- [ ] Same checkpoint passes the per-asset breakdown gate: ≥80% of top-60 assets have positive Sharpe individually (not just portfolio-level)
- [ ] `app.rl.inference.decide_action` integrates with `app.predictor.build_prediction` and degrades gracefully to equal-weight when no active checkpoint exists
- [ ] `brain_decisions` rows are hash-chained and pass `verify_chain`
- [ ] Drawdown circuit-breaker fires correctly on synthetic DD scenario; auto-pauses via SP-PAUSE
- [ ] Telegram approval flow round-trips: training → message → button click → activation → backend reload
- [ ] After 100 trades with brain active, `vs_baseline_sharpe_pct` measured on rolling 100-trade window matches the backtest within ±20% (sanity check that the eval harness isn't cheating)

---

## 10. Risk + fallback plan

**Risk: Brain converges to "always FLAT" on the first run.**
Cause: with sparse positive rewards in offline replay, the policy may collapse to no-trading. Mitigation: entropy bonus c2 starts at 0.05 (5x normal) for first 10 epochs, then anneals to 0.01.

**Risk: Brain over-trades during paper-mode.**
Cause: free-tier feedback loop hasn't seen real slippage costs yet. Mitigation: turnover cap penalty already in §7.1; if observed in early rollouts, raise the penalty from -1.0 to -2.0.

**Risk: Cold-start asset (newly added to top-60) has wild brain decisions.**
Cause: median embedding may not be representative. Mitigation: §Q5 blending; new asset is shadow-mode-only (does not enter `shadow_trades`) for first 100 candles after listing.

**Risk: Telegram bot down → can't approve checkpoint.**
Mitigation: 7-day fallback — if no Telegram response within 7 days, the candidate auto-rejects (logged as `rl_promotion_timeout`) and the operator gets a daily reminder until they trigger a fresh run.

**Risk: Drawdown breaker fires repeatedly (false-positive on slow-moving DDs).**
Mitigation: SP-PAUSE reason is logged with the DD value; if breaker fires >2x in 30 days the operator should manually retune the 15% threshold.

**Risk: Brain checkpoint loads on production but inferences are NaN/inf.**
Mitigation: `decide_action` wraps in try/except, logs error, falls back to equal-weight for that tick. After 5 consecutive NaN ticks on any asset, auto-pauses via SP-PAUSE.

**Fallback if SP-4 v1 fails to beat baseline:** Keep equal-weight as production. Iterate on observation features (try per-asset LoRA, longer obs window, multi-TF features) in SP-4.1.

---

## 11. Phasing

| Phase | Deliverable | Acceptance gate | Wall time | Subagents |
|-------|-------------|-----------------|-----------|-----------|
| **A** | Data plumbing offline: `obs.py`, `reward.py`, `replay_buffer.py`, migration 0015, fixtures | Replay buffer materializes 365d of past trades; obs/reward unit tests green | ~1 week | 1 |
| **B** | PPO trainer + per-asset embedding + Colab notebook + `train_brain.py` | Trains 1-epoch on synthetic data without crash; first real Colab run produces a checkpoint that beats random-action baseline | ~2 weeks | 1 |
| **C** | Inference integration in `predictor.py`; `app.rl.inference`; `brain_decisions` write path; safety guards | Brain action overrides equal-weight in dev backtest; drawdown breaker + smoothing + turnover cap unit-tested | ~1 week | 1 |
| **D** | Admin endpoints `/api/v1/admin/rl-checkpoints` + `register_brain.py` + champion-challenger gate extension | Register/list/activate flow works end-to-end; existing SP-7 evaluator passes new metric=Sharpe path | ~1 week | 1 |
| **E** | Telegram approval bot integration + 90-day mode → auto-mode flip switch | Round-trip: train → message → button → activate → restart → backend loads new checkpoint | ~1 week | 1 |

**Total estimate: ~6 weeks.** Matches meta-plan §3.2 SP-4 budget.

---

## 12. Reference

- SP-4 primer: `2026-05-07-SP-4-rl-brain-primer.md` (this spec supersedes it)
- Meta-plan: `2026-05-01-trading-radar-meta-plan-design.md` §2.5 (brain architecture), §3.2 (SP-4 exit criteria), §6 (cross-cutting policies)
- SP-1 ML infra: `2026-05-05-SP-1-ml-data-ghost-candles-design.md` — patterns we mirror for L8 ↔ L10 parallels
- SP-7 ops: `2026-05-05-SP-7-ops-hardening-design.md` — champion-challenger gate that L10 extends
- SP-PAUSE: `2026-05-06-SP-PAUSE-master-pause-design.md` — drawdown circuit-breaker uses this surface
- PPO paper: Schulman et al 2017 "Proximal Policy Optimization Algorithms"
