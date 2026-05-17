# FU-1 — Wire heartbeats for currently-silent workers

**Status**: Design draft 2026-05-17. Awaiting operator review.
**Owner**: Backend (ops + worker modules + watchdog).
**Parent**: [Master rollout plan — Option D](2026-05-17-master-rollout-plan-option-d.md) — operator decision 2026-05-17 to prioritize observability before PR2 implementation.
**Predecessor**: PR #171 hotfix (post-mortem revealed live_worker prediction silence went undetected for 6 days because the watchdog had no liveness signal).
**Behavior change**: YES — each touched worker gains a `record_heartbeat` call inside its main loop, and the watchdog's `pending_heartbeat` quiet-bucket is reduced or eliminated. No new tasks, no schema changes.

---

## 1. Goal

Replace the 9 `pending_heartbeat=True` quiet-bucket entries in `worker_registry.py` with real heartbeats, and add belt-and-suspenders heartbeats on top of the 5 natural-table-liveness workers operator explicitly listed. Net result: every worker the watchdog needs to monitor produces an observable `worker_heartbeats` row every loop iteration, so a silent failure can be detected within `max_staleness_seconds` (5–15 minutes for most workers) instead of however long it takes a human to notice the symptom (6 days, in PR #171's case).

FU-1 also closes **FU-15** (single-shot watchdog classification): `mtf_cache_prewarm_task` shows as `no_signal` instead of `single_shot_completed` after it exits cleanly. The watchdog state machine gains a fourth state to handle this case correctly.

---

## 2. Scope (in FU-1)

| ID | Worker | Current liveness | Action |
|---|---|---|---|
| H1 | `live_worker` | `HEARTBEAT` + `pending_heartbeat=True` | Add `record_heartbeat` in `run_live_prediction` candle loop; drop flag |
| H2 | `shadow_worker` | `HEARTBEAT` + `pending_heartbeat=True` | Add `record_heartbeat` in `_handle_candle`; drop flag |
| H3 | `audit_verifier_task` | `HEARTBEAT` + `pending_heartbeat=True` | Add `record_heartbeat` in `_loop` per tick; drop flag |
| H4 | `liquidation_monitor_task` | `HEARTBEAT` + `pending_heartbeat=True` | Add `record_heartbeat` in 30s poll loop; drop flag |
| H5 | `telegram_poller_task` | `HEARTBEAT` + `pending_heartbeat=True` | Add `record_heartbeat` in long-poll loop; drop flag |
| H6 | `auto_promote_task` | `HEARTBEAT` + `pending_heartbeat=True` | Add `record_heartbeat` in daily tick; drop flag |
| H7 | `news_cleanup_task` | `HEARTBEAT` + `pending_heartbeat=True` | Add `record_heartbeat` in nightly tick; drop flag |
| H8 | `intermarket_cleanup_task` | `HEARTBEAT` + `pending_heartbeat=True` | Add `record_heartbeat` in nightly tick; drop flag |
| H9 | `mtf_cache_prewarm_task` | `None` + `pending_heartbeat=True` (single-shot) | **Special — see §4.3 FU-15 fix**. Worker records ONE heartbeat with `status="single_shot_completed"` on clean exit. Registry gains `single_shot: bool = True` field. Watchdog reports `single_shot_completed` (not `no_signal`). |
| N1 | `news_ingest_task` | `MAX(fetched_at) FROM news_items` (natural) | Add `record_heartbeat` on every loop iteration (defense in depth — natural signal doesn't advance when adapters return 0 articles). Keep natural query as primary liveness. |
| N2 | `intermarket_snapshot_task` | `MAX(captured_at) FROM intermarket_snapshots` | Same defense-in-depth pattern. |
| N3 | `universe_refresh_task` | `MAX(snapshot_at) FROM asset_universe` | Same. Daily cadence; heartbeat protects against the worker running but failing all writes. |
| N4 | `universe_sync_task` | `MAX(last_synced_at) FROM universe_history` | Same. |
| N5 | `health_pinger_task` | `MAX(checked_at) FROM adapter_health` | Same. |

**Total touched: 14 workers** — 9 pending_heartbeat (H1-H9) + 5 natural-liveness defense-in-depth (N1-N5).

**Operator's authorization listed 12 workers**: 7 of the 9 H-workers (omits `news_cleanup_task` + `intermarket_cleanup_task`) + all 5 N-workers. Spec proposes adding the 2 omitted cleanup tasks for completeness — they're already `pending_heartbeat=True` and bringing them into scope closes FU-1 fully in one PR. Operator may scope down at review.

## 3. Explicitly NOT in FU-1

- Watchdog auto-restart for stateful workers (`live_worker`, `shadow_worker`, `liquidation_monitor`, `telegram_poller`, `ws_keepalive_task`) — deferred to PR9 (self-healing supervisor).
- New worker addition.
- Changes to `record_heartbeat`'s signature or behavior (already battle-tested by `ws_keepalive_task`, `mtf_cache_ttl_refresh_task`, `worker_watchdog_task`, `scanner_batch_task`, `prediction_validator_task` — 5 workers heartbeat cleanly today).
- Migration of the `worker_heartbeats` schema.
- Removal of natural-table liveness queries on the 5 N-workers (kept as primary; heartbeats are belt-and-suspenders).

---

## 4. Components

### 4.1 Worker main-loop pattern (canonical)

Existing pattern from `prediction_validator_task._loop` (already working):

```python
async def _loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    interval_s: float = INTERVAL_SECONDS,
) -> None:
    log.info("%s: starting (interval=%ds)", WORKER_NAME, int(interval_s))
    while True:
        try:
            result = await do_one_tick(session_factory)
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="ok", details={"work_done": result},
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("%s tick failed: %s", WORKER_NAME, e)
            # Record heartbeat ANYWAY with status="error" so watchdog
            # knows the worker is alive even if its work is failing.
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="error", details={"error": str(e)[:200]},
            )
        await asyncio.sleep(interval_s)
```

**Bounds from operator (apply to every H1-H8 + N1-N5):**
- Heartbeat call wraps in its own try/except inside `record_heartbeat` (already does — best-effort, never raises). No additional try needed at call site.
- Heartbeat MUST fire even on tick failure (status="error") so the watchdog distinguishes "worker dead" from "worker alive but failing work". This is the lesson from PR #171's investigation: the validator silently rolled back predictions while the prediction_validator_task heartbeat kept advancing, masking the issue.
- Use the worker's existing `WORKER_NAME` constant (or define one at module-top if absent).
- `session_factory` is already passed to the worker; no signature changes.

### 4.2 Worker-by-worker hook points

For each of the 14 workers, the spec identifies the exact main-loop location. Plan phase confirms with grep; concrete `file:line` references locked at plan time:

| Worker | File | Loop function | Where to insert |
|---|---|---|---|
| H1 live_worker | `backend/app/ws/live_prediction.py` | `run_live_prediction` | After each candle is processed (inside `async for candle in stream.stream():`) — heartbeat at the end of every successful iteration |
| H2 shadow_worker | `backend/app/shadow/worker.py` | `ShadowWorker.run` | Inside `_handle_candle` after persist completes |
| H3 audit_verifier_task | `backend/app/ops/audit_verifier.py` (locate exact module in plan) | `_loop` | After each `_check_all_chains` run |
| H4 liquidation_monitor_task | `backend/app/trading/liquidation_monitor.py` (or similar) | poll loop | After each poll cycle |
| H5 telegram_poller_task | `backend/app/ops/telegram_polling.py` | `_loop` | After each `getUpdates` returns |
| H6 auto_promote_task | `backend/app/trading/auto_promote.py` (or similar) | daily tick | After each tick (status="ok" or "error") |
| H7 news_cleanup_task | `backend/app/news/cleanup.py` (or similar) | nightly tick | After cleanup completes |
| H8 intermarket_cleanup_task | `backend/app/data/intermarket_cleanup.py` (or similar) | nightly tick | After cleanup completes |
| H9 mtf_cache_prewarm_task | `backend/app/core/scoring/mtf_confluence.py` | `prewarm_cache` | ONE heartbeat at the end of `prewarm_cache` (or in the registered task wrapper) with `status="single_shot_completed"`, `details={"prewarmed_count": N, "duration_s": X}` |
| N1 news_ingest_task | `backend/app/news/ingest_worker.py` | ingest loop | After each adapter pass (even when 0 articles added) |
| N2 intermarket_snapshot_task | `backend/app/data/intermarket_worker.py` | snapshot loop | After each snapshot batch (success OR failure) |
| N3 universe_refresh_task | `backend/app/shadow/universe_refresh.py` | daily tick | After refresh completes |
| N4 universe_sync_task | `backend/app/data/universe_sync.py` (locate) | daily tick | After sync completes |
| N5 health_pinger_task | `backend/app/ops/health_pinger.py` (locate) | 5-min tick | After ping cycle |

**Bound from operator (plan-phase trace requirement):**
- Before writing implementation, the plan phase greps for each worker's entry point and confirms the file path + function name. Per the `dispatcher-outbound-telegram-was-unwired` memory: do not iterate on guesses; trace the call graph end-to-end first.

### 4.3 FU-15 fix: single-shot worker watchdog state

```python
# backend/app/ops/worker_registry.py
@dataclass(frozen=True)
class WorkerSpec:
    ...
    pending_heartbeat: bool = False
    single_shot: bool = False  # NEW: True for tasks that fire once at startup, exit clean

# mtf_cache_prewarm_task — replace pending_heartbeat=True with single_shot=True
WorkerSpec(
    name="mtf_cache_prewarm_task",
    description="Single-shot MTF kline cache pre-warm over the top-30 universe",
    liveness_query=HEARTBEAT,    # changed from None
    max_staleness_seconds=10 * 60,  # not used post-completion; see watchdog
    stateful=False,
    single_shot=True,             # NEW
),
```

```python
# backend/app/ops/worker_watchdog.py — extend the state machine
if spec.single_shot:
    # Has the worker recorded ONE heartbeat with status="single_shot_completed"?
    if last_status == "single_shot_completed":
        entry["state"] = "single_shot_completed"  # NEW non-alarming state
    elif last_beat_at is None:
        # Still running OR hasn't started yet — within startup grace period?
        if container_uptime < startup_grace_seconds:
            entry["state"] = "starting"
        else:
            entry["state"] = "single_shot_never_completed"  # ALERT
    else:
        # Last status was something else (error?) — should not happen for single-shot
        entry["state"] = "single_shot_failed"  # ALERT
elif spec.pending_heartbeat:
    entry["state"] = "pending_heartbeat"  # existing quiet bucket (empty after FU-1)
else:
    # existing logic
```

The new `single_shot_completed` state is **non-alarming**. The existing `pending_heartbeat` bucket can stay for backward compatibility but is unused after FU-1.

**Bounds:**
- `single_shot_completed` is not in the watchdog's `bad_states` tuple.
- `single_shot_never_completed` IS in `bad_states`.

### 4.4 Tests

| File | Coverage |
|---|---|
| `tests/ops/test_record_heartbeat.py` (extend if exists; create if not) | Smoke test for each of the 14 workers: call the worker's tick function with mocked session_factory + verify `record_heartbeat` was called with the correct `WORKER_NAME` |
| `tests/ops/test_worker_registry_consistency.py` (UPDATE) | After FU-1: assert 0 entries have `pending_heartbeat=True` (single-shot mtf_cache_prewarm uses `single_shot=True` instead). Catches accidental flag reintroduction. |
| `tests/ops/test_worker_watchdog_single_shot.py` (NEW) | 4 cases: `single_shot_completed` → non-alarming; `single_shot_never_completed` (post-grace) → alarming; `starting` (within grace) → non-alarming; old `pending_heartbeat` (empty bucket) → non-alarming. |
| `tests/integration/test_fu1_heartbeat_propagation.py` (NEW) | Postgres integration test: each worker writes a heartbeat row within its `max_staleness_seconds` window when ticked. |

Each worker test follows TDD: failing test asserts `worker_heartbeats` row exists after a tick → run, expect FAIL → add heartbeat call → re-run, expect PASS → commit.

### 4.5 No schema migration

`worker_heartbeats` table already exists from prior work. `WorkerSpec` gains a Python field (`single_shot: bool`) — no DB change.

---

## 5. Decision points

| # | Question | Decision | Rationale |
|---|---|---|---|
| D1 | Add heartbeats to natural-liveness workers (N1-N5)? | YES | Defense in depth — natural-table signals can stall when adapters return 0 rows; heartbeats catch that case. Operator explicitly listed all 5 in the FU-1 scope. |
| D2 | Drop natural-table queries from N-workers? | NO | Keep both signals; watchdog prefers HEARTBEAT but can use either. Cheap insurance. |
| D3 | Scope cleanup workers (H7 + H8)? | YES — propose all 9 pending_heartbeat in one PR | Closes FU-1 fully; the cleanup tasks have the same pattern as the other H-workers. Operator scopes down at review if preferred. |
| D4 | FU-15 fix in same PR? | YES — single_shot field + watchdog state addition | Operator authorized "resolve FU-15 single-shot watchdog classification while you're there"; same file (`worker_registry.py`) + adjacent file (`worker_watchdog.py`) |
| D5 | Heartbeat cadence | One per loop iteration (matching existing `prediction_validator_task` pattern) | Simplest; no rate limiting needed; `worker_heartbeats` UPSERT is one row per worker so write volume is bounded by worker count not iteration count |
| D6 | Heartbeat on tick error | YES with status="error" | Per §4.1 bounds — watchdog distinguishes "dead worker" from "alive but failing" |
| D7 | Watchdog `max_staleness_seconds` review | Keep existing values | Already tuned per worker cadence; heartbeat addition only fills in the silent-bucket gap, doesn't change cadence math |

## 6. Bounds from operator

### 6.1 No new worker additions
FU-1 only wires heartbeats into existing workers. No new workers, no new tasks, no new schedules.

### 6.2 No signature changes
Heartbeat calls use the existing `record_heartbeat(session_factory, name, status=, details=)` signature unchanged.

### 6.3 No `try/except` around `record_heartbeat`
The helper already catches everything inside (`# noqa: BLE001 — best-effort, must never kill the worker`). Wrapping it again is redundant.

### 6.4 No watchdog `max_staleness_seconds` changes
Existing values stay. The current FU-1 problem is "we have no signal at all," not "the signal is stale by the wrong threshold."

### 6.5 No auto-restart for stateful workers
Deferred to PR9. FU-1 is observability only.

### 6.6 5-day staging soak
Behavior-changing work — heartbeats become a new prerequisite for "worker is alive" classification. 5+ day soak required before main merge.

### 6.7 Hard out-of-scope
- ❌ Telegram alerting for watchdog alerts (FU-2 territory)
- ❌ Schema changes to `worker_heartbeats`
- ❌ Removal of natural-table liveness queries
- ❌ Worker auto-restart
- ❌ New workers

---

## 7. Risks + mitigations

| Risk | Mitigation |
|---|---|
| **R1** Heartbeat write contention under high load | UPSERT on PK; one row per worker; ~14 writes/min worst case across all workers. Negligible vs the predictions/intermarket tables. |
| **R2** Worker's main loop throws inside the heartbeat path | `record_heartbeat` is best-effort wrapped (existing). Worker continues regardless. |
| **R3** Stateful workers (live, shadow, etc.) get auto-restarted incorrectly | §6.5 — auto-restart deferred to PR9. FU-1 changes watchdog state classification only; existing `stateful=True` ALERT-ONLY behavior stays. |
| **R4** Heartbeats fire faster than `max_staleness_seconds` window (false-fresh) | Existing `max_staleness_seconds` values are 5-26h; heartbeat cadence is 30s-1h. Heartbeats land well inside the window. |
| **R5** FU-15 `single_shot_completed` misclassified as alarming | Test `test_worker_watchdog_single_shot.py` asserts the non-alarming behavior. |
| **R6** Watchdog state-machine refactor breaks pending_heartbeat backward compat | Keep `pending_heartbeat: bool = False` field as a no-op fallback; all existing entries set it to True become harmless after their heartbeat lands. After FU-1 lands, set them all to False in the same PR for consistency. |
| **R7** Soak surfaces a worker whose existing main-loop pattern doesn't match §4.1 | Plan phase traces each worker; non-conforming workers get a refactor task in the same PR. |

## 8. Rollback

**Stage 1 — per-worker disable (no code revert)**: there's no env flag to disable individual heartbeats. The fallback is reverting the per-worker commit. Each worker is a small atomic change.

**Stage 2 — full PR revert**: `git revert <merge-commit>` restores `pending_heartbeat=True` on all 9 H-workers and removes the heartbeat calls. Workers continue to function (heartbeat absence isn't fatal); watchdog goes back to the silent-bucket UX.

No DB rollback. `worker_heartbeats` rows remain in place; they just stop updating for the reverted workers.

## 9. Exit criteria (FU-1 ships when)

1. ✅ All CI green (backend, frontend, docker-compose-smoke).
2. ✅ mypy clean (404+ source files).
3. ✅ ruff clean.
4. ✅ All new + extended tests pass:
   - `tests/ops/test_record_heartbeat.py` (14 worker smoke tests)
   - `tests/ops/test_worker_registry_consistency.py` (no `pending_heartbeat=True` remaining)
   - `tests/ops/test_worker_watchdog_single_shot.py` (FU-15 state machine)
   - `tests/integration/test_fu1_heartbeat_propagation.py` (Postgres roundtrip)
5. ✅ Spec compliance reviewer: PASS on all §6 bounds.
6. ✅ Code quality reviewer: 0 Critical findings.
7. ✅ Manual operator review of the full diff.
8. ✅ **5+ day staging soak** with all 14 workers heartbeating fresh:
   - Every worker's `worker_heartbeats.beat_at` advances within its `max_staleness_seconds` window
   - Zero `pending_heartbeat=True` entries in `/admin/workers` after deploy
   - `mtf_cache_prewarm_task` reports `single_shot_completed` post-startup
9. ✅ Operator free-text "ship it" for dev → main merge.

## 10. References

- Parent: [Master rollout plan — Option D](2026-05-17-master-rollout-plan-option-d.md)
- Predecessor incident: PR #171 hotfix (2026-05-17) — 6-day prediction silence undetected because `live_worker` had no heartbeat
- KNOWN_ISSUES: `backend/docs/KNOWN_ISSUES.md` (FU-1 main entry; FU-15 single-shot resolved by same PR)
- Touch list (file paths confirmed in plan phase via grep):
  - [`backend/app/ops/heartbeat.py`](backend/app/ops/heartbeat.py) — `record_heartbeat` (unchanged, just called more)
  - [`backend/app/ops/worker_registry.py`](backend/app/ops/worker_registry.py) — drop `pending_heartbeat=True` from 9 entries; add `single_shot=True` to mtf_cache_prewarm_task entry
  - [`backend/app/ops/worker_watchdog.py`](backend/app/ops/worker_watchdog.py) — add `single_shot_completed` / `single_shot_never_completed` states
  - [`backend/app/ws/live_prediction.py`](backend/app/ws/live_prediction.py) — H1 hook
  - [`backend/app/shadow/worker.py`](backend/app/shadow/worker.py) — H2 hook
  - [`backend/app/ops/audit_verifier.py`](backend/app/ops/audit_verifier.py) (locate in plan) — H3 hook
  - liquidation_monitor / telegram_polling / auto_promote / news_cleanup / intermarket_cleanup / mtf_confluence — H4-H9 hooks
  - news/ingest_worker / data/intermarket_worker / shadow/universe_refresh / data/universe_sync / ops/health_pinger — N1-N5 hooks
- MEMORY: `worker-watchdog-system`, `complete-modules-before-merge`, `dispatcher-outbound-telegram-was-unwired`
