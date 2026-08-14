# Backend — Known Issues

Long-lived issues that affect prod but are out of scope for current PRs.
Each entry has root cause, scope of impact, and remediation options.

---

## Tracked follow-up PRs (out of scope for the 9-PR upgrade rollout)

Operational fixes orthogonal to the upgrade plan. Acknowledged by
operator 2026-05-16. Not blocking PR1 or the 9-PR rollout.

### FU-1 — Wire heartbeats for all 16 registered workers — ✅ CLOSED 2026-05-17
- **Problem**: 12 of 16 workers in `worker_registry.py` are flagged
  `pending_heartbeat=True` — the watchdog cannot tell whether they are
  alive or dead. Verified via `worker_heartbeats` query on 2026-05-16
  (only 4 workers have ever heartbeated).
- **Scope**: Add `record_heartbeat(session_factory, name)` inside the
  main loop of each `pending_heartbeat=True` worker. Pattern visible
  in `ws_keepalive_task`. Remove `pending_heartbeat=True` flag once
  wired.
- **Effort**: ~1 day. Touches ~12 worker modules + their tests.
- **Tracking**: this file (FU-1). See also the "Worker heartbeats" section below.
- **Status**: ✅ **CLOSED** on `feat/fu-1-worker-heartbeats-impl` branch —
  9 H-workers had `pending_heartbeat=True` flags dropped after wiring
  in-loop `record_heartbeat()` calls (status="ok" on success, "error"
  in the exception handler, "ok" + `details={"paused": True}` when
  pause_state is set). 5 N-workers (universe_refresh, universe_sync,
  health_pinger, news_ingest, intermarket_snapshot) gained
  defense-in-depth heartbeats alongside their existing natural-table
  liveness queries — heartbeats catch the case where the worker runs
  fine but the natural query returns no advance (e.g. news_ingest
  ticks return 0 articles). FU-15 (single-shot watchdog state) closed
  alongside: `mtf_cache_prewarm_task` was getting reported as
  `no_signal` (alarming); it now records ONE heartbeat with
  `status="single_shot_completed"` on clean exit and the watchdog
  classifies as `single_shot_completed` (non-alarming) per a new
  4-state machine (starting / single_shot_completed /
  single_shot_never_completed / single_shot_failed). CI gates:
  `test_no_pending_heartbeat_after_fu1` hard-fails if the flag
  re-appears; per-worker static smoke + 4 behavioural propagation
  tests.

### FU-2 — Audit chain v2 — canonical JSONB hashing + alert routing fix + CHAINED_TABLES expansion
- **Problem (a)**: JSONB column tampering not detectable (root cause
  documented below in "Audit hash chain" section).
- **Problem (b)**: Verifier alerts route through `alert_admin` → SMTP,
  but SMTP is not configured in prod — alerts fall back to WARNING
  logs that scroll out within minutes.
- **Problem (c)**: `verifier_scheduler.py CHAINED_TABLES` only walks
  3 of 7 hash-chained tables (`predictions`, `paper_trades`,
  `shadow_trades`). The other 4 (`live_trades`, `brain_decisions`,
  `mode_change_log`, `tax_events`) are written but never verified.
- **Scope (estimated 1-2 days)**:
  1. Add canonical JSONB column hashing at write time: e.g. write a
     `{col}_hash_canonical TEXT` column computed via
     `json.dumps(value, sort_keys=True, separators=(",", ":"))`.
     Verifier hashes the canonical-text column. Trade-off: doubles
     storage for JSONB cells; cheaper than schema-cast-to-TEXT.
  2. Route audit chain alerts to Telegram (matches the operational
     alerting that already exists for self-healing — see
     `app/ops/alerts.py`). Bypasses SMTP requirement.
  3. Extend `CHAINED_TABLES` in `verifier_scheduler.py` to iterate
     `HASH_PAYLOAD_COLUMNS.keys()` automatically (no hardcoded list).
- **Tracking**: this file (FU-2). See also "Audit hash chain" section.
- **Status**: queued; needed before audit chain can be trusted for
  forensics on JSONB-bearing tables.

### FU-3 — Investigate 5-each-in-8-seconds auth_violations pattern
- **Problem**: Today's nightly audit_verifier run (2026-05-16
  02:59:59 — 03:00:00 UTC) produced 10 `audit_chain_broken` rows in
  8 seconds, alternating `predictions:1` and `shadow_trades:1` —
  5 entries per table per second instead of the expected 1 per table.
- **Hypotheses to test**:
  1. Retry storm: `_record_violation` commit fails + something retries.
  2. Multiple verifier task instances running concurrently (would
     manifest as a docker-compose scale issue or duplicate
     `start_audit_verifier_task` in lifespan).
  3. Logging artifact: same DB row visible from multiple async
     iterations.
- **Scope**: read-only investigation — grep `tr-backend` logs around
  03:00 UTC for the verifier's log lines, count how many times
  `_check_all_chains` executed, and trace `_record_violation` paths.
- **Effort**: ~2 hours.
- **Tracking**: this file (FU-3).
- **Status**: queued; low-impact (alerts already silent) but worth
  knowing to inform FU-2's design.

### FU-4 — Investigate telegram_polling user_id fallback masking potential telegram_signals.user_id NULL rows
- **Problem**: `telegram_polling.py:193` uses `row.user_id or user_id`
  defensively. If `row.user_id` is ever falsy (NULL, 0), the
  function kwarg `user_id` silently substitutes. The auto-dispatch
  path (`dispatcher.py:354`) trusts `user.user_id` directly with no
  fallback. The defensive fallback in telegram could be masking a
  data integrity issue in `telegram_signals`.
- **Scope**:
  1. Query for any `telegram_signals` rows where `user_id IS NULL`
     or `user_id = 0`.
  2. If found, investigate root cause (which writer left it
     unset?).
  3. If clean, remove the fallback in a follow-up PR so the path
     fails loudly on missing data.
- **Effort**: ~2 hours investigation + 0.5 day fix if needed.
- **Tracking**: this file (FU-4). Surfaced during PR1 Phase 2
  divergence audit on 2026-05-16.
- **Status**: queued.

### FU-5 — Telegram-approve path loses layer_summary in live_trades.reasoning
- **Problem**: `dispatcher.py:367-371` (auto-dispatch) includes
  `proposal.layer_summary` in the `reasoning` JSON column.
  `telegram_polling.py:206-209` (telegram-approve callback) omits
  `layer_summary` because `telegram_signals.payload` doesn't carry
  it through. Result: telegram-approved live trades have strictly
  less audit/debug info than fully-auto trades.
- **Scope**:
  1. Modify dispatcher's `send_trade_signal_message` to include
     `layer_summary` in `telegram_signals.payload` at write time.
  2. Modify `telegram_polling._place_approved_order` to read it
     back and include in the reasoning dict.
  3. Backfill existing `live_trades` rows: **not possible** —
     the data was never captured at write time.
- **Note**: The key-ordering inconsistency between the two
  reasoning dicts (`{confidence_pct, layer_summary, signal_id}` vs
  `{signal_id, confidence_pct}`) is intentionally NOT addressed
  here — FU-2's canonical JSONB hashing will resolve ordering
  naturally.
- **Effort**: ~0.5 day.
- **Tracking**: this file (FU-5). Surfaced during PR1 Phase 2
  divergence audit on 2026-05-16.
- **Status**: queued.

### FU-6 — Telegram-approve path silently defaults empty inputs_hash when missing from payload
- **Problem**: `telegram_polling.py:210` uses
  `payload.get("inputs_hash", "")`. An empty-string `inputs_hash`
  defeats the audit chain's input traceability — you can't
  reconstruct what inputs led to that trade. The auto-dispatch
  path (`dispatcher.py:372`) uses `proposal.inputs_hash` directly
  (AttributeError if absent — fails loud). For a live-money system
  this is a real audit gap.
- **Scope**:
  1. Verify dispatcher always writes `inputs_hash` to
     `telegram_signals.payload` (it should — `inputs_hash` is
     required on `SignalProposal`).
  2. Query for any existing `telegram_signals` rows where the
     payload doesn't have `inputs_hash`; if found, investigate.
  3. Remove the silent `""` default in `telegram_polling` — make
     it raise `KeyError` if missing.
- **Effort**: ~2 hours investigation + 0.5 day fix.
- **Tracking**: this file (FU-6). Surfaced during PR1 Phase 2
  divergence audit on 2026-05-16.
- **Status**: queued.

### FU-7 — Three pre-existing tests fail on SQLite (CI green on Postgres but local SQLite broken)
- **Problem**: Three tests fail consistently when the suite runs
  against `sqlite+aiosqlite:///:memory:` (the default for local
  dev). All three were added pre-PR1 (SP-2 / SP-3 / SP-3.5 era).
  CI runs Postgres + TimescaleDB and presumably passes these
  tests, so they're "SQLite-incompatible" rather than "broken in
  prod":
    - `tests/integration/test_api_intermarket_route.py::test_get_intermarket_route_returns_latest`
      — added commit `07cf03e` (SP-3.5)
    - `tests/unit/test_ml_checkpoints.py::test_load_active_downloads_and_loads_state_dict`
      — added commit `e9b645a` (SP-2)
    - `tests/unit/test_ratelimit_client.py::test_multiple_named_buckets_routed_by_endpoint_key`
      — added commit `2962c8f` (SP-3)
- **Why this matters now**: PR1's CI matrix verification surfaced
  them. They're NOT introduced by PR1. They make `pytest tests/`
  on a developer laptop fail with 3 errors out of ~2400+ tests,
  which is misleading.
- **Scope**: For each test, either:
  1. Add `@pytest.mark.skipif("sqlite" in DATABASE_URL, reason="postgres-only")`
     decorator, OR
  2. Refactor the test to be SQLite-compatible (preferred if the
     incompatibility is incidental — e.g., a feature the test
     doesn't actually need).
- **Effort**: ~2-3 hours.
- **Tracking**: this file (FU-7). Surfaced during PR1 Phase 6
  flakiness check on 2026-05-17.
- **Status**: queued. Not blocking PR1 — these tests are
  Postgres-only-passing in CI which is what determines mergeability.

### FU-9 — Per-call `httpx.AsyncClient` construction across ~20 modules
- **Problem**: The PR1 latency benchmark (Phase 7) surfaced a 560ms
  cold-construction cost in `httpx.AsyncClient.__init__` loading the
  OS trust store. `compute_mtf_confluence` constructs httpx per call
  when `_http=None`. This pattern is NOT unique to PR1 — the codebase
  uses per-call construction across ~20 modules:
  `binance_live.py`, `binance_filters.py`, `binance_ticker.py`,
  `binance_futures_intermarket.py`, `scanner/batch.py`,
  `news/fear_greed.py`, `news/adapters/cryptopanic.py`,
  `telegram/trade_signals.py`, `ops/telegram_bot.py`,
  `ops/telegram_polling.py`, `ops/telegram_timeout.py`,
  `trading/tax/inr_converter.py`, `ml/validator.py`,
  `api/routes/tab1.py`, `api/routes/admin_test_trade.py`,
  `shadow/worker.py`, `shadow/universe_refresh.py`,
  `data/adapters/twelvedata.py`, `data/adapters/binance.py`,
  `ws/live_prediction.py`, `deps.py`.
- **Steady-state**: After the first construction in a process,
  httpx's trust-store loading is cached — subsequent constructions
  are fast (~µs). Cold cost only bites at process startup or after
  GC cycles that drop the cache. The V-7 latency gate measures
  steady-state and passes with comfortable margin (delta_p50=7.3ms,
  delta_p99=8.8ms vs budgets 50/200ms).
- **Scope**: Audit each call site + decide:
  1. Module-level singleton `httpx.AsyncClient` constructed at
     import time, closed at lifespan teardown.
  2. Or a dependency-injected client passed down from `main.py`
     lifespan (cleaner for testing).
  Pick approach, refactor ~20 files. ~1 day work.
- **Tracking**: this file (FU-9). Surfaced during PR1 Phase 7 V-7
  gate on 2026-05-17.
- **Status**: queued. **Not blocking PR1** — pattern is pre-existing
  + steady-state measurements pass V-7 gate. Should land before any
  latency-sensitive feature in PR2+.

### FU-8 — CI backend job missing `alembic upgrade head` step before pytest
- **Problem**: `.github/workflows/ci.yml` backend job runs pytest
  against the Postgres service container but does NOT run
  `alembic upgrade head` first. PR1's `test_pr1_migration.py`
  (8 tests) introspect live Postgres columns and assume the schema
  is at HEAD. Without the alembic-upgrade step, these tests will
  either fail (missing columns) or produce wrong results.
- **Scope**: Add a step between "Install deps" and "Unit +
  integration tests":
    ```yaml
    - name: Apply alembic migrations
      working-directory: backend
      env:
        DATABASE_URL: postgresql+asyncpg://postgres:testpw@localhost:5432/trading_radar
      run: python -m alembic upgrade head
    ```
- **Effort**: ~15 minutes (single ci.yml edit + verify).
- **Tracking**: this file (FU-8). Surfaced during PR1 Phase 6 CI
  matrix verification on 2026-05-17.
- **Status**: **CLOSED 2026-05-17** by commit `e8513c8` (ci.yml
  edit included in PR1).

### FU-13 — paper_trades table: verify dead, decide delete vs revive
- **Problem**: `paper_trades` is registered as a hash-chained table
  (key exists in `HASH_PAYLOAD_COLUMNS`) but appears unused — 0 rows in
  prod and no active call sites found during PR1 Phase 1 audit. PR1's
  audit-whitelist consistency test (now running on Postgres in CI per
  FU-8 + Fix A) surfaced that `paper_trades.user_id` was unclassified;
  the FU-8-followup commit added it to `NON_HASHED_ALLOW_LIST` to
  match current runtime behavior (no hashing was happening before).
  This leaves `paper_trades` asymmetric with its sibling chained
  tables — `predictions`, `shadow_trades`, `live_trades` all hash
  `user_id`; `paper_trades` does not.
- **Scope**:
  1. Grep for any `insert_with_chain(..., "paper_trades", ...)` call
     sites. Check the worker registry + lifespan startup paths.
  2. **If zero callers**: delete the `paper_trades` table via alembic
     downgrade-or-drop migration; remove from `HASH_PAYLOAD_COLUMNS`,
     `NON_HASHED_ALLOW_LIST`, and `verifier_scheduler.py
     CHAINED_TABLES`. Clean removal.
  3. **If callers exist**: align `paper_trades` whitelist with sibling
     tables — move `user_id` from `NON_HASHED_ALLOW_LIST` to
     `HASH_PAYLOAD_COLUMNS`. This changes hashing behavior on future
     inserts (existing 0 rows unaffected). Document the schema bump.
- **Effort**: ~2 hours.
- **Tracking**: this file (FU-13). Surfaced 2026-05-17 during PR1
  Phase 7 CI cleanup when the audit-whitelist consistency test ran on
  Postgres for the first time and flagged `paper_trades.user_id`.
- **Status**: queued. **Not blocking PR1** — current asymmetric
  classification matches actual runtime behavior exactly; no hashing
  semantics change. FU-13 fixes the underlying dead-or-asymmetric
  question.

### FU-11 — ci.yml has duplicated env blocks across steps; hoist to job level
- **Problem**: The backend job's `Apply alembic migrations` step and
  `Unit + integration tests` step both require identical `DATABASE_URL`
  + `REDIS_URL` + `ENV` env vars (because both load `Settings()` via
  `get_settings()`). Currently each step duplicates the env block. PR1
  shipped a 3-line per-step env addition twice — the FU-8 onion-peel
  pattern: each new step that touches app code requires re-declaring
  the same envs. Future steps that load app modules will hit the same
  trap silently (CI fails on the new step, gets per-step env added,
  next new step trips again).
- **Scope**: Move `DATABASE_URL` + `REDIS_URL` + `ENV` from per-step
  env blocks to `jobs.backend.env` so all steps inherit. Retain
  per-step env only for vars that genuinely differ per step
  (`WORKER_ENABLED: "false"` is pytest-only; `ENV: development` vs
  `ENV: ci` differs between steps and is the only reason a per-step
  override would be needed — pick one consistent value or document the
  divergence).
- **Effort**: ~1 hour including diff review + CI re-run to confirm.
- **Tracking**: this file (FU-11). Surfaced 2026-05-17 when the
  `Apply alembic migrations` step (added by FU-8 close, commit
  `e8513c8`) failed with `redis_url Field required` because only
  `DATABASE_URL` was propagated. Fixed inline with FU-8 follow-up
  commit adding `REDIS_URL` + `ENV: ci` to the alembic step. The
  underlying duplication pattern remains.
- **Status**: queued. **Not blocking PR1** — Fix A landed for PR1 to
  ship cleanly. FU-11 is the permanent fix for the per-step env
  duplication pattern.

### FU-10 — Migration downgrade path is untested on every dialect
- **Problem**: 0 of 8 existing migration tests in
  `backend/tests/db/test_pr1_migration.py` exercise
  `alembic downgrade -1`. Every test asserts the post-upgrade state
  only. PR1's migration introduces 3 DROP COLUMN sweeps + 3 DROP
  INDEX statements + reverse of `live_trades.timeframe`; a future
  migration could silently break the rollback story (e.g., a typo in
  a DROP statement, a missing IF EXISTS, dialect-asymmetric ALTER)
  and CI would not catch it.
- **Scope**: Add `test_migration_downgrade_cleanly` +
  `test_migration_upgrade_downgrade_upgrade_round_trip` that:
  1. `alembic upgrade head` → introspect schema → assert post-state
  2. `alembic downgrade -1` → introspect → assert pre-state
     (7 PR1 cols absent, indexes absent, `live_trades.timeframe`
     absent)
  3. `alembic upgrade head` again → assert post-state matches step 1
  Parametrize over SQLite (in-memory) and Postgres (CI service
  container). Mirrors the round-trip pattern from
  `test_audit_replay_identity.py`.
- **Effort**: ~3 hours.
- **Tracking**: this file (FU-10). Surfaced during PR1 Phase 7 lint
  cleanup on 2026-05-17 (F841 on a dead `is_pg` variable in
  `downgrade()` revealed that the downgrade path had never been
  exercised on either dialect).
- **Status**: queued. **Not blocking PR1** — no production downgrade
  planned. Should land before any future PR that adds a non-trivial
  migration body.

### FU-19 — Live-trade hold-timeout infrastructure missing (PR2 Phase 5 blocker)
- **Problem**: PR2 spec §4.2 (F-1) calls for `SHORT_FUNDING_HALVE_HOLD` to
  halve the max-hold timeout when a SHORT trade's funding rate exceeds
  `SHORT_FUNDING_HALVE_THRESHOLD_PCT`. PR2 Phase 5 plan called for a
  call-graph trace to find the existing hold-timeout enforcement to hook
  into. The trace found **no such infrastructure for live trades**:
  - `app/shadow/exit_monitor.py:7` has `TIMEOUT_BARS=24` hardcoded for
    SHADOW trades only — not configurable, doesn't apply to live.
  - `app/trading/execution/liquidation_monitor.py` is the only worker
    that closes live trades autonomously, and it closes on
    `<10% liquidation buffer`, not on a hold-time timeout.
  - No `expires_at` / `max_hold_hours` column on `live_trades`. No
    timer worker. The spec assumed timer infrastructure that doesn't
    exist.
- **PR2 disposition (superseded 2026-08-14, see below)**: the FLAG and
  the THRESHOLD setting were shipped (`SHORT_FUNDING_HALVE_HOLD: bool =
  False`, `SHORT_FUNDING_HALVE_THRESHOLD_PCT: float = 0.05`). The HOOK
  was deferred — there was nothing to hook into.
- **2026-08-14 update (remediation work order E2)**: `SHORT_FUNDING_HALVE_HOLD`
  and `SHORT_FUNDING_HALVE_THRESHOLD_PCT` have been **removed** from
  `config.py`. They were dead config with zero code references beyond
  the `Settings` declaration itself — a flag with no hook is not a
  rollback lever, just a landmine for a future reader who assumes it
  does something. The underlying infrastructure gap described above
  (no live-trade hold-timeout enforcement) is unchanged and still
  open; this update only removes the two placeholder settings, not
  the problem. If FU-19's timeout infrastructure is ever built, the
  flag/threshold pair should be reintroduced alongside the real hook
  in the same PR — not ahead of it.
- **Scope (estimated 2-3 days)**:
  1. Decide where to enforce live-trade timeouts (new worker that
     polls open `live_trades` and closes on age, OR `expires_at`
     column + extension of `liquidation_monitor` to check both
     liquidation-buffer AND age).
  2. Add a `MAX_HOLD_HOURS` setting (default e.g. 24 to match shadow).
  3. Implement `effective_max_hold_hours(direction, base, funding_pct,
     settings)` per the sketch in
     `docs/superpowers/plans/2026-05-17-pr2-mtf-gate-and-short-safety.md#task-53`,
     reintroducing `SHORT_FUNDING_HALVE_HOLD`/`_THRESHOLD_PCT` at this step.
  4. Wire the helper at the chosen enforcement site.
  5. 4 new tests covering Flag OFF, LONG, SHORT+low-funding,
     SHORT+high-funding cases.
- **Tracking**: this file (FU-19). Surfaced during PR2 Phase 5 trace.
- **Status**: queued; not blocking PR2 (dead flags removed 2026-08-14,
  no observed ops gap).

### FU-18 — staging-verify probe windows mismatched 1h cadence
- **Problem**: The `staging-verify` and analogous prod-side probes
  use criteria designed for 5m/15m continuous cadence systems:
    1. Predictions count: `SELECT COUNT(*) FROM predictions WHERE ts > NOW() - INTERVAL '60 minutes'`
    2. Log signal count: `docker compose logs --tail=500 backend | grep -c 'aggregator_hook'`
  For the production 1h candle cadence, both windows are always
  mis-timed relative to the most-recent prediction batch:
  - The hourly batch is written ~5-10 seconds after each top-of-hour
    candle close. Its `ts` is the candle open time, so the row's
    `ts` is ~1 hour in the past relative to write time.
  - For criterion 1: between hourly closes, `ts > NOW() - 60m` does
    not match any rows. Right after a close, the just-written rows
    have `ts ≈ NOW() - 1h`, still outside the window.
  - For criterion 3: the 500-line tail covers ~5-10 minutes of
    log activity; the hourly aggregator_hook burst is 30+ lines
    in a single second every 60 minutes, almost always outside
    the 500-line window.
- **Evidence**: Hotfix #171 staging verification run 25987406217
  (2026-05-17) showed criterion 1=0 + criterion 3=0 INDETERMINATE
  while Section 4's direct row-inspection query showed 10
  predictions persisted with PR1 analytics populated. Required
  operator override on both criteria. Prod verification run
  25988802156 PASSED criterion 1 strictly via the 2h column
  (window 2h matched the 51-min-old batch) but still failed
  criterion 3 (200-line tail too narrow).
- **Scope**:
  1. Replace criterion 1 query with an id-based monotonic check:
     `SELECT COUNT(*) FROM predictions WHERE id > <baseline_max_id_captured_at_deploy>`.
     The baseline is captured by a pre-deploy probe call or stored
     in a deploy-side marker. Robust across timeframe cadences.
  2. Replace criterion 3 with a time-based log query:
     `docker compose logs --since=60m backend 2>&1 | grep -c 'aggregator_hook'`.
     `--since` is time-bounded, not line-bounded; covers the full
     60-min hourly cycle regardless of activity rate.
  3. Document the design choice in the probe YAML comment so future
     maintainers don't re-introduce the windowing mismatch.
- **Severity**: MEDIUM. Probe gives false-negative INDETERMINATE
  results during 1h-cadence verification cycles, but section 4's
  row data and section 5b's record_pending_validation count remain
  authoritative. No production risk; only verification-workflow
  friction.
- **Effort**: ~2 hours (rewrite both queries, smoke-test against
  staging, document in commit message + probe comment).
- **Tracking**: this file (FU-18). Surfaced during hotfix #171
  verification on 2026-05-17.
- **Status**: queued. **Not blocking** — current probes still
  produce the authoritative answer via Section 4 row inspection;
  the windowing mismatch is friction not breakage. Refactor before
  the next 1h-cadence hotfix verification cycle.

---

---

## Audit hash chain — JSONB column tampering is not detectable

Verified 2026-05-16 during PR1 implementation. The audit hash
chain has structural integrity (prev_hash → row_hash linkage is
cryptographically verified) but does NOT detect tampering of
JSONB column contents.

### Root cause
Postgres JSONB does not preserve write-time key order. When
asyncpg reads a JSONB cell back into a Python dict, the dict
reflects Postgres's internal storage order, which is neither
alphabetical nor write-time-Python-order. Six canonicalization
strategies were tested; none can reproduce the original
write-time `json.dumps(...)` byte output from a JSONB read.

### Affected columns
- `predictions.layer_scores` (JSONB) — 0/95 rows replay
- `shadow_trades.layer_scores` (JSONB) — 3/20 rows replay (the 3
  that pass are empty dicts; serialization is trivial)
- `live_trades.reasoning` (JSONB) — replay depends on contents
- `brain_decisions.observation`, `brain_decisions.action_logits` (JSONB)
- `mode_change_log.gate_snapshot` (JSONB) — replays when NULL
- `predictions.mtf_directions_json` (JSONB — PR1 addition, NOT in
  `HASH_PAYLOAD_COLUMNS`, so unaffected)

### What this means
- An attacker with direct DB write access can mutate a JSONB cell
  and the `audit_verifier` will NOT detect the change.
- The chain link integrity (`Brk=0`) is still meaningful: no rows
  were inserted, deleted, or reordered.
- Non-JSONB column tampering IS detectable.

### Mitigation options (future PR)
1. Hash JSONB columns with canonical form (`sort_keys=True`,
   `separators=(",", ":")`) at write time, stored to a
   `{col}_hash_canonical TEXT` column. Verifier hashes the
   canonical column.
2. Cast JSONB columns to TEXT in schema. Loses JSONB query
   benefits.
3. Accept JSONB tamper-undetectability and add an explicit row
   integrity column at write time.

Tracking: queued as a future PR. Not in scope for the 9-PR
upgrade rollout.

### Why this was not detected earlier
`ARCHITECTURE.md §13` implied the hash chain provides
tamper-evidence on all columns. This was an inherited assumption,
never independently verified until PR1 implementation forced the
replay-identity check.

### Production evidence (2026-05-16)
The `audit_verifier_task` HAS been running nightly at 03:00 UTC.
It HAS been finding "chain breaks" the entire time:

- **`auth_violations` table has 559 `audit_chain_broken:*` rows**
  spanning many months of nightly runs.
- **Today's run (2026-05-16 02:59:59 — 03:00:00 UTC) recorded
  10 violation rows** alternating between `predictions:1` and
  `shadow_trades:1` (5 each in 8 seconds — the loop pattern is
  worth a separate investigation; suggests retry-on-commit or
  multiple verifier task instances).
- Alerts are routed to `alert_admin` (SMTP), but **SMTP is not
  configured in prod** (per memory `worker-watchdog-system`) —
  alerts silently fall back to WARNING log lines, which then
  scroll out of the 200-line tail before any operator reads them.
- `audit_verifier_task` is in `worker_registry.py` with
  `pending_heartbeat=True` — by design no heartbeat alerts. The
  watchdog cannot tell whether the task is dead or alive.

All 559 historical "breaks" are inferred to be JSONB-canonicalization
false positives, not actual tampering. The PR1 replay-identity
probe (run 25961889343) confirmed the prev_hash→row_hash chain
linkage is intact for every row.

---

## Audit verifier — only 3 of 7 chained tables are walked

`backend/app/ops/verifier_scheduler.py CHAINED_TABLES` (line 52-107)
lists only `predictions`, `paper_trades`, `shadow_trades`. The
audit hash chain is also written for `brain_decisions`,
`mode_change_log`, `live_trades`, `tax_events` (confirmed via
`backend/app/trading/preflight.py:206-207` and direct call-site grep).

**These 4 tables are NEVER verified by the nightly task** — neither
their chain linkage nor their content hashes are checked.

### Why this matters
- `live_trades` and `tax_events` are particularly sensitive (live
  Binance order audit trail, tax-authority defensibility). They
  are hash-chained at write time but their chain is never validated.
- `brain_decisions` and `mode_change_log` similar concern.

### Remediation
Trivial — extend `CHAINED_TABLES` in `verifier_scheduler.py` to
include the 4 missing tables, with their column lists matching
`HASH_PAYLOAD_COLUMNS` in `app/db/audit.py`. The new whitelist-aware
`verify_chain` already handles them correctly (the PR1
`audit-replay-verify` probe exercised all 7 successfully).

Caveat: extending the verifier WILL cause it to find the same
JSONB-canonicalization "false breaks" on `brain_decisions.observation`,
`brain_decisions.action_logits`, `mode_change_log.gate_snapshot`,
`live_trades.reasoning` once they have non-trivial content. Until
the JSONB-tampering hole (above) is fixed, those "breaks" will
flood `auth_violations` similar to today's predictions/shadow_trades.

Tracking: queued as a separate future PR. Either:
1. Walk all 7 tables now and accept the additional false-positive
   logging (since alerts already silent), OR
2. Fix the JSONB hashing first (above) then extend `CHAINED_TABLES`.

---

## Worker heartbeats — ✅ CLOSED 2026-05-17 (was: 12 of 16 registered workers never beat)

**Status**: ✅ Closed on `feat/fu-1-worker-heartbeats-impl`. All 18
workers in the registry now emit `record_heartbeat()` from their main
loop or (for `mtf_cache_prewarm_task`) on clean single-shot exit. The
`pending_heartbeat=True` flag has been removed from every entry; the
new `test_no_pending_heartbeat_after_fu1` regression test prevents
the flag from reappearing. See the FU-1 entry above for the closure
summary. The historical snapshot below is preserved for context.

---

### Historical snapshot (pre-FU-1, 2026-05-16)

`backend/app/ops/worker_registry.py` declares 16 workers. Querying
`worker_heartbeats` table (2026-05-16 12:35 UTC) shows only **4**
workers have ever recorded a heartbeat:

| Worker | Latest beat | Status |
|---|---|---|
| `prediction_validator_task` | 2026-05-16 12:35:39 UTC | ✅ active |
| `scanner_batch_task` | 2026-05-16 12:35:39 UTC | ✅ active |
| `worker_watchdog_task` | 2026-05-16 12:34:31 UTC | ✅ active |
| `ws_keepalive_task` | 2026-05-16 12:34:29 UTC | ✅ active |

The other 12 are marked `pending_heartbeat=True` in
`worker_registry.py`. Per the docstring at
`worker_registry.py:39-47`: this is by-design, watchdog skips
staleness alerts for them. But it means the watchdog has **no way
to tell whether they're alive or dead**.

Particularly noteworthy for `audit_verifier_task`: its DB-write
side-channel (`auth_violations` rows) confirms it IS running, but
without a heartbeat the watchdog can't distinguish "running fine"
from "crashed silently" from "task object never created".

### Affected workers (pending_heartbeat=True)
- `live_worker` (stateful)
- `shadow_worker` (stateful)
- `audit_verifier_task`
- `news_cleanup_task`
- `intermarket_cleanup_task`
- `liquidation_monitor_task` (stateful)
- `telegram_poller_task` (stateful)
- `auto_promote_task`
- The 4 newly-added ones in PR1 (if applicable per PR1 spec)
- (others depending on snapshot)

### Remediation
Each worker needs a `record_heartbeat(session_factory, name)` call
inside its main loop. Pattern visible in `ws_keepalive_task`. Then
remove `pending_heartbeat=True` flag from `worker_registry.py`.

Tracking: queued as a future PR. Not blocking for PR1 because PR1
adds new workers explicitly marked `pending_heartbeat=True` to
match existing pattern (single-shot prewarm) or wires heartbeat
inline (TTL-refresh).

---

### FU-21 — Stateful-worker auto-restart with state migration
- **Filed**: 2026-05-18 (carved out of PR9 scope per operator decision)
- **Severity**: MEDIUM
- **Depends on**: FU-1 heartbeats (✅ DONE), PR9 self-healing supervisor foundation (in flight)
- **When to schedule**: After Phase 1 ops hygiene sprint completes (~late 2026-05-29). Can ship before or after PR5.

#### Problem
PR9 ships Telegram alert routing for stateful-worker critical alerts,
but does NOT auto-restart stateful workers (`live_exit_monitor`,
`liquidation_monitor`, `telegram_poller`, `ws_keepalive` children).
These workers hold in-flight state — open positions being monitored,
telegram update cursor, child task statuses — that would be lost on
a naive restart.

Current behavior (post-PR9): watchdog detects stateful-worker silence
→ Telegram alert → operator manually restarts. This works but requires
human availability for stateful-worker failures. Auto-restart would
close the last gap in the self-healing story.

#### Scope
Design a state-migration protocol:
- **(a) Pre-shutdown**: snapshot worker state to a
  `stateful_worker_snapshots` table (`worker_name`, `snapshot_at`,
  `state_json`, `schema_version`).
- **(b) Restart**: worker loads snapshot, validates schema_version,
  resumes from snapshot point (e.g., telegram_poller picks up the
  cursor; live_exit_monitor re-loads open trades; ws_keepalive
  re-subscribes its child symbols).
- **(c) Watchdog**: after detecting stateful-worker silence, trigger
  snapshot (from the LAST known good heartbeat's accompanying state)
  + restart instead of alerting only. Telegram alert still fires for
  audit but the action is automated.
- **(d) Tests**: per-worker snapshot/restore round-trip; watchdog
  integration test that simulates a crash and asserts auto-restart
  resumes the loop.

#### Effort
~3-4 days (design + per-worker snapshot serializers + watchdog
integration + tests).

#### Why MEDIUM not HIGH
Current alert + manual-restart path is functional. Operator availability
risk is what FU-21 mitigates, not data loss — the live_trades table
itself doesn't depend on the monitor's in-memory state to stay
consistent (it's reconciled from Binance position queries each tick).

---

### FU-31 — /open-positions has no fallback polling

**Status**: open · **Discovered**: 2026-05-20 (PR10.8 investigation)
**Severity**: LOW (operator-visible dashboard staleness; no data risk)

#### Symptom
`useOpenPositionsLive` refetches `/api/v1/bot-status/open-positions`
ONLY on:
- WebSocket events (`shadow_position_opened`, `shadow_position_closed`),
- Manual `refetch()` calls, or
- Hard refresh.

There is no time-based polling on this endpoint (unlike `/per-asset`
which polls every 60s as of PR10.5 T-UI.2). If a WS event is dropped
(server restart mid-session, network blip, client tab backgrounded
through a close event), the Open Positions card silently stales and
the operator may not notice for hours.

#### Scope
Add a low-priority 60s `setInterval` refetch to `useOpenPositionsLive`
as belt-and-braces. Match the existing PR10.5 pattern used for
`usePerAssetStats`: configurable `pollIntervalMs` prop with 0-disables,
in-flight guard, cleanup on unmount.

#### Effort
~1 hour. Frontend-only. No backend or DB changes.

#### Why LOW not MEDIUM
WS is the primary source and works in steady state. This is a
defensive fallback for edge cases. PR10.5's healer (FU-28) already
detects stale ticks; this just adds a self-correcting refresh on
top.

---

### FU-32 — SHADOW_SPOT_BLACKLIST is misnamed

**Status**: cosmetic follow-up · **Discovered**: 2026-05-20 (PR10.8 Inv2)
**Severity**: LOW (naming confusion, not a bug)

#### Symptom
`Settings.SHADOW_SPOT_BLACKLIST` (PR10.7) was added to filter symbols
that fail `/api/v3/ticker/price` BATCH calls (EDENUSDT, LUNCUSDT,
PAXGUSDT, XAUTUSDT, UUSDT). But Inv2 of PR10.8 confirmed that at
least UUSDT IS priceable on Binance SPOT via klines + WebSocket — the
shadow worker fetches `/api/v3/klines?symbol=UUSDT` every ~70s with
200 OK, and `publish_pnl_tick` emits price updates from those candles.
So "SPOT blacklist" is misleading: the symbols are batch-API-incompatible,
not actually unpriceable.

#### Scope
Rename `SHADOW_SPOT_BLACKLIST` → `TICKER_BATCH_BLACKLIST` (or similar)
in `app/config.py`, `app/data/binance_ticker.py`, `app/shadow/universe.py`,
related tests, and the frontend mirror in `OpenPositions.tsx`. Update
the comment block above the list to describe semantics accurately.
PR10.8 also documents this in the Inv2 commit chat.

#### Effort
~30 min. Pure rename + comment update. No behavior change. No tests
beyond compile-time renaming.

#### Why LOW
Just a misnomer. The filter does what it's supposed to do (avoids
batch 400) and the WS-tick path correctly fills Now/P&L for the
listed symbols when they trade. Operator confusion was the only
downside; the PR10.8 tooltip + ⓘ icon already mitigate the user-facing
ambiguity.

---

### FU-33 — Slippage circuit-breaker (FIDAUSDT-class outliers)

**Status**: open · **Discovered**: 2026-05-20 (PR-DIAG-1.5 Q2)
**Severity**: MEDIUM (single-symbol tail-risk; observable in shadow today, real-money risk if flipped to fully-auto without guardrail)

#### Symptom
PR-DIAG-1.5 Q2 surfaced a FIDAUSDT trade on 2026-05-18 05:00:00 UTC that
hit its stop-loss with a realized pnl_pct of **-14.50%** vs the
expected SL distance of roughly -1.45% (10x slippage past SL). Cause:
the candle that triggered the SL had a low far below the SL price —
crypto thin-liquidity gap. The strategy's expected-loss math
(`SL_ATR_MULT=1.5`) assumes the close-on-touch model holds; for
thin-book symbols at unfortunate moments, it doesn't.

Concrete row from Q2:
```
FIDAUSDT  LONG  entry=0.02492  SL=0.02131  TP=0.03215
  closed 18h after open
  pnl_pct = -14.502  (expected at touch: ~ -1.45)
```

This single outlier was 10x worse than the next-worst SL in the same
window. Two of the three FIDAUSDT STOP_LOSS exits in the 30d window
showed the same pattern (-14.5%, -6.9%, -4.8% vs an expected baseline
of ~-1.5%).

#### Scope
Add a runtime "slippage circuit-breaker" gate that fires when a closed
trade's realized SL pnl_pct exceeds expected by a factor of N (default
3x). Behavior on trip:
- Mark the symbol as cooldown'd in `shadow_cooldowns` for a configurable
  duration (default 24h).
- Log WARNING + record_heartbeat on a new `slippage_circuit_breaker`
  worker name so the watchdog picks up repeated trips.
- Operator gets visibility in the Per-Asset Stats card (potential FU
  for the UI side).

For real-money trading the breaker should be more aggressive (the
realized 10x slippage on a single LONG is unacceptable on a leveraged
fully-auto position).

#### Effort
~1-2 days. New module under `app/core/gates/` mirroring the PR-strategy-1
`entry_quality` pattern. Wires into `shadow_worker._maybe_close_position`
on the SL exit branch. Plus a new Settings field for the multiplier +
cooldown duration. No DB migration (uses existing `shadow_cooldowns`
table).

#### Why MEDIUM not HIGH
Today the bot is in `mode=manual`, so the only impact is shadow-data
quality (one outlier biases the symbol's WR + avg_pnl_pct stats badly
— FIDA's avg SL is -8.74% vs the rest of the universe at -0.5% to
-2%). When real-money trading flips on, this becomes HIGH — recommend
shipping FU-33 before `AUTONOMOUS_TRADING_ENABLED=True`.

#### Out of scope for PR-strategy-1
PR-strategy-1's entry-quality gate is preventive (don't open low-score
LONGs / any SHORTs). FU-33 is reactive (cooldown the symbol after
demonstrated bad slippage). Different category — separate PR.

### FU-34 — `ohlcv` table is empty (0 rows ever); no persisted candle history

**Impact.** Any historical-path research (MFE/MAE, drawdown replay, entry-timing
analytics) requires on-demand Binance kline fetches — the `ohlcv` table exists
but was never populated by any worker. A `LEFT JOIN ohlcv ON ...` silently
returns empty rather than raising, so a query that assumes the table has data
will produce a misleading zero result. Discovered 2026-07-28 during the Phase 1
MFE study; workaround pattern in `backend/scripts/mfe_mae_curve.py` (fetch klines
on demand via the public SPOT `/api/v3/klines` endpoint — no auth required, well
under Binance rate limits at sequential-200ms cadence). Not a fix target;
recorded so the next author doesn't lose an hour to it.

### FU-35 — `p_win` column is 100% NULL; `predict_p_win` never implemented

**Impact.** `shadow_trades.p_win` has been NULL for every trade since PR1
shipped. Root cause: `backend/app/core/scoring/p_win_calibrator.py:predict_p_win`
is a documented PR1 stub that always returns None (see module docstring —
"PR5 will replace"). PR5 was never fully implemented. Sibling PR1 columns are
partially populated: `effective_score` and `realized_vol_20d` are 89.7% NULL
(compute-path failures on newer symbols lacking 20d history); `mtf_agreement`
and `funding_directional_adj` populate reliably (~83% for fdadj).

Discovered 2026-07-28 during Phase 1 MFE study; the `pwin-threshold-whatif`
probe works because it refits isotonic in-memory on demand — analysis-time
calibrator is the operational workaround. Aug 3-10 dual-axis validation (score
deciles × p_win bands) uses this same refit approach in `pwin_threshold_whatif.py`;
no fix required to run the validation.

Fix path (if operator wants persistent p_win column populated): implement PR5
per the docstring — sklearn isotonic fit-and-persist per direction, nightly
refit worker, lazy-load at predict-time. Non-trivial (~1-2 days). Only affects
trades opened AFTER deploy; historical NULLs stay NULL.

**PR5 deferred (operator ruling 2026-07-28):** analysis-time calibrator
(via `pwin_threshold_whatif` in-memory refit) is the operational workaround
for Aug 3-10. PR5 real implementation not worth 1-2 days plus soak for the
~50-100 rows that would populate inside the validation window. Recorded here
as a capability gap to revisit after the Aug decision.

**Evidence hierarchy for Aug 3-10 (operator ruling 2026-07-28):**
- **`entry_score`** — primary (stored ground truth on every trade).
- **`p_win`** — corroborating only. Because every p_win result this project
  has ever produced comes from an in-memory refit on a small validation
  split (stored p_win has ALWAYS been NULL), p_win MUST NOT be the
  tiebreaker if the two axes disagree. Note: the July [0.24-0.35] p_win-band
  contradiction — where a specific window looked profitable in the
  isotonic-refit output — falls in this category and cannot on its own
  override an entry_score-axis finding.

### FU-36 — `effective_score` + `realized_vol_20d` are analytics-only (89.7% NULL is a gap not a bug)

**Verified 2026-07-28** in response to the Phase 1 MFE-study question of
whether the ~90% NULL rate on these two columns represents a live-behavior
bug. It does not.

**Trace (read-only, dev tip 2026-07-28):**
- Producer: `backend/app/core/predictor.py:406-407` calls
  `compute_realized_vol_20d(_bar_list)` and `compute_effective_score(final.score, realized_vol)`.
  Both are wrapped in a `try/except → log.exception → None` fail-open at
  predictor.py:403-410. Values are then packed into a tuple at :671-672 and
  attached to the Prediction object at :715-716.
- Consumers of the STORED `shadow_trades.effective_score` column: **none**
  in any decision path. It's written by `payload_builders.build_shadow_trade_payload`
  (payload_builders.py:198), and never re-read. Grep of `effective_score` in
  `backend/app/**` returns only the write path (payload builders, worker attach,
  persistence, live_prediction pass-through) plus API schema output.
- Consumers of the STORED `shadow_trades.realized_vol_20d` column: **none**.
  Same story — write-only column.
- The dispatcher entry-quality gate at `backend/app/core/gates/entry_quality.py:184-215`
  has a LOCAL variable named `effective_score` (line 188) initialized from
  `entry_score` and adjusted by Layer-2 pattern boost/penalty. **This is
  NOT the stored column** — it's a different computation with a colliding
  name. The gate reads `entry_score` (populated on every trade), never the
  stored `effective_score`. Naming collision is a readability trap but not
  a live-behavior bug.
- `realized_vol_20d` feeds `compute_effective_score` at vol_normalization.py:83
  (returns None if rvol None per :106) — but the downstream `effective_score`
  it produces is only stored, never gated on.

**Why the 89.7% NULL rate on both.** Correlated failure: same compute path
(`realized_vol` → `effective_score`) so nulling one nulls the other. Root
cause is `compute_realized_vol_20d` requires ≥20 daily bars derived from
the input bar list; the input is the predictor's in-memory bar buffer.
Every backend recreate resets in-memory buffers; prod recreated ≥5 times
in the 6 days preceding this finding (see FU-35 companion investigation).
Bars accumulate from that point at ~24/day; a symbol needs ~20 days of
continuous subscription post-recreate to accumulate 20 daily closes. This
correlates with the FU-35 KEEPALIVE_TOP_N fleet-rotation hypothesis: symbols
that rotate in and out of top-20 don't accumulate continuous history.

**Verdict.** 89.7% NULL on effective_score + realized_vol_20d is an
**analytics gap** (missing observability data), NOT a live-behavior bug.
No fix required for live trading; if operator wants continuous stored
history, the fix would be to make the vol computation source-of-truth-agnostic
(e.g. compute from on-demand Binance klines rather than in-memory buffer).

### FU-37 — Shadow cooldown is PER-TIMEFRAME, no cross-TF check, no per-symbol concentration limit

**Discovered 2026-07-30** while investigating a 4× −5% COTIUSDT cluster over
9 hours (2026-07-29 → 2026-07-30) that dragged the top-20 60d avg from
+0.228%/trade to +0.0822%/trade.

**Trace:** `SHADOW_COOLDOWN_HOURS = {"1h": 4.0, "15m": 4.0}` at
`backend/app/config.py:84`. `set_cooldown` at `backend/app/shadow/worker.py:453`
fires after every close. `shadow_cooldowns` table PK is
`(user_id, symbol, timeframe)` — so a `(COTIUSDT, 1h)` cooldown does NOT
block a `(COTIUSDT, 15m)` new open. Grep of `backend/app/shadow/**` and
`backend/app/trading/**` for `concentration | max_per_symbol | MAX_PER_SYMBOL`
returned zero hits — **no per-symbol concentration limit exists at all**.

**Observed exploit:** COTIUSDT ran 4 concurrent-lane stop-outs in 18h
(1h: 2253, 2270; 15m: 2271, 2302) plus 1 earlier TP win (15m: 2247).
Each individual open was LEGAL under per-TF cooldown, but the aggregate
lost -20% on one symbol in less than a day.

**Interaction with live path (CORRECTED 2026-07-30 via settings-check
probe on running container):** `LIVE_COOLDOWN_ENABLED = True` on prod
(overridden in operator's `.env`; the `config.py:114` default `False`
does NOT apply on the running container). Live cooldown is per-symbol
(no timeframe column on `live_cooldowns`); live path is 1h-only per
fleet-cap ratification (`KEEPALIVE_TIMEFRAME = "1h"`, singleton also
1h). So live has EXACTLY ONE cooldown lane per symbol, and a stop-out
on COTI blocks all further COTI entries for `LIVE_COOLDOWN_HOURS_BY_
OUTCOME['stop_loss'] = 8h` — matching shadow's per-TF 4h for the 1h
lane and being STRICTER than shadow because live has no separate 15m
lane to re-enter through.

**Direction of bias (corrected):** shadow's cross-TF re-entry cluster
(4× COTI −5% in 18h across 1h+15m) CANNOT happen in live. Live is
1h-only + per-symbol cooldown. So **shadow OVERSTATES live tail-loss
frequency**, and the shadow-side baseline is PESSIMISTIC for live
(not optimistic as earlier stated).

**Standing rule established 2026-07-30**: live capacity expansion is
gated on shadow demonstrating positive net edge, never on measurement
speed. See `docs/superpowers/decisions/2026-07-30-live-capacity-
expansion-rule.md` when written; memory `live_capacity_expansion_rule`.

**Fix candidates (still not scheduled, but the ranking changed):**
1. Cross-TF cooldown on the SHADOW side — extend `shadow_cooldowns` PK
   to `(user_id, symbol)` only, OR add a cross-TF check that treats any
   cooldown row for the symbol as blocking all TFs. This aligns shadow's
   measurement with live's actual behavior — makes shadow a better
   proxy for what live would do. Non-urgent unless shadow-vs-live
   accuracy becomes a decision blocker.
2. Per-symbol concentration limit on shadow (max N losses per rolling
   window before hard blacklist). Same rationale as (1).
3. Live cooldown parity: ALREADY IN PLACE — this correction supersedes
   the earlier "flip LIVE_COOLDOWN_ENABLED=True" candidate.

Sequencing: not a live-blocker (mode=manual today). Not a blocker for
Aug-3-10 either (Aug 3-10 CANCELLED per operator 2026-07-30). Only a
blocker for shadow-vs-live proxy accuracy, and that's dominated by the
signal-earns-its-own-fees finding, not this cooldown asymmetry.

### FU-38 — Asymmetric SL cap / uncapped TP (informational, not a defect)

**Verified 2026-07-30** during Q1 investigation of the -5% SL floor
hypothesis (see FU-37 for the COTI trigger context).

**Formula** at `backend/app/shadow/engine.py:167-168` (LONG):
```
sl = max(entry - 1.5*ATR, entry * 0.95)     # closer stop wins → 5% cap
tp = entry + 3.0*ATR                         # UNCAPPED
```

Nominal R:R = 2.0:1 (3×ATR / 1.5×ATR) when ATR is small. When ATR
exceeds 3.33% of entry (`1.5×ATR > 0.05`), the 5% floor binds → SL is
tighter than nominal 1.5×ATR while TP stays at unbounded 3×ATR distance.

**Top-20 60d empirical split (n=179, gate 0.36, LONG):**

| group | n | WR% | avg_pnl | avg_sl_dist | avg_tp_dist | realized_RR | -5% floor hits |
|---|---|---|---|---|---|---|---|
| atr_bound (1.5×ATR≤5%) | 167 | 28.7 | -0.064 | 1.21% | 3.10% | 2.41:1 | 0 |
| cap_bound (1.5×ATR>5%) | 12 | 33.3 | +2.123 | 5.23% | 20.30% | 3.93:1 | 6 |

**COUNTERINTUITIVE**: the cap-bound cohort is currently PROFITABLE
despite the -5% floor because uncapped TP delivers big wins on the
6/12 non-stopped trades (avg ~+9%/win). The asymmetry is accidentally
favorable in the aggregate 60d window.

Caveats: n=12 is thin. The 6/12 -5% floor hits include the recent 3×
COTIUSDT cluster (FU-37); the "positive avg" is dominated by 6
historical big TP wins with high variance.

**Not a strategy defect. IS a variance amplifier.** Any decision
touching the cap should acknowledge the cap_bound cohort's 4× wider
TP distance and 6× wider R:R range vs the atr_bound cohort. Removing
or widening the SL cap would degrade the currently-profitable
cap_bound cohort — the fix candidate is more nuanced than "widen the
stop".

### FU-39 — Live per-symbol open-position check missing — ✅ FIXED-BY PR (pending merge)

**Class-1 SAFETY defect**, live-money. Discovered 2026-07-30 during
repeat-signal position-upgrade investigation.

**Root cause**: the only position gate in `dispatcher.dispatch` was
GLOBAL — `if user.open_positions_count >= user.max_concurrent_positions`
at `backend/app/trading/execution/dispatcher.py:~882`. `open_positions_count`
comes from `glue.build_user_context` which counts `SELECT count(*) FROM
live_trades WHERE user_id=:u AND status='open'` across all symbols.

**Failure mode**: with `max_concurrent_positions=5` and 0-4 slots used,
a second approved signal on a symbol already open could reach
`_place_live_order` / `_place_approved_order`, which calls
`place_with_sltp` and lays a NEW market entry + a NEW SL/TP pair on
the same Binance position. Because `place_with_sltp` uses
`closePosition=true` on the SL/TP, the newer (higher) stop covers
the DOUBLED position, so a routine wick can prematurely liquidate the
older entry at 2× size. Prior known win-loss asymmetry in the same
class (BANKUSDT repeat-signal cluster) was hypothesized to be signal
confirmation but is at least partially explained by this defect.

**Fix**: new `symbol_position_gate.get_open_position_trade_id` — pure
read-only `SELECT id FROM live_trades WHERE user_id=:u AND symbol=:s
AND status='open' LIMIT 1`. Called from BOTH:
- `dispatcher.dispatch` (pre-card, before the global max-concurrent
  check) — emits new `blocked_symbol_position_open` DispatchOutcome
- `telegram_polling._place_approved_order` (approve-time Phase 0b,
  before any Binance call) — logs warning with same outcome name and
  returns None; no live_trades row inserted, no order placed.

Defense-in-depth at both sites because a card can be sent, the
operator can tap Approve minutes later, and in the interim a
different signal or manual trade may have opened a position on the
same symbol.

**Regression coverage**:
- `tests/unit/test_symbol_position_gate.py` — 7 cases including fail-
  open on DB error, ignores closed / pending / different-symbol /
  different-user rows.
- `tests/unit/test_trading_dispatcher.py::test_blocked_when_symbol_already_has_open_position`
  — seeds open row + asserts blocked outcome AND that no
  `telegram_signals` card row was written.
- `tests/unit/test_trading_dispatcher.py::test_allowed_when_open_position_is_different_symbol`
  and `::test_allowed_when_only_closed_row_on_symbol` — negative
  cases.
- `tests/unit/test_telegram_polling.py::test_place_approved_order_blocked_when_symbol_has_open_position`
  — seeds pre-existing open + asserts no Binance stub call and no
  new live_trades row.

**Error policy (split by caller)**:
- Pre-card gate — **fail-OPEN** on DB error. A card is only a
  notification; operator is still the approval step. Backstopped
  by the GLOBAL max-concurrent gate + the approve-time gate below.
- Approve-time gate — **fail-CLOSED** on DB error. Distinct outcome
  literal `blocked_position_check_failed` (separate from
  `blocked_symbol_position_open`) so the operator can distinguish
  a verification failure from a real duplicate. Asymmetric cost
  rationale: missed trade ~= zero cost (net-zero edge, signal
  recurs); doubled position = 2× risk on one symbol with an
  undesigned exit level, at the moment the system is least healthy.
The helper `get_open_position_trade_id` itself does NOT swallow
exceptions — callers own their policy via try/except wraps.

### FU-40 — W4 flow-features fire-and-forget tasks bypass the rate limiter

**Problem**: `app/data/ratelimit.py`'s `RateLimitedClient` (token-bucket,
Binance-response-header-aware) is the app's real rate-limiting
mechanism, and `update_flow_cache`'s own docstring says it "reuses an
injected httpx.AsyncClient when provided (preferred in the shadow
worker to reuse the shared connection pool)". In practice the shadow
worker's actual call site never injects one:
`backend/app/shadow/worker.py:399` —
`_aio.create_task(_upd_flow(candle.symbol))` — passes only `symbol`.
`update_flow_cache` (`app/core/features/flow_features.py:82-84`) falls
back to `http = httpx.AsyncClient(timeout=_TIMEOUT)`, a fresh unmanaged
client per call, with no shared bucket and no coordination across
symbols.

**Blast radius**: this fires once per symbol on every 1h candle close
(`if tf == "1h"`), i.e. one unmanaged client + 3 concurrent Binance
Futures GETs (`globalLongShortAccountRatio`,
`takerlongshortRatio`-class endpoint, OI history) per symbol, for the
whole shadow universe (~30-46 symbols per the 2026-08-06 system-truth
run), all within the same few seconds at the top of every hour — a
burst the app's own rate limiter exists specifically to smooth out.

**Why not yet a real incident**: current call volume is small — 3
endpoints × ~30-46 symbols (per system-truth's `shadow_trades`/
`predictions` distinct-symbol counts) ≈ 90-138 calls in the same
few-second burst once per hour, far under Binance's published limits
even unthrottled — and each
`update_flow_cache` call independently catches + logs (DEBUG) any
network/parse error without crashing the worker — a 429 here just
leaves that symbol's flow_feature_snapshots row partially null for one
hour, not a cascading failure. Flagged by the operator (2026-08-06,
system-truth follow-up) as "worth a line in KNOWN_ISSUES — harmless
now, but the kind of thing that bites at scale": as the shadow universe
grows or Binance tightens limits, this burst pattern has no backoff and
no shared budget with the rest of the app's Binance traffic.

**Fix (not yet scoped/authorized)**: the shadow worker doesn't currently
hold a long-lived client either — its own kline-seed fetch
(`worker.py:203`) opens a scoped `async with httpx.AsyncClient() as
http:` per call, same pattern. A real fix needs a genuinely shared,
worker-lifetime `RateLimitedClient` instance threaded into both the
kline-seed path and `_upd_flow(candle.symbol, http=...)`, not just
patching the one call site.

### FU-41 — SHADOW_15M_ELIGIBLE_FOR_PROMOTION: real-authorization gap — ✅ FIXED

**Class-2 defect**, real-money-adjacent authorization path. Discovered
2026-08-06 (defect sweep, TIER 3).

**Root cause**: `SHADOW_15M_ELIGIBLE_FOR_PROMOTION` (`config.py:92`,
default `False`) was only ever read by `app/api/routes/bot_status.py`'s
`/promotion-gate` endpoint — a **read-only dashboard display**.
`app/trading/promotion.py::compute_gates_from_db`, the function that
actually computes the gate snapshot for both real mode-upgrade paths, had
no timeframe parameter at all. Mode upgrades always included 15m shadow
trades regardless of the flag, in TWO real authorization call sites:
- `app/trading/auto_promote.py::evaluate_user` — the daily automated
  manual → telegram-approve → fully-auto promotion (`auto_promote_task`)
- `app/api/routes/me.py:332` — the manual, TOTP-gated `PATCH
  /me/trading-mode` upgrade endpoint

`bot_status.py`'s own comment claimed the display "mirrors promotion.py
gate logic... so the display matches what the actual authorization check
will see" — true for `direction_filter`, false for the timeframe filter.

**Fix**: `compute_gates_from_db` gained an `exclude_timeframes: list[str]
| None` parameter (same parametrized `NOT IN` pattern
`bot_status.py::_select_trades_since` already used). Both real call sites
now derive `exclude_timeframes = None if
_cfg.SHADOW_15M_ELIGIBLE_FOR_PROMOTION else ["15m"]`, mirroring the
existing `direction_filter` derivation from `DISABLE_SHORT_SIGNALS`
immediately above each call site.

**Regression coverage**: `tests/unit/test_promotion_timeframe_filter.py`
— SQL/param wiring, metric correctness with a blended 1h+15m fixture,
composition with `direction_filter`, and both `evaluate_user` branches.
`tests/unit/test_auto_promote.py`'s SQLite fixture predated the real
`timeframe` column on `shadow_trades` — added it (seeded `'1h'`, matching
its existing trades) so the new `NOT IN` predicate doesn't break on a
stale test schema.

**Live-execution caveat — this is the actual blocker to record**: per the
2026-08-07 worker-heartbeat census, `auto_promote_task` has **zero beats
ever** — `AUTO_PROMOTE_TO_TELEGRAM_ENABLED` and
`AUTO_PROMOTE_TO_FULLYAUTO_ENABLED` have both been off for the entire
project history. This fix has therefore never executed against real
data. **Before either `AUTO_PROMOTE_TO_*` flag is ever enabled**: run a
staging dry run (or at minimum the `/promotion-gate` endpoint against
real prod shadow_trades with both flag values) to confirm
`exclude_timeframes` behaves as expected against the real data shape,
not just the SQLite fixtures above.
