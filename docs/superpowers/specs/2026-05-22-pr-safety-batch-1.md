# PR-SAFETY-BATCH-1 — FU-24 race fix + FU-33 slippage circuit-breaker

**Status:** spec drafted 2026-05-22.
**Branch:** `feat/pr-safety-batch-1` (off `origin/dev` at `b9aacad`).
**Class:** **behavior-changing.** FU-24 changes write serialization (small latency, no semantic change). FU-33 adds an entry/close gate (default-OFF, recording-only until flag flipped).

---

## Problem

### F — FU-24 chain race

`backend/app/db/audit.py::insert_with_chain` reads "latest `row_hash`" then INSERTs without serialization. Concurrent writers at hourly close → all read the same latest value → all set their `prev_hash` identically → chain breaks. Verified 2026-05-22T10:03Z: 14 new broken predictions rows in the ~1 hour since the FU-24 sweep completed (race is ongoing at ~14 breaks/hour). Current mitigation (PR-DECOUPLE-WORKERS) keeps safety-net workers alive despite breaks, but operator alert spam + the `chain_writer` preflight failure remain.

### G — FU-33 slippage circuit-breaker

Per `KNOWN_ISSUES.md:709`, the FIDAUSDT 2026-05-18 incident lost -14.5% on a single trade due to slippage. Hard prereq for telegram-approve mode per `autonomous_launch_preflight_gates` memory. Must ship before any live-trade dispatch path activates.

---

## Fix F — per-TABLE `pg_advisory_xact_lock`

**Chain logic verified in [PR-DIAG-CHAIN-LOGIC](backend/app/db/audit.py#L180-L208):** chain is per-table global, ordered by `id` (autoincrement), no partition. Per-symbol locking would NOT fix the race. **Correct lock granularity is per-table.**

### Change

Wrap `insert_with_chain` body to acquire `pg_advisory_xact_lock(hashtext(table_name))` BEFORE `_last_row_hash` reads:

```python
# In backend/app/db/audit.py::insert_with_chain
async def insert_with_chain(session, table, payload):
    hashable = _filter_for_hash(table, payload)  # validates table BEFORE any I/O
    # FU-24 fix: serialize per-table writers via Postgres advisory lock.
    # Lock key = hashtext(table_name) — a 32-bit signed int derived from
    # the table name. Different tables get different keys (no cross-table
    # contention). Same table → strict serial inserts. Lock auto-releases
    # on COMMIT or ROLLBACK (xact_lock variant).
    await session.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtext(:t))"),
        {"t": table},
    )
    prev = await _last_row_hash(session, table)
    new_hash = compute_row_hash(prev, hashable)
    full = {**payload, "prev_hash": prev, "row_hash": new_hash}
    ...existing INSERT...
    return new_hash
```

**Why per-table not per-symbol:** chain construction uses `SELECT row_hash FROM {table} ORDER BY id DESC LIMIT 1` — global per-table. Per-symbol lock would let two writers on different symbols read the same `latest_hash` and break the chain. See PR-DIAG-CHAIN-LOGIC report for full proof.

**Why `pg_advisory_xact_lock` not `pg_try_advisory_xact_lock`:** we WANT writers to wait, not fail. Hourly close has ~30 concurrent writers; serial execution = ~30 × 10ms = 300ms total. Acceptable for a once-per-hour event.

**Always-on:** no flag. This is a bug fix that's strictly safer than the current behavior.

### TDD tests (added to `backend/tests/db/test_audit_race_fix.py`, new file)

1. `test_concurrent_inserts_same_table_serialize_and_chain_intact` — spawn 10 concurrent inserts on `shadow_trades` using `asyncio.gather` + separate sessions; assert all 10 rows have valid chain linkage (each `prev_hash` matches previous `row_hash` by id).
2. `test_concurrent_inserts_different_tables_dont_block` — concurrent inserts on `shadow_trades` AND `predictions`; assert total wall-time < 2× single-table time (proves cross-table independence).
3. `test_advisory_lock_released_on_commit` — after a successful insert, attempt to acquire `pg_advisory_xact_lock(hashtext('shadow_trades'))` from a new session; expect immediate acquire (lock was released).
4. `test_advisory_lock_released_on_rollback` — start a transaction, insert, ROLLBACK, then acquire from new session; expect immediate acquire.
5. `test_single_writer_unchanged_behavior` — regression: one writer, one row, assert chain row produced identical to pre-PR behavior.

**Note:** These tests need Postgres (advisory locks are Postgres-only). Mark `@pytest.mark.skipif(not POSTGRES_TEST_URL)`. The existing `tests/db/` directory has this pattern.

### Performance

- Uncontended: one extra `SELECT pg_advisory_xact_lock(...)` round-trip ≈ 1-2ms per insert.
- 30-way contention at hourly close: serialized = ~300ms total wall-clock vs ~50ms parallel-but-broken today. Net: ~250ms slower at hourly close, every other tick unchanged.
- Lock auto-releases on commit/rollback (xact_lock variant) → no deadlock surface.

---

## Fix G — Slippage circuit-breaker

### New module

**`backend/app/trading/slippage_guard.py`** (new file):

```python
"""FU-33 — symbol-level slippage circuit-breaker.

When realized SL slippage exceeds N× expected, halt new entries on
that symbol for a cooldown period. Prevents the FIDAUSDT 2026-05-18
class of incident (single trade lost -14.5%) from repeating.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

@dataclass(frozen=True)
class SlippageDecision:
    halt: bool
    reason: str
    halt_until: datetime | None

async def check_slippage(
    session, *, symbol: str, expected_sl_pct: float, actual_sl_pct: float,
    settings,
) -> SlippageDecision:
    """Compute whether the realized slippage triggers a halt.

    Returns halt=True when |actual / expected| > SLIPPAGE_THRESHOLD_MULTIPLIER.
    Writes a `symbol_halt_state` row when halting + sends telegram alert.
    Caller (shadow/worker._maybe_close_position) is responsible for
    actually preventing new entries — this function only computes the
    decision + persists state.
    """
    if not settings.SLIPPAGE_GUARD_ENABLED:
        return SlippageDecision(halt=False, reason="guard_disabled", halt_until=None)
    if expected_sl_pct == 0:
        return SlippageDecision(halt=False, reason="zero_expected_sl", halt_until=None)
    ratio = abs(actual_sl_pct / expected_sl_pct)
    if ratio <= settings.SLIPPAGE_THRESHOLD_MULTIPLIER:
        return SlippageDecision(halt=False, reason=f"within_threshold (ratio={ratio:.2f})", halt_until=None)
    halt_until = datetime.now(tz=timezone.utc) + timedelta(
        hours=settings.SLIPPAGE_HALT_COOLDOWN_HOURS,
    )
    await session.execute(sa.text(
        "INSERT INTO symbol_halt_state (symbol, halted_until, reason, created_at) "
        "VALUES (:s, :u, :r, NOW()) "
        "ON CONFLICT (symbol) DO UPDATE SET halted_until=:u, reason=:r, created_at=NOW()"
    ), {"s": symbol, "u": halt_until, "r": f"slippage_ratio={ratio:.2f}"})
    return SlippageDecision(halt=True, reason=f"slippage_ratio={ratio:.2f} > {settings.SLIPPAGE_THRESHOLD_MULTIPLIER}", halt_until=halt_until)


async def is_symbol_halted(session, *, symbol: str) -> bool:
    """Read-only check: is this symbol currently halted? Used by entry path."""
    row = (await session.execute(sa.text(
        "SELECT halted_until FROM symbol_halt_state WHERE symbol=:s"
    ), {"s": symbol})).first()
    if row is None:
        return False
    return row.halted_until > datetime.now(tz=timezone.utc)
```

### New migration

`backend/alembic/versions/2026_05_22_0026_symbol_halt_state.py`:

```python
"""PR-SAFETY-BATCH-1: symbol_halt_state for FU-33 circuit-breaker.

Stores per-symbol halt state. Not hash-chained (operational state, not
audit data). Only one row per symbol — UPSERT pattern.
"""
revision = "0026_symbol_halt_state"
down_revision = "0025_pr_plumbing_1_op_pr1_cols"

def upgrade():
    op.create_table(
        "symbol_halt_state",
        sa.Column("symbol", sa.Text, primary_key=True),
        sa.Column("halted_until", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )

def downgrade():
    op.drop_table("symbol_halt_state")
```

### New Settings fields (all default-safe)

`backend/app/config.py`, in the operator-tunable region:

```python
# FU-33: slippage circuit-breaker.
# Default-OFF — operator flips per-env after observing the metric.
SLIPPAGE_GUARD_ENABLED: bool = False
# Halt threshold: realized SL pct / expected SL pct > this → halt.
# 3.0 = "slippage was 3x worse than expected" — generous default.
SLIPPAGE_THRESHOLD_MULTIPLIER: float = 3.0
# How long to halt the symbol after a trigger. 4h gives enough time
# for the operator to investigate without missing >1 candle of opportunity.
SLIPPAGE_HALT_COOLDOWN_HOURS: int = 4
```

### Wiring

1. **`backend/app/shadow/worker.py::_maybe_close_position`** — after computing `actual_sl_pct` (the realized SL %) for a position hitting SL, call `await check_slippage(...)`. If `halt=True`, log + return decision.

2. **`backend/app/shadow/worker.py::_maybe_open_position`** — at the very top, call `if await is_symbol_halted(session, symbol=symbol): return` to skip this symbol for the duration.

3. **Telegram alert on halt:** use existing `app.ops.alert_routing.alert_admin(message, level="critical")` with message:
   ```
   ⚠️ FU-33 slippage halt: {symbol} halted until {halt_until} —
   ratio={ratio:.2f}× expected SL. Investigate Binance liquidity / fill quality.
   ```

### TDD tests (new file `backend/tests/unit/test_slippage_guard.py`)

1. `test_no_slippage_no_trigger` — expected=actual → halt=False.
2. `test_below_threshold_no_trigger` — actual=1.5× expected, threshold=3.0 → halt=False.
3. `test_above_threshold_triggers_halt` — actual=4.0× expected, threshold=3.0 → halt=True; symbol_halt_state row inserted.
4. `test_halt_blocks_new_entries_for_symbol_only` — halt BTCUSDT, attempt to open BTC + ETH; BTC blocked, ETH proceeds.
5. `test_halt_expires_after_cooldown` — set `halted_until` in the past; `is_symbol_halted` returns False.
6. `test_telegram_alert_on_trigger` — mock `_route_alert`; assert called with expected message.
7. `test_flag_off_disables_circuit_breaker` — `SLIPPAGE_GUARD_ENABLED=False` → halt=False even with ratio=10×.
8. `test_zero_expected_sl_no_trigger` — defensive: don't divide by zero, return halt=False with reason='zero_expected_sl'.

---

## What this PR does NOT change

- ❌ No flag flip — `SLIPPAGE_GUARD_ENABLED=False` by default. Operator flips when ready.
- ❌ No change to entry-quality gate, MIN_ENTRY_SCORE_LONG, or any signal-scoring logic.
- ❌ No change to preflight checks or worker spawn order.
- ❌ FU-24 lock granularity is per-table, NOT per-symbol (corrected from operator's earlier draft).
- ❌ No retroactive sweep — the existing `audit-chain-sweep-apply-final` probe stays the rollback tool for one-shot repairs of breaks that occurred before this PR landed.

## Audit chain impact

The FU-24 fix changes the chain INSERT path (adds the lock acquire). It does NOT change `_filter_for_hash` semantics, `compute_row_hash`, or `HASH_PAYLOAD_COLUMNS`. **The hash values themselves are unchanged** — only the timing of when each writer reads `prev` is serialized.

`symbol_halt_state` is NOT chained (operational state, not audit-relevant trade data).

## V-7 latency budget

- Uncontended insert: +1-2ms (one `pg_advisory_xact_lock` round-trip)
- Worst-case hourly close (30 concurrent writers): +250ms total wall-clock
- Slippage check: only fires on SL exit (not every tick); +1 DB round-trip per SL hit.
- `is_symbol_halted` check: +1 DB round-trip per entry attempt. Could be cached in-memory if performance becomes a concern (current frequency ~50/day at most → not worth caching yet).

## Rollback

`git revert <PR-SAFETY-BATCH-1-squash>`. Migration downgrade drops `symbol_halt_state` (data loss for any halted-symbol records, but those are transient operational state). FU-24 lock revert restores the race but doesn't break anything that wasn't already broken.

## Auto-merge authorization

Per operator's PR-WEEK1-MEGA-BATCH directive:
- F (FU-24 race fix): behavior-changing (serialization), no flag, always-on
- G (FU-33 slippage breaker): flag-gated default-OFF, recording-only until operator flips

Soak: 4-6h for FU-24 (verify chain stops re-breaking — should see ZERO new broken rows in predictions). FU-33 ships dormant so no soak needed for its specific behavior.

## Post-deploy verification

After cherry-pick to main + container restart:
1. **FU-24 verify:** query `predictions` chain after 2h: `WITH ordered AS (SELECT id, prev_hash AS stored, LAG(row_hash) OVER (ORDER BY id) AS expected FROM predictions WHERE id > <pre-deploy max_id>) SELECT COUNT(*) FROM ordered WHERE expected IS NOT NULL AND stored != expected`. Expected: 0.
2. **FU-33 verify:** `docker compose exec backend python -c "from app.config import get_settings; print(get_settings().SLIPPAGE_GUARD_ENABLED)"` → False (safe default).
3. **Telegram preflight alert:** should now be `passed` (chain is no longer re-breaking) instead of `reader_only_passed` (chain WRITER blocked).

## Commit message

```
feat(pr-safety-batch-1): FU-24 audit chain race fix + FU-33 slippage circuit-breaker
```
