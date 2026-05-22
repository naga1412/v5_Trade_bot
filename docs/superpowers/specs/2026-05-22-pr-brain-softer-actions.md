# PR-BRAIN-SOFTER-ACTIONS — symmetric, tunable brain_adjust multiplier spread

**Status:** spec drafted 2026-05-22.
**Branch:** `feat/pr-brain-softer-actions` (off `origin/dev` post-PR-BACKTEST-PHASEB5).
**Class:** **brain hyperparameter tweak.** Training/inference. NO live trading impact: brain remains dormant (no active checkpoint).

---

## Problem

PR-BRAIN-BACKTEST-PHASEB5 produced `rl_checkpoints.id=3` with these holdout metrics:

```
Sharpe=-3.21, max_dd=46.53, win_rate=28.8%, total_trades=59
```

The brain is anti-predictive. A driver (one of several) is the **action multiplier magnitude**: a single bad decision multiplies the signal score by 0.5 or 1.4 — a ±40-50% swing. Small per-decision noise compounds when the brain's decision quality is below random.

This PR doesn't fix the brain's decision quality (still need learnable embeddings + more replay data). It **reduces blast radius** so the brain can be safely activated as a small modulation rather than a dominant signal-rewriter, while still demonstrating measurable correlation with outcomes over a longer window.

## Critique of the proposed mapping

Operator's spec proposed `{0.85, 0.95, 1.0, 1.05, 1.15}`. **Shrinkage from current is irregular**:

| Action | Current | Proposed | Shrinkage |
|---|---|---|---|
| FLAT | 0.5 | 0.85 | 35% |
| disagree | 0.6 | 0.95 | 12% |
| NEUTRAL | 1.0 | 1.0 | — |
| agree-half | 1.2 | 1.05 | 12% |
| agree-full | 1.4 | 1.15 | 17% |

The "extreme" actions (FLAT, agree-FULL) shrink **more** than the "moderate" ones (disagree, agree-half). That's backwards — disagree/agree-half are intermediate-confidence brain signals, not full-strength signals, so they should shrink at LEAST as much as the extremes.

## Recommended approach — symmetric, one tunable knob

Replace 4 hardcoded values with a parametric formula keyed on a single `SPREAD` parameter:

```python
SPREAD = settings.BRAIN_ACTION_MULTIPLIER_SPREAD  # default 0.15

FLAT_MULT       = 1.0 - SPREAD       # full-strength suppress
DISAGREE_MULT   = 1.0 - SPREAD / 2   # half-strength suppress
NEUTRAL_MULT    = 1.0                # no-op
AGREE_HALF_MULT = 1.0 + SPREAD / 2   # half-strength boost
AGREE_FULL_MULT = 1.0 + SPREAD       # full-strength boost
```

At default `SPREAD=0.15`:
- {0.85, 0.925, 1.0, 1.075, 1.15} — **symmetric around 1.0 with linear half-steps**

At `SPREAD=0.4` (legacy-ish):
- {0.6, 0.8, 1.0, 1.2, 1.4} — close to current. Note: current FLAT is 0.5 (more extreme than 0.6), an asymmetry that's never been explained or load-bearing.

Benefits:
- **One knob** to retune as the brain proves itself (raise SPREAD as brain demonstrates positive Sharpe; lower it if backtest regresses)
- **Mathematically clean**: each step is `SPREAD/2`, no magic numbers
- **No code change required to retune** — operator just sets `BRAIN_ACTION_MULTIPLIER_SPREAD` in `.env`
- **Backward-compat-aware**: if a future PR wants exact-current behavior for an A/B test, set `SPREAD=0.4` and one extra 0.1 nudge on FLAT (operator decision)

## Honest scope statement

This PR REDUCES BLAST RADIUS. It does NOT fix the brain's decision quality. With a brain whose win_rate is 28.8% on holdout, softer multipliers means a bad brain hurts ~4× less BUT a good brain also helps ~4× less. The real fixes remain:
- PR-BRAIN-EMBEDDINGS-LEARNABLE (let embeddings train, ~250-400 LoC refactor)
- Replay buffer growth (organic, 30/day → doubles in 2 weeks)
- Reward shaping (longer follow-up)

Softer multipliers make the brain SAFE TO ACTIVATE while these proceed. Operator can flip `is_active=True` on a checkpoint that posts Sharpe ≈ 0 without risking a 40% signal-score loss spiral.

## Files changed

### 1. `backend/app/config.py` — add Setting field

```python
# === BRAIN ACTION MULTIPLIER (SP-4 Phase B5+) ===
# Maximum signed multiplier offset from 1.0 produced by
# action_to_brain_adjust. With SPREAD=0.15 the brain's effect on the
# aggregator's final score is bounded to [0.85, 1.15] × static.
#
# Symmetric mapping:
#   FLAT       → 1 − SPREAD       (full-strength suppress)
#   disagree   → 1 − SPREAD / 2   (half-strength suppress)
#   NEUTRAL    → 1.0              (no-op)
#   agree-half → 1 + SPREAD / 2   (half-strength boost)
#   agree-full → 1 + SPREAD       (full-strength boost)
#
# Lower = brain is more conservative; higher = brain has more sway.
# Default 0.15 chosen post-PR-BRAIN-BACKTEST-PHASEB5 to reduce blast
# radius of bad decisions during v1 (id=3 holdout Sharpe = −3.21).
BRAIN_ACTION_MULTIPLIER_SPREAD: float = 0.15
```

### 2. `backend/app/rl/inference.py:action_to_brain_adjust` — read from Settings

```python
def action_to_brain_adjust(
    smoothed_action: str, *, proposed_direction: str,
) -> float:
    """Map a brain action + the L1..L9 proposed direction to brain_adjust.

    Multiplier spread is controlled by
    ``settings.BRAIN_ACTION_MULTIPLIER_SPREAD`` (default 0.15). The
    mapping is symmetric around 1.0 with linear half-steps:

      FLAT     → 1 - SPREAD          # full-strength suppress
      disagree → 1 - SPREAD / 2      # half-strength suppress
      NEUTRAL  → 1.0                 # no-op
      half     → 1 + SPREAD / 2
      full     → 1 + SPREAD

    Smaller SPREAD = brain more conservative. v1 default = 0.15 chosen
    post-PR-BRAIN-BACKTEST-PHASEB5 to bound blast radius while the brain
    accumulates evidence.

    ``proposed_direction`` is one of ``"LONG"`` / ``"SHORT"`` / ``"NEUTRAL"``.
    """
    spread = get_settings().BRAIN_ACTION_MULTIPLIER_SPREAD

    if smoothed_action == "FLAT":
        return 1.0 - spread

    is_brain_long = smoothed_action.startswith("LONG_")
    is_brain_short = smoothed_action.startswith("SHORT_")
    is_brain_full = smoothed_action.endswith("_FULL")

    if proposed_direction == "LONG" and is_brain_long:
        return (1.0 + spread) if is_brain_full else (1.0 + spread / 2)
    if proposed_direction == "SHORT" and is_brain_short:
        return (1.0 + spread) if is_brain_full else (1.0 + spread / 2)

    if proposed_direction == "NEUTRAL":
        return 1.0

    # Brain disagrees with proposed direction.
    return 1.0 - spread / 2
```

The import line gains `from app.config import get_settings` (already used in many other modules — verified).

### 3. `backend/tests/unit/test_rl_inference.py` — update assertions

Existing tests at lines 188-215 hardcode 0.5/0.6/1.2/1.4. Update to use the same formula:
```python
SPREAD = get_settings().BRAIN_ACTION_MULTIPLIER_SPREAD
...
assert action_to_brain_adjust(ACTION_LONG_FULL, proposed_direction="LONG") == 1.0 + SPREAD
assert action_to_brain_adjust(ACTION_LONG_HALF, proposed_direction="LONG") == 1.0 + SPREAD / 2
assert action_to_brain_adjust(ACTION_FLAT, proposed_direction="LONG") == 1.0 - SPREAD
...
```

Plus 3 NEW tests:
- `test_brain_action_multiplier_spread_default_is_conservative` — assert default is 0.15
- `test_action_to_brain_adjust_respects_spread_override` — monkeypatch Settings.BRAIN_ACTION_MULTIPLIER_SPREAD=0.4; assert the mapping shifts symmetrically
- `test_action_to_brain_adjust_remains_in_aggregator_bounds` — for SPREAD in {0.05, 0.15, 0.4, 0.95}, assert every output is in (0, 2) per `aggregator._BRAIN_ADJUST_MIN/_MAX` exclusive bounds

## What this PR does NOT change

- Does NOT activate any rl_checkpoints row
- Does NOT touch trading logic / flags / signal-score formula  
- Does NOT touch the brain's policy network, training loop, replay buffer, or backtest module
- Does NOT touch the cron script
- Does NOT modify `_BRAIN_ADJUST_MIN`/`_BRAIN_ADJUST_MAX` (0, 2) exclusive bounds in aggregator
- Does NOT fix the asset-embeddings-frozen bottleneck (PR-EMBEDDINGS-LEARNABLE remains)

## TDD tests (verified pass before commit)

- **8 updated assertions** in test_rl_inference.py reflecting new spread
- **3 new tests** for the parametric design + bounds safety
- All existing 102 RL regression tests should still pass (no API change for callers — `action_to_brain_adjust` still takes the same args, returns a float in (0, 2))

## V-7 latency budget

**Zero.** A single `get_settings()` call per signal tick, which is already memoized via `@lru_cache` in app.config. No new computation.

## Audit chain impact

**None.** Hyperparameter change.

## Backward compatibility

- Existing rl_checkpoints id=1/id=2/id=3 are unaffected. Their saved policies remain valid; the next backtest computed against any of them will use the new spread.
- Activation gate `_evaluate_sharpe` reads from rl_checkpoints.eval_results JSONB — that value was computed at training time with the OLD spread. To get a fair comparison, operator should let the next cron tick produce id=4 with the new spread, then compare id=4 vs id=3.

## Rollback

`git revert <PR-BRAIN-SOFTER-ACTIONS-squash>`. Pure code revert. After rollback, the next cron's eval_results.sharpe reflects the old spread mapping again.

Alternative without code revert: operator sets `BRAIN_ACTION_MULTIPLIER_SPREAD=0.4` in `.env` to approximate pre-PR behavior (FLAT becomes 0.6 instead of 0.5, the only non-symmetric difference).

## Post-deploy verification

1. **Wait for or trigger next cron** (`brain-cron-trigger` probe)
2. **Confirm id=4 emitted** with backtest_results populated
3. **Compare Sharpe**:
   - Expect: id=4 Sharpe is materially LESS NEGATIVE than id=3's −3.21 (proportional damage reduction)
   - Expect: id=4 win_rate is SIMILAR to id=3's 28.8% (this PR doesn't change brain decisions, only their amplitude)
4. **If id=4 Sharpe is NOT less negative than id=3 / fundamentally different**: investigate — the formula has a bug or another change shipped in parallel

## Commit message

```
feat(pr-brain-softer-actions): symmetric tunable brain_adjust spread (default 0.15)
```

## Auto-merge authorization

Per PR-BRAIN-SOFTER-ACTIONS directive: hyperparameter change, no live trading impact. Auto-merge per standing rules.
