# Healer Phase 1 — Auto-Recovery Action Matrix

**Status:** proposal only. NOT implemented in Phase 0.
**Written:** 2026-07-23 (after Phase 0 detection layer landed via PR #352 + #353).

Phase 0 (detect-only) gave us the eyes: a watchdog that catches error-status
heartbeats + a healer_detector_task that catches four classes of silent
degradation. Phase 1 is the hand — safe auto-actions the healer takes when
a Phase 0 detector fires. Every row here is a proposal for operator
line-item review. Nothing gets implemented until the operator approves it
explicitly.

Operator-only forever rows are marked ⛔ and will stay operator-only in
every future phase. The healer must NEVER perform them regardless of
detector confidence.

---

## Action-safety scoring

Each proposal is graded on three axes:

| Axis | Values |
|---|---|
| **Blast radius** | *contained* (in-process only), *reversible* (writes DB row that operator can flip back), *external* (touches Binance / Telegram / env), *live-money* (any code path that can move USDT on prod exchange) |
| **False-positive cost** | *free* (no impact if detector is wrong), *low* (log noise, no user-visible), *medium* (dev soak needed), *high* (real user impact) |
| **Rollback path** | *automatic* (next tick resets), *operator-1-command* (a documented undo runbook), *manual-forensic* (requires DB inspection + surgical UPDATE) |

The action matrix ranks proposals by (low blast × low FP cost × automatic
rollback) first. The operator's job is to pick which rows earn auto-action;
Phase 1 implements only approved rows.

---

## Fault → Proposed action matrix

### Row 1 — `worker_watchdog: heartbeat_error` (B1)

| Field | Value |
|---|---|
| Detector | `worker_watchdog` (Phase 0) |
| Fault | Worker heartbeats fresh with `last_status='error'` past streak threshold |
| Proposed auto-action | Log + `alert_admin(level='critical')`. **No auto-restart** for `heartbeat_error` — a worker that reports its own error is signaling "I ran, I know what went wrong" whereas silence is what auto-restart heals. |
| Blast radius | *contained* — same as staleness alarm today |
| False-positive cost | *low* — extra Telegram ping |
| Rollback | *automatic* — next healthy beat clears the streak |
| **Recommendation** | ✅ **enable auto-action** (already in PR #352; alerting is the auto-action) |

### Row 2 — `worker_watchdog: stale` on **non-stateful, registered** worker (unchanged from today)

| Field | Value |
|---|---|
| Detector | `worker_watchdog` (existing) |
| Fault | Non-stateful worker stale past `max_staleness_seconds`, registered with `worker_supervisor` |
| Proposed auto-action | Already implemented: `worker_supervisor.restart(name)`. Downgrades alarm to warning if the restart succeeds. |
| Blast radius | *contained* — task cancel + factory respawn, no cross-process effect |
| False-positive cost | *low* — a restart during natural quiet costs one tick of work; the restart itself heartbeats |
| Rollback | *automatic* — worker respawns clean; supervisor tracks the new task |
| **Recommendation** | ✅ **keep as is** (this is the existing behavior; documenting it for completeness) |

### Row 3 — `worker_watchdog: stale` on **stateful** worker

| Field | Value |
|---|---|
| Detector | `worker_watchdog` (existing) |
| Fault | Stateful worker stale — live_worker, shadow_worker, liquidation_monitor, telegram_poller, ws_keepalive |
| Proposed auto-action | Alert-only. Auto-restart risks lost open positions / duplicate orders / vault re-init / WS rate-limit hits (per safety contract in `worker_supervisor.py`). |
| Blast radius | *live-money* if wrong |
| **Recommendation** | ⛔ **operator-only forever** |

### Row 4 — `C1 dispatch_error_rate: novel exception class`

| Field | Value |
|---|---|
| Detector | `detect_dispatch_error_rate` (C1) |
| Fault | Never-before-seen exception class from dispatcher's outermost try/except |
| Proposed auto-action | Alert critical + persist to `healer_findings`. **No auto-mitigation** — a novel exception is exactly the class where the healer's disease model is provably wrong (we've never seen this before, so any recipe we apply is guessing). |
| Blast radius | *contained* — just alerting |
| False-positive cost | *free* — critical Telegram + row in DB |
| Rollback | *automatic* — next known class doesn't re-alarm |
| **Recommendation** | ✅ **enable** (already the case in PR #353) |

### Row 5 — `C1 dispatch_error_rate: known class > 5/hr`

| Field | Value |
|---|---|
| Detector | `detect_dispatch_error_rate` (C1) |
| Fault | Known exception class crossed the hourly rate limit |
| Proposed auto-action | **Option A** — alert warning only (Phase 0 behavior).<br>**Option B (proposal)** — auto-throttle: if the exception class is a known network/timeout class (BinanceLiveError HTTP 5xx, httpx.TimeoutException), inject a 30-min extra delay on the next dispatch attempt for that symbol/user. |
| Blast radius (B) | *contained* — delays retry but doesn't refuse it |
| False-positive cost (B) | *low* — one delayed dispatch is invisible to shadow trading and shows up in dispatch logs |
| Rollback (B) | *automatic* — the delay is per-hit and clears after the wait |
| **Recommendation** | ⏸ **defer Option B to Phase 2** — needs a canonical retry-throttle module first |

### Row 6 — `C2 score_distribution_anomaly` (universe-wide predictor degradation)

| Field | Value |
|---|---|
| Detector | `detect_score_distribution_anomaly` (C2) |
| Fault | Universe-wide all-NEUTRAL / near-zero scores for >1h |
| Proposed auto-action | **Option A** — alert critical only (Phase 0).<br>**Option B (proposal)** — auto-restart `shadow_worker` if it hasn't been restarted in >12h.<br>**Option C (proposal)** — auto-flip predictor cache eviction (rebuild feature cache from scratch on next tick). |
| Blast radius (B) | *reversible* — `shadow_worker` is stateful (holds open positions in memory) so this violates the safety contract as written. Would need a "hot-reload predictor without dropping positions" path. |
| Blast radius (C) | *contained* — flushing MTF cache burns ~60s of REST + burnt Binance quota; positions unaffected |
| False-positive cost (C) | *low* — 60s of extra latency once; cache warms back |
| Rollback (C) | *automatic* — cache re-populates from live WS candles |
| **Recommendation** | ⏸ **defer B forever** (stateful-worker restart risks positions); **operator-approve C** if desired |

### Row 7 — `C3 per_symbol_prediction_freshness` (per-symbol silent drop)

| Field | Value |
|---|---|
| Detector | `detect_per_symbol_prediction_freshness` (C3) |
| Fault | One or more universe symbols with no prediction for >2× timeframe |
| Proposed auto-action | **Option A** — alert warning only (Phase 0).<br>**Option B (proposal)** — auto-restart the WS keepalive fleet child for that (symbol, timeframe) — the fleet already has a per-child restart-with-backoff loop; this would just short-circuit the backoff wait. |
| Blast radius (B) | *contained* — child task cancel + respawn; siblings unaffected |
| False-positive cost (B) | *low* — one Binance WS reconnect per false positive; Binance per-IP rate limit is ~5 conn/sec, this is well under |
| Rollback (B) | *automatic* — the fleet already tolerates transient reconnects; the respawned child heartbeats on the next kline |
| **Recommendation** | ✅ **enable Option B** — cleanest first Phase-1 auto-action; low blast, high leverage |

### Row 8 — `C4 blocked_rate_anomaly` (>95% blocked for >2h)

| Field | Value |
|---|---|
| Detector | `detect_blocked_rate_anomaly` (C4) |
| Fault | >95% of telegram_signals blocked over 2h |
| Proposed auto-action | Info-log only. Any auto-action here is either "loosen a gate" (env flip → ⛔ operator-only) or "flip trading_mode" (⛔ operator-only). |
| **Recommendation** | ⛔ **operator-only forever** — every action is a live-money/env decision |

### Row 9 — Vault re-init on `vault_keys() is None` observed by healer

| Field | Value |
|---|---|
| Detector | *proposal — not in Phase 0* — a new detector that samples `vault_keys() is None` after container startup + past a grace window |
| Fault | Vault decrypt didn't run at startup — dispatcher silent-drops every LONG/SHORT |
| Proposed auto-action | **Cannot** — the vault passphrase lives in the operator's head; the healer has no way to obtain it. |
| **Recommendation** | ⛔ **operator-only forever** |

### Row 10 — `symbol_allowlist_refresh` auto-heal on `heartbeat_error`

| Field | Value |
|---|---|
| Detector | Watchdog B1 |
| Fault | The exact 2026-07-22 incident: daily worker fails cycle-body with status='error' |
| Proposed auto-action | **Option A** — alert only (Phase 0).<br>**Option B (proposal)** — after 2 consecutive error beats OR novel exception class in details, auto-restart the worker via `worker_supervisor` (already registered, non-stateful, safe). |
| Blast radius (B) | *contained* — same as Row 2 |
| False-positive cost (B) | *low* — a same-day double-write; `insert_snapshot_row` is idempotent via the hash chain |
| Rollback (B) | *automatic* |
| **Recommendation** | ✅ **enable Option B** — natural extension of the existing supervisor.restart() path; would have closed the 2026-07-22 incident in one 5-min watchdog tick |

### Row 11 — Env-var flips (`MIN_ENTRY_SCORE_LONG`, `SHADOW_ALLOW_SHORTS`, `AUTONOMOUS_TRADING_ENABLED`, `AUTO_PROMOTE_TO_*`, `BINANCE_USE_TESTNET`, `DISABLE_SHORT_SIGNALS`, `MTF_MIN_AGREEMENT_*`, `SHORT_VETO_*`, `SHORT_FUNDING_HALVE_HOLD`)

| Field | Value |
|---|---|
| Detector | Any |
| Fault | Any |
| Proposed auto-action | Nothing. Env flips are the operator's control surface for the whole strategy. Healing them means the healer is making strategy decisions. |
| **Recommendation** | ⛔ **operator-only forever** |

### Row 12 — `users.trading_mode` changes (manual ↔ telegram-approve ↔ fully-auto)

| Field | Value |
|---|---|
| Detector | Any |
| Fault | Any |
| Proposed auto-action | Nothing. Same reason as Row 11. |
| **Recommendation** | ⛔ **operator-only forever** |

### Row 13 — Any DB write to `live_trades`, `shadow_trades`, `users`, `predictions`, `telegram_signals`, `brain_decisions`, `rl_checkpoints`

| Field | Value |
|---|---|
| Detector | Any |
| Fault | Any |
| Proposed auto-action | Nothing. The audit hash chain is the operator's source of truth. Any healer write to a chained table poisons downstream verification. |
| **Recommendation** | ⛔ **operator-only forever**. (Writes to healer_findings / healer_known_error_types are fine — those are operational, non-chained.) |

---

## Recommended Phase 1 opening move

Ship the three ✅ auto-actions in one PR:

1. **Row 7** — C3 auto-restart WS keepalive child for the stale (symbol, timeframe)
2. **Row 10** — B1 auto-restart `symbol_allowlist_refresh` on heartbeat_error (would have closed the motivating incident in one tick)
3. **Row 1** — Alerting on heartbeat_error already ships in PR #352; document formally that this is the Phase 1 action for that fault

Total blast radius: three contained/reversible actions with automatic
rollback. False-positive cost: low across all three. Combined they close
the motivating incident + one adjacent silent-drop class + one existing
blind spot without touching any ⛔ row.

Everything else in the matrix stays deferred to Phase 2 (Options B on
Rows 5/6/7 that need a canonical throttle / cache-rebuild module) or
stays ⛔ operator-only forever.

## Operator sign-off

The operator reviews rows individually. Each ✅ row that gets sign-off
becomes a Phase 1 PR — one row per PR so soak / verify pattern stays
clean. ⛔ rows stay ⛔ regardless of any future Phase.
