# PR2 — MTF Gate + SHORT Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert PR1's recording-only MTF infrastructure into an active dispatcher gate. Wire 3 SHORT-side safety branches default-OFF. Add `live_trades.mtf_*` persistence on dispatch. First behavior-change PR in the 5-PR Option D rollout.

**Architecture:** Single insertion point in `dispatcher.dispatch()` between funding-block and max-concurrent checks. Three new helper functions: `_apply_mtf_gate`, `_apply_short_safety_gates`, `_maybe_tighten_short_sl`. `SignalProposal` grows 3 optional MTF fields; `proposal_from_prediction` threads them from `LivePredictionOut`. `build_live_trade_payload` persists them on dispatch. `SHORT_FUNDING_HALVE_HOLD` (the only flag not in dispatcher) hooks the exit-timeout path — exact module identified in Phase 5 call-graph trace. All flags default-OFF except `MTF_MIN_AGREEMENT_1H=3`.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 async / Pydantic v2 BaseSettings / pytest + pytest-asyncio. No alembic migration. No new workers.

**Source spec:** `docs/superpowers/specs/2026-05-17-pr2-mtf-gate-and-short-safety-design.md`

**Branch:** `feat/pr2-mtf-gate` off `dev` (NEVER push to `main`).

> **Note on branch base:** This branch is created when PR1 has merged into dev. The current `feat/pr2-spec-draft-mtf-gate` branch carries only the spec + this plan — implementation work happens on a fresh `feat/pr2-mtf-gate` branch off the post-PR1 dev tip.

---

## File Structure (locked in via design)

### NEW files

| Path | Responsibility |
|---|---|
| `backend/tests/trading/execution/test_dispatcher_mtf_gate.py` | Gate fire/pass; None-agreement fail-open; NEUTRAL never reaches gate |
| `backend/tests/trading/execution/test_dispatcher_higher_tf_veto.py` | 1d+1w veto fires; partial-opposition does not; flag-off disables |
| `backend/tests/trading/execution/test_dispatcher_short_flags.py` | Defaults False; env-var overrides; LONG never triggers SHORT flags |
| `backend/tests/trading/execution/test_dispatcher_sl_tightening.py` | SHORT + low MTF tightens SL 20%; LONG + low MTF unchanged; flag-off unchanged |
| `backend/tests/integration/test_pr2_mtf_gate_e2e.py` | End-to-end: high-MTF emits, low-MTF blocks, high-borrow SHORT blocks |
| `backend/tests/integration/test_pr2_telegram_approve_uniformity.py` | Auto + telegram paths apply same gate identically |
| `backend/tests/unit/test_pr2_settings_defaults.py` | All 5 flags + 4 thresholds carry exact default values from spec §6.1 |

### MODIFIED files

| Path | Reason |
|---|---|
| `backend/app/config.py` | Add 5 new flags (`MTF_MIN_AGREEMENT_1H=3`, `MTF_HIGHER_TF_VETO=True`, 3× SHORT_*=False) + 4 threshold knobs |
| `backend/app/trading/execution/dispatcher.py` | (a) extend `DispatchOutcome` Literal with 3 new members at ~line 118; (b) add 3 helpers `_apply_mtf_gate` / `_apply_short_safety_gates` / `_maybe_tighten_short_sl`; (c) call them in `dispatch()` between funding-block check and max-concurrent check (~line 408 post-PR1) |
| `backend/app/trading/execution/proposal.py` (or wherever `SignalProposal` lives) | Add 3 Optional fields: `mtf_agreement: int \| None`, `mtf_dominant_tf: str \| None`, `mtf_directions: dict[str, int] \| None` |
| `backend/app/trading/execution/glue.py` | `proposal_from_prediction` reads `pred.mtf_*` from `LivePredictionOut` + parses `mtf_directions_json`; populates the 3 new `SignalProposal` fields |
| `backend/app/db/payload_builders.py` | `build_live_trade_payload` accepts `proposal.mtf_*` and emits `mtf_agreement` / `mtf_dominant_tf` / `mtf_directions_json` (canonical `json.dumps(sort_keys=True, separators=(",",":"))` for the JSON column) |
| `backend/app/trading/<exit-or-timeout-module>.py` | Hook `SHORT_FUNDING_HALVE_HOLD` — exact module TBD in Task 5.1 call-graph trace |
| `backend/scripts/bench_aggregator_latency.py` | Add `--mtf-gate-disabled` / `--mtf-gate-enabled` CLI modes; same JSON output shape as PR1 |
| `backend/tests/db/test_payload_builders.py` | Add a golden-dict case: proposal with `mtf_*` populated → builder emits the 3 MTF columns; existing `mtf_*=None` golden still passes (PR1 compat) |
| `.github/workflows/ci.yml` | If PR1's bench-smoke step exists, no change needed; otherwise no change (bench is operator-gated, not CI-gated) |

### DELETED files

None.

---

## Phase 0 — Branch + setup

### Task 0: Create feature branch off dev (after PR1 merges)

**Files:** none

- [ ] **Step 1: Verify PR1 has merged to dev**

```
gh pr view 169 --json mergedAt,baseRefName --jq '{merged: (.mergedAt != null), base: .baseRefName}'
```
Expected: `{"merged": true, "base": "dev"}`. If `merged: false`, STOP — PR2 cannot start until PR1 lands.

- [ ] **Step 2: Fetch + reset to current dev tip**

```
git fetch origin dev
git checkout dev && git reset --hard origin/dev
git log --oneline -5
```
Expected: PR1 merge commit visible in recent history; `feat/pr1-record-only-foundation` content present (e.g., `backend/app/core/scoring/mtf_confluence.py` exists).

- [ ] **Step 3: Create feature branch**

```
git checkout -b feat/pr2-mtf-gate
```
Expected: `Switched to a new branch 'feat/pr2-mtf-gate'`.

- [ ] **Step 4: Confirm mypy + ruff baseline still clean**

```
cd backend && python -m ruff check . && python -m mypy app 2>&1 | tail -1
```
Expected: `All checks passed!` and `Success: no issues found in 404+ source files`.

- [ ] **Step 5: Confirm PR1 fixtures are usable**

The PR2 integration tests will use the `pipeline_session` fixture pattern from `tests/integration/test_pr1_full_pipeline.py`. Confirm the file exists and imports cleanly:

```
python -c "from tests.integration.test_pr1_full_pipeline import _make_hourly_bars; print(_make_hourly_bars(10).shape)"
```
Expected: `(10, 5)`.

---

## Phase 1 — Settings: new flags + thresholds (LANDS FIRST)

Rationale: every downstream phase depends on these settings being readable. TDD-first establishes the contract.

### Task 1.1: Write failing test for default settings values

**Files:**
- Test: `backend/tests/unit/test_pr2_settings_defaults.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
"""PR2 Settings defaults — codify the design's §6.1 bound that every
new flag reproduces PR1 behavior EXCEPT MTF_MIN_AGREEMENT_1H=3 (the
one explicit behavior flip)."""
from __future__ import annotations

from app.config import Settings


def test_mtf_gate_defaults() -> None:
    s = Settings(database_url="postgresql://x", redis_url="redis://x")
    assert s.MTF_MIN_AGREEMENT_1H == 3
    assert s.MTF_HIGHER_TF_VETO is True


def test_short_safety_flags_default_off() -> None:
    s = Settings(database_url="postgresql://x", redis_url="redis://x")
    assert s.SHORT_FUNDING_HALVE_HOLD is False
    assert s.SHORT_TIGHTEN_SL_LOW_MTF is False
    assert s.SHORT_VETO_HIGH_BORROW is False


def test_short_threshold_knobs() -> None:
    s = Settings(database_url="postgresql://x", redis_url="redis://x")
    assert s.SHORT_FUNDING_HALVE_THRESHOLD_PCT == 0.05
    assert s.SHORT_VETO_BORROW_APR_PCT == 10.0
    assert s.SHORT_TIGHTEN_SL_MTF_CUTOFF == 5
    assert s.SHORT_TIGHTEN_SL_PCT == 0.20


def test_env_var_override_persists(monkeypatch) -> None:
    monkeypatch.setenv("MTF_MIN_AGREEMENT_1H", "5")
    monkeypatch.setenv("SHORT_VETO_HIGH_BORROW", "true")
    s = Settings(database_url="postgresql://x", redis_url="redis://x")
    assert s.MTF_MIN_AGREEMENT_1H == 5
    assert s.SHORT_VETO_HIGH_BORROW is True


def test_mtf_min_agreement_zero_is_valid_rollback() -> None:
    """Per spec §6.1: MTF_MIN_AGREEMENT_1H=0 is the single-env-var
    rollback path. 0 must validate cleanly (not fail at < 0 check)."""
    s = Settings(
        database_url="postgresql://x",
        redis_url="redis://x",
        MTF_MIN_AGREEMENT_1H=0,
    )
    assert s.MTF_MIN_AGREEMENT_1H == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_pr2_settings_defaults.py -v`
Expected: 5 errors — `AttributeError: 'Settings' object has no attribute 'MTF_MIN_AGREEMENT_1H'`.

### Task 1.2: Add settings to `app/config.py`

**Files:**
- Modify: `backend/app/config.py`

- [ ] **Step 1: Add 5 flag fields + 4 threshold fields**

Locate the `Settings(BaseSettings)` class. Add the following fields in a clearly-labeled PR2 block (alphabetical within block, comment cites the spec):

```python
    # --- PR2: MTF gate (active in PR2; recording-only in PR1) -------------
    # MTF_MIN_AGREEMENT_1H=0 is the single-env-var rollback path (gate
    # passes for all agreement values when set to 0). Default 3 = 3-of-6
    # TF majority. Tunable post-launch via env var.
    MTF_MIN_AGREEMENT_1H: int = 3
    MTF_HIGHER_TF_VETO: bool = True

    # --- PR2: SHORT-side safety (default OFF; per-env enable) -------------
    # All 3 flags must default False — spec §6.1 hard bound. Env var
    # override allowed per-environment.
    SHORT_FUNDING_HALVE_HOLD: bool = False
    SHORT_TIGHTEN_SL_LOW_MTF: bool = False
    SHORT_VETO_HIGH_BORROW: bool = False

    # --- PR2: SHORT-side thresholds (only consulted when flag ON) --------
    SHORT_FUNDING_HALVE_THRESHOLD_PCT: float = 0.05   # %/8h
    SHORT_VETO_BORROW_APR_PCT: float = 10.0           # % APR
    SHORT_TIGHTEN_SL_MTF_CUTOFF: int = 5
    SHORT_TIGHTEN_SL_PCT: float = 0.20
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_pr2_settings_defaults.py -v`
Expected: 5 passed.

- [ ] **Step 3: Confirm mypy + ruff clean**

```
cd backend && python -m ruff check app/config.py && python -m mypy app/config.py
```
Expected: clean.

- [ ] **Step 4: Commit**

```
git add backend/app/config.py backend/tests/unit/test_pr2_settings_defaults.py
git commit -m "feat(pr2): settings — MTF gate + SHORT safety flags (defaults per spec §6.1)"
```

---

## Phase 2 — `SignalProposal` extension

### Task 2.1: Locate the `SignalProposal` definition

**Files:** none (research-only)

- [ ] **Step 1: Find the SignalProposal class**

```
grep -rn "class SignalProposal" backend/app/
```
Expected: a single hit in `backend/app/trading/execution/` (likely `proposal.py` or `glue.py`). Note the exact `file:line` for Task 2.2.

- [ ] **Step 2: Inspect the current shape**

Read the file. Note:
- Is it `@dataclass(frozen=True)` or `pydantic.BaseModel` or `NamedTuple`?
- What fields exist today?
- Does it have `__init__` with all-positional or keyword-only params?

The 3 new fields will be added consistent with whatever pattern already exists.

### Task 2.2: Write failing test for the new fields

**Files:**
- Test: `backend/tests/trading/execution/test_signal_proposal_mtf_fields.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
"""PR2 SignalProposal extension — 3 new optional MTF fields."""
from __future__ import annotations

from app.trading.execution.proposal import SignalProposal  # adjust import per Task 2.1


def _minimal_proposal_kwargs() -> dict:
    """Minimal kwargs that satisfy the existing required fields.
    Adjust per the actual SignalProposal shape discovered in Task 2.1."""
    return dict(
        symbol="BTCUSDT",
        direction="LONG",
        entry=80000.0,
        stop_loss=78000.0,
        take_profit=84000.0,
        # ... whatever else is required pre-PR2 ...
    )


def test_mtf_fields_default_none() -> None:
    p = SignalProposal(**_minimal_proposal_kwargs())
    assert p.mtf_agreement is None
    assert p.mtf_dominant_tf is None
    assert p.mtf_directions is None


def test_mtf_fields_can_be_populated() -> None:
    p = SignalProposal(
        **_minimal_proposal_kwargs(),
        mtf_agreement=4,
        mtf_dominant_tf="1h",
        mtf_directions={"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": 0},
    )
    assert p.mtf_agreement == 4
    assert p.mtf_dominant_tf == "1h"
    assert p.mtf_directions["1d"] == -1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/trading/execution/test_signal_proposal_mtf_fields.py -v`
Expected: `TypeError: SignalProposal.__init__() got an unexpected keyword argument 'mtf_agreement'`.

### Task 2.3: Add 3 Optional fields to `SignalProposal`

**Files:**
- Modify: file from Task 2.1 (likely `backend/app/trading/execution/proposal.py` or similar)

- [ ] **Step 1: Add fields at the end of the existing field list**

Pattern depends on what Task 2.1 finds. Examples:

**If dataclass:**
```python
@dataclass(frozen=True)
class SignalProposal:
    ... existing fields ...
    # PR2: MTF fields threaded from LivePredictionOut. None when PR1 MTF
    # compute returned None or when this proposal pre-dates the threading
    # (recording-only fallback).
    mtf_agreement: int | None = None
    mtf_dominant_tf: str | None = None
    mtf_directions: dict[str, int] | None = None
```

**If pydantic BaseModel:**
```python
class SignalProposal(BaseModel):
    ... existing fields ...
    mtf_agreement: int | None = None
    mtf_dominant_tf: str | None = None
    mtf_directions: dict[str, int] | None = None
```

**Bounds from spec §4.3:**
- Fields are Optional with `None` default. Existing call sites that don't set them get `None` — bit-identical behavior at the proposal layer.
- `mtf_directions` is the parsed dict (not the JSON string). The string form lives only at the DB layer.

- [ ] **Step 2: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/trading/execution/test_signal_proposal_mtf_fields.py -v`
Expected: 2 passed.

- [ ] **Step 3: Run the full trading execution test suite — no regressions**

Run: `cd backend && python -m pytest tests/trading/execution/ tests/unit/test_execution_glue.py tests/unit/test_trading_dispatcher.py tests/unit/test_dispatcher_e2e.py -v --no-cov`
Expected: all pre-existing tests still pass. The new fields default to None so no caller is forced to change.

- [ ] **Step 4: Commit**

```
git add backend/app/trading/execution/proposal.py backend/tests/trading/execution/test_signal_proposal_mtf_fields.py
git commit -m "feat(pr2): SignalProposal — 3 optional MTF fields threaded from LivePredictionOut"
```

---

## Phase 3 — `proposal_from_prediction` threading

### Task 3.1: Write failing test for MTF thread-through

**Files:**
- Test: `backend/tests/unit/test_execution_glue.py` (MODIFY — add new test functions)

- [ ] **Step 1: Add failing tests at the end of the existing file**

```python
def test_proposal_from_prediction_threads_mtf_fields(tmp_path):
    """When LivePredictionOut carries mtf_* values, proposal_from_prediction
    threads them through to SignalProposal."""
    pred = make_live_prediction_out(  # use existing helper
        direction=Direction.LONG,
        mtf_agreement=4,
        mtf_dominant_tf="1h",
        mtf_directions_json='{"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": 0}',
    )
    user = make_user(trading_mode="fully-auto")
    proposal = proposal_from_prediction(pred, user)
    assert proposal is not None
    assert proposal.mtf_agreement == 4
    assert proposal.mtf_dominant_tf == "1h"
    assert proposal.mtf_directions == {"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": 0}


def test_proposal_from_prediction_no_mtf_threads_none():
    """When LivePredictionOut carries no mtf_* (PR1 fallback), proposal
    fields are None."""
    pred = make_live_prediction_out(direction=Direction.LONG)  # default mtf_*=None
    user = make_user(trading_mode="fully-auto")
    proposal = proposal_from_prediction(pred, user)
    assert proposal is not None
    assert proposal.mtf_agreement is None
    assert proposal.mtf_dominant_tf is None
    assert proposal.mtf_directions is None


def test_proposal_from_prediction_invalid_mtf_json_yields_none(caplog):
    """If mtf_directions_json is malformed, log a warning and set
    mtf_directions=None on the proposal (fail-open — never poison dispatch)."""
    pred = make_live_prediction_out(
        direction=Direction.LONG,
        mtf_agreement=4,
        mtf_dominant_tf="1h",
        mtf_directions_json="not-valid-json",
    )
    user = make_user(trading_mode="fully-auto")
    proposal = proposal_from_prediction(pred, user)
    assert proposal is not None
    assert proposal.mtf_agreement == 4  # other fields still threaded
    assert proposal.mtf_directions is None  # parse failed → None
    assert any("mtf_directions_json" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/unit/test_execution_glue.py -v -k mtf`
Expected: 3 failures — `AttributeError: 'SignalProposal' object has no attribute 'mtf_agreement'` (or similar, depending on what the current code does).

### Task 3.2: Implement threading in `proposal_from_prediction`

**Files:**
- Modify: `backend/app/trading/execution/glue.py`

- [ ] **Step 1: Add a private parse helper at module top-level**

```python
import json
import logging

log = logging.getLogger(__name__)


def _parse_mtf_directions_json(raw: str | None) -> dict[str, int] | None:
    """Parse mtf_directions_json from LivePredictionOut. Returns None on
    None input, None on parse failure with a WARNING log. Fail-open: a
    malformed JSON must NOT poison dispatch."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            log.warning("mtf_directions_json parsed to non-dict: %r", parsed)
            return None
        return {str(k): int(v) for k, v in parsed.items()}
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        log.warning("mtf_directions_json parse failed: %s (raw=%r)", exc, raw[:80])
        return None
```

- [ ] **Step 2: Update `proposal_from_prediction` to thread the fields**

Inside the function, after the existing fields are constructed and before the `return SignalProposal(...)` call, add:

```python
    return SignalProposal(
        ... existing kwargs ...,
        # PR2: MTF fields threaded from LivePredictionOut
        mtf_agreement=pred.mtf_agreement,
        mtf_dominant_tf=pred.mtf_dominant_tf,
        mtf_directions=_parse_mtf_directions_json(pred.mtf_directions_json),
    )
```

- [ ] **Step 3: Run the 3 new tests**

Run: `cd backend && python -m pytest tests/unit/test_execution_glue.py -v -k mtf`
Expected: 3 passed.

- [ ] **Step 4: Run full glue test suite — no regressions**

Run: `cd backend && python -m pytest tests/unit/test_execution_glue.py -v --no-cov`
Expected: all pass.

- [ ] **Step 5: Commit**

```
git add backend/app/trading/execution/glue.py backend/tests/unit/test_execution_glue.py
git commit -m "feat(pr2): proposal_from_prediction — thread mtf_* fields with fail-open JSON parse"
```

---

## Phase 4 — Dispatcher gate + helpers

This is the core of PR2. Three helper functions land sequentially, each TDD-first.

### Task 4.1: Write failing test for `_apply_mtf_gate`

**Files:**
- Test: `backend/tests/trading/execution/test_dispatcher_mtf_gate.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
"""PR2 dispatcher MTF gate — fires when mtf_agreement < threshold;
passes when >= threshold; None agreement (PR1 fallback) → passes
(fail-open per spec §4.2)."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.config import Settings
from app.trading.execution.dispatcher import _apply_mtf_gate
from app.trading.execution.proposal import SignalProposal


def _make_proposal(*, direction="LONG", mtf_agreement=None, mtf_directions=None):
    return SignalProposal(
        symbol="BTCUSDT", direction=direction,
        entry=80000.0, stop_loss=78000.0, take_profit=84000.0,
        # ... fill in any other required fields ...
        mtf_agreement=mtf_agreement,
        mtf_dominant_tf="1h",
        mtf_directions=mtf_directions,
    )


def _settings(min_agreement=3, higher_tf_veto=True):
    return Settings(
        database_url="postgresql://x", redis_url="redis://x",
        MTF_MIN_AGREEMENT_1H=min_agreement,
        MTF_HIGHER_TF_VETO=higher_tf_veto,
    )


def test_gate_passes_when_agreement_above_threshold():
    p = _make_proposal(mtf_agreement=4)
    assert _apply_mtf_gate(p, _settings(min_agreement=3)) is None


def test_gate_blocks_when_agreement_below_threshold():
    p = _make_proposal(mtf_agreement=2)
    result = _apply_mtf_gate(p, _settings(min_agreement=3))
    assert result is not None
    assert result.outcome == "blocked_mtf_low_agreement"


def test_gate_passes_when_agreement_is_none_fail_open():
    """Per spec §4.2 + R6: None agreement (PR1 compute failed or fallback)
    → gate PASSES. Never poison dispatch on missing MTF data."""
    p = _make_proposal(mtf_agreement=None)
    assert _apply_mtf_gate(p, _settings()) is None


def test_gate_threshold_zero_is_rollback_bypass():
    """MTF_MIN_AGREEMENT_1H=0 → any agreement >= 0 passes. This is the
    single-env-var rollback path (spec §8)."""
    p = _make_proposal(mtf_agreement=0)
    assert _apply_mtf_gate(p, _settings(min_agreement=0)) is None


def test_higher_tf_veto_fires_when_both_1d_and_1w_oppose_long():
    p = _make_proposal(
        direction="LONG", mtf_agreement=5,
        mtf_directions={"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": -1},
    )
    result = _apply_mtf_gate(p, _settings(higher_tf_veto=True))
    assert result is not None
    assert result.outcome == "blocked_mtf_higher_tf_veto"


def test_higher_tf_veto_does_not_fire_when_only_one_opposes():
    p = _make_proposal(
        direction="LONG", mtf_agreement=5,
        mtf_directions={"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": 1},  # only 1d
    )
    assert _apply_mtf_gate(p, _settings(higher_tf_veto=True)) is None


def test_higher_tf_veto_disabled_by_flag():
    p = _make_proposal(
        direction="LONG", mtf_agreement=5,
        mtf_directions={"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": -1},
    )
    assert _apply_mtf_gate(p, _settings(higher_tf_veto=False)) is None


def test_higher_tf_veto_no_directions_fails_open():
    """mtf_directions=None → veto cannot evaluate → fail-open."""
    p = _make_proposal(direction="LONG", mtf_agreement=5, mtf_directions=None)
    assert _apply_mtf_gate(p, _settings(higher_tf_veto=True)) is None


def test_higher_tf_veto_short_direction_inverted():
    """SHORT signal: 1d AND 1w must both be POSITIVE to veto."""
    p = _make_proposal(
        direction="SHORT", mtf_agreement=5,
        mtf_directions={"5m": -1, "15m": -1, "1h": -1, "4h": -1, "1d": 1, "1w": 1},
    )
    result = _apply_mtf_gate(p, _settings(higher_tf_veto=True))
    assert result is not None
    assert result.outcome == "blocked_mtf_higher_tf_veto"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/trading/execution/test_dispatcher_mtf_gate.py -v`
Expected: 9 errors — `ImportError: cannot import name '_apply_mtf_gate' from app.trading.execution.dispatcher`.

### Task 4.2: Implement `_apply_mtf_gate`

**Files:**
- Modify: `backend/app/trading/execution/dispatcher.py`

- [ ] **Step 1: Extend the DispatchOutcome Literal**

Find the existing `DispatchOutcome = Literal[...]` declaration (around line 118 in current PR1-post code). Add 3 new members:

```python
DispatchOutcome = Literal[
    # existing:
    "emitted",
    "blocked_funding",
    "blocked_max_concurrent",
    "manual_mode",
    "place_failed",
    # PR2 additions:
    "blocked_mtf_low_agreement",
    "blocked_mtf_higher_tf_veto",
    "blocked_short_high_borrow",
]
```

- [ ] **Step 2: Add the gate helper**

Place this private helper near other helpers in `dispatcher.py` (e.g., next to `_check_funding_block` or `_compute_sl_distance_pct`):

```python
def _apply_mtf_gate(
    proposal: SignalProposal,
    settings: Settings,
) -> DispatchResult | None:
    """Return a DispatchResult to short-circuit dispatch when MTF gate
    or higher-TF veto fires. Return None to allow dispatch to continue.

    Fail-open semantics (spec §4.2 + R6):
      - mtf_agreement=None → gate passes (PR1 fallback / compute failure)
      - mtf_directions=None → veto cannot evaluate → passes
      - MTF_MIN_AGREEMENT_1H=0 → any agreement >= 0 passes (rollback path)
    """
    # Min-agreement check
    if proposal.mtf_agreement is not None:
        if proposal.mtf_agreement < settings.MTF_MIN_AGREEMENT_1H:
            return DispatchResult(
                outcome="blocked_mtf_low_agreement",
                # ... other DispatchResult fields per existing constructor ...
            )

    # Higher-TF veto check
    if settings.MTF_HIGHER_TF_VETO and proposal.mtf_directions is not None:
        dirs = proposal.mtf_directions
        d_1d = dirs.get("1d", 0)
        d_1w = dirs.get("1w", 0)
        if proposal.direction == "LONG" and d_1d < 0 and d_1w < 0:
            return DispatchResult(outcome="blocked_mtf_higher_tf_veto")
        if proposal.direction == "SHORT" and d_1d > 0 and d_1w > 0:
            return DispatchResult(outcome="blocked_mtf_higher_tf_veto")

    return None
```

**Bounds from spec §4.2:**
- The function must NOT touch `proposal` (no mutation).
- It must NOT consult settings at module load (always read via `get_settings()` or the passed `settings` arg) — env-var overrides must take effect at request time.
- NEUTRAL direction never reaches this function — `proposal_from_prediction` already filters NEUTRAL → None. Adding a `direction == "NEUTRAL"` branch here would be dead code; the integration test in Phase 6 confirms unreachability.

- [ ] **Step 3: Run the 9 new tests**

Run: `cd backend && python -m pytest tests/trading/execution/test_dispatcher_mtf_gate.py -v`
Expected: 9 passed.

- [ ] **Step 4: Run existing dispatcher tests — no regressions**

Run: `cd backend && python -m pytest tests/unit/test_trading_dispatcher.py tests/unit/test_dispatcher_e2e.py -v --no-cov`
Expected: all pre-existing tests still pass. The gate helper isn't called yet from `dispatch()` (that's Task 4.7), so behavior is unchanged.

- [ ] **Step 5: Commit**

```
git add backend/app/trading/execution/dispatcher.py backend/tests/trading/execution/test_dispatcher_mtf_gate.py
git commit -m "feat(pr2): dispatcher — _apply_mtf_gate + 2 new DispatchOutcome literals (not wired yet)"
```

### Task 4.3: Write failing test for `_apply_short_safety_gates`

**Files:**
- Test: `backend/tests/trading/execution/test_dispatcher_short_flags.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
"""PR2 SHORT-side safety gates — high-borrow veto + flag-default validation.

Spec §4.2: SHORT_VETO_HIGH_BORROW only fires when (a) flag ON,
(b) direction=SHORT, (c) borrow APR > threshold.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from app.config import Settings
from app.trading.execution.dispatcher import _apply_short_safety_gates
from app.trading.execution.proposal import SignalProposal


def _make_proposal(direction="SHORT"):
    return SignalProposal(
        symbol="DOGEUSDT", direction=direction,
        entry=0.10, stop_loss=0.11, take_profit=0.08,
        # ... required fields ...
    )


def _settings(veto=False, threshold=10.0):
    return Settings(
        database_url="postgresql://x", redis_url="redis://x",
        SHORT_VETO_HIGH_BORROW=veto,
        SHORT_VETO_BORROW_APR_PCT=threshold,
    )


def test_veto_disabled_by_default():
    """Spec §6.1: all SHORT flags default False."""
    s = Settings(database_url="postgresql://x", redis_url="redis://x")
    assert s.SHORT_VETO_HIGH_BORROW is False


def test_veto_does_not_fire_for_long():
    p = _make_proposal(direction="LONG")
    with patch(
        "app.trading.execution.dispatcher._lookup_borrow_apr",
        return_value=99.0,  # very high
    ):
        assert _apply_short_safety_gates(p, _settings(veto=True), user=None) is None


def test_veto_does_not_fire_when_flag_off():
    p = _make_proposal()
    with patch(
        "app.trading.execution.dispatcher._lookup_borrow_apr",
        return_value=99.0,
    ):
        assert _apply_short_safety_gates(p, _settings(veto=False), user=None) is None


def test_veto_fires_when_borrow_above_threshold():
    p = _make_proposal()
    with patch(
        "app.trading.execution.dispatcher._lookup_borrow_apr",
        return_value=12.0,  # > 10% default
    ):
        result = _apply_short_safety_gates(p, _settings(veto=True), user=None)
        assert result is not None
        assert result.outcome == "blocked_short_high_borrow"


def test_veto_does_not_fire_when_borrow_below_threshold():
    p = _make_proposal()
    with patch(
        "app.trading.execution.dispatcher._lookup_borrow_apr",
        return_value=5.0,
    ):
        assert _apply_short_safety_gates(p, _settings(veto=True), user=None) is None


def test_veto_fails_open_on_stale_or_missing_borrow_data():
    """Per spec §R7: if borrow data unavailable, fail-open (don't veto)."""
    p = _make_proposal()
    with patch(
        "app.trading.execution.dispatcher._lookup_borrow_apr",
        return_value=None,
    ):
        assert _apply_short_safety_gates(p, _settings(veto=True), user=None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/trading/execution/test_dispatcher_short_flags.py -v`
Expected: failures — function and `_lookup_borrow_apr` don't exist yet.

### Task 4.4: Implement `_lookup_borrow_apr` + `_apply_short_safety_gates`

**Files:**
- Modify: `backend/app/trading/execution/dispatcher.py`

- [ ] **Step 1: Locate the existing `borrow_cost_high` trap's data path**

```
grep -rn "borrow_cost_high\|borrow_rate" backend/app/
```
Confirm what column/source the existing trap uses (likely `intermarket_snapshots.borrow_rate_pct` or equivalent). The `_lookup_borrow_apr` helper reuses the SAME source — DRY per spec D6.

- [ ] **Step 2: Implement the lookup helper**

```python
async def _lookup_borrow_apr(symbol: str, session: AsyncSession) -> float | None:
    """Latest borrow APR % for `symbol` from intermarket_snapshots.

    Returns None when:
      - No row exists for symbol
      - The latest row is older than spec §R7's staleness budget (6h)

    Fail-open: callers treat None as "no veto" — never block on missing
    borrow data.
    """
    STALENESS_BUDGET_HOURS = 6
    row = await session.execute(
        sa.text(
            "SELECT borrow_rate_pct, captured_at "
            "FROM intermarket_snapshots "
            "WHERE symbol = :sym "
            "ORDER BY captured_at DESC LIMIT 1"
        ),
        {"sym": symbol},
    ).fetchone()
    if row is None or row.borrow_rate_pct is None:
        return None
    age = datetime.now(timezone.utc) - row.captured_at
    if age > timedelta(hours=STALENESS_BUDGET_HOURS):
        log.info(
            "borrow data stale for %s (age=%.1fh) — fail-open",
            symbol, age.total_seconds() / 3600,
        )
        return None
    return float(row.borrow_rate_pct)
```

- [ ] **Step 3: Implement `_apply_short_safety_gates`**

```python
async def _apply_short_safety_gates(
    proposal: SignalProposal,
    settings: Settings,
    *,
    session: AsyncSession,
) -> DispatchResult | None:
    """SHORT-side safety branches. Only runs when proposal.direction='SHORT'.

    Currently checks: SHORT_VETO_HIGH_BORROW.
    SL tightening lives in _maybe_tighten_short_sl (modifies, doesn't block).
    Funding-rate hold halving lives in the exit-timeout path (Phase 5).
    """
    if proposal.direction != "SHORT":
        return None

    if settings.SHORT_VETO_HIGH_BORROW:
        borrow_apr = await _lookup_borrow_apr(proposal.symbol, session)
        if borrow_apr is not None and borrow_apr > settings.SHORT_VETO_BORROW_APR_PCT:
            return DispatchResult(outcome="blocked_short_high_borrow")

    return None
```

**Bounds from spec §4.2:**
- LONG signals MUST never enter this code path (early return on `direction != "SHORT"`).
- All checks consult `settings` (not module-level config) so env-var overrides take effect.
- Fail-open on missing borrow data per §R7.

- [ ] **Step 4: Run the 6 new tests**

Run: `cd backend && python -m pytest tests/trading/execution/test_dispatcher_short_flags.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```
git add backend/app/trading/execution/dispatcher.py backend/tests/trading/execution/test_dispatcher_short_flags.py
git commit -m "feat(pr2): dispatcher — _apply_short_safety_gates (high-borrow veto, default OFF)"
```

### Task 4.5: Write failing test for `_maybe_tighten_short_sl`

**Files:**
- Test: `backend/tests/trading/execution/test_dispatcher_sl_tightening.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
"""PR2 SHORT SL tightening — spec §4.2 _maybe_tighten_short_sl.

When (a) flag ON + (b) direction=SHORT + (c) mtf_agreement < cutoff:
  tighten SL distance by SHORT_TIGHTEN_SL_PCT.
"""
from __future__ import annotations

from app.config import Settings
from app.trading.execution.dispatcher import _maybe_tighten_short_sl
from app.trading.execution.proposal import SignalProposal


def _make_short_proposal(entry=100.0, stop_loss=110.0, mtf_agreement=3):
    return SignalProposal(
        symbol="DOGEUSDT", direction="SHORT",
        entry=entry, stop_loss=stop_loss, take_profit=90.0,
        mtf_agreement=mtf_agreement,
    )


def _settings(flag=False, cutoff=5, pct=0.20):
    return Settings(
        database_url="postgresql://x", redis_url="redis://x",
        SHORT_TIGHTEN_SL_LOW_MTF=flag,
        SHORT_TIGHTEN_SL_MTF_CUTOFF=cutoff,
        SHORT_TIGHTEN_SL_PCT=pct,
    )


def test_flag_off_returns_proposal_unchanged():
    p = _make_short_proposal(stop_loss=110.0, mtf_agreement=3)
    out = _maybe_tighten_short_sl(p, _settings(flag=False))
    assert out is p  # same object (or .stop_loss equal — pick one based on impl)
    assert out.stop_loss == 110.0


def test_long_direction_unchanged():
    p = _make_short_proposal(stop_loss=110.0, mtf_agreement=3)
    long_p = p.copy(update={"direction": "LONG"}) if hasattr(p, "copy") else \
             dataclasses.replace(p, direction="LONG")
    out = _maybe_tighten_short_sl(long_p, _settings(flag=True))
    assert out.stop_loss == 110.0  # unchanged


def test_high_agreement_unchanged():
    p = _make_short_proposal(stop_loss=110.0, mtf_agreement=5)
    out = _maybe_tighten_short_sl(p, _settings(flag=True, cutoff=5))
    assert out.stop_loss == 110.0  # >= cutoff → no tightening


def test_low_agreement_tightens_sl_20pct():
    """SHORT, entry=100, SL=110 → SL distance 10. Tighten 20% → new SL=108."""
    p = _make_short_proposal(entry=100.0, stop_loss=110.0, mtf_agreement=3)
    out = _maybe_tighten_short_sl(p, _settings(flag=True, cutoff=5, pct=0.20))
    assert out.stop_loss == pytest.approx(108.0)


def test_none_agreement_unchanged_fail_open():
    """mtf_agreement=None → no tightening (we don't know it's low)."""
    p = _make_short_proposal(stop_loss=110.0, mtf_agreement=None)
    out = _maybe_tighten_short_sl(p, _settings(flag=True))
    assert out.stop_loss == 110.0
```

- [ ] **Step 2: Run test to verify it fails**

Expected: import error.

### Task 4.6: Implement `_maybe_tighten_short_sl`

**Files:**
- Modify: `backend/app/trading/execution/dispatcher.py`

- [ ] **Step 1: Implement the helper**

```python
def _maybe_tighten_short_sl(
    proposal: SignalProposal,
    settings: Settings,
) -> SignalProposal:
    """Return a (possibly modified) proposal with SL distance tightened
    when SHORT + low MTF + flag ON. Otherwise returns the input unchanged.

    No DispatchOutcome change — this is a modification, not a block.
    """
    if proposal.direction != "SHORT":
        return proposal
    if not settings.SHORT_TIGHTEN_SL_LOW_MTF:
        return proposal
    if proposal.mtf_agreement is None:
        return proposal
    if proposal.mtf_agreement >= settings.SHORT_TIGHTEN_SL_MTF_CUTOFF:
        return proposal

    # SHORT trade: SL is ABOVE entry. Tighten by reducing the distance
    # between entry and SL.
    sl_distance = proposal.stop_loss - proposal.entry
    new_distance = sl_distance * (1.0 - settings.SHORT_TIGHTEN_SL_PCT)
    new_sl = proposal.entry + new_distance
    # Use dataclass replace or pydantic copy depending on SignalProposal type:
    return dataclasses.replace(proposal, stop_loss=new_sl)
```

- [ ] **Step 2: Run the 5 new tests**

Run: `cd backend && python -m pytest tests/trading/execution/test_dispatcher_sl_tightening.py -v`
Expected: 5 passed.

- [ ] **Step 3: Commit**

```
git add backend/app/trading/execution/dispatcher.py backend/tests/trading/execution/test_dispatcher_sl_tightening.py
git commit -m "feat(pr2): dispatcher — _maybe_tighten_short_sl (SHORT + low MTF + flag ON)"
```

### Task 4.7: Wire helpers into `dispatch()`

**Files:**
- Modify: `backend/app/trading/execution/dispatcher.py`

Until now the 3 helpers exist but are not called. This task wires them between the existing funding-block check and the max-concurrent check.

- [ ] **Step 1: Locate the insertion point in `dispatch()`**

In `dispatch()` (~line 378-490 post-PR1), find the block after `_check_funding_block` returns and before the `max_concurrent_positions` check (~line 408 post-PR1).

- [ ] **Step 2: Insert the gate calls**

```python
async def dispatch(proposal: SignalProposal, *, session: AsyncSession, user: User) -> DispatchResult:
    ... existing code (mode check, funding-block check) ...

    # --- PR2 gates ---
    settings = get_settings()
    gate_result = _apply_mtf_gate(proposal, settings)
    if gate_result is not None:
        return gate_result
    gate_result = await _apply_short_safety_gates(proposal, settings, session=session)
    if gate_result is not None:
        return gate_result
    proposal = _maybe_tighten_short_sl(proposal, settings)
    # --- end PR2 gates ---

    ... existing code (max-concurrent, leverage, place) ...
```

**Bounds from spec:**
- Order matters: MTF gate before SHORT safety before SL tightening. MTF gate is the cheapest (no DB), so fails fastest.
- `_maybe_tighten_short_sl` returns the (possibly modified) proposal — the rest of `dispatch()` MUST use the returned value, not the original.

- [ ] **Step 3: Run the full dispatcher test suite**

Run: `cd backend && python -m pytest tests/unit/test_trading_dispatcher.py tests/unit/test_dispatcher_e2e.py tests/trading/execution/ -v --no-cov`
Expected: all tests pass — both the new PR2 tests AND existing dispatcher tests.

If existing tests fail because they don't set up `proposal.mtf_agreement`, they may need to set `MTF_MIN_AGREEMENT_1H=0` via a settings override fixture, since the default 3 now actively gates. Update those tests' settings fixture to set `MTF_MIN_AGREEMENT_1H=0` (rollback mode) so they remain testing pre-PR2 behavior of those specific code paths.

- [ ] **Step 4: Commit**

```
git add backend/app/trading/execution/dispatcher.py
git commit -m "feat(pr2): dispatch() — wire mtf_gate + short_safety + sl_tightening"
```

---

## Phase 5 — `SHORT_FUNDING_HALVE_HOLD` wiring

The dispatcher doesn't enforce hold time — exit/timeout logic does. Phase 5 traces the call graph end-to-end before wiring (spec §6.3 hard bound: no "I'll figure out the wiring as I go" per `dispatcher-outbound-telegram-was-unwired` memory).

### Task 5.1: Call-graph trace — where is hold timeout enforced?

**Files:** none (research-only)

- [ ] **Step 1: Find the current 4h cooldown / hold-time enforcement**

```
grep -rn "cooldown\|hold_time\|timeout_hours\|max_hold" backend/app/trading/ backend/app/shadow/
```

- [ ] **Step 2: Trace the call graph**

For each match, walk UP the call graph to find:
1. Who reads the configured timeout?
2. Where is the timeout actually counted/elapsed?
3. Is there a worker that polls open trades and closes them on timeout, or is it event-driven?

Report findings as a markdown table:

| File:line | Function | What it does | Hook candidate? |
|---|---|---|---|

- [ ] **Step 3: Identify the SINGLE hook location**

Based on the trace, pick exactly ONE location where the halve-hold logic lives. Options:
- (a) When the timeout is READ from config (multiply by 0.5 if flag + funding > threshold)
- (b) When the timeout is COMPARED to elapsed time (compare against 0.5×timeout when flag + funding > threshold)
- (c) When the trade is INSERTED (precompute the effective deadline and store it; close logic just compares against the stored deadline)

The implementation plan **does not** prescribe which — that decision lives in the call-graph trace output and gets reviewed by the operator before Task 5.2 proceeds.

- [ ] **Step 4: Surface findings + await operator decision**

Write the call-graph table to a temp file `tmp_pr2/phase5_callgraph.md` (gitignored — operator scratch). Surface the table + recommended hook to the operator. Wait for operator approval before Task 5.2.

### Task 5.2: Write failing test for SHORT_FUNDING_HALVE_HOLD (only after operator approves hook)

**Files:**
- Test: `backend/tests/trading/<chosen-module>/test_short_funding_halve_hold.py` (NEW — path depends on Task 5.1 outcome)

- [ ] **Step 1: Write failing tests covering 4 cases**

```python
"""PR2 SHORT_FUNDING_HALVE_HOLD — halve the max-hold timeout on SHORT
trades when funding > SHORT_FUNDING_HALVE_THRESHOLD_PCT.

Cases:
  1. Flag OFF (default): hold timeout unchanged regardless of funding.
  2. Flag ON, direction=LONG: timeout unchanged.
  3. Flag ON, direction=SHORT, funding BELOW threshold: timeout unchanged.
  4. Flag ON, direction=SHORT, funding ABOVE threshold: timeout halved.
"""
# Implementation depends on the hook chosen in Task 5.1.
# Test shape is concrete; test target is parameterized on the hook.
```

- [ ] **Step 2: Run test to verify it fails**

Expected: ImportError or AttributeError on the new function.

### Task 5.3: Implement at the chosen hook

**Files:**
- Modify: chosen module from Task 5.1

- [ ] **Step 1: Implement the logic**

Pattern (sketch — adapts to chosen hook):

```python
def effective_max_hold_hours(
    direction: Direction,
    base_max_hold_hours: float,
    funding_rate_pct: float | None,
    settings: Settings,
) -> float:
    """Spec §4.2 F-1. Halve max-hold for SHORT trades when funding pressure
    is high enough that holding into a positive-funding regime burns alpha.
    """
    if not settings.SHORT_FUNDING_HALVE_HOLD:
        return base_max_hold_hours
    if direction != "SHORT":
        return base_max_hold_hours
    if funding_rate_pct is None:
        return base_max_hold_hours  # fail-open on missing data
    if funding_rate_pct <= settings.SHORT_FUNDING_HALVE_THRESHOLD_PCT:
        return base_max_hold_hours
    return base_max_hold_hours / 2.0
```

- [ ] **Step 2: Wire into the chosen call site**

Replace the current `base_max_hold_hours` use with `effective_max_hold_hours(...)`.

- [ ] **Step 3: Run the 4 new tests**

Expected: all pass.

- [ ] **Step 4: Run the broader exit-logic test suite — no regressions**

Run: `cd backend && python -m pytest tests/<chosen-area>/ -v --no-cov`
Expected: pass.

- [ ] **Step 5: Commit**

```
git add backend/app/trading/<chosen-module>/ backend/tests/...
git commit -m "feat(pr2): SHORT_FUNDING_HALVE_HOLD wired at <chosen-module> per Task 5.1 trace"
```

---

## Phase 6 — Telegram-approve uniformity

The gate MUST apply identically to the auto path AND the telegram-approve path (spec §6.3 hard bound — R3 mitigation).

### Task 6.1: Telegram-approve path call-graph trace

**Files:** none (research-only)

- [ ] **Step 1: Trace the approve path end-to-end**

```
grep -rn "approve\|_place_approved_order\|callback_query" backend/app/ops/telegram_polling.py
```

- [ ] **Step 2: Identify the function that places the approved order**

Walk the code from "user taps Approve in Telegram" to "Binance order placed". Note:
- Does the approve path construct a `SignalProposal` and call `dispatch()`? **If yes → Option U1 (preferred) — gate already applies via the shared `dispatch()` path.**
- Does the approve path place the order directly (bypassing `dispatch()`)? **If yes → Option U2 — duplicate the gate call inline.**

- [ ] **Step 3: Surface findings to operator**

Write the call-graph to `tmp_pr2/phase6_callgraph.md`. Surface to operator. The decision between U1 (refactor approve path to use `dispatch()`) and U2 (inline duplicate gate) is operator-approved before Task 6.2 proceeds.

### Task 6.2: Wire the gate in the telegram path

**Files:**
- Modify: `backend/app/ops/telegram_polling.py` (or the function from Task 6.1)

**If U1 chosen:**

- [ ] **Step 1: Refactor `_place_approved_order` to construct a SignalProposal + call `dispatch()`**

The approve callback constructs the proposal from `telegram_signals.payload` (matching PR1's PR1-payload-builders pattern), then calls `dispatch()`. If dispatch returns a `blocked_*` outcome, return that as the telegram callback response. If `emitted`, proceed as before.

- [ ] **Step 2: Audit that no FU-4/5/6 data-integrity gap widens**

Per spec §6.4: confirm `inputs_hash`, `layer_summary`, `user_id` flow continues unchanged. The payload-construction path may need to read the PR1 MTF fields from `telegram_signals.payload` — if they aren't there, file a FU and stop.

**If U2 chosen:**

- [ ] **Step 1: Inline the gate check before order placement**

```python
async def _place_approved_order(payload: dict, ...) -> ...:
    ... existing payload parsing ...
    proposal = _build_proposal_from_payload(payload)  # PR1 builder

    settings = get_settings()
    gate_result = _apply_mtf_gate(proposal, settings)
    if gate_result is not None:
        await _reply_to_user(f"Trade blocked by MTF gate: {gate_result.outcome}")
        return
    short_result = await _apply_short_safety_gates(proposal, settings, session=session)
    if short_result is not None:
        await _reply_to_user(f"Trade blocked: {short_result.outcome}")
        return
    proposal = _maybe_tighten_short_sl(proposal, settings)

    ... existing order placement ...
```

### Task 6.3: Write the uniformity integration test

**Files:**
- Test: `backend/tests/integration/test_pr2_telegram_approve_uniformity.py` (NEW)

- [ ] **Step 1: Write end-to-end test**

```python
"""PR2 telegram-approve uniformity — gate applies identically to auto + telegram paths."""

async def test_low_mtf_signal_via_telegram_approve_is_blocked():
    """An auto-path proposal that would emit (high MTF) emits identically
    via telegram-approve. A low-MTF proposal that blocks via auto also
    blocks via telegram-approve."""
    # ... arrange shared SignalProposal with mtf_agreement=2 ...
    # Auto path:
    auto_result = await dispatch(proposal, session=s, user=u_auto)
    # Telegram approve path (simulating Approve callback):
    telegram_result = await _place_approved_order(payload, ...)
    # Assert both block, both with same outcome:
    assert auto_result.outcome == "blocked_mtf_low_agreement"
    assert telegram_result.outcome == "blocked_mtf_low_agreement"


async def test_high_mtf_signal_via_telegram_approve_emits():
    """High-MTF signal: emits identically on both paths."""
    # ... ditto with mtf_agreement=5 ...
```

- [ ] **Step 2: Run + verify**

Run: `cd backend && python -m pytest tests/integration/test_pr2_telegram_approve_uniformity.py -v`
Expected: 2 passed.

- [ ] **Step 3: Commit**

```
git add backend/app/ops/telegram_polling.py backend/tests/integration/test_pr2_telegram_approve_uniformity.py
git commit -m "feat(pr2): telegram-approve — uniform gate application (R3 mitigation)"
```

---

## Phase 7 — Payload persistence

`live_trades.mtf_*` columns exist (PR1) but are NULL on all rows. Phase 7 populates them on the auto path AND the telegram path.

### Task 7.1: Write failing golden-dict test

**Files:**
- Modify: `backend/tests/db/test_payload_builders.py`

- [ ] **Step 1: Add a new golden-dict test case**

```python
def test_build_live_trade_payload_with_mtf_populated():
    """When proposal.mtf_* is populated, the payload includes the 3 MTF cols."""
    proposal = make_signal_proposal(
        mtf_agreement=4,
        mtf_dominant_tf="1h",
        mtf_directions={"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": 0},
    )
    order = make_order_result()
    payload = build_live_trade_payload(
        proposal, order,
        user_id=1, approved_via="auto", mode_at_open="fully-auto",
        margin_usdt=100.0, leverage=10, opened_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
    )
    assert payload["mtf_agreement"] == 4
    assert payload["mtf_dominant_tf"] == "1h"
    # Canonical JSON: sort_keys=True, separators=(",", ":")
    assert payload["mtf_directions_json"] == \
        '{"15m":1,"1d":-1,"1h":1,"1w":0,"4h":1,"5m":1}'


def test_build_live_trade_payload_with_mtf_none_pr1_compat():
    """When proposal.mtf_* is None (PR1 fallback), the 3 MTF cols are None.
    Golden assertion ensures bit-identical with the PR1 NULL case."""
    proposal = make_signal_proposal()  # default mtf_*=None
    payload = build_live_trade_payload(proposal, ...)
    assert payload["mtf_agreement"] is None
    assert payload["mtf_dominant_tf"] is None
    assert payload["mtf_directions_json"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Expected: the populated-MTF test fails because the builder doesn't yet read from `proposal.mtf_*`.

### Task 7.2: Extend `build_live_trade_payload`

**Files:**
- Modify: `backend/app/db/payload_builders.py`

- [ ] **Step 1: Add MTF fields to the returned dict**

```python
def build_live_trade_payload(
    proposal: SignalProposal,
    order: OrderResult,
    *,
    user_id: int,
    approved_via: Literal["auto", "telegram"],
    mode_at_open: str,
    extra_reasoning: dict[str, Any] | None = None,
    margin_usdt: float,
    leverage: int,
    opened_at: datetime,
) -> dict[str, Any]:
    return {
        ... existing fields ...,
        # PR2: MTF persistence (PR1 added the columns; this PR populates them)
        "mtf_agreement": proposal.mtf_agreement,
        "mtf_dominant_tf": proposal.mtf_dominant_tf,
        "mtf_directions_json": (
            json.dumps(proposal.mtf_directions, sort_keys=True, separators=(",", ":"))
            if proposal.mtf_directions is not None else None
        ),
        # Other 4 PR1 analytics fields (p_win, effective_score,
        # realized_vol_20d, funding_directional_adj) stay NULL on
        # live_trades in PR2 — spec D8 hard bound. Out of scope.
    }
```

- [ ] **Step 2: Run the 2 new tests**

Expected: both pass.

- [ ] **Step 3: Run the full payload-builders test suite**

Run: `cd backend && python -m pytest tests/db/test_payload_builders.py -v`
Expected: all pass — PR1 golden cases (with `mtf_*=None`) plus the 2 new ones.

- [ ] **Step 4: Run the replay-identity test — no regressions**

Run: `cd backend && python -m pytest tests/db/test_audit_replay_identity.py -v`
Expected: pass. The 3 new MTF fields are in `NON_HASHED_ALLOW_LIST` (already classified by PR1), so they don't affect the hash chain.

- [ ] **Step 5: Commit**

```
git add backend/app/db/payload_builders.py backend/tests/db/test_payload_builders.py
git commit -m "feat(pr2): build_live_trade_payload — populate mtf_* from proposal"
```

---

## Phase 8 — Bench gate (V-7 same budgets as PR1)

### Task 8.1: Extend `bench_aggregator_latency.py`

**Files:**
- Modify: `backend/scripts/bench_aggregator_latency.py`

- [ ] **Step 1: Add 2 new CLI modes**

```python
parser.add_argument(
    "--mtf-gate-enabled", action="store_const", dest="mode",
    const="mtf-gate-enabled",
    help="Run dispatch() with MTF_MIN_AGREEMENT_1H=3 (PR2 default)",
)
parser.add_argument(
    "--mtf-gate-disabled", action="store_const", dest="mode",
    const="mtf-gate-disabled",
    help="Run dispatch() with MTF_MIN_AGREEMENT_1H=0 (PR1 behavior, gate bypassed)",
)
```

- [ ] **Step 2: Wire the mode into the timing loop**

For both new modes, the timing loop calls `dispatch()` on a fixed BTCUSDT proposal with mocked order placement (mock `_place_live_order` to return a fixed `OrderResult` instantly). The mode toggles `MTF_MIN_AGREEMENT_1H` via env var or settings override.

- [ ] **Step 3: Smoke-run both modes**

```
python backend/scripts/bench_aggregator_latency.py --mtf-gate-disabled --n=500
python backend/scripts/bench_aggregator_latency.py --mtf-gate-enabled --n=500
```

Expected: each completes in <10s. Output JSON with `p50_ms`, `p99_ms`, etc.

### Task 8.2: Run the gate

**Files:** none (gate is a measurement)

- [ ] **Step 1: Run both modes and capture JSON**

Save both outputs to `tmp_pr2/bench_disabled.json` and `tmp_pr2/bench_enabled.json` (operator scratch — gitignored).

- [ ] **Step 2: Compute deltas**

```
delta_p50 = enabled.p50_ms - disabled.p50_ms
delta_p99 = enabled.p99_ms - disabled.p99_ms
```

- [ ] **Step 3: V-7 gate check**

Pass criteria (same as PR1):
- `delta_p50 ≤ 50ms`
- `delta_p99 ≤ 200ms`

Expected: sub-1ms delta. The gate is 3 boolean checks + 1 dict lookup + 1 optional DB query (borrow lookup) — trivial compared to the MTF compute itself (in baseline).

- [ ] **Step 4: If gate fails, STOP**

Per spec §6.5: "If either gate fails → STOP, surface to operator, redesign before merge." Investigate before any further work.

- [ ] **Step 5: Record numbers for the PR description**

The PR open task (Task 9.4) reads these numbers.

- [ ] **Step 6: Commit**

```
git add backend/scripts/bench_aggregator_latency.py
git commit -m "bench(pr2): add --mtf-gate-enabled / --mtf-gate-disabled modes (V-7 gate same as PR1)"
```

---

## Phase 9 — Docs + PR open

### Task 9.1: Update `ARCHITECTURE.md`

**Files:**
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Append a new subsection 10c**

Append after PR1's `## 10b. Math accuracy upgrades (PR1)`:

```markdown
## 10c. MTF gate + SHORT safety (PR2)

**Files**: `app/trading/execution/dispatcher.py` (3 new helpers),
`app/config.py` (5 flags + 4 thresholds) — added 2026-05-XX.

PR2 enables PR1's recording-only MTF data as an active dispatch gate.

| Helper | Fires when | Outcome |
|---|---|---|
| `_apply_mtf_gate` | `mtf_agreement < MTF_MIN_AGREEMENT_1H` (3 by default) | `blocked_mtf_low_agreement` |
| `_apply_mtf_gate` (veto) | 1d AND 1w both vote opposite + `MTF_HIGHER_TF_VETO=True` | `blocked_mtf_higher_tf_veto` |
| `_apply_short_safety_gates` | SHORT + borrow APR > 10% + `SHORT_VETO_HIGH_BORROW=True` | `blocked_short_high_borrow` |
| `_maybe_tighten_short_sl` | SHORT + mtf_agreement < 5 + `SHORT_TIGHTEN_SL_LOW_MTF=True` | (modifies SL, doesn't block) |
| `effective_max_hold_hours` | SHORT + funding > 0.05%/8h + `SHORT_FUNDING_HALVE_HOLD=True` | (halves timeout) |

**Rollback**: `MTF_MIN_AGREEMENT_1H=0` bypasses the gate entirely.
All SHORT flags default OFF.

**Persistence**: `live_trades.mtf_agreement`, `mtf_dominant_tf`,
`mtf_directions_json` now populated on dispatched trades.
PR1 added the columns; PR2 populates them.
```

- [ ] **Step 2: Update Dispatcher outcome table**

Find the `DispatchOutcome` enumeration table (if one exists in ARCHITECTURE.md). Append 3 new rows:
- `blocked_mtf_low_agreement`
- `blocked_mtf_higher_tf_veto`
- `blocked_short_high_borrow`

- [ ] **Step 3: Commit**

```
git add docs/ARCHITECTURE.md
git commit -m "docs(architecture): PR2 MTF gate + SHORT safety section + 3 new DispatchOutcome literals"
```

### Task 9.2: Self-review checklist

Before opening the PR, verify each:

- [ ] All flags default per spec §6.1 — confirmed by `test_pr2_settings_defaults.py`.
- [ ] `MTF_MIN_AGREEMENT_1H=0` rollback path tested — confirmed.
- [ ] Telegram-approve path applies same gate as auto — confirmed by `test_pr2_telegram_approve_uniformity.py`.
- [ ] No score/symmetry knob touched (spec §6.2):
  - `grep -n "_SHORT_DIRECTION_PENALTY" backend/app/core/scoring/aggregator.py` shows `1.0` unchanged
  - `grep -n "SHORT_BIAS_PP" backend/app/core/scoring/tiers.py` shows `0.0` unchanged
  - Shadow LONG/SHORT thresholds unchanged (`backend/app/shadow/engine.py`)
- [ ] V-7 latency gate PASSED — `delta_p50 ≤ 50ms` AND `delta_p99 ≤ 200ms`.
- [ ] `test_audit_replay_identity.py` still passes (no new hash-chain columns).
- [ ] No new alembic migration (PR2 is config + code, no schema change).
- [ ] No new workers (PR2 reuses PR1's MTF cache prewarm + refresh).
- [ ] mypy clean (404+ source files).
- [ ] ruff clean.
- [ ] No `--no-verify` git commits anywhere.
- [ ] No direct pushes to `main`. All commits on `feat/pr2-mtf-gate`.
- [ ] Untracked files (`HANDOVER.md`, `populate_universe.py`, `secrets.enc`, `tmp_*/`) NOT in any commit.
- [ ] FU-4/5/6 telegram-path data-integrity gaps NOT made worse (spec §6.4 verification).

### Task 9.3: Push the branch

**Files:** none

- [ ] **Step 1: Push**

```
git push -u origin feat/pr2-mtf-gate
```

### Task 9.4: Open the PR

**Files:** none

- [ ] **Step 1: Open the PR with full body**

```bash
gh pr create --base dev --head feat/pr2-mtf-gate \
  --title "feat(pr2): MTF gate enabled + SHORT-side safety flags wired" \
  --body "$(cat <<'EOF'
## Summary

PR2 of the 5-PR Option D rollout. **First behavior-change PR.**
Converts PR1's recording-only MTF infrastructure into an active
dispatcher gate. Wires 3 SHORT-side safety branches default-OFF.

Default flag values:
- \`MTF_MIN_AGREEMENT_1H=3\` (was 0 in PR1 = bypass) — **the one behavior flip**
- \`MTF_HIGHER_TF_VETO=True\` (no-op in PR1 since gate was off)
- \`SHORT_FUNDING_HALVE_HOLD=False\`
- \`SHORT_TIGHTEN_SL_LOW_MTF=False\`
- \`SHORT_VETO_HIGH_BORROW=False\`

Per-environment override via env vars for all 5 flags.

### What this PR does NOT change
- ❌ \`final_score\` math unchanged
- ❌ \`_SHORT_DIRECTION_PENALTY=1.0\` unchanged (spec §6.2 hard bound)
- ❌ \`SHORT_BIAS_PP=0.0\` unchanged (spec §6.2 hard bound)
- ❌ Shadow LONG/SHORT thresholds unchanged (still symmetric since PR #121)
- ❌ ±0.05 NEUTRAL band unchanged
- ❌ No alembic migration (PR2 is config + code only)
- ❌ No new workers (reuses PR1's MTF cache)
- ❌ \`p_win\` / \`effective_score\` / \`realized_vol_20d\` / \`funding_directional_adj\`
  STAY NULL on \`live_trades\` (spec D8 — out of scope for PR2)

### Latency Gate (V-7) — same budgets as PR1
| Mode | p50 | p99 |
|---|---|---|
| \`--mtf-gate-disabled\` (baseline) | _MS ms | _MS ms |
| \`--mtf-gate-enabled\` (active) | _MS ms | _MS ms |
| **delta** | _MS ms (budget 50) | _MS ms (budget 200) |

[Replace _MS with actual numbers from Task 8.2.]

### Rollback
- Stage 1 (no code change): set \`MTF_MIN_AGREEMENT_1H=0\` in env → gate bypassed.
- Stage 2 (full revert): \`git revert <merge-commit>\`. No alembic downgrade needed.

### Telegram-approve uniformity
Per spec §6.3 hard bound, the gate applies identically to auto AND
telegram-approve paths. Confirmed by \`test_pr2_telegram_approve_uniformity.py\`.

## Test plan
- [ ] Backend CI green (mypy + ruff + alembic + pytest)
- [ ] Frontend CI green (no schema/API impact expected)
- [ ] docker-compose-smoke CI green
- [ ] V-7 latency gate PASSED locally
- [ ] Manual operator review of full diff
- [ ] 5+ day staging soak with \`MTF_MIN_AGREEMENT_1H=3\` (spec §9 exit criteria)
- [ ] Shadow stats during soak: signal rate drops 30-40%, win rate flat or improving

## Linked
- Spec: \`docs/superpowers/specs/2026-05-17-pr2-mtf-gate-and-short-safety-design.md\`
- Plan: \`docs/superpowers/plans/2026-05-17-pr2-mtf-gate-and-short-safety.md\`
- Master rollout plan: \`docs/superpowers/specs/2026-05-17-master-rollout-plan-option-d.md\`
- Predecessor: PR #169 (PR1 record-only foundation)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Wait for CI green**

Run: `gh pr checks <PR_NUMBER> --watch`
Expected: all 3 checks (backend, frontend, docker-compose-smoke) green within ~7 min.

- [ ] **Step 3: Report PR number to operator + STOP**

DO NOT auto-merge to dev. DO NOT auto-promote to main. Per `dev-prod-branch-workflow` memory, both gates require explicit free-text "ship it" / "merge it" in the current operator turn.

---

## Phase 10 — Post-merge staging soak (operator action)

Not an agent phase. Documented here for completeness.

**Operator runs:**
1. Merge PR2 to dev (CI green is necessary but not sufficient).
2. Auto-deploy to staging.
3. Set staging env: `MTF_MIN_AGREEMENT_1H=3` (the default — confirms env override path works).
4. Optionally set staging-only: any of the SHORT_* flags to True for testing.
5. 5+ day soak with shadow stats monitored.

**Watch for:**
- Signal rate drops 30-40% (false positives filtered) — if drops <20% the threshold may be too permissive; if drops >50% may be too strict.
- Win rate flat or improving — if win rate drops, threshold may not be filtering the right signals.
- Zero new `auth_violations` chain_broken alerts (existing 559 are FU-2 pre-existing).
- New `DispatchOutcome` values appear in dispatcher logs: `blocked_mtf_low_agreement`, `blocked_mtf_higher_tf_veto`.

**Surface to operator after 5 days**: shadow stats summary + recommended next action (proceed to dev→main, tune threshold, or rollback).

---

## Out-of-scope reminders (do NOT add)

- ❌ 15m shadow lane (PR3)
- ❌ Multi-resolution shadow / SHADOW_NARROW_UNIVERSE (PR3)
- ❌ Outcome-adaptive cooldown (PR8)
- ❌ Dynamic position sizing (PR9)
- ❌ Self-healing supervisor / FU-1/FU-2/FU-3 remediation (PR9)
- ❌ Changes to `final_score` math or any score-formula knob
- ❌ New analytics fields on `live_trades` beyond the 3 MTF fields
- ❌ New worker tasks
- ❌ p_win model fitting (PR5)
- ❌ Frontend changes (PR2 is pure backend)
- ❌ Closing FU-4/5/6 inline (those are their own follow-ups; spec §6.4 only requires PR2 doesn't widen the gaps)
- ❌ Closing FU-9 (httpx hygiene) inline (queued separately)

---

## Self-review checklist (for the implementing engineer)

Before opening the PR, verify each:

- [ ] Phase 1 — settings defaults match spec §6.1 exactly.
- [ ] Phase 2 — `SignalProposal` 3 new fields default None.
- [ ] Phase 3 — `proposal_from_prediction` threads cleanly + fail-open JSON parse.
- [ ] Phase 4 — 3 helpers added + wired in `dispatch()` in correct order.
- [ ] Phase 5 — `SHORT_FUNDING_HALVE_HOLD` hook chosen via call-graph trace + operator-approved.
- [ ] Phase 6 — telegram-approve path call-graph traced + uniformity test passes.
- [ ] Phase 7 — `live_trades.mtf_*` populated on dispatched trades; replay-identity still passes.
- [ ] Phase 8 — V-7 bench gate passed.
- [ ] Phase 9 — ARCHITECTURE.md updated + PR description carries V-7 numbers.

**Stop conditions** (no PR open):
- V-7 gate fails.
- Any score/symmetry knob accidentally touched.
- Telegram-approve uniformity test fails.
- A 4th onion layer surfaces (per the PR1 pattern) — surface, do not auto-fix.
