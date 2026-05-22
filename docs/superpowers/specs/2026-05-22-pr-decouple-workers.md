# PR-DECOUPLE-WORKERS — split preflight into chain-reader / chain-writer profiles

**Status:** spec drafted 2026-05-22.
**Branch:** `feat/pr-decouple-workers` (off `origin/dev` at `9fe5754` = PR-PREFLIGHT-ALERT squash).
**Class:** structural fix to autonomous-trading spawn chain. **Behavior-changing** (more workers may spawn under chain-broken conditions), **safety-positive** (more safety-net workers alive, never fewer).

---

## Problem

The 3 autonomous safety-net workers (`telegram_poller_task`, `liquidation_monitor_task`, `live_exit_monitor`) are gated by all 5 preflight checks including `audit_chain_intact` at [`app/main.py:281-394`](backend/app/main.py#L281-L394). But these workers do **not** write to the audit chain — they READ open positions, call Binance, optionally update `live_trades.exit_reason` (which is an UPDATE on existing rows, NOT a new chained INSERT). Their correctness does not depend on chain INSERT integrity.

Today's state confirms the coupling problem: FU-24's concurrent-insert race in `insert_with_chain` breaks `predictions.prev_hash` linkage daily, which fails `audit_chain_intact`, which blocks the 3 safety-net workers — leaving any live trade without buffer-breach guard, TP/SL monitor, or telegram approval path. This is the **opposite** of the intent (safety net dies when write-side has a race).

Verified non-writer status (via grep of safety-net worker source):
- `app/trading/execution/liquidation_monitor.py`: only SELECTs live_trades + UPDATEs exit_reason. No `insert_with_chain`.
- `app/trading/execution/live_exit_monitor.py`: same pattern. No `insert_with_chain`.
- `app/ops/telegram_polling.py`: writes to `mode_change_log` via approval flow, but ONLY when the operator approves a trade (downstream of the spawn gate, not at startup). At lifespan boot the worker is just a long-poll loop.

## Fix

Split preflight into two profiles. The 3 safety-net workers use the reader profile (4 checks). Future workers that DO write to the chain use the writer profile (all 5 checks, current behavior). Default behavior of `run_preflight()` is unchanged.

### Profiles

```python
# app/trading/preflight.py
from typing import Literal

PreflightProfile = Literal["chain_writer", "chain_reader"]
```

| Profile | Checks |
|---|---|
| `chain_writer` (default) | master_passphrase_set + vault_decrypt_ok + binance_permissions_safe + migration_0016_applied + **audit_chain_intact** |
| `chain_reader` | master_passphrase_set + vault_decrypt_ok + binance_permissions_safe + migration_0016_applied (skips audit_chain_intact) |

### `run_preflight` signature change

```python
async def run_preflight(
    session: AsyncSession,
    *,
    use_testnet: bool = True,
    secrets_path: Path | None = None,
    http: httpx.AsyncClient | None = None,
    profile: PreflightProfile = "chain_writer",   # NEW; default preserves existing behavior
) -> PreflightResult:
    ...
    # Existing 4 checks (passphrase, vault, binance, migration) run unconditionally.
    # The audit_chain_intact check (existing line 304) becomes gated on profile:
    if profile == "chain_writer":
        results.append(await check_audit_chain_intact(session))
    # When profile == "chain_reader": skip; result has 4 checks only.
    ...
```

This keeps the public `run_preflight()` API backwards-compatible — any caller that doesn't pass `profile=` gets the existing all-5-check behavior.

### `app/main.py` spawn chain

The spawn chain at [`app/main.py:282-394`](backend/app/main.py#L282-L394) currently does:

```python
pf = await run_preflight(session, use_testnet=...)         # all 5
if pf.all_passed:                                          # gate → spawn 3 workers
    ...spawn...
```

After this PR:

```python
# Gate spawn on reader profile (skips audit_chain_intact)
pf_reader = await run_preflight(session, use_testnet=..., profile="chain_reader")

# Separately check the chain (cheap; same DB session). Used ONLY for differential
# alerting — does NOT gate spawn.
chain_check = await check_audit_chain_intact(session)

if pf_reader.all_passed:
    if chain_check.passed:
        # Happy path: all 5 effective checks passed.
        log.info("autonomous trading: preflight passed (5/5)")
        await _record_heartbeat(name=_PREFLIGHT_WORKER_NAME, status="passed",
                                details={"profile": "chain_writer", "passed_count": 5, "total_count": 5})
    else:
        # Partial path: safety-net workers spawn, chain WRITER blocked.
        log.warning("autonomous trading: chain WRITER blocked (FU-24); safety-net OK")
        await _route_alert(
            "⚠️ Audit chain WRITER blocked (FU-24 race active). "
            "Safety-net workers RUNNING. Run FU-24 sweep when convenient. "
            "Live writes blocked until chain healed.",
            level="critical",
        )
        await _record_heartbeat(name=_PREFLIGHT_WORKER_NAME, status="reader_only_passed",
                                details={
                                    "profile": "chain_reader",
                                    "passed_count": 4, "total_count": 5,
                                    "failed_checks": [chain_check.name],
                                    "failed_check_details": {chain_check.name: chain_check.detail},
                                })

    # In BOTH branches above, spawn the 3 safety-net workers per the existing
    # vault → liquidation_monitor → live_exit_monitor → telegram_poller chain.
    ...existing spawn chain...
else:
    # Reader-side failure: fall through to existing PR-PREFLIGHT-ALERT path
    # (same as today's behavior). log.critical, telegram alert, heartbeat with
    # status='failed'.
    failed_checks = [c.name for c in pf_reader.failures()]
    ...existing failure handler from PR-PREFLIGHT-ALERT...
```

### New heartbeat status values

| Status | Meaning |
|---|---|
| `passed` | All 5 checks passed (existing PR-PREFLIGHT-ALERT semantic) |
| `reader_only_passed` | **NEW** — 4 reader checks passed, audit_chain_intact failed; safety-net workers spawned |
| `failed` | Reader-side check(s) failed; no workers spawned (existing) |
| `raised` | `run_preflight()` raised an exception (existing) |

The watchdog already escalates `status='error'`-style entries; `reader_only_passed` is intentionally NOT an error — it's a documented partial state that the operator should resolve via FU-24 sweep at their convenience.

### New telegram alert message

| Pre-PR | Post-PR (with `reader_only_passed`) |
|---|---|
| (no alert when ALL 5 pass) | (no alert when ALL 5 pass) |
| `⚠️ Preflight FAILED (4/5) — autonomous workers NOT spawned. Failed checks: audit_chain_intact. Investigate before next deploy.` | `⚠️ Audit chain WRITER blocked (FU-24 race active). Safety-net workers RUNNING. Run FU-24 sweep when convenient. Live writes blocked until chain healed.` |

The new message correctly reflects the operational state: the safety net IS up, chain writes ARE blocked. Operator knows their bot is partially functional and what to do about it.

## TDD test plan

Add to `backend/tests/integration/test_main_preflight_alert.py` (extend the existing file from PR-PREFLIGHT-ALERT). The 8 existing tests stay green; new tests below.

1. **`test_preflight_chain_reader_profile_skips_audit_chain_check`** — call `run_preflight(profile='chain_reader')` against a session with broken chain; assert `result.all_passed` AND `len(result.checks) == 4`.

2. **`test_preflight_chain_writer_profile_includes_audit_chain_check`** — call `run_preflight(profile='chain_writer')` with a broken chain; assert `not result.all_passed` AND `'audit_chain_intact' in [c.name for c in result.failures()]`.

3. **`test_preflight_default_profile_is_chain_writer`** — call `run_preflight()` (no profile arg) with a broken chain; assert behavior matches `profile='chain_writer'` exactly (5 checks, failure includes audit_chain_intact). Regression test for backwards compat.

4. **`test_safety_net_workers_spawn_when_chain_broken_but_reader_passes`** — drive the lifespan with chain broken + everything else OK. Patch `start_liquidation_monitor` / `start_live_exit_monitor` / `start_telegram_poller`. Assert all 3 mocks called.

5. **`test_chain_broken_emits_reader_only_passed_heartbeat`** — same fixture. Assert `_record_heartbeat` called with `status="reader_only_passed"` AND `details` includes `failed_checks=["audit_chain_intact"]`.

6. **`test_chain_broken_emits_audit_chain_writer_blocked_alert`** — same fixture. Assert `_route_alert` called once with `level="critical"` and message containing `"Audit chain WRITER blocked"`, `"Safety-net workers RUNNING"`, and `"FU-24 sweep"`.

7. **`test_chain_intact_emits_passed_heartbeat_unchanged`** — chain OK + everything OK. Assert heartbeat `status="passed"` AND **no** `_route_alert` call (regression on PR-PREFLIGHT-ALERT's "no alert on pass").

8. **`test_reader_fail_uses_existing_pr_preflight_alert_path`** — reader check fails (e.g., vault decrypt fail). Assert behavior matches PR-PREFLIGHT-ALERT exactly: `_route_alert` with the OLD `Preflight FAILED` message, `_record_heartbeat` with `status="failed"`, **no** safety-net worker mocks called.

## What this PR does NOT change

- ❌ Does NOT change preflight check logic, thresholds, or ordering.
- ❌ Does NOT add or remove preflight checks.
- ❌ Does NOT auto-retry preflight on failure.
- ❌ Does NOT change which env vars or flags are read.
- ❌ Does NOT change the dispatcher / signal-flow chain-WRITE paths (predictions / live_trades inserts). Future workers that write to the chain still get the full 5-check `chain_writer` profile via the default.
- ❌ Does NOT fix FU-24's underlying race in `insert_with_chain` (separate PR).
- ❌ Does NOT touch `app/ops/alert_routing.py` or `app/ops/heartbeat.py` — uses both as-is.

## Audit chain impact

**None.** No new chained rows. No schema change. `worker_heartbeats` is unchanged in schema (just gains a new value in the existing `last_status` text column).

## V-7 latency budget

**Zero hot-path cost.** At lifespan boot:
- Pre-PR: `run_preflight()` called once → 5 checks → ~50ms total (audit_chain_intact dominates at ~10-30ms for a fresh chain; ~100-300ms for the 7-table walk under chain-broken state).
- Post-PR: `run_preflight(profile='chain_reader')` called once → 4 checks (~20ms) + `check_audit_chain_intact()` called once separately (~100-300ms). **Same SQL load as today.**

The `await check_audit_chain_intact(session)` is the same function the existing preflight calls — just exposed for direct invocation from `main.py`'s spawn chain. No new SQL.

## Rollback

`git revert <PR-DECOUPLE-WORKERS-squash>`. No DB migration, no schema change, no new env var, no new dependency.

## Auto-merge authorization

Per operator's PR-DECOUPLE-WORKERS directive: *"Class: behavior-changing (changes which workers spawn under chain-break conditions), but safety-positive (more workers alive, never fewer). Auto-merge per standing authorization. 4-6h soak to verify the 3 workers spawn with chain still broken."*

## Post-deploy verification

Prod's audit chain remains broken (FU-24 sweep has not been run yet — that's the operator's separate action per PR-OPS-FU24-SWEEP-PREP). Container restart on this PR's deploy will trigger preflight under the new code path.

**Expected post-deploy behavior:**

1. **No more `Preflight FAILED` alert.** Instead, a different alert arrives:
   > ⚠️ Audit chain WRITER blocked (FU-24 race active). Safety-net workers RUNNING. Run FU-24 sweep when convenient. Live writes blocked until chain healed.

2. **`worker_heartbeats` row:**
   - `worker_name='preflight_gate'`
   - `last_status='reader_only_passed'` (NEW value)
   - `details.profile='chain_reader'`, `details.passed_count=4`, `details.failed_checks=['audit_chain_intact']`

3. **The 3 previously-dead workers now heartbeat fresh** (within 2-3min of container start):
   - `telegram_poller_task` — last_status='ok', recent beat_at
   - `liquidation_monitor_task` — same
   - `live_exit_monitor` — same

4. **Backend log shows `WARNING` (not CRITICAL):**
   ```
   WARNING app.main: autonomous trading: chain WRITER blocked (FU-24); safety-net OK
   ```

**If telegram alert message is the OLD `Preflight FAILED` (not the new WRITER-blocked one), OR if the 3 workers stay dead, that's a regression — investigate, do not consider deploy successful.**

## Commit message

```
feat(pr-decouple-workers): split preflight into chain_writer / chain_reader profiles
```

## Operator follow-up (out of this PR's scope)

After PR-DECOUPLE-WORKERS lands AND the 3 workers are confirmed running with the broken chain:

1. **FU-24 sweep** (operator-only DB write per PR-OPS-FU24-SWEEP-PREP) heals the chain; next restart promotes the heartbeat from `reader_only_passed` → `passed`. Telegram receives the silent (no-alert) success — operator can `gh workflow run ops-debug.yml ... preflight-alert-verify` to confirm.

2. **FU-24 real fix** (separate PR, ~1-2 days): `pg_advisory_xact_lock` keyed on table name in `insert_with_chain` to serialize concurrent inserts per chained table. Removes the need for sweeps entirely. Out of this PR's scope.

3. **PR-DECOUPLE-WORKERS DOES NOT remove the chain-broken state.** It only ensures the safety-net workers don't die because of it. The chain remains broken until FU-24's sweep heals it.
