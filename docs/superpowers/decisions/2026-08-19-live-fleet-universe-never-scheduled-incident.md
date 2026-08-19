# `live_fleet_universe` was never scheduled — staging caught it before main did (2026-08-19)

**Class:** incident record + governance ratification for the fix (universe-refresh scheduler, new task).
**Do not amend by squash.** Any future change to this decision requires a new decision record replayed in order.

## What happened

Task 5 (`refresh_live_fleet_universe`, PR #469, 2026-08-17) built the function that populates `live_fleet_universe` — the single table `ws_keepalive_task` (Task 5b) and `futures_poll_task` (Task 8, wired to dev by Task 17, 2026-08-18) both read to decide which symbols to poll. Task 5b's redraft repointed `ws_keepalive_task` at this table, replacing the old `asset_universe`/`KEEPALIVE_TOP_N` selector entirely. **No task in the 18-task plan ever scheduled `refresh_live_fleet_universe` to actually run.** It has zero callers anywhere in the application — only ever invoked from its own unit tests (`backend/tests/unit/test_live_fleet_universe.py`).

Consequence, confirmed directly on staging 2026-08-18 (`ops-debug` `sql-select`/`staging-verify` probes, target=staging): `live_fleet_universe` is empty. `ws_keepalive_task` heartbeats `status: ok` with `{"children": 0}` — it queries the table, finds nothing, and quietly runs an empty fleet. `futures_poll_task`, once Task 17 landed, hit the identical empty table and would also spawn zero children. **Every symbol in the fleet stopped receiving predictions the moment Task 5b's PR merged to dev on 2026-08-17 — silently, with every downstream test and CI check passing, because nothing in the test suite exercises "is this actually scheduled in a running deployment."**

## Severity

**This is not "Phase 4 is inert." Had this sequence (Task 5 → 5b → 8 → 17) been cherry-picked to main under the standing soak-then-promote discipline, the same gap would have silently killed ALL live predictions and signals on a green deploy** — not just the new futures-only/liquidity-added-spot cohorts, but `established_top20` too, since Task 5b removed the old `asset_universe`-based fallback entirely when it repointed the fleet. The operator's Telegram feed would have gone dark, main's CI would have stayed green throughout, and the healer's own worker-heartbeat watchdog would not have caught it either — `ws_keepalive_task`'s heartbeat reports `status: ok` regardless of `children` count; a healthy-but-empty fleet does not trip any existing alarm.

This is the strongest argument on record for the staging-soak-before-main gate. It did exactly what it exists to do: catch a defect that every automated check (unit tests, CI, green heartbeats) missed, before it reached money-adjacent production behavior. Recorded here as that gate's first concrete save, not a hypothetical justification for keeping it.

## Root cause

Task 5's own module docstring calls `refresh_live_fleet_universe` "the daily job" and the plan doc's Task 5 note describes it the same way — the *intent* that it run on a schedule was stated from the start. No task in the 18-task plan (1 through 18) ever turned that intent into a wired scheduler. The gap slipped through because every task that depended on the table (5b, 8, 9, 10, 11, 12-17) tested against a table seeded directly by its own test fixtures, never against the real, empty, unscheduled table a fresh deployment actually has.

## Ruling: universe-refresh scheduler (new task, ratified 2026-08-19 — same standing as Task 5b's own addition to the original 18)

Four decisions, made by the operator 2026-08-19 after a clarifying question resolved a fifth (see below):

1. **Mechanism: in-process asyncio worker, registered in the worker registry — not a host cron script.** Decisive reason: registered workers get heartbeats the watchdog and healer can see stop. The brain-trainer's host cron (`hetzner_brain_cron.sh`) failed silently for 12 consecutive nights specifically because nothing was watching it — a directly-cited prior incident. A component this load-bearing must be visible to the healer, not running invisibly outside its view.
2. **Cadence: every 6 hours, not daily.** Faster re-evaluation of the liquidity floor, lower staleness window between a market shift and the fleet reflecting it.
3. **Fire once on startup, with an explicit cold-start bootstrap rule.** A fresh environment must not sit dark waiting for the first scheduled tick. On the very first-ever refresh (table completely empty, no `prior` snapshot), the entry bar relaxes from the normal 3-of-5 within-refresh microsample threshold to admit-on-any-pass — every subsequent refresh (table non-empty) uses the standard asymmetric hysteresis unchanged.
4. **The `stateful=True` gap on `futures_poll_task` (flagged in Task 17's review) gets fixed, not just documented.** A full worker restart recomputes desired symbols from the liquidity floor alone, which does not consider currently-open positions — a symbol with an open position that has since dropped off the floor would silently lose candle coverage on restart. This violates the open-position retention rule (addendum (a) point 4) at a code path (cold start) the original design didn't consider. Confirmed structurally identical in `ws_keepalive_task` (`keepalive.py`'s own cold-start comment: *"If the fleet is empty (first boot, table never populated), log + run with no children"* — written before this specific sub-case was considered) — fixed in both fleets, not just the one flagged.

**Fifth point, clarified via direct question rather than assumed:** the operator's initial framing implied hysteresis should track pass/fail across the last 5 *scheduled refresh cycles* (a symbol "enters in ~18h" at 6-hourly cadence). Reading Task 5's actual shipped code plus the plan doc's own "Note on dwell time" text confirmed hysteresis is fully resolved *within* a single refresh call (5 order-book microsamples ~10s apart, smoothing second-scale spread flicker measured directly on thin-tick symbols during the 2026-08-15 liquidity-floor-selector decision) — there is no cross-cycle state anywhere in the code. **Operator confirmed: keep the within-refresh mechanism exactly as shipped, do not redesign it.** Real latency is bounded by the scheduler cadence (up to 6h to be re-evaluated at all) plus the within-refresh resolution time (~50s), not the 18h/~3-day figures from the original framing.

**Dwell-time consequence, flagged not silently absorbed:** the plan doc's own pre-existing note on dwell time explicitly anticipated this: *"If this refresh cadence ever changes to run more than once a day, this note stops being true and dwell needs its own explicit tracking — flag that explicitly if it comes up."* It has come up. Moving from daily to 6-hourly cadence drops the structural minimum dwell time from ~24h to ~6h (a symbol could theoretically enter on one refresh and exit on the very next, 6 hours later, if conditions unanimously fail all 5 microsamples on that call). No additional dwell-timer state was added for this — the operator's own Task 18 checklist item 10 ("no symbol entering and leaving more than once in the [24h] window — report any flapping") is the empirical check for whether this matters in practice during the staging soak, rather than a structural guarantee. If real flapping is observed, that is the trigger to revisit this, not a reason to block the scheduler now.

## Reversal criteria

Re-examined if:
1. The staging soak (restarted from the top per this ruling) shows real hysteresis flapping within the 24h window — triggers adding explicit dwell-timer state.
2. `FUTURES_POLL_SAFETY_MAX_N`'s fallback branch ever engages at 6-hourly cadence in a way it didn't at daily cadence (faster re-evaluation could plausibly surface qualifying-candidate-count spikes sooner).
3. A future architecture removes the fleet-supervisor pattern (`ws_keepalive_task`/`futures_poll_task`) entirely — this decision's scope premise (child-task-set supervisors reading a shared selector table) no longer applies.
