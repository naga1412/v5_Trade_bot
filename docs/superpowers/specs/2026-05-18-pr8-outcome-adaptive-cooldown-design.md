# PR8 — Outcome-adaptive cooldown (live trades + scope-clarified shadow)

**Status**: Design draft 2026-05-18. Awaiting operator review.
**Owner**: Backend (dispatcher + live exit_reason wiring + alembic + persistence + tests).
**Parent**: [Master rollout plan — Option D, 5 PRs](2026-05-17-master-rollout-plan-option-d.md).
**Predecessor**: PR3 multi-resolution shadow (merged to dev 2026-05-18 as #189, prod-promotion via #190).
**Behavior change**: YES — adds a per-asset post-trade cooldown gate to the live dispatcher (currently absent) AND populates `live_trades.exit_reason` at close time so the cooldown can be outcome-aware. Default-OFF in prod via `LIVE_COOLDOWN_ENABLED=False`.

---

## 1. Goal

The master rollout doc describes PR8 as *"replace fixed 4h cooldown with outcome-aware: SL → require fresh MTF agreement, TP → faster re-entry, regime-aware in wave regime."*

A surface scan reveals the premise is wrong: there **is no fixed 4h cooldown** on live trades. The dispatcher declares `blocked_cooldown` as a possible `DispatchOutcome` (`backend/app/trading/execution/dispatcher.py:133`) and the docstring at line 23 mentions *"Per-asset cooldown elapsed (sec 2.6)"*, but **no cooldown check exists in the dispatch path** — pre-conditions only check funding, MTF gate, SHORT safety, and max-positions count. Likewise `live_trades.exit_reason` is a NULL-by-default column that is **never populated** anywhere in the codebase.

PR8 therefore lands three intertwined deliverables:

1. **Wire `live_trades.exit_reason` at close time** — distinguish TP-hit / SL-hit / timeout / liquidation-buffer-auto-close / manual-close. Outcome-adaptive logic needs this to read what happened on the last trade.
2. **Add a `live_cooldowns` table + cooldown gate in dispatcher** — same schema shape as `shadow_cooldowns`. Default-OFF.
3. **Outcome-adaptive duration logic** — SL → long cooldown + require fresh MTF agreement to clear; TP → short cooldown (fast re-entry on the winner); TIMEOUT / EXTERNAL → baseline. Regime-aware mode is **deferred to a follow-up** since wave-regime detection does not exist in live code paths.

PR8 does **NOT** change:
- Entry thresholds, scoring math, MTF compute, p_win, vol-norm, funding-adj helpers.
- Shadow worker cooldown (PR3 just landed per-TF `SHADOW_COOLDOWN_HOURS` 0.5h default — no change here).
- Position sizing, leverage selection, dispatcher pre-conditions other than the new cooldown gate.
- Liquidation monitor's auto-close logic (PR8 only adds the exit_reason write-back).

---

## 2. Scope (in PR8)

| ID | Feature | What lands |
|---|---|---|
| L1 | `live_cooldowns` table | New alembic migration: `live_cooldowns(user_id, symbol, cooldown_until, last_exit_reason, last_mtf_agreement)`. PK `(user_id, symbol)` — live is not TF-segmented (live always trades 1h primary). |
| L2 | Populate `live_trades.exit_reason` on close | The liquidation_monitor.py auto-close path writes `exit_reason="liquidation_buffer_breach"`. A new `live_exit_monitor` (mirrors `shadow.exit_monitor`) writes `take_profit`/`stop_loss`/`timeout`. Manual close (Telegram-approve flow) writes `manual_close`. External close (position vanished from Binance) writes `external_close`. |
| L3 | `_apply_cooldown_gate` in dispatcher | New pre-condition fn: reads `live_cooldowns` for `(user_id, symbol)`. If `cooldown_until > now`: return `DispatchResult("blocked_cooldown", ...)`. Default-OFF via `LIVE_COOLDOWN_ENABLED=False`. |
| L4 | Outcome-adaptive duration | `compute_cooldown_duration(exit_reason, mtf_agreement, settings) -> timedelta`. Reads `LIVE_COOLDOWN_HOURS_BY_OUTCOME: dict[str, float] = {"stop_loss": 8.0, "take_profit": 1.0, "timeout": 4.0, "manual_close": 0.0, "external_close": 0.0, "liquidation_buffer_breach": 24.0}`. Defaults gate SL to 8h, TP to 1h — operator-tunable per env. |
| L5 | SL → require fresh MTF | When `last_exit_reason="stop_loss"` AND `LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF=True`: cooldown clears only if the *new* trade's `mtf_agreement > last_mtf_agreement`. Same-or-lower agreement remains blocked until calendar cooldown expires, whichever comes later. |
| L6 | Write cooldown at close | Closing a live trade upserts `live_cooldowns(user_id, symbol, cooldown_until=now+duration, last_exit_reason, last_mtf_agreement)`. The mtf_agreement at trade-entry time is already on `live_trades.mtf_agreement` (PR2 wired this), so the close path just reads it back. |
| L7 | API surface | `/bot-status/cooldowns` endpoint returns `{user_id, symbol, cooldown_until, last_exit_reason, last_mtf_agreement, blocked_until_fresh_mtf: bool}` for all active cooldowns. Frontend "Live Trades" tab gains a "Cooldowns" subtab (read-only, can wait for PR8.5 if time-boxed). |
| L8 | Audit chain extension | `live_cooldowns` is NOT chained (state table, not append-only). `live_trades.exit_reason` *is* chained but its column is already allow-listed (column existed pre-PR8); confirm allow-list still covers it. |
| L9 | `DispatchOutcome` already has `blocked_cooldown` | No enum change needed — just wire its emission. |
| BENCH | Dispatcher latency bench | `bench_dispatcher_preconditions.py` measures Δp50/Δp99 of dispatch's pre-conditions block with cooldown gate enabled vs disabled. V-7 budget: Δp50 ≤ 5ms, Δp99 ≤ 20ms (cooldown lookup is a single PK SELECT). |
| DEFERRED | Regime-aware cooldown | Wave-regime detection doesn't exist in live code paths (only in ML backtest windows). PR8 adds a `LIVE_COOLDOWN_REGIME_AWARE: bool = False` flag as a forward-compat hook but no detector lands. Defer to a future PR (PR8.5 or PR-REGIME) once a live regime classifier exists. |

---

## 3. Architecture

### 3.1 Data flow

```
TRADE OPENS                                                TRADE CLOSES
─────────────                                              ─────────────
SignalProposal                                             live_trade row
       │                                                          │
       ▼                                                          ▼
dispatcher.dispatch()                                    live_exit_monitor
       │                                                          │
       │   pre-conditions:                                        │   classify outcome:
       │     killswitch ─┐                                        │     TP / SL / timeout / manual / external
       │     funding   ──┤                                        │   read live_trades.mtf_agreement
       │     MTF gate  ──┤   ◄─ NEW: cooldown gate                │            │
       │     cooldown  ──┤   reads live_cooldowns                 │            │
       │     SHORT     ──┤                                        │   compute_cooldown_duration(
       │     max_pos   ──┘                                        │       exit_reason, mtf_agreement,
       │                                                          │       settings)
       ▼                                                          ▼
   place / send_telegram / blocked_cooldown            UPDATE live_trades SET exit_reason=...
                                                                  │
                                                                  ▼
                                                       UPSERT live_cooldowns(
                                                         user_id, symbol,
                                                         cooldown_until=now+duration,
                                                         last_exit_reason,
                                                         last_mtf_agreement)
```

### 3.2 Cooldown clearance logic

```python
def is_cooldown_blocked(
    *, now: datetime, cooldown_row: LiveCooldown | None,
    new_mtf_agreement: int | None,
    settings: Settings,
) -> tuple[bool, str]:
    if not settings.LIVE_COOLDOWN_ENABLED:
        return False, "cooldown_disabled"
    if cooldown_row is None:
        return False, "no_cooldown"
    if now < cooldown_row.cooldown_until:
        return True, f"calendar_until_{cooldown_row.cooldown_until.isoformat()}"
    # Calendar expired — check the SL fresh-MTF override
    if (
        cooldown_row.last_exit_reason == "stop_loss"
        and settings.LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF
    ):
        last_mtf = cooldown_row.last_mtf_agreement or 0
        new_mtf = new_mtf_agreement or 0
        if new_mtf <= last_mtf:
            return True, f"sl_stale_mtf_{new_mtf}<={last_mtf}"
    return False, "cleared"
```

### 3.3 Fail-open contract

**The dispatcher fails open on cooldown errors.** A DB read failure, malformed cooldown row, or unexpected exception in the cooldown gate emits a warning log + lets the trade proceed. Reasoning: a stuck cooldown gate that errors silently to-blocked could indefinitely shut down trading after a single DB blip. Operator chose fail-open for PR2's MTF gate for the same reason. Add to KNOWN_ISSUES as FU-PR8-X if this turns out wrong post-launch.

---

## 4. File structure

### 4.1 Created

| Path | Responsibility |
|---|---|
| `backend/alembic/versions/<NNNN>_pr8_live_cooldowns.py` | Migration: `live_cooldowns` table + index. 2-step pattern (CREATE TABLE → no backfill since starts empty). |
| `backend/app/trading/execution/cooldown_gate.py` | `_apply_cooldown_gate(proposal, session, settings) -> DispatchResult | None`. Mirrors `_apply_mtf_gate`. |
| `backend/app/trading/execution/live_exit_monitor.py` | New worker: 30s poll of open live_trades. For each: read live position from Binance, compare current price vs SL/TP/entry-age, classify exit, write `live_trades.exit_reason`, upsert `live_cooldowns`. Mirrors `shadow.exit_monitor` but writes the cooldown row instead of just a closed-trade row. |
| `backend/app/trading/cooldown_compute.py` | `compute_cooldown_duration(exit_reason, settings) -> timedelta`, `is_cooldown_blocked(...)`. Pure functions — testable without DB. |
| `backend/app/db/live_cooldowns.py` | Persistence: `load_cooldown(uid, sym)`, `upsert_cooldown(...)`, `delete_cooldown(...)`. Mirrors `shadow.persistence` patterns. |
| `backend/tests/db/test_pr8_migration.py` | Migration introspection (table exists, PK shape, columns, downgrade round-trip). |
| `backend/tests/unit/test_cooldown_compute.py` | Pure-function tests: duration table, SL-fresh-MTF override, fail-open on bad data. |
| `backend/tests/trading/test_cooldown_gate.py` | Integration: dispatcher pre-conditions block path with gate enabled vs disabled. |
| `backend/tests/trading/test_live_exit_monitor.py` | TP/SL/timeout/external classification + exit_reason write-back + cooldown upsert. |
| `backend/tests/integration/test_pr8_e2e_sl_blocks_then_clears.py` | E2E: SL closes trade → 8h cooldown set → next signal blocked → calendar expires → fresh-MTF check still blocks if mtf stale → fresh MTF clears. |
| `backend/scripts/bench_dispatcher_preconditions.py` | V-7 bench (Δp50 ≤ 5ms, Δp99 ≤ 20ms). |

### 4.2 Modified

| Path | Change |
|---|---|
| `backend/app/config.py` | Add `LIVE_COOLDOWN_ENABLED=False`, `LIVE_COOLDOWN_HOURS_BY_OUTCOME` dict, `LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF=True`, `LIVE_COOLDOWN_REGIME_AWARE=False`. |
| `backend/app/trading/execution/dispatcher.py` | Wire `_apply_cooldown_gate` into pre-conditions block (between funding and MTF). Order matters: cooldown is cheapest check (single PK lookup) so it should run early. |
| `backend/app/trading/execution/liquidation_monitor.py` | Auto-close path writes `exit_reason="liquidation_buffer_breach"` + upserts `live_cooldowns`. |
| `backend/app/api/routes/bot_status.py` | New `/cooldowns` endpoint. |
| `backend/app/api/schemas.py` | `LiveCooldownOut` schema. |
| `backend/app/ops/worker_registry.py` | Register `live_exit_monitor` (max_staleness=2min — 30s poll). |
| `backend/app/main.py` | Start `live_exit_monitor` task alongside `liquidation_monitor`. |
| `docs/ARCHITECTURE.md` | New §12 — Outcome-adaptive cooldown. |
| `docs/superpowers/specs/2026-05-17-master-rollout-plan-option-d.md` | Update PR8 section to reflect actual landed scope. |

---

## 5. Settings (new)

```python
# Default-OFF for prod safety
LIVE_COOLDOWN_ENABLED: bool = False

# Cooldown duration by outcome (hours).
# stop_loss: long enough to let next-bar conditions develop
# take_profit: short — fast re-entry on a winning setup
# timeout: middle — neither breakout nor breakdown, give it room
# manual_close: zero — operator override means operator decides re-entry
# external_close: zero — Binance closed it without our consent (treat as manual)
# liquidation_buffer_breach: long — clearly something was wrong with sizing/leverage
LIVE_COOLDOWN_HOURS_BY_OUTCOME: dict[str, float] = {
    "stop_loss": 8.0,
    "take_profit": 1.0,
    "timeout": 4.0,
    "manual_close": 0.0,
    "external_close": 0.0,
    "liquidation_buffer_breach": 24.0,
}

# After SL: require strictly-greater mtf_agreement on the new signal
# to clear the cooldown (even after calendar time elapsed). Defends
# against "same losing setup keeps firing every 8h".
LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF: bool = True

# Forward-compat hook for a future regime-aware cooldown classifier.
# No detector exists today, so this is functionally a no-op until that
# lands. Documents intent so the wire-up is one PR away when needed.
LIVE_COOLDOWN_REGIME_AWARE: bool = False
```

---

## 6. Schema

### 6.1 Migration plan (2-step)

```sql
-- Step 1: CREATE TABLE (starts empty, no backfill needed)
CREATE TABLE live_cooldowns (
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    cooldown_until TIMESTAMP WITH TIME ZONE NOT NULL,
    last_exit_reason TEXT NOT NULL,
    last_mtf_agreement SMALLINT,  -- NULL if PR1 hadn't landed yet
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, symbol)
);

-- Step 2: Index for the dispatcher hot path (single PK lookup is already indexed,
-- but add a partial covering index for the "active cooldowns only" /cooldowns endpoint)
CREATE INDEX ix_live_cooldowns_active
    ON live_cooldowns (cooldown_until)
    WHERE cooldown_until > NOW();
```

Downgrade: `DROP INDEX ix_live_cooldowns_active; DROP TABLE live_cooldowns;`

### 6.2 `live_trades.exit_reason` ENUM contract

```python
class LiveExitReason(StrEnum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TIMEOUT = "timeout"
    MANUAL_CLOSE = "manual_close"
    EXTERNAL_CLOSE = "external_close"
    LIQUIDATION_BUFFER_BREACH = "liquidation_buffer_breach"
```

These strings are persisted to `live_trades.exit_reason` (TEXT column). Any other value is an invariant violation — log + raise in tests.

---

## 7. Test surface

**~50 cases across 7 files:**

- Migration introspection (5)
- Settings defaults (4)
- `compute_cooldown_duration` per outcome (6)
- `is_cooldown_blocked` matrix: gate disabled / no row / calendar active / calendar expired / SL+stale MTF / SL+fresh MTF / TP-clear (8)
- `cooldown_gate` integration with dispatcher pre-conditions (6)
- `live_exit_monitor` outcome classification (TP / SL / timeout / external / DB error fail-open) (6)
- `live_exit_monitor` writes exit_reason + upserts cooldown (4)
- `liquidation_monitor` writes exit_reason="liquidation_buffer_breach" + cooldown (3)
- E2E: SL closes → 8h cooldown set → next signal blocked → calendar expires + stale MTF still blocks → fresh MTF clears (4)
- Bench: dispatcher pre-conditions Δp50 ≤ 5ms (1)

---

## 8. Operator decision points

Items below are choices I made to keep this design self-contained. **Operator: please confirm or redirect each before plan-write.**

1. **Default cooldown durations** (8h SL / 1h TP / 4h timeout / 24h liquidation). Pulled from the master doc's mention of "fixed 4h cooldown" — used 4h as the timeout baseline, then scaled outward by outcome riskiness. Adjustable per env.
2. **`LIVE_COOLDOWN_ENABLED=False` default.** Same default-OFF philosophy as PR2/PR3. Operator flips per env once soak verifies.
3. **`LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF=True` default.** This is the "SL → require fresh MTF agreement" leg of the master doc spec. Defaulting it ON because the master doc states it as the desired behavior. Easy operator flip if it turns out to over-block.
4. **Wave-regime deferred.** No detector exists, so PR8 adds the flag but no behavior. Detector lands in a future PR.
5. **Live cooldown is NOT TF-segmented.** Live always trades on 1h primary today. Shadow PR3 needed (sym, tf) because shadow runs both lanes. If live ever spawns a 15m lane, the PK extends — straightforward future migration.
6. **Cooldown gate placement** between funding and MTF gates (cheapest check first). MTF compute is heavier than a single PK lookup.
7. **`live_exit_monitor` as a separate worker** (vs. piggybacking on liquidation_monitor). Reasoning: liquidation_monitor's responsibility is liquidation-buffer hygiene; trade-outcome classification is a distinct concern that warrants its own polling loop + its own heartbeat + watchdog entry. Mirrors shadow's `exit_monitor` separation.

---

## 9. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Cooldown gate erroneously blocks all trades after a DB blip | HIGH | Fail-open contract (§3.3) — DB errors emit warn + let trade proceed. Tested in `test_cooldown_gate_fails_open_on_db_error`. |
| SL-fresh-MTF over-blocks (legitimate re-entries denied) | MEDIUM | Operator-flippable. Monitor `/bot-status/cooldowns` for "blocked_until_fresh_mtf=true" frequency. If >10% of signals get blocked this way, flip flag off. |
| `live_exit_monitor` misclassifies an outcome (e.g., records TIMEOUT when price actually hit TP between polls) | MEDIUM | 30s poll cadence is tight enough for the typical trade lifecycle. Conservative tie-break: if `price >= TP` at close-detection time, classify TP regardless of which boundary was crossed first. |
| `live_cooldowns` writes during high-frequency closing (e.g., Binance flash-cascade) lock the row | LOW | PK lookup, fast UPSERT, no FK cascades. Postgres handles concurrency natively. |
| Existing dispatcher latency regresses past V-7 budget | LOW | Bench `bench_dispatcher_preconditions.py` enforces Δp50 ≤ 5ms; the cooldown gate is one PK SELECT. |

---

## 10. Out of scope (deferred)

- **Frontend Cooldowns subtab** — designed in §2 L7 but can land in PR8.5 if time-boxed. API surface is ready in PR8.
- **Regime-aware cooldown** — flag added, no detector. Future PR.
- **Per-direction cooldown** — currently `live_cooldowns(user_id, symbol)`. If we want separate LONG/SHORT cooldowns, that's a PK extension — future PR. Reasoning to defer: most flip-flop on the same symbol is the loss-then-revenge-trade pattern, which works regardless of direction.
- **TF-segmented live cooldown** — only matters if live ever runs a 15m lane. PR3 didn't enable that for live trading (15m-eligible-for-promotion is False), so no need.

---

## 11. Acceptance criteria

PR8 ships when **all** of these hold:

- [ ] Migration applies cleanly on staging Postgres + reversible downgrade tested.
- [ ] All 50 tests pass; lint + mypy clean.
- [ ] Dispatcher latency bench passes V-7 budget.
- [ ] Default-OFF in prod (LIVE_COOLDOWN_ENABLED=False) — no change to existing trade flow at deploy time.
- [ ] After flipping ON in staging: 1 trade-cycle round-trip writes exit_reason + cooldown_row; next signal on same symbol blocks correctly.
- [ ] After flipping ON in prod: 24h staging soak with no false-positive blocks (or false-positive rate < 5%).
- [ ] Audit chain replay-identity verifies post-deploy.
- [ ] ARCHITECTURE.md §12 published; master rollout doc updated.

---

**End of design draft.** Operator: review §8 decision points; redirect if any defaults need to change before plan-write.
