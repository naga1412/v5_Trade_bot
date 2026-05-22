# PR-CLEANUP-BATCH-1 — healer card-completeness + UUSDT/universe fix + Sharpe display sanity

**Status:** spec drafted 2026-05-22.
**Branch:** `feat/pr-cleanup-batch-1` (off `origin/dev` at `b9aacad`).
**Class:** observability + UX-only. **NO trading-logic change. NO behavior risk.** Auto-merge per standing authorization.

---

## Problem

Three independent observability/UX gaps surfaced by PR-DIAG-AUDIT-BUNDLE:

- **H — Healer card-completeness gap.** [`workers/ui_freshness_monitor.py:60-66`](backend/app/workers/ui_freshness_monitor.py#L60-L66): per-symbol-missing-tick state is not flagged. If a symbol's WS subscription never fires (e.g. UUSDT blacklisted, or TRUMPUSDT dropped from top-30), the per-symbol gap doesn't trigger the healer.
- **I — Stale blacklist + universe orphaning.** `SHADOW_SPOT_BLACKLIST` includes `UUSDT` (PR10.7 entry, now obsolete — verified priceable on Binance SPOT). Also: shadow_worker only subscribes to top-30; open positions on dropped symbols (TRUMPUSDT) orphan with stale `last_check_at`.
- **J — Misleading Sharpe display with low N.** Per-asset table renders Sharpe values computed on N<5 trades, which are statistically meaningless and confuse the operator.

---

## Fix H — ui_freshness_monitor card-completeness

### Change

`backend/app/workers/ui_freshness_monitor.py::run_one_freshness_check`:

Replace the existing per-symbol `relevant_emits` computation (which silently filters out never-emitting symbols) with explicit tracking of three categories:

```python
# Today's loop (lines 60-66) — discards never-emitted symbols:
relevant_emits = [last_emit[s] for s in symbols_open if s in last_emit]

# Post-fix — explicit tracking:
missing_symbols = [
    s for s in symbols_open
    if s not in last_emit and s not in settings.SHADOW_SPOT_BLACKLIST
]
stale_symbols = [
    s for s in symbols_open
    if s in last_emit
    and (now - last_emit[s]).total_seconds() > threshold
]
```

Heartbeat `details` gains `missing_symbols` and `stale_symbols` fields. Status goes to `degraded` if either list is non-empty (subject to the existing 5-min open-position grace period; new check requires position held >5 min).

**Resubscribe-on-3-events:** maintain a process-local counter `_missing_event_counts: dict[str, int]` keyed by symbol. Increment on each tick where symbol is in `missing_symbols`. If count ≥ 3 within a 10-min sliding window, log + emit `healer_resubscribe_requested_total{symbol}` metric (does NOT actually resubscribe — that requires shadow_worker hooks out of scope here; this is observability scaffolding for a future PR).

### TDD tests (extend `backend/tests/unit/test_ui_freshness_monitor.py`)

1. `test_blacklisted_symbol_null_price_does_not_trigger` — open position on UUSDT (in SHADOW_SPOT_BLACKLIST), no emit; expect NOT in `missing_symbols`.
2. `test_non_blacklisted_null_price_after_5min_triggers` — open position on BTCUSDT (not blacklisted), no emit, position held 6 min; expect in `missing_symbols`.
3. `test_3_triggers_in_10min_emits_resubscribe_metric` — drive the loop 3× over 8 min with same symbol missing; assert metric incremented.
4. `test_single_trigger_does_not_emit_resubscribe` — single tick with missing; assert metric NOT incremented.
5. `test_stale_symbols_distinct_from_missing` — symbol emits 2h ago (stale) vs never (missing); both tracked in correct list.

---

## Fix I — UUSDT blacklist removal + open-position universe union

### Change 1: Remove UUSDT from `SHADOW_SPOT_BLACKLIST`

`backend/app/config.py`:

```python
# Before (PR10.7):
SHADOW_SPOT_BLACKLIST: list[str] = [
    "EDENUSDT", "LUNCUSDT", "PAXGUSDT", "XAUTUSDT", "UUSDT",
]

# After (PR-CLEANUP-BATCH-1 — UUSDT confirmed priceable 2026-05-22T07:03Z):
SHADOW_SPOT_BLACKLIST: list[str] = [
    "EDENUSDT", "LUNCUSDT", "PAXGUSDT", "XAUTUSDT",
]
```

Per PR-DIAG-AUDIT-BUNDLE Section A2b probe (2026-05-22): `GET https://api.binance.com/api/v3/ticker/price?symbol=UUSDT` → `{"symbol":"UUSDT","price":"1.00100000"}`. UUSDT is priceable today; PR10.7's blacklist entry was correct at the time but is now stale.

### Change 2: shadow_worker subscription set = top-30 ∪ {open-position symbols}

`backend/app/shadow/worker.py::setup` (the function that computes the subscription list before opening MultiStreamReader connections):

```python
async def setup(self, session: AsyncSession) -> None:
    # Existing: load top-30 from asset_universe (blacklist-filtered).
    top_30 = await load_top_30_filtered(session)  # existing logic
    
    # NEW: union with open-position symbols (regardless of universe / blacklist).
    open_position_symbols = await self._load_open_position_symbols(session)
    
    subscription_set = sorted(set(top_30) | set(open_position_symbols))
    # ... rest of setup uses subscription_set instead of top_30 ...
```

Helper:

```python
async def _load_open_position_symbols(self, session: AsyncSession) -> set[str]:
    """Symbols with at least one open position, regardless of universe membership.
    
    Prevents universe drift from orphaning positions (TRUMPUSDT-class issue
    from PR-DIAG-AUDIT-BUNDLE).
    """
    result = await session.execute(sa.text(
        "SELECT DISTINCT symbol FROM shadow_open_positions"
    ))
    return {r.symbol for r in result}
```

### TDD tests

1. `test_uusdt_no_longer_in_blacklist` — `from app.config import get_settings; assert 'UUSDT' not in get_settings().SHADOW_SPOT_BLACKLIST`.
2. `test_subscription_includes_open_position_symbols_outside_top30` — fixture: 30 top symbols + 1 open position on `RANDOMUSDT` (not in top-30); assert `RANDOMUSDT` in subscription set.
3. `test_subscription_dedupes_when_symbol_in_both_top30_and_open_position` — open position on `BTCUSDT` (in top-30); assert BTCUSDT appears exactly once.
4. `test_no_open_positions_subscription_equals_top30` — regression: zero open positions, subscription == top-30.

---

## Fix J — Sharpe display sanity for N<5

### Change

`frontend/src/tabs/BotStatus/components/PerAssetTable.tsx` (or wherever the per-asset Sharpe column renders):

```tsx
// Before:
<td>{row.sharpe?.toFixed(2) ?? '—'}</td>

// After:
<td title={row.trades < 5 ? `Insufficient sample (N=${row.trades})` : undefined}>
  {row.trades < 5 ? '—' : (row.sharpe?.toFixed(2) ?? '—')}
</td>
```

Suppress Sharpe display + show tooltip when trade count below threshold. Same treatment as PR10.8's tooltip pattern for blacklisted symbols.

**Backend optional companion (defer to follow-up if scope creep):** the `/per-asset` REST endpoint could nullify Sharpe in the response when `trades < 5`. Not included in this PR — frontend-only suppression is sufficient and keeps the API contract stable.

### TDD tests (extend `frontend/tests/unit/PerAssetTable.test.tsx`)

1. `test_per_asset_renders_em_dash_when_trades_under_5` — row with `trades=3`, `sharpe=1.2` → cell shows `—`.
2. `test_per_asset_renders_sharpe_when_trades_5_or_more` — row with `trades=5`, `sharpe=1.2` → cell shows `1.20`.
3. `test_tooltip_message_when_below_threshold` — assert tooltip text matches "Insufficient sample (N=3)".

---

## What this PR does NOT change

- ❌ No trading-logic change anywhere.
- ❌ No flag flip — SHADOW_SPOT_BLACKLIST mutation is a list-literal edit, not a Settings flag.
- ❌ No migration, no schema change.
- ❌ Does NOT auto-resubscribe WS streams when symbol goes missing (metric only; manual operator action via existing watchdog probes still required).
- ❌ Does NOT modify the `/per-asset` API contract (frontend-only suppression).

## Audit chain impact

**None.** Pure observability + UX.

## V-7 latency budget

- Healer: one extra dict lookup per symbol per 5-min tick. Negligible.
- shadow_worker setup: one extra `SELECT DISTINCT symbol FROM shadow_open_positions` at boot time only. ~5ms one-shot.
- Frontend: trivial render condition.

## Rollback

`git revert <PR-CLEANUP-BATCH-1-squash>`. No schema change, no flag introduced.

## Auto-merge authorization

Per operator's PR-WEEK1-MEGA-BATCH directive: observability + UX-only, no trading-logic change. 12h observability-class soak.

## Post-deploy verification

After cherry-pick to main + container restart:
1. **H verify:** confirm UI healer emits `missing_symbols` field in heartbeat details when any open position lacks an emit AND symbol not blacklisted.
2. **I verify:** check shadow_worker setup log for subscription including any open-position symbols (TRUMPUSDT if still open, UUSDT). Confirm `fetch_spot_prices` now returns a value for UUSDT.
3. **J verify:** Per-Asset Stats tab shows `—` for symbols with `trades<5`.

## Commit message

```
feat(pr-cleanup-batch-1): healer per-symbol completeness + UUSDT/universe union + Sharpe display sanity
```
