# Healer Phase 1 — Auto-Recovery Action Matrix

**Status:** proposal + ruling. Row-by-row grading, plus the operator's Phase 1 ruling.
**Written:** 2026-07-23 (after Phase 0 detection layer landed via PR #352 + #353).
**Ratified:** 2026-07-23 close-out. All ⛔ rows are RATIFIED PERMANENT and will
never be revisited regardless of any future Phase.
**Amended 2026-07-24:** Row 7 (WS-child auto-restart) gains a hard prerequisite —
the C3 redesign (boot grace + severity split, shipping as the Phase 0 completion
PR) must land and run its own clean observation window before Row 7 auto-action
merges. See Row 7 detail below.
**Amended 2026-07-28:** Row 7 is **DOWNGRADED from conditionally-approved to
BLOCKED PENDING ROOT CAUSE.** Post-#362 investigation of the C3-flagged stale
majors (LINK 50d, UNI 13d, LTC 6d, plus rolling drops on XLM/PUMP/ADA/ONDO/ENA/
SUI/NEAR/U across the last 10 days) found the failure is **NOT WS-child death.**
Prod's backend was recreated ≥5 times in the 6 days preceding the finding (7/21,
7/22, 7/23×2, 7/27) and the stale symbols survived every restart. Container
recreate rebuilds every WS subscription from scratch; a per-symbol WS-child
restart cannot heal a failure class that already ignores full-stack restarts.
The actual failure layer is prediction-persistence for the (symbol, timeframe)
row — the predictor evidently still scores LINK (shadow_open_positions has a
`LINKUSDT 1h SHORT opened 2026-07-27 22:00 UTC`) and Binance is returning 200-OK
klines for LINKUSDT every scanner tick, but the `predictions` INSERT never lands.
An auto-heal that acts without healing is worse than no auto-heal — the healer
would loop-restart WS children forever without changing the outcome. Row 7 may
be reinstated ONLY when the persistence-layer root cause is identified AND a
per-symbol WS-child restart demonstrably resolves that class. The 8/3 clock
keeps running but is no longer sufficient on its own — root cause is the gate.
See Row 7 detail below.

Phase 0 (detect-only) gave us the eyes: a watchdog that catches error-status
heartbeats + a healer_detector_task that catches four classes of silent
degradation. Phase 1 is the hand — safe auto-actions the healer takes when
a Phase 0 detector fires. Every row here is a proposal for operator
line-item review. Nothing gets implemented until the operator approves it
explicitly.

## Operator's Phase 1 ruling (2026-07-23 close-out)

Two ✅ rows are **CONDITIONALLY APPROVED** for Phase 1 implementation:
  * Row 7 — C3 auto-restart WS keepalive child for stale (symbol, timeframe)
  * Row 10 — B1 auto-restart `symbol_allowlist_refresh` on `heartbeat_error`

**Approval condition** (both rows):
  1. Phase 0 runs **detect-only in prod for 7 consecutive days** with ZERO
     false-positive CRITICAL alerts on the operator's phone.
  2. `healer-selftest` probe fires successfully AND the operator confirms
     the [SELFTEST]-tagged message physically arrived on Telegram.
  3. Explicit operator GO in the current conversation before any merge.

Branches may be prepared in advance. They will NOT merge until all three
conditions are met.

All ⛔ rows below are **RATIFIED PERMANENT** — the healer must NEVER perform
them regardless of detector confidence, disease-model refinement, or any
future automation ambition. Ratification is not up for review.

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
| **Recommendation** | ⛔ **operator-only forever — RATIFIED PERMANENT 2026-07-23** |

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
| **Recommendation** | ⛔ **B RATIFIED PERMANENT 2026-07-23** (stateful-worker restart risks positions and is Row-3-class); **operator-approve C** if desired |

### Row 7 — `C3 per_symbol_prediction_freshness` (per-symbol silent drop)

| Field | Value |
|---|---|
| Detector | `detect_per_symbol_prediction_freshness` (C3) |
| Fault | One or more universe symbols with no prediction for >2× timeframe |
| Proposed auto-action | **Option A** — alert warning only (Phase 0).<br>**Option B (proposal)** — auto-restart the WS keepalive fleet child for that (symbol, timeframe) — the fleet already has a per-child restart-with-backoff loop; this would just short-circuit the backoff wait. |
| Blast radius (B) | *contained* — child task cancel + respawn; siblings unaffected |
| False-positive cost (B) | *low* — one Binance WS reconnect per false positive; Binance per-IP rate limit is ~5 conn/sec, this is well under |
| Rollback (B) | *automatic* — the fleet already tolerates transient reconnects; the respawned child heartbeats on the next kline |
| **Recommendation** | ⛔ **BLOCKED PENDING ROOT CAUSE (amended 2026-07-28, post prod-promotion #28)** — downgraded from CONDITIONALLY APPROVED after post-#362 stale-majors investigation showed the failure class is NOT WS-child death. Predictions.LINK/USDT has been silent for 50 days; UNI 13d, LTC 6d; rolling per-symbol drops across XLM/PUMP/ADA/ONDO/ENA/SUI/NEAR/U in the last 10 days. Prod's backend was recreated ≥5 times in the 6 days preceding the finding — each recreate resubscribes every WS child from scratch, and the stale symbols survived every one. A per-symbol WS-child restart cannot heal what a full-stack restart already ignores. Meanwhile the predictor evidently still scores LINK (shadow_open_positions has `LINKUSDT 1h SHORT opened 2026-07-27 22:00 UTC`) and Binance is returning 200-OK klines for LINKUSDT each scanner tick — the failure is at the prediction-persistence layer, not the ingestion layer. Auto-restarting the WS child would fire repeatedly and change nothing; an auto-heal that acts without healing is worse than no auto-heal. Reinstate ONLY when (1) the persistence-layer root cause is identified, AND (2) a per-symbol WS-child restart is demonstrated to resolve that specific class. The 8/3 clock keeps running but is no longer sufficient on its own.<br><br>**Historical amendments preserved:**<br>• **2026-07-23**: CONDITIONALLY APPROVED for Phase 1 — cleanest first Phase-1 auto-action; low blast, high leverage. Condition: 7-day Phase-0 clean run + selftest confirmation + explicit operator GO.<br>• **2026-07-24 (post prod-promotion #27)**: PREREQUISITE ADDED — the C3 redesign (boot grace + severity split shipped as Phase 0 completion) MUST land AND run its own clean observation window BEFORE this row's auto-restart merges. Auto-restarting on the previous C3 shape = churn every deploy (85 warnings observed on the #27 first tick). The redesign fixes that; the auto-restart layer builds on the fixed detector, not the noisy one. |

### Row 8 — `C4 blocked_rate_anomaly` (>95% blocked for >2h)

| Field | Value |
|---|---|
| Detector | `detect_blocked_rate_anomaly` (C4) |
| Fault | >95% of telegram_signals blocked over 2h |
| Proposed auto-action | Info-log only. Any auto-action here is either "loosen a gate" (env flip → ⛔ operator-only) or "flip trading_mode" (⛔ operator-only). |
| **Recommendation** | ⛔ **operator-only forever — RATIFIED PERMANENT 2026-07-23** — every action is a live-money/env decision |

### Row 9 — Vault re-init on `vault_keys() is None` observed by healer

| Field | Value |
|---|---|
| Detector | *proposal — not in Phase 0* — a new detector that samples `vault_keys() is None` after container startup + past a grace window |
| Fault | Vault decrypt didn't run at startup — dispatcher silent-drops every LONG/SHORT |
| Proposed auto-action | **Cannot** — the vault passphrase lives in the operator's head; the healer has no way to obtain it. |
| **Recommendation** | ⛔ **operator-only forever — RATIFIED PERMANENT 2026-07-23** |

### Row 10 — `symbol_allowlist_refresh` auto-heal on `heartbeat_error`

| Field | Value |
|---|---|
| Detector | Watchdog B1 |
| Fault | The exact 2026-07-22 incident: daily worker fails cycle-body with status='error' |
| Proposed auto-action | **Option A** — alert only (Phase 0).<br>**Option B (proposal)** — after 2 consecutive error beats OR novel exception class in details, auto-restart the worker via `worker_supervisor` (already registered, non-stateful, safe). |
| Blast radius (B) | *contained* — same as Row 2 |
| False-positive cost (B) | *low* — a same-day double-write; `insert_snapshot_row` is idempotent via the hash chain |
| Rollback (B) | *automatic* |
| **Recommendation** | ✅ **CONDITIONALLY APPROVED for Phase 1 (2026-07-23)** — natural extension of the existing supervisor.restart() path; would have closed the 2026-07-22 incident in one 5-min watchdog tick. Condition: 7-day Phase-0 clean run + selftest confirmation + explicit operator GO. Branch may be prepped in advance; no merge until all three are met. |

### Row 11 — Env-var flips (`MIN_ENTRY_SCORE_LONG`, `SHADOW_ALLOW_SHORTS`, `AUTONOMOUS_TRADING_ENABLED`, `AUTO_PROMOTE_TO_*`, `BINANCE_USE_TESTNET`, `DISABLE_SHORT_SIGNALS`, `MTF_MIN_AGREEMENT_*`, `SHORT_VETO_*`, `SHORT_FUNDING_HALVE_HOLD`)

| Field | Value |
|---|---|
| Detector | Any |
| Fault | Any |
| Proposed auto-action | Nothing. Env flips are the operator's control surface for the whole strategy. Healing them means the healer is making strategy decisions. |
| **Recommendation** | ⛔ **operator-only forever — RATIFIED PERMANENT 2026-07-23** |

### Row 12 — `users.trading_mode` changes (manual ↔ telegram-approve ↔ fully-auto)

| Field | Value |
|---|---|
| Detector | Any |
| Fault | Any |
| Proposed auto-action | Nothing. Same reason as Row 11. |
| **Recommendation** | ⛔ **operator-only forever — RATIFIED PERMANENT 2026-07-23** |

### Row 13 — Any DB write to `live_trades`, `shadow_trades`, `users`, `predictions`, `telegram_signals`, `brain_decisions`, `rl_checkpoints`

| Field | Value |
|---|---|
| Detector | Any |
| Fault | Any |
| Proposed auto-action | Nothing. The audit hash chain is the operator's source of truth. Any healer write to a chained table poisons downstream verification. |
| **Recommendation** | ⛔ **operator-only forever — RATIFIED PERMANENT 2026-07-23**. (Writes to healer_findings / healer_known_error_types are fine — those are operational, non-chained.) |

---

## Phase 1 opening move — CONDITIONALLY APPROVED

Ship the two ✅ auto-actions as one-row PRs (soak / verify per PR stays
clean). Both are CONDITIONALLY APPROVED as of 2026-07-23:

1. **Row 7** — C3 auto-restart WS keepalive child for the stale (symbol, timeframe)
2. **Row 10** — B1 auto-restart `symbol_allowlist_refresh` on heartbeat_error (would have closed the 2026-07-22 motivating incident in one 5-min tick)

Row 1's alerting-as-Phase-1-action is already in-flight via PR #352 —
mentioned for completeness but requires no new PR.

Total blast radius across Row 7 + Row 10: two *contained* actions with
*automatic* rollback and *low* false-positive cost. Combined they close
the motivating incident + one adjacent per-symbol silent-drop class
without touching any ⛔ row.

### Approval condition — ALL THREE must hold before either PR merges

1. **7 consecutive days** of Phase 0 running detect-only in prod with
   ZERO false-positive CRITICAL alerts on the operator's phone. Measured
   from the deploy timestamp of the last Phase 0 promotion (#352/#353).
2. **`healer-selftest` probe fires successfully** AND the operator
   confirms the `[SELFTEST]`-tagged message physically arrived on their
   Telegram. This is the acceptance test for the whole alert path.
3. **Explicit operator GO** in the current conversation. Branches may
   be prepped in advance; the merge waits for the GO.

Everything else in the matrix stays deferred to Phase 2 (Rows 5 / 6
Option C, needing a canonical throttle / cache-rebuild module) or stays
⛔ RATIFIED PERMANENT.

## Detection-latency precision (2026-07-23 close-out fix)

Watchdog worst-case latency from "the failing beat lands in the DB" to
"the operator's phone lights up" is bounded at **one watchdog tick** =
`WATCHDOG_INTERVAL_SECONDS` = **300 seconds**. That's the interval
between two consecutive `check_all_workers` passes.

Daily-cadence workers add the worker's OWN cadence to the worst case:
the failing beat can only exist after the worker's own tick fires and
writes `last_status='error'`. So the fully-specified worst case for a
daily-cadence worker is:

  **`worker_cadence + WATCHDOG_INTERVAL_SECONDS` = 24 h + 300 s**

For the 2026-07-22 `symbol_allowlist_refresh` incident specifically:
the first error beat lands ~seconds after container boot (cycle-first
loop shape from #344), so the practical latency to alert was
~10 s + 300 s ≈ **~5 min**, not 24 h + 5 min. The 24 h in the formula
represents the wait for the ERROR STATE TO ARISE at all — not a
detection lag once it does. Both framings matter:

  * Time-to-error state onset: at most one worker cadence
  * Time-to-alert once error is on disk: ≤ 300 s

This system's product is trust; the claims stay exact.

## Operator sign-off

The operator reviews rows individually. Each ✅ row that gets sign-off
becomes a Phase 1 PR — one row per PR so soak / verify pattern stays
clean. ⛔ rows are RATIFIED PERMANENT; they will not be revisited.
