# PR-strategy-1 — Entry-score threshold + SHORT disable + PR1 column-population fix

**Status:** spec drafted 2026-05-20 per operator's approved PR-strategy-1 directive.
**Branch:** `feat/pr-strategy-1-entry-quality` (off `origin/dev` at PR10.8 squash `454792d`).

## Why

PR-DIAG-1 + PR-DIAG-1.5 surfaced three actionable insights from 163 closed shadow trades:
1. **Score quality matters at the top end:** Decile-10 (entry_score ≥ ~0.36) has WR 50% / avg +1.24%; Decile-9 has WR 31% but avg −1.96% (FIDA tail). Imposing a minimum-score threshold should improve WR + avg PnL.
2. **SHORT side is structurally worse:** 89 SHORT trades / WR 19.1% vs 74 LONG / WR 27.0%. The model's score distribution clusters at ±0.30 — there's no "high-conviction SHORT" tail (deciles 6-10 are all LONG-dominated). Disabling SHORT entirely until a SHORT-specific edge surfaces is a defensible scope-narrowing.
3. **PR1 analytics columns are 100% NULL on closed shadow_trades** (`mtf_agreement`, `p_win`, `effective_score` etc.). Migration 0020 added the columns; `build_shadow_trade_payload` was never threaded. Fix is plumbing-only.

All three behaviors are **flag-gated, default OFF** — zero deploy-time behavior change. Operator flips per environment.

## Three changes

### 1. New shared gate: `open_position_gate`

**File:** `backend/app/core/gates/entry_quality.py` (new module).

**Public surface:**

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class AllowDecision:
    allow: bool
    reason: str | None  # short ID like "short_disabled" / "below_long_threshold"

def open_position_gate(signal, settings) -> AllowDecision:
    """Duck-typed gate: `signal` has `.direction` (str "LONG"/"SHORT") and
    `.entry_score` (float | None). Settings provides the two flags.

    Logic:
    1. signal.direction == "SHORT" + settings.DISABLE_SHORT_SIGNALS → deny("short_disabled")
    2. signal.direction == "LONG" + settings.MIN_ENTRY_SCORE_LONG is not None
       + signal.entry_score < settings.MIN_ENTRY_SCORE_LONG → deny("below_long_threshold")
    3. Otherwise → allow
    """
```

**Settings (2 new in `app/config.py`):**

```python
# PR-strategy-1: entry-quality gate. Default OFF.
MIN_ENTRY_SCORE_LONG: float | None = None  # None = gate is off
DISABLE_SHORT_SIGNALS: bool = False
```

### 2. Wire at two call sites

**Site 1: shadow_worker open path** (`backend/app/shadow/worker.py::_maybe_open_position`).
After `evaluator.evaluate()` returns a non-None signal and the SIGNAL info log line, **before** `ShadowPosition.from_signal(...)`:

```python
from app.core.gates.entry_quality import open_position_gate
from app.config import get_settings as _get_quality_settings
decision = open_position_gate(signal, _get_quality_settings())
if not decision.allow:
    _entry_quality_metrics["gate_denial_total"][decision.reason or "unknown"] = \
        _entry_quality_metrics["gate_denial_total"].get(decision.reason or "unknown", 0) + 1
    log.info(
        "shadow_worker: %s/%s GATE_DENIED %s score=%.3f",
        candle.symbol, tf, decision.reason, signal.score,
    )
    return
```

**Site 2: dispatcher pre-conditions** (`backend/app/trading/execution/dispatcher.py::dispatch`).
Insert AFTER the PR10 symbol-allowlist gate and BEFORE the funding gate. This requires:
- Adding `entry_score: float | None = None` to `SignalProposal`.
- Threading `pred_score: float | None = None` through `proposal_from_prediction`.
- Updating `live_prediction.py` caller to pass `pred.final.score`.

Gate code at dispatcher:

```python
from app.core.gates.entry_quality import open_position_gate
quality_decision = open_position_gate(proposal, get_settings())
if not quality_decision.allow:
    return DispatchResult(
        outcome="blocked_entry_quality",
        detail=f"entry_quality_gate: {quality_decision.reason}",
    )
```

Add `"blocked_entry_quality"` to the `DispatchOutcome` Literal.

### 3. Fix PR1 column population on shadow_trades

**Files:**
- `backend/app/db/payload_builders.py::build_shadow_trade_payload` — add 7 PR1 params.
- `backend/app/shadow/engine.py::ShadowPosition` — add 7 fields, default None.
- `backend/app/shadow/engine.py::ShadowPosition.from_signal` — accept + forward the 7 fields.
- `backend/app/shadow/worker.py::_maybe_open_position` — populate from `pred` at signal time, then attach to the `position` before persisting.
- `backend/app/shadow/persistence.py::persist_closed_trade` — read from `pos` + pass to builder.

**Known limitation (documented):** `shadow_open_positions` doesn't have these columns (PR1 migration 0020 covered only `predictions`, `shadow_trades`, `live_trades`). So if backend restarts mid-position, the in-memory PR1 values are lost (re-loaded position has them all None). For positions that open + close within the same session, PR1 columns are populated correctly. Restart-survivor positions remain NULL — same as current behavior. **Out of scope:** a follow-up alembic migration to add PR1 columns to `shadow_open_positions` + thread persist/load.

## TDD test plan (11 tests)

1. `test_gate_allows_when_flags_off`
2. `test_gate_denies_short_when_disabled`
3. `test_gate_allows_short_when_not_disabled`
4. `test_gate_denies_long_below_threshold` (score 0.35, threshold 0.36)
5. `test_gate_allows_long_at_threshold` (score 0.36, threshold 0.36)
6. `test_gate_allows_long_when_threshold_null`
7. `test_shadow_open_path_calls_gate_and_short_circuits_on_deny`
8. `test_dispatcher_precondition_calls_gate_and_short_circuits_on_deny`
9. `test_build_shadow_trade_payload_includes_all_7_pr1_cols`
10. `test_persist_closed_trade_writes_all_7_pr1_cols` (round-trip integration; SQLite)
11. `test_shadow_worker_open_with_score_below_threshold_writes_no_row` (integration)

## Default state at deploy

- `MIN_ENTRY_SCORE_LONG = None` → gate allows all LONG entries regardless of score.
- `DISABLE_SHORT_SIGNALS = False` → SHORT entries continue normally.
- PR1 column-population is unconditional (no flag) — fills NULL columns going forward; old NULLs stay NULL.

Operator must opt in per env. The reviewers should verify that on a default config:
- `pytest backend/tests/` is bit-identical with respect to gate behavior (deny path never fires)
- Shadow trades opened post-deploy have populated PR1 columns; pre-deploy NULL trades stay NULL

## Out of scope (explicit deferrals)

- PR1 column add to `shadow_open_positions` table (alembic migration) — restart-survivor positions still get NULL PR1 on close.
- FIDA-style slippage circuit-breaker (FU-33 in KNOWN_ISSUES).
- SHORT-side specific edge research / second strategy experiment.

## Rollback

Single env var per flag:
- `MIN_ENTRY_SCORE_LONG=` (unset / None) → LONG threshold disabled.
- `DISABLE_SHORT_SIGNALS=False` → SHORT re-enabled.

`get_settings()` is `lru_cache`'d; restart required (consistent with every other flag in the codebase).

`git revert <PR-strategy-1-squash>` reverts gate + PR1 plumbing. Audit-chain hash for any trades closed in the window between merge + revert remains intact (PR1 fields are in `NON_HASHED_ALLOW_LIST` per `app/db/audit.py`).

## Operator authorization

Standing PR-strategy-1 directive: "ship the code with flags OFF. Operator will flip in staging first, observe, then flip in prod." Auto-merge per signal-suppressive class (default-OFF → zero deploy-time behavior change → no soak required at code-ship time).
