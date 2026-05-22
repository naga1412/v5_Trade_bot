# PR-BRAIN-BACKTEST-PHASEB5 — wire real Sharpe/max_dd/win_rate into brain eval JSONs

**Status:** spec drafted 2026-05-22.
**Branch:** `feat/pr-brain-backtest-phaseb5` (off `origin/dev` post-PR-WARMSTART-FIX).
**Class:** **infrastructure feature.** Training-only path. NO impact on live trading: brain remains dormant.

---

## Problem (and what the audit revealed)

Two parallel issues:

1. **Eval JSON is a placeholder.** `build_eval_doc` in [tools/ml/train_brain.py:142-150](tools/ml/train_brain.py#L142) writes:
   ```python
   "backtest_results": {
       "_status": "deferred_to_phase_b5",
       "_note": "6-month per-asset Sharpe... deferred",
   }
   ```
   `register_brain.py:78` passes this dict into `rl_checkpoints.eval_results` (JSONB). Result: every checkpoint has eval_results = `{"_status": "deferred_to_phase_b5"}`. No Sharpe, no max_dd.

2. **The champion-challenger Sharpe gate has a ghost import.** [champion_challenger.py:186-208](backend/app/ml/champion_challenger.py#L186) has `_evaluate_sharpe`, gated behind `# pragma: no cover — TDD seam`. Its real implementation imports `from tools.backtest import run_backtest`. **That module does NOT exist** — verified by `python -c "from tools.backtest import run_backtest"` → `ModuleNotFoundError`.

   So any `?force=false` activation in production crashes. All historical activations have implicitly required `?force=true` to bypass this dead path. The "5% Sharpe improvement bar" has never been enforced in real life.

This PR fixes both with one design.

## MVP design

### Approach: backtest at training time, read at activation time

Instead of building a separate runtime backtest harness (which would need OHLCV replay, policy reload, simulation infrastructure — large surface area), we compute the backtest **as part of training** when we already have:
- The trained policy in memory
- The full replay buffer of transitions
- The asset_table state

We split the buffer **chronologically** (last 20% as held-out), run the policy on the held-out window, and emit `sharpe`, `max_dd`, `win_rate`, `total_trades`. These get baked into the eval JSON.

Then `_evaluate_sharpe` is changed to **read from `rl_checkpoints.eval_results->>'sharpe'`** in Postgres (the JSONB column where register_brain.py persisted the eval) instead of calling the dead `tools.backtest`.

### Why this MVP over walk-forward / OOS replay

- **Free**: the buffer and policy are already in memory at training time. No additional data pipeline, no OHLCV reload, no replay engine.
- **Honest**: the held-out window is chronologically AFTER training data, so it's an out-of-sample proxy. Better than the "in-sample" alternative.
- **Implementable in ~150 LoC**: a single new module + 1 integration point.
- **Honest limitations** (documented inline + in spec follow-ups):
  - The brain reward already encodes `clip(R - 0.5 σ_R, ±3)` — Sharpe of reward is a scaled proxy of PnL Sharpe, not raw $ returns.
  - With ~292 trades and 20% holdout = ~58 holdout trades, Sharpe noise is large. Not for fine-grained promotion bars; suffices for the bootstrap "any model beats no model" + crude regression detection.
  - The policy makes argmax decisions on holdout transitions whose `proposed_direction` we infer from the trade's actual recorded action. A real deployment scenario differs (the predictor would generate signals + the brain modulates them). MVP proxy is good enough to unblock the gate.

### Synthetic reward formula

For each held-out transition `t`:
1. Run `policy.act(obs_t, deterministic=True)` → action_idx
2. Map to action string via `ALL_ACTIONS[action_idx]`
3. Infer `proposed_direction` from `t.action` (the rule-based system's choice): `"LONG"` if starts with `LONG_`, `"SHORT"` if starts with `SHORT_`, else `"NEUTRAL"`
4. `brain_adjust = action_to_brain_adjust(action_str, proposed_direction=...)` ∈ {0.5, 0.6, 1.0, 1.2, 1.4}
5. `synthetic_reward = t.reward * brain_adjust`

Then:
- `sharpe = mean(synthetic_rewards) / std(synthetic_rewards) * sqrt(N)` (annualized — N reflects the holdout size; not literal-daily, but a relative score)
- `win_rate = mean(synthetic_rewards > 0)`
- `max_dd = max(running_peak - cumulative_returns)` on the synthetic-reward series
- `total_trades = N`

If `len(holdout) < MIN_HOLDOUT_TRADES` (set to 20), emit `{"_status": "insufficient_data", "min_required": 20, "had": N}` instead — preserves the "deferred" semantics for very small buffers.

## Files changed

### 1. **New module**: `tools/ml/backtest_brain.py` (~150 LoC)

```python
@dataclass(frozen=True)
class BacktestResult:
    sharpe: float
    max_dd: float
    win_rate: float
    total_trades: int
    window_start: str
    window_end: str

    def as_eval_dict(self) -> dict:
        return {
            "sharpe": self.sharpe,
            "max_dd": self.max_dd,
            "win_rate": self.win_rate,
            "total_trades": self.total_trades,
            "window_start": self.window_start,
            "window_end": self.window_end,
        }


def evaluate_brain_on_holdout(
    *,
    policy: PolicyNetwork,
    transitions: Sequence[Transition],
    holdout_fraction: float = 0.2,
    device: torch.device | None = None,
) -> BacktestResult | dict:
    """MVP backtest: chronological 80/20 split, run policy on holdout,
    compute Sharpe/max_dd/win_rate on synthetic_reward = t.reward × brain_adjust.
    Returns BacktestResult on success or {"_status": "insufficient_data", ...}
    when the holdout is too small for stable metrics.
    """
    ...
```

### 2. **`tools/ml/train_brain.py`** — call backtest in `_async_main`, populate eval doc

```python
# After train_ppo returns, before save_checkpoint
from tools.ml.backtest_brain import evaluate_brain_on_holdout
backtest_outcome = evaluate_brain_on_holdout(
    policy=policy, transitions=transitions, device=device,
)
log.info("backtest: %s", backtest_outcome)
```

Update `build_eval_doc` signature to accept the backtest outcome:
```python
def build_eval_doc(*, version, transitions, train_history, all_pass, backtest):
    ...
    if isinstance(backtest, BacktestResult):
        backtest_results = backtest.as_eval_dict()
    else:
        backtest_results = backtest  # the {"_status": "insufficient_data"} dict
    return {..., "backtest_results": backtest_results, ...}
```

### 3. **`backend/app/ml/champion_challenger.py`** — fix `_evaluate_sharpe`

```python
async def _evaluate_sharpe(session, checkpoint_id) -> float:
    """Read the Sharpe from rl_checkpoints.eval_results JSONB column.

    The trainer (tools/ml/train_brain.py via tools/ml/backtest_brain.py)
    computes Sharpe on a chronological 80/20 holdout at training time and
    persists it in eval_results. No runtime re-backtest needed.

    Returns 0.0 when the checkpoint has no Sharpe (legacy id=1, id=2 from
    pre-PR-BACKTEST-PHASEB5 era, or buffers too small for holdout).
    """
    row = (await session.execute(
        sa.text(
            "SELECT eval_results->>'sharpe' AS sharpe "
            "FROM rl_checkpoints WHERE id = :i"
        ),
        {"i": checkpoint_id},
    )).first()
    if row is None or row.sharpe is None:
        return 0.0
    try:
        return float(row.sharpe)
    except (TypeError, ValueError):
        return 0.0
```

(Removes the `from tools.backtest import run_backtest` ghost import.)

### 4. **Schema doc update**: comment the eval_results JSONB shape in `admin_rl.py`

(Comment-only change — no API change.)

## Backward compatibility

- `rl_checkpoints.id=1, id=2`: their `eval_results` is `{"_status": "deferred_to_phase_b5"}`. `_evaluate_sharpe` returns `0.0` for them. This means:
  - **As champion**: 0.0 × 1.05 = 0.0; any new challenger with Sharpe > 0 wins. Correct.
  - **As challenger**: returns 0.0; needs to beat champion × 1.05. If no champion → first-checkpoint case → auto-passes (handled upstream in `evaluate_challenger`).
- New checkpoints (id=3+) get real Sharpe in eval_results → gate works as designed.

No checkpoint migration needed.

## What this PR does NOT change

- Does NOT activate any `rl_checkpoints` row
- Does NOT touch trading logic, flags, signal scoring
- Does NOT touch brain inference path
- Does NOT touch the ConvLSTM `_evaluate_mae` path (still broken; separate concern out of scope here)
- Does NOT fix the embedding-frozen-during-PPO issue (separate PR-BRAIN-EMBEDDINGS-LEARNABLE; sequenced AFTER this one so we have backtest metrics to compare id=3 vs id=4)

## TDD test plan

### `backend/tests/unit/test_backtest_brain.py` (new file)

1. `test_backtest_returns_real_sharpe` — feed 50 hand-crafted transitions with known reward distribution; assert returned Sharpe is within tolerance of the analytically-expected value
2. `test_backtest_returns_real_max_dd` — transitions designed with a clear drawdown; assert max_dd ≈ expected
3. `test_backtest_returns_real_win_rate` — transitions with 60% positive rewards; assert win_rate ≈ 0.6
4. `test_backtest_insufficient_data_below_threshold` — < 20 holdout trades → returns `{"_status": "insufficient_data", ...}`
5. `test_backtest_uses_chronological_split` — transitions are NOT shuffled before splitting; assert last 20% by index is the holdout
6. `test_backtest_handles_zero_variance_rewards` — all rewards identical; Sharpe denominator near zero, the +1e-8 epsilon prevents NaN

### `backend/tests/unit/test_champion_challenger_sharpe_from_db.py` (new file)

1. `test_evaluate_sharpe_reads_from_eval_results` — sqlite fixture with rl_checkpoints row having `eval_results = {"sharpe": 1.23}`; assert helper returns 1.23
2. `test_evaluate_sharpe_returns_zero_for_legacy_checkpoint` — eval_results = `{"_status": "deferred_to_phase_b5"}` → returns 0.0 (no crash)
3. `test_evaluate_sharpe_returns_zero_for_missing_row` — non-existent checkpoint_id → returns 0.0
4. `test_evaluate_sharpe_returns_zero_for_malformed_value` — eval_results.sharpe is the string "not-a-number" → returns 0.0 (graceful)

### Integration in train_brain (covered by existing flow)

The next probe run will validate end-to-end via the cron's brain-cron-trigger.

## V-7 latency budget

**Zero.** Backtest computation runs during the daily 03:30 UTC cron only. The DB read in `_evaluate_sharpe` is a single SELECT keyed on PK — sub-millisecond.

## Audit chain impact

**None.** `rl_checkpoints` is not in `HASH_PAYLOAD_COLUMNS`. The new module reads `eval_results` JSONB — no chain writes.

## Rollback

`git revert <PR-BRAIN-BACKTEST-PHASEB5-squash>`. Pure code revert. After rollback:
- New checkpoints stop getting backtest metrics in eval_results
- `_evaluate_sharpe` reverts to importing the ghost `tools.backtest` (back to crashing)
- All non-force activation paths return to silent failure

No data loss. id=3+ rows retain their populated `eval_results` but the gate stops reading from it.

## Post-deploy verification

1. **Run the brain-cron-trigger probe.**
2. **Confirm new `rl_checkpoints.id=3` row.**
3. **Inspect V1 output** (the probe's psql dump): `train_data_window` and `eval_results` should be visible. The eval_results JSONB should now contain `{"sharpe": <float>, "max_dd": <float>, "win_rate": <float>, "total_trades": ~58, "window_start": "...", "window_end": "..."}`.
4. **Sanity check**: with the warm-started clustered embeddings and ~58 holdout trades, expect:
   - Sharpe in roughly [-1.0, +1.0] (small-sample noise; no real expectation of dramatic positive Sharpe yet)
   - win_rate in [0.4, 0.6]
   - total_trades ≈ floor(0.2 × buffer_size)

## Commit message

```
feat(pr-brain-backtest-phaseb5): real Sharpe/max_dd/win_rate via training-time holdout
```

## Auto-merge authorization

Per PR-BRAIN-BACKTEST-PHASEB5 directive: training-only feature, no live trading impact. Auto-merge per standing rules once CI green + reviewers PASS + TDD tests pass.
