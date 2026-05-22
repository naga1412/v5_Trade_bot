# PR-PREFLIGHT-ALERT — surface preflight failures via telegram + heartbeat

**Status:** spec drafted 2026-05-22.
**Branch:** `feat/pr-preflight-alert` (off `origin/dev` at `ea843be` = PR-FIX-FLAG-BINDING squash).
**Class:** observability + safety alerting. **NO trading-logic change, NO flag default change.**

---

## Problem

The autonomous-trading subsystem runs a 5-check preflight at lifespan boot:

1. `master_passphrase_set`
2. `vault_decrypt_ok`
3. `binance_permissions_safe`
4. `migration_0016_applied`
5. `audit_chain_intact`

When any check fails, [`app/main.py:382-390`](backend/app/main.py#L382-L390) logs:

```python
log.error(
    "autonomous trading DISABLED: pre-flight failed (%d/%d) — %s",
    len(pf.failures()), len(pf.checks), failures,
)
```

…and skips spawning `telegram_poller_task`, `liquidation_monitor_task`, `live_exit_monitor`. **No telegram alert, no heartbeat row, no DB record.** Operator only finds out by manually checking `worker_heartbeats` N days later.

PR-DIAG-AUTONOMOUS-WORKERS confirmed: workers have been dead 2+ days (since 2026-05-19T23:40Z), preflight failure logged at 2026-05-22T03:02:10Z, operator never alerted. The watchdog's "stale" log lines are also stdout-only (no escalation).

## Fix

Three additions, all inside the preflight call path in [`app/main.py:282-394`](backend/app/main.py#L282-L394). **No changes to preflight check logic, no changes to which workers spawn, no changes to which flags default to what.**

### Fix A — telegram alert on preflight FAIL

Use the existing `app.ops.alert_routing.alert_admin(message, level="critical")` function. With `level="critical"` it routes to Telegram first (per the existing precedence: Telegram → SMTP → log).

**Message format:**
```
⚠️ Preflight FAILED (4/5) — autonomous workers NOT spawned.
Failed checks: audit_chain_intact.
Investigate before next deploy.
```

(The exact text below in §Implementation.)

If telegram is unavailable (no `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`), `alert_admin` already falls through to SMTP, then to `log.warning`. We additionally emit a `log.critical` at the call site so an unconfigured-telegram operator still sees a level-elevated signal in container logs (currently only `log.error`).

### Fix B — heartbeat row on preflight FAIL

Use the existing `app.ops.heartbeat.record_heartbeat(session_factory, worker_name, *, status, details)` helper. Insert a row with:

- `worker_name='preflight_gate'`
- `status='failed'`
- `details={'passed_count': 4, 'total_count': 5, 'failed_checks': ['audit_chain_intact'], 'failed_check_details': {'audit_chain_intact': 'chain break in predictions at row index 796: ...'}}`

This brings the failure into the standard `worker_heartbeats` audit trail. The watchdog already escalates `status='error'` heartbeats; `status='failed'` is the same idea but more specific to "preflight ran and didn't pass" vs "worker crashed mid-loop." Future enhancement (not this PR): teach the watchdog to escalate `status='failed'` on the `preflight_gate` row too.

### Fix C — heartbeat row on preflight PASS

Same helper, with:
- `worker_name='preflight_gate'`
- `status='passed'`
- `details={'passed_count': 5, 'total_count': 5}`

This distinguishes "preflight never ran" (no row at all — autonomous_trading_enabled=False) from "preflight ran and passed" (row with status='passed'). The watchdog can then differentiate "this deploy was supposed to enable autonomous trading and did" vs "this deploy never reached the preflight step."

### Fix D — exception path

If `run_preflight()` itself raises (catch at [`app/main.py:391`](backend/app/main.py#L391)), the existing handler only logs. Mirror Fix A + Fix B with:
- alert message: `⚠️ Preflight RAISED: {exception_type}: {exception_str[:200]}`
- heartbeat: `status='raised'`, `details={'error_type': type(e).__name__, 'error_msg': str(e)[:500]}`

## What this PR does NOT change

- ❌ Does NOT fix the underlying FU-24 chain race in `insert_with_chain` (separate PR).
- ❌ Does NOT decouple the 3 monitoring workers from `audit_chain_intact` (PR-DECOUPLE-WORKERS — separate).
- ❌ Does NOT change preflight check logic, thresholds, or ordering.
- ❌ Does NOT auto-retry preflight after failure.
- ❌ Does NOT add any new env vars, settings fields, or migrations.
- ❌ Does NOT change which workers spawn or under what conditions.

## Implementation sketch

```python
# Imports added near the top of app/main.py:
from app.ops.alert_routing import alert_admin as _route_alert
from app.ops.heartbeat import record_heartbeat as _record_heartbeat

_PREFLIGHT_WORKER_NAME: Final[str] = "preflight_gate"

# Inside the autonomous_trading_enabled branch (current line ~282):
if settings.autonomous_trading_enabled:
    try:
        async with session_factory() as preflight_session:
            pf = await run_preflight(
                preflight_session, use_testnet=settings.binance_use_testnet,
            )
        if pf.all_passed:
            log.info("autonomous trading: pre-flight passed (%s)", pf.summary_line())
            # PR-PREFLIGHT-ALERT Fix C: record the pass.
            await _record_heartbeat(
                session_factory, _PREFLIGHT_WORKER_NAME,
                status="passed",
                details={"passed_count": len(pf.checks), "total_count": len(pf.checks)},
            )
            ...existing spawn chain...
        else:
            failed_checks = [c.name for c in pf.failures()]
            failures = "; ".join(f"{c.name}={c.detail}" for c in pf.failures())
            # PR-PREFLIGHT-ALERT: elevate from error→critical AND alert.
            log.critical(
                "autonomous trading DISABLED: pre-flight failed (%d/%d) — %s",
                len(pf.failures()), len(pf.checks), failures,
            )
            # Fix A — telegram (best-effort)
            try:
                await _route_alert(
                    f"⚠️ Preflight FAILED ({len(pf.checks) - len(pf.failures())}/"
                    f"{len(pf.checks)}) — autonomous workers NOT spawned. "
                    f"Failed checks: {', '.join(failed_checks)}. "
                    f"Investigate before next deploy.",
                    level="critical",
                )
            except Exception as alert_exc:  # noqa: BLE001
                log.error("preflight-alert dispatch failed: %s", alert_exc)
            # Fix B — heartbeat (best-effort, record_heartbeat itself swallows)
            await _record_heartbeat(
                session_factory, _PREFLIGHT_WORKER_NAME,
                status="failed",
                details={
                    "passed_count": len(pf.checks) - len(pf.failures()),
                    "total_count": len(pf.checks),
                    "failed_checks": failed_checks,
                    "failed_check_details": {c.name: c.detail for c in pf.failures()},
                },
            )
    except Exception as e:  # noqa: BLE001
        # Fix D — exception path
        log.critical("autonomous trading pre-flight raised: %s", e)
        try:
            await _route_alert(
                f"⚠️ Preflight RAISED: {type(e).__name__}: {str(e)[:200]}",
                level="critical",
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            await _record_heartbeat(
                session_factory, _PREFLIGHT_WORKER_NAME,
                status="raised",
                details={"error_type": type(e).__name__, "error_msg": str(e)[:500]},
            )
        except Exception:  # noqa: BLE001
            pass
```

## TDD test plan

All tests live in `backend/tests/integration/test_main_preflight_alert.py` (new file). They patch `run_preflight` to return controlled results + mock the alerter + assert heartbeat row contents via in-memory SQLite.

1. **`test_preflight_pass_writes_heartbeat_with_passed_status`**
   Patch `run_preflight` to return all-passed. Assert a row with `worker_name='preflight_gate'`, `last_status='passed'`, `details` matches.

2. **`test_preflight_fail_writes_heartbeat_with_failed_status_and_failed_check_names`**
   Patch `run_preflight` to return one failure (`audit_chain_intact`). Assert heartbeat row has `last_status='failed'`, `details.failed_checks == ['audit_chain_intact']`, `details.passed_count == 4`.

3. **`test_preflight_fail_sends_telegram_alert`**
   Patch `run_preflight` to fail + patch `alert_routing.alert_admin` (or wherever the imported reference lives in main.py). Assert `alert_admin` called once with `level='critical'` and message containing `'Preflight FAILED'` + `'audit_chain_intact'`.

4. **`test_preflight_fail_logs_critical_if_telegram_unavailable`**
   With no `TELEGRAM_BOT_TOKEN`/`CHAT_ID` env set (clean env via monkeypatch.delenv), preflight fail. Assert `caplog` captured at least one record at `CRITICAL` level mentioning preflight failure. (The actual telegram POST is short-circuited inside `alert_admin` when env is unset.)

5. **`test_preflight_pass_does_not_send_alert`**
   Patch all-passed. Patch `alert_admin`. Assert `alert_admin` NEVER called (regression — alerts only on failure).

6. **`test_preflight_raises_writes_heartbeat_with_raised_status`**
   Patch `run_preflight` to raise `RuntimeError("simulated")`. Assert heartbeat `last_status='raised'`, `details.error_type='RuntimeError'`. Also assert alert sent.

7. **`test_preflight_alert_dispatch_failure_does_not_kill_lifespan`**
   Patch alerter to itself raise. Assert lifespan completes without uncaught exception; heartbeat row still written.

8. **`test_preflight_heartbeat_failure_does_not_kill_lifespan`** (regression-safety)
   Patch `record_heartbeat` to raise (it shouldn't, but defense in depth). Assert lifespan completes; alert still sent.

## Audit chain impact

**None.** `worker_heartbeats` is not in `HASH_PAYLOAD_COLUMNS`. Existing chained tables untouched. `alert_routing.alert_admin` writes nothing to the DB.

## V-7 latency budget

Two extra short-lived DB writes (one heartbeat insert + one telegram HTTP call) gated behind the lifespan startup-only branch. **Not in any hot path.** Boot time impact: ~50ms on slow Telegram response, 0ms otherwise.

## Rollback

`git revert <PR-PREFLIGHT-ALERT-squash>`. No DB migration, no schema change, no flag introduced.

## Auto-merge authorization

Per operator's PR-PREFLIGHT-ALERT directive: *"Class: observability + safety alerting, NO trading-logic change, NO flag default change. Soak: 12h observability-class."*

## Commit message

```
feat(pr-preflight-alert): telegram + heartbeat surface for preflight gate
```

## Post-deploy verification

Container restart triggers preflight (currently FAILS due to FU-24 chain break at row 796).

**Expected post-PR behavior:**
- Telegram alert fires within seconds of restart with `⚠️ Preflight FAILED (4/5) — ... Failed checks: audit_chain_intact ...`.
- `worker_heartbeats` row inserted: `worker_name='preflight_gate'`, `last_status='failed'`, `details.failed_checks=['audit_chain_intact']`.
- Container log shows `CRITICAL` level (not just ERROR).

**If alert does NOT fire on the failing preflight, that's a regression — investigate, do not merge.**

## Operator follow-up (out of this PR's scope)

After PR-PREFLIGHT-ALERT lands, operator's separate action is to run the FU-24 sweep to heal the chain (operator-only DB write per merge_authorization). Container restart will then spawn the 3 workers (with the new alert wiring confirming preflight now PASSES with heartbeat `status='passed'`). Then PR-DECOUPLE-WORKERS (the structural fix) can be designed.
