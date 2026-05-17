# FU-1 — Worker Heartbeats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the observability gap that allowed the 2026-05-11 live_worker prediction silence to go undetected for 6 days. Wire `record_heartbeat` into 14 workers (9 currently silent + 5 defense-in-depth), resolve FU-15 (single-shot watchdog classification), no schema migrations.

**Architecture:** Each touched worker gains one `await record_heartbeat(session_factory, WORKER_NAME, status=, details=)` call inside its main loop. The watchdog gains a `single_shot` WorkerSpec field + matching state machine branch for tasks that fire-once-and-exit (mtf_cache_prewarm_task). `pending_heartbeat=True` flags drop to False as each worker's heartbeat lands.

**Tech Stack:** Python 3.12 / SQLAlchemy 2.0 async / pytest + pytest-asyncio. Reuses existing `app.ops.heartbeat.record_heartbeat` helper (battle-tested by `ws_keepalive_task`, `mtf_cache_ttl_refresh_task`, `worker_watchdog_task`, `scanner_batch_task`, `prediction_validator_task`).

**Source spec:** `docs/superpowers/specs/2026-05-17-fu-1-worker-heartbeats-design.md`

**Branch:** `feat/fu-1-worker-heartbeats-impl` off `dev` (NEVER push to `main`).

---

## File Structure (locked in via spec)

### NEW files

| Path | Responsibility |
|---|---|
| `backend/tests/ops/test_record_heartbeat_per_worker.py` | Per-worker smoke test: each worker's tick function calls `record_heartbeat` with the correct `WORKER_NAME` |
| `backend/tests/ops/test_worker_watchdog_single_shot.py` | FU-15 state machine: `single_shot_completed` / `single_shot_never_completed` / `starting` (within grace) |
| `backend/tests/integration/test_fu1_heartbeat_propagation.py` | Postgres roundtrip: each worker's tick writes a `worker_heartbeats` row |

### MODIFIED files

| Path | Reason |
|---|---|
| `backend/app/ops/worker_registry.py` | Add `single_shot: bool = False` to `WorkerSpec`. Drop `pending_heartbeat=True` from 9 H-workers as their heartbeats land. Set `single_shot=True` + `liveness_query=HEARTBEAT` + `pending_heartbeat=False` for `mtf_cache_prewarm_task`. |
| `backend/app/ops/worker_watchdog.py` | Extend state machine: `single_shot_completed` (non-alarming), `single_shot_never_completed` (alarming after startup grace), `starting` (non-alarming within grace). |
| `backend/app/ws/live_prediction.py` | H1: heartbeat in `run_live_prediction` candle loop |
| `backend/app/shadow/worker.py` | H2: heartbeat in `_handle_candle` |
| `backend/app/ops/verifier_scheduler.py` | H3: heartbeat in `audit_verifier_task` loop |
| `backend/app/trading/execution/liquidation_monitor.py` | H4: heartbeat in 30s poll loop |
| `backend/app/ops/telegram_polling.py` | H5: heartbeat in long-poll loop |
| `backend/app/trading/auto_promote.py` | H6: heartbeat in daily tick |
| `backend/app/news/ingest_worker.py` | H7: heartbeat in `news_cleanup_task` nightly tick + N1: heartbeat in `news_ingest_task` per-iteration |
| `backend/app/data/intermarket_worker.py` | H8: heartbeat in `intermarket_cleanup_task` nightly tick + N2: heartbeat in `intermarket_snapshot_task` per-iteration |
| `backend/app/core/scoring/mtf_confluence.py` | H9: ONE heartbeat at end of `prewarm_cache` with `status="single_shot_completed"`, `details={"prewarmed_count": N, "duration_s": X}` |
| `backend/app/shadow/universe_refresh.py` | N3: heartbeat in daily tick |
| `backend/app/data/universe_sync.py` | N4: heartbeat in daily tick |
| `backend/app/data/adapter_health.py` | N5: heartbeat in 5-min tick |
| `backend/tests/unit/test_worker_registry_consistency.py` | Add assertion: after FU-1, 0 entries have `pending_heartbeat=True` (single-shot uses `single_shot=True` instead) |
| `backend/tests/unit/test_worker_watchdog.py` | Extend with single_shot scenarios |
| `backend/docs/KNOWN_ISSUES.md` | Mark FU-1 as **CLOSED** + FU-15 as **CLOSED** |

### DELETED files

None.

---

## Phase 0 — Branch + setup

### Task 0: Create feature branch off dev

**Files:** none

- [ ] **Step 1: Verify on correct branch + clean tree**

Run: `git branch --show-current && git status --short`
Expected: `feat/fu-1-worker-heartbeats-impl` (already created), nothing modified in this worktree.

- [ ] **Step 2: Confirm mypy + ruff baseline**

Run: `cd backend && python -m ruff check . && python -m mypy app 2>&1 | tail -1`
Expected: `All checks passed!` and `Success: no issues found in 404+ source files`.

---

## Phase 1 — WorkerSpec.single_shot + watchdog state machine (FU-15)

Foundation phase. Other phases depend on `single_shot` field existing.

### Task 1.1: Write failing test for `single_shot_completed` state

**Files:**
- Test: `backend/tests/ops/test_worker_watchdog_single_shot.py` (NEW)

- [ ] **Step 1: Write failing tests**

```python
"""FU-15: watchdog state machine handles single-shot workers correctly.

A single_shot=True worker fires once at startup and exits clean. It
should NOT be classified as 'no_signal' (which is alarming). Instead:
  - During startup grace period: 'starting' (non-alarming)
  - After clean exit (one heartbeat with status='single_shot_completed'):
    'single_shot_completed' (non-alarming)
  - After grace period with no heartbeat: 'single_shot_never_completed'
    (ALARMING — task crashed or hung)
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.ops.worker_registry import WorkerSpec, HEARTBEAT
from app.ops.worker_watchdog import check_all_workers


@pytest.fixture
def single_shot_spec():
    return WorkerSpec(
        name="test_single_shot",
        description="test",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=10 * 60,
        stateful=False,
        single_shot=True,
    )


async def test_single_shot_completed_non_alarming(single_shot_spec, ...):
    """Heartbeat with status=single_shot_completed → state=single_shot_completed (non-alarming)."""
    # ... mock session_factory + worker_heartbeats row with status='single_shot_completed' ...
    # statuses = await check_all_workers(sf, registry=(single_shot_spec,))
    # assert statuses[0]['state'] == 'single_shot_completed'
    # assert 'single_shot_completed' not in BAD_STATES  # imported from worker_watchdog


async def test_single_shot_starting_within_grace(single_shot_spec):
    """No heartbeat yet, container uptime < startup_grace → state=starting."""
    # ...


async def test_single_shot_never_completed_after_grace(single_shot_spec):
    """No heartbeat, container uptime > startup_grace → state=single_shot_never_completed (ALARMING)."""
    # ...
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `cd backend && python -m pytest tests/ops/test_worker_watchdog_single_shot.py -v`
Expected: `AttributeError: 'WorkerSpec' has no attribute 'single_shot'` or similar import error.

### Task 1.2: Add `single_shot` field to WorkerSpec

**Files:**
- Modify: `backend/app/ops/worker_registry.py`

- [ ] **Step 1: Add field**

Insert into `WorkerSpec` after `pending_heartbeat`:

```python
    # FU-15: True for tasks that fire once at startup, exit clean, and never
    # heartbeat again. The watchdog reports such tasks as
    # 'single_shot_completed' (non-alarming) instead of 'no_signal'.
    # mtf_cache_prewarm_task is the canonical example.
    single_shot: bool = False
```

Run: `cd backend && python -m pytest tests/ops/test_worker_watchdog_single_shot.py -v`
Expected: import succeeds; tests still FAIL because watchdog state machine doesn't handle `single_shot` yet.

### Task 1.3: Update watchdog state machine

**Files:**
- Modify: `backend/app/ops/worker_watchdog.py`

- [ ] **Step 1: Locate the per-worker classification block**

```
grep -n "pending_heartbeat\|state.*=\|bad_states\|BAD_STATES" backend/app/ops/worker_watchdog.py
```

- [ ] **Step 2: Add single_shot branch**

Inside `check_all_workers`, before the `pending_heartbeat` branch:

```python
if spec.single_shot:
    # FU-15: single-shot task watchdog logic.
    if last_status == "single_shot_completed":
        entry["state"] = "single_shot_completed"  # non-alarming
    elif last_beat_at is None:
        # No heartbeat yet — startup grace period?
        # Use a 5-minute startup grace (container ramp + first task run).
        if _container_uptime_seconds() < 5 * 60:
            entry["state"] = "starting"  # non-alarming
        else:
            entry["state"] = "single_shot_never_completed"  # ALARMING
    else:
        entry["state"] = "single_shot_failed"  # ALARMING (unexpected status)
    return entry
```

Add `single_shot_completed` and `starting` to the non-alarming-states tuple. Add `single_shot_never_completed` and `single_shot_failed` to the `BAD_STATES` tuple.

- [ ] **Step 3: Run tests — expect PASS**

Run: `cd backend && python -m pytest tests/ops/test_worker_watchdog_single_shot.py -v`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```
git add backend/app/ops/worker_registry.py backend/app/ops/worker_watchdog.py backend/tests/ops/test_worker_watchdog_single_shot.py
git commit -m "feat(watchdog): single_shot worker state (closes FU-15)"
```

---

## Phase 2 — Heartbeat wiring (H-workers + N-workers)

Each task follows the canonical TDD pattern. The plan body covers the canonical pattern once + a per-worker file:line + hook-location table. Each commit is one worker.

### Canonical TDD pattern (apply to every worker H1-H9 + N1-N5)

For worker `<NAME>` in module `<MODULE>` with main-loop function `<FN>`:

- [ ] **Step 1: Write failing test**

In `backend/tests/ops/test_record_heartbeat_per_worker.py` (file accumulates tests as workers land):

```python
@pytest.mark.asyncio
async def test_<NAME>_records_heartbeat(sqlite_session_factory):
    """After one tick, <NAME> writes a row to worker_heartbeats."""
    from app.<MODULE> import <FN>  # adjust import per worker
    # Mock minimal context (HTTP, exchange, etc.) — keep dependency surface tight
    with patch(...), patch(...):
        await <FN>(sqlite_session_factory)
    # Assert heartbeat row exists with WORKER_NAME='<NAME>'
    async with sqlite_session_factory() as s:
        row = (await s.execute(sa.text(
            "SELECT worker_name, last_status FROM worker_heartbeats WHERE worker_name = :n"
        ), {"n": "<NAME>"})).first()
    assert row is not None
    assert row.worker_name == "<NAME>"
    assert row.last_status in ("ok", "error", "single_shot_completed")
```

- [ ] **Step 2: Run — expect FAIL (heartbeat not yet wired)**

- [ ] **Step 3: Wire heartbeat in worker's main loop**

```python
from app.ops.heartbeat import record_heartbeat

WORKER_NAME: str = "<name>"  # define at module top if not already present

async def <main_loop_fn>(session_factory, ...):
    ...
    while True:
        try:
            result = await do_one_tick(...)
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="ok", details={"work_done": result},
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("%s tick failed: %s", WORKER_NAME, e)
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="error", details={"error": str(e)[:200]},
            )
        await asyncio.sleep(interval_s)
```

**Bounds (apply universally):**
- Heartbeat fires on BOTH success AND failure (status="ok" vs "error")
- `record_heartbeat` is already best-effort wrapped — no try around the call
- Use existing `WORKER_NAME` constant or define one matching `worker_registry.py`'s entry
- `session_factory` already in scope (every worker takes it)

- [ ] **Step 4: Drop `pending_heartbeat=True` from worker_registry.py entry** (H-workers only)

- [ ] **Step 5: Run test — expect PASS**

- [ ] **Step 6: Commit**

```
git commit -m "feat(<area>): wire heartbeat in <name> (closes FU-1 for this worker)"
```

### Per-worker target table

| Task | Worker | Module | Main-loop function (locate exact) | Hook position | Flag change |
|---|---|---|---|---|---|
| 2.1 H1 | `live_worker` | `backend/app/ws/live_prediction.py` | `run_live_prediction` | After each closed candle processed (inside `async for candle in stream.stream():` after persist) | drop `pending_heartbeat=True` |
| 2.2 H2 | `shadow_worker` | `backend/app/shadow/worker.py` | `_handle_candle` (or `run`) | After persist completes per candle | drop `pending_heartbeat=True` |
| 2.3 H3 | `audit_verifier_task` | `backend/app/ops/verifier_scheduler.py` | `_loop` or equivalent | After each `_check_all_chains` run | drop `pending_heartbeat=True` |
| 2.4 H4 | `liquidation_monitor_task` | `backend/app/trading/execution/liquidation_monitor.py` | poll loop | After each 30s poll cycle | drop `pending_heartbeat=True` |
| 2.5 H5 | `telegram_poller_task` | `backend/app/ops/telegram_polling.py` | long-poll loop | After each `getUpdates` returns | drop `pending_heartbeat=True` |
| 2.6 H6 | `auto_promote_task` | `backend/app/trading/auto_promote.py` | daily tick | After each tick | drop `pending_heartbeat=True` |
| 2.7 H7 | `news_cleanup_task` | `backend/app/news/ingest_worker.py` (or sibling cleanup file) | nightly tick | After cleanup completes | drop `pending_heartbeat=True` |
| 2.8 H8 | `intermarket_cleanup_task` | `backend/app/data/intermarket_worker.py` (or sibling) | nightly tick | After cleanup completes | drop `pending_heartbeat=True` |
| 2.9 H9 | `mtf_cache_prewarm_task` | `backend/app/core/scoring/mtf_confluence.py` | `prewarm_cache` | ONE heartbeat on clean exit with `status="single_shot_completed"`, `details={"prewarmed_count": N, "duration_s": X}` | `pending_heartbeat=False`, `single_shot=True`, `liveness_query=HEARTBEAT` (was None) |
| 2.10 N1 | `news_ingest_task` | `backend/app/news/ingest_worker.py` | ingest loop | After each adapter pass | (no flag change — already not pending_heartbeat) |
| 2.11 N2 | `intermarket_snapshot_task` | `backend/app/data/intermarket_worker.py` | snapshot loop | After each batch (success OR failure) | (no flag change) |
| 2.12 N3 | `universe_refresh_task` | `backend/app/shadow/universe_refresh.py` | daily tick | After refresh completes | (no flag change) |
| 2.13 N4 | `universe_sync_task` | `backend/app/data/universe_sync.py` | daily tick | After sync completes | (no flag change) |
| 2.14 N5 | `health_pinger_task` | `backend/app/data/adapter_health.py` | 5-min tick | After ping cycle | (no flag change) |

Each task is one commit. 14 commits total for Phase 2.

---

## Phase 3 — Tests (integration + consistency)

### Task 3.1: Postgres heartbeat-propagation integration test

**Files:**
- Test: `backend/tests/integration/test_fu1_heartbeat_propagation.py` (NEW)

- [ ] **Step 1: Write test**

```python
"""FU-1: every active worker writes a worker_heartbeats row within its
max_staleness_seconds window after one tick on Postgres.

CI-only (Postgres required). Skips on SQLite via the postgres_engine
fixture pattern from test_pr1_migration.py.
"""
import pytest
from sqlalchemy import text

ACTIVE_WORKERS = [
    # H-workers
    "live_worker", "shadow_worker", "audit_verifier_task",
    "liquidation_monitor_task", "telegram_poller_task", "auto_promote_task",
    "news_cleanup_task", "intermarket_cleanup_task",
    "mtf_cache_prewarm_task",  # single-shot, special status
    # N-workers (defense in depth)
    "news_ingest_task", "intermarket_snapshot_task",
    "universe_refresh_task", "universe_sync_task", "health_pinger_task",
]


async def test_all_workers_can_write_heartbeat(postgres_engine):
    """Smoke-test record_heartbeat for each worker name."""
    from app.ops.heartbeat import record_heartbeat
    sf = ...  # session_factory from postgres_engine
    for name in ACTIVE_WORKERS:
        await record_heartbeat(sf, name, status="ok")
    async with postgres_engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT worker_name FROM worker_heartbeats WHERE worker_name = ANY(:names)"
        ), {"names": ACTIVE_WORKERS})).all()
    assert {r.worker_name for r in rows} == set(ACTIVE_WORKERS)
```

- [ ] **Step 2: Run + commit**

### Task 3.2: Registry consistency test — no pending_heartbeat remaining

**Files:**
- Modify: `backend/tests/unit/test_worker_registry_consistency.py`

- [ ] **Step 1: Add assertion**

```python
def test_no_pending_heartbeat_after_fu1():
    """FU-1 closure: every WorkerSpec has pending_heartbeat=False.
    Single-shot workers use single_shot=True instead."""
    from app.ops.worker_registry import WORKER_REGISTRY
    pending = [s.name for s in WORKER_REGISTRY if s.pending_heartbeat]
    assert pending == [], (
        f"Found pending_heartbeat=True workers after FU-1: {pending}. "
        "Either wire heartbeat in the worker's main loop or set "
        "single_shot=True if the task is fire-once-and-exit."
    )
```

- [ ] **Step 2: Run + commit**

---

## Phase 4 — Docs

### Task 4.1: Update KNOWN_ISSUES.md — mark FU-1 + FU-15 as closed

**Files:**
- Modify: `backend/docs/KNOWN_ISSUES.md`

- [ ] **Step 1: Add CLOSED status to FU-1 entry**

Change `**Status**: queued; high-priority operational hygiene.` to `**Status**: **CLOSED 2026-05-XX** by FU-1 implementation (this PR's merge commit).`

- [ ] **Step 2: Add CLOSED status to the FU-15 entry (if filed previously) OR add a closure note inline**

If FU-15 was never filed as a separate entry (it was surfaced informally during PR1 verification), add a closure note to FU-1's entry: "FU-15 (single-shot watchdog classification for `mtf_cache_prewarm_task`) also resolved by this PR — see new `single_shot` field on WorkerSpec + the watchdog state machine update in `worker_watchdog.py`."

- [ ] **Step 3: Commit**

---

## Phase 5 — Reviewers + PR

### Task 5.1: Run spec-compliance reviewer subagent

Dispatch:
- Spec source: `docs/superpowers/specs/2026-05-17-fu-1-worker-heartbeats-design.md`
- Diff: full PR diff vs `origin/dev`
- Output: APPROVED / APPROVED WITH DIVERGENCES / REJECTED

If any deviation from spec §6 bounds: fix inline, re-run reviewer.

### Task 5.2: Run code-quality reviewer subagent

Same dispatch shape; output: APPROVED / SUGGESTIONS / REJECT WITH CRITICAL FIXES.

If any Critical: fix inline, re-run.

### Task 5.3: Run ruff + mypy + full pytest suite

```
cd backend && python -m ruff check . && python -m mypy app && python -m pytest --no-cov -q
```

Expected: all clean.

### Task 5.4: Open PR + auto-merge per permanent authorization

```
git push -u origin feat/fu-1-worker-heartbeats-impl
gh pr create --base dev --head feat/fu-1-worker-heartbeats-impl \
  --title "feat(fu-1): wire heartbeats for 14 workers + FU-15 single-shot watchdog state" \
  --body "<full body with spec link, what changed, test plan, soak watchpoints>"
# Wait for CI green
gh pr checks <PR#> --watch
# Auto-merge per operator's 2026-05-17 permanent authorization
gh pr merge <PR#> --squash --delete-branch=false
```

### Task 5.5: Watch staging deploy + first-hour heartbeat verification

- [ ] **Step 1: Watch deploy.yml**

After dev merge, deploy.yml fires for staging.

- [ ] **Step 2: Wait ~5 min after staging deploy completes**

- [ ] **Step 3: Fire `staging-verify` probe**

```
gh workflow run ops-debug.yml --ref main -f probe=staging-verify
```

- [ ] **Step 4: Cross-check `worker_heartbeats` on staging postgres**

The `staging-verify` probe already runs section-3 SELECT against staging postgres. Add a one-off SELECT (manual or new probe) to verify all 14 workers have heartbeat rows within last 5 min on staging.

If any worker is missing or stale: STOP, investigate, file as bug or fix inline.

---

## Phase 6 — 5-day staging soak

Per spec §6.6 and §9 exit criteria #8. During the soak window:

**Watch points (operator monitors; agent only intervenes on critical failure):**
- Every worker's `worker_heartbeats.beat_at` advances within its `max_staleness_seconds` window
- Zero new `auth_violations` `chain_broken` entries
- `tr-staging-backend` restarts count stays 0
- No new `pending_heartbeat=True` entries in `/admin/workers` (would mean someone added a new worker without wiring heartbeat — flag as regression)
- `mtf_cache_prewarm_task` reports `single_shot_completed` post-startup, not `no_signal`
- No degradation in `predictions` write rate, `shadow_trades` open rate, etc.

After 5+ days of clean soak: auto-merge dev→main per operator's permanent authorization (behavior-changing per spec §6.6, but the change is observability-additive, not user-visible).

---

## Phase 7 — Prod verification + close

### Task 7.1: After prod deploy completes (~1 min after main merge)

- [ ] **Step 1: Wait for next worker tick cycles (5-15 min depending on slowest cadence)**

- [ ] **Step 2: Fire `health-summary` + `audit-history` probes against prod**

```
gh workflow run ops-debug.yml --ref main -f probe=health-summary
gh workflow run ops-debug.yml --ref main -f probe=audit-history
```

- [ ] **Step 3: Verify prod state**

- `tr-backend` container restarted (start time advanced)
- Worker breakdown: 14 workers in `ok` state (vs pre-FU-1: 4 ok + 9 pending_heartbeat + 1 single_shot)
- Zero new `auth_violations` `chain_broken` since the deploy

### Task 7.2: Surface ✅ FU-1 VERIFIED IN PROD report

Single message summarizing:
- All 14 workers heartbeating fresh
- FU-15 resolved (mtf_cache_prewarm_task reports `single_shot_completed`)
- Bot now has 5-minute granularity visibility into worker liveness instead of the 6-day-blind-spot that allowed PR #171's bug to persist

---

## Self-review checklist (for the implementing engineer)

Before opening the PR, verify each:

- [ ] All 14 workers have `await record_heartbeat(...)` calls in their main loops (grep verifies).
- [ ] `WorkerSpec.single_shot` field exists; `mtf_cache_prewarm_task` registry entry sets it True.
- [ ] `worker_watchdog.py` state machine handles `single_shot_completed` as non-alarming.
- [ ] No `pending_heartbeat=True` flags remain on any registry entry (test_no_pending_heartbeat_after_fu1 passes).
- [ ] No new alembic migration introduced.
- [ ] No new worker tasks introduced.
- [ ] Each per-worker commit is a single, focused change (worker code + registry flag drop).
- [ ] ruff + mypy clean.
- [ ] All new + existing tests pass.
- [ ] No `--no-verify` git commits.
- [ ] No direct pushes to `main`. All commits on `feat/fu-1-worker-heartbeats-impl`.
- [ ] Untracked operator scratch files NOT in any commit.

---

## Out-of-scope reminders (do NOT add)

- ❌ Telegram alerting for watchdog alerts (FU-2 territory)
- ❌ Auto-restart for stateful workers (PR9 self-healing supervisor)
- ❌ Schema changes to `worker_heartbeats`
- ❌ Removal of natural-table liveness queries on N-workers
- ❌ `max_staleness_seconds` re-tuning per worker
- ❌ Any change to PR1, PR2, PR3 code
