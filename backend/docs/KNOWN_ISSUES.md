# Backend — Known Issues

Long-lived issues that affect prod but are out of scope for current PRs.
Each entry has root cause, scope of impact, and remediation options.

---

## Tracked follow-up PRs (out of scope for the 9-PR upgrade rollout)

Operational fixes orthogonal to the upgrade plan. Acknowledged by
operator 2026-05-16. Not blocking PR1 or the 9-PR rollout.

### FU-1 — Wire heartbeats for all 16 registered workers
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
- **Status**: queued; high-priority operational hygiene.

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

## Worker heartbeats — 12 of 16 registered workers never beat

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
