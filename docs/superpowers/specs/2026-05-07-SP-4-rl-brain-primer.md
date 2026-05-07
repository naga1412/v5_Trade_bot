# SP-4 RL Brain (L10) — Decision Primer

**Status:** PRIMER ONLY — not a final design. This doc surfaces every open
design decision for SP-4 with my recommended answer + rationale, so the
brainstorming session can move fast (read this, approve or redirect each
decision, then the spec writes itself).

**Read first:**
- `2026-05-01-trading-radar-meta-plan-design.md` §2.5 (brain architecture
  decision: global PPO + per-asset LoRA adapter), §3.2 (SP-4 exit criteria),
  §6 (cross-cutting policies), §13 row 5 ("LoRA on which layer of the
  global brain?" — open from day one).
- `MASTER_PLAN.md` §10.X (RL/L10 module — coarse vision, supersedes by
  the meta-plan).

**SP-4 exit criteria (from meta-plan §3.2):**
> Global PPO brain + per-asset LoRA adapter trains nightly, beats
> equal-weight baseline by ≥10% Sharpe on backtest

---

## Open decisions

### D1. State / observation space

**Question:** What goes into the observation vector the PPO brain sees on
each prediction tick?

**Recommended:**
```
obs = [
    asset_embedding (32-dim, learned per-asset, comes from per-asset
                     LoRA adapter — see D2),
    layer_scores L1..L9 (9 floats in [-1, +1]),
    market_state_features (9 floats: ATR_pct, funding_rate, OI_delta_24h,
                           DXY_corr_30d, gold_corr_30d, regime_one_hot[5]),
    position_state (3 floats: cur_position {-1, 0, +1}, unrealized_pnl_R,
                    bars_in_position),
    macro_calendar (4 floats: hours_to_next_high_impact, FOMC_window_flag,
                    weekend_flag, asia_open_flag),
]
```
Total: ~57 floats. Small enough that PPO converges fast, expressive enough
to capture the layered output + market regime.

**Why:** The 9 layer scores are the actual L1-L9 outputs already computed
by SP-5; the brain's job is to learn the right combination weights. The
`asset_embedding` lets the global brain specialize per asset without
needing 200 separate brains. Position state + macro calendar are needed
because the brain decides whether to ENTER, HOLD, or EXIT — context
beyond just "what does the chart say right now".

---

### D2. LoRA adapter placement

**Question:** LoRA on which layer of the global brain?

**Recommended:** Single learned per-asset embedding fed into the FIRST
hidden layer of the policy MLP. Not LoRA in the strict sense (no rank
decomposition over weight matrices) — just a per-asset 32-dim vector.

```
policy(asset_id, market_obs) =
    MLP([asset_embedding[asset_id], market_obs])
    where asset_embedding ∈ R^32, learned alongside policy weights
```

**Why:** Strict LoRA (low-rank deltas to attention/MLP weights) is a
transformer technique. Our policy is a 3-layer MLP (~100k params). True
LoRA on it would save 5-10x params per asset; an embedding saves 100x
(~32 params per asset vs ~3k LoRA matrices) and is much simpler to train.
Storage: 200 assets * 32 floats * 4 bytes = 25 KB total for all asset
adapters, vs ~10 MB with strict LoRA.

**Alternative if rejected:** Use proper LoRA on the first dense layer
(rank=4 or 8) — gives more per-asset capacity at 30x storage cost.

---

### D3. Action space

**Question:** Discrete actions or continuous position sizing?

**Recommended:** Discrete, 5 actions: `{LONG_FULL, LONG_HALF, FLAT,
SHORT_HALF, SHORT_FULL}`. Position size is fraction of paper-trade
account allocated to this asset (capped at the per-asset max from
SP-3.5).

**Why:** PPO converges much faster with discrete actions and the platform
already has fixed position-sizing rules from SP-7 (Kelly with cap). Going
continuous (a number in [-1, +1]) would require a separate critic for
size — extra training complexity for little real-world gain since the
broker step has discrete tick sizes anyway. 5 buckets is enough granularity
for the swing-trading horizon (1h candles).

---

### D4. Reward function

**Question:** What does the brain optimize for?

**Recommended:** Risk-adjusted P&L — Sharpe on the trailing 30-day
rolling window, computed per asset, sampled at trade close (not per bar).

```
reward = (realized_pnl_R - 0.5 * realized_volatility_R) / 1.0
                                                          ↑ scale to ~[-3, +3]
```

Where:
- `realized_pnl_R` = trade P&L in R-multiples (wins of 2R, losses of 1R, etc.)
- `realized_volatility_R` = std-dev of last 20 trades on this asset
- 0.5 is the risk-aversion weight (tunable)

**Why:** The exit criterion is "beat equal-weight baseline by ≥10%
Sharpe on backtest" — train on what you measure. Per-trade reward
(not per-bar) makes credit assignment clean — the trade is the natural
episode boundary. R-multiples normalize across assets (BTC and SOL
trades contribute equally to the brain's learning regardless of
absolute price).

**Reject:** raw P&L (encourages overtrading), win-rate (misses the
asymmetric long/short threshold work from SP-5), Sortino (similar to
Sharpe but adds complexity without clear win on this horizon).

---

### D5. Training cadence + data source

**Question:** How often does the brain retrain, and on what data?

**Recommended:**
- **Daily, at 03:30 UTC** (after the 03:00 backup cron, after Asia close
  and before EU open — quietest market window).
- Trains on the trailing **365 days of paper-trade outcomes** stored in
  `shadow_trades`, joined with `predictions` (for the layer-score
  context that was live when the trade was opened) and `intermarket_snapshots`
  (for funding/OI context).
- Each training run = ~3-5 hours on Colab T4 GPU. The Hetzner box is
  CPU-only (4 vCPU AMD); CPU training would take 30-60 hours per run,
  so this also goes to Colab like SP-1.1.

**Why:** Daily retrain keeps the brain adapted to recent regime shifts
without overfitting to noise. 365-day window is the sweet spot — 90
days is too noisy on 1h candles, 1000+ days exceeds the platform's own
data history. Off-loading to Colab keeps the production server lean.

**Alternative if rejected:** Weekly (Sundays after backup) — trades
some adaptation speed for half the compute cost.

---

### D6. Champion-challenger integration with SP-7

**Question:** Does SP-4 use the same champion-challenger gate as SP-1.1?

**Recommended:** Yes, with two changes:
1. The metric flipped from MAE (predictor accuracy) to **Sharpe ratio
   over the same backtest window** (RL agent quality).
2. The 5%-improvement bar stays — challenger Sharpe > champion Sharpe
   * 1.05.

**Why:** Same `?force=true` bypass for the very first checkpoint (no
champion to beat). Same admin route (`PATCH /api/v1/admin/ml-checkpoints/
{id}`) — extend the existing surface, don't add a parallel one. The
SP-7 evaluator already has the hook for "future RL agent metric"; SP-4
just plugs into it.

---

### D7. Reward-hacking + safety guards

**Question:** What stops the brain from learning a degenerate policy?

**Recommended (mandatory):**
- **Hard cap on position turnover:** ≤ 12 trades per asset per day
  (training penalty if exceeded).
- **Drawdown circuit-breaker in production:** if the brain's signal
  drives a portfolio drawdown >15% in a 7-day window, auto-deactivate
  via the SP-PAUSE master pause (logs `system_paused: rl_drawdown` to
  `auth_violations`).
- **Action smoothing:** the brain's discrete action is exponentially
  smoothed in production with α=0.3 over 3 ticks. Stops "flicker
  trading" caused by borderline observations.
- **No leverage:** position size is always ≤ 100% of allocated capital
  in v1. Leverage is SP-8's problem (and is explicitly gated on a
  hardware confirm).

---

### D8. Phasing + acceptance gate

**Recommended phase split:**

| Phase | Deliverable | Acceptance gate |
|-------|-------------|-----------------|
| A | Replay buffer + observation builder + reward computer (offline only, no PPO yet) | Buffer materializes 365 days of past trades into `(obs, action, reward)` tuples without crashing on the existing `shadow_trades` data |
| B | PPO trainer + LoRA adapter + Colab notebook | First training run produces a checkpoint that beats random-action baseline (any positive Sharpe) on the backtest |
| C | Inference integration in `app/predictor.py` | Brain's action overrides equal-weight scoring for paper trades; SP-PAUSE drawdown guard wired |
| D | Champion-challenger gate + admin endpoints | New checkpoint must beat champion Sharpe by ≥5% to activate; ≥10% over equal-weight baseline to ship the SP-4 tag |
| E | Telegram alerts for activations + drawdown trips | Operator gets push on every model swap and circuit-breaker fire |

**SP-4 ship criterion:** Phase E complete + first activated checkpoint
beats equal-weight by ≥10% Sharpe on a 6-month backtest window
(2024-04 → 2024-09 — different from SP-1's 5 regime windows; this is
overall portfolio Sharpe, not per-window MAE).

---

## Open questions for the brainstorm

These don't have a clear default — they need a decision from you:

| # | Question | Why it matters |
|---|---|---|
| Q1 | Start SP-4 immediately, or wait for SP-1.1 to ship a real activated checkpoint and run for 1-2 weeks first? | If we start SP-4 now, the brain trains on stale predictor outputs (predictor still on baseline). 1-2 weeks of real ghost-candle data after SP-1.1 activation gives the brain better ground truth. Delay = 2-3 weeks; benefit = much faster brain convergence. |
| Q2 | Multi-asset training scope — top-30 universe (matches `shadow_trades`), or BTC-only first? | BTC-only is faster to ship (1 week → working backtest). Top-30 is the production target but adds 4-5 weeks of cross-asset debugging. Risk-first answer: BTC-only, then expand. |
| Q3 | Should the brain be allowed to set the SL/TP in addition to the entry/exit decision? | Currently SL/TP comes from L5 (ATR-based). Letting the brain override it adds 2 more action dimensions and significant training time. v1 keeps L5's SL/TP, v2 explores brain-controlled exits. |
| Q4 | Telegram-mode (alert + manual approve) vs auto-mode for SP-4 promotions? | SP-7 promotion gate currently auto-promotes if metrics pass. SP-4 is a bigger blast radius — RL agents can fail in surprising ways. Recommendation: Telegram-mode for the first 90 days post-launch, then auto-mode if no surprises. |
| Q5 | Where does the LoRA adapter for a NEW asset come from? Cold-start = 100 trades per meta-plan §2.5. What does it use until then? | Recommendation: cold-start uses the median embedding of the trained assets, blended toward the per-asset learned embedding as `min(1.0, n_trades / 100)`. Mathematically: `effective_emb = (1 - α) * median_emb + α * adapter_emb` where α ramps from 0 to 1 over 100 trades. |

---

## What needs to happen before SP-4 brainstorm

1. **SP-1.1 activated checkpoint live** (PR #33 → human runs Colab → register → activate). Without this, SP-4 has nothing to learn from.
2. **1-2 weeks of paper-trade data with real ghost candles flowing.** Otherwise the L8 input column to the brain is constantly NULL and the trained policy generalizes badly.
3. **Decisions from the table above** (Q1-Q5).

When all 3 are satisfied, the brainstorming → spec → plan → implementation cycle for SP-4 takes ~6 weeks per the meta-plan budget.

---

## Why this primer exists, and what it isn't

This is a **starter document for the SP-4 brainstorm**, written autonomously while you were AFK so the dialog can move 5x faster when you pick it up. It is NOT:

- A finished design (each "Recommended" above is a proposal, not a commitment)
- A plan (the writing-plans skill comes after design approval)
- An implementation gate (no code shipped against any of these decisions)

When you're ready to start SP-4 — read this, mark each decision approved/redirected/deferred, and the formal spec writes itself in a follow-up brainstorm session.
