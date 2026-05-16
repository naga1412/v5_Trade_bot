# PR1 Record-Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the analytics foundation for the 9-PR upgrade plan — record-only MTF / p_win / effective_score / funding_adj columns with zero behavior change, plus two critical-path structural cleanups (`audit.py` whitelist + `payload_builders.py` consolidation) that the rest of the plan depends on.

**Architecture:** New scoring modules (`mtf_confluence`, `p_win_calibrator`, plus vol-norm and funding-adj helpers) hooked into `build_prediction` post-aggregation. Results attached as new top-level fields on `LivePredictionOut`, persisted via 3 new shared payload builders. Audit chain unchanged for existing rows via a fail-secure whitelist refactor in `audit.py`. Single alembic migration adds 7 new nullable columns + flips `timeframe` to NOT NULL on 3 chained tables.

**Tech Stack:** Python 3.12 / FastAPI lifespan / SQLAlchemy 2.0 async + asyncpg / Alembic / Pydantic v2 / httpx / numpy + pandas / sklearn (IsotonicRegression) / pytest + pytest-asyncio + respx.

**Source spec:** `docs/superpowers/specs/2026-05-16-pr1-record-only-design.md`

**Branch:** `feat/pr1-record-only-foundation` off `dev` (NEVER push to `main`).

---

## File Structure (locked in via design)

### NEW files
| Path | Responsibility |
|---|---|
| `backend/app/db/payload_builders.py` | 3 pure functions that produce dicts for `insert_with_chain` — single source of truth for `predictions / shadow_trades / live_trades` payload shape. `paper_trades` has no active production caller, so no 4th builder. |
| `backend/app/core/scoring/mtf_confluence.py` | 6-TF SPOT-REST kline-vote module with in-memory cache, `asyncio.gather`, pre-warm (registered as `mtf_cache_prewarm_task`), TTL-refresh loop (registered as `mtf_cache_ttl_refresh_task`) |
| `backend/app/core/scoring/p_win_calibrator.py` | Per-direction sklearn IsotonicRegression fit + lazy-load predict |
| `backend/app/core/scoring/vol_normalization.py` | `realized_vol_20d` + `effective_score` pure-math helpers |
| `backend/app/core/scoring/funding_directional.py` | Signed funding-rate → boost adjustment lookup |
| `backend/alembic/versions/2026_05_16_XXXX_pr1_record_only_columns.py` | DB migration (3-step `timeframe` + 7 new nullable cols × 3 tables) |
| `backend/scripts/bench_aggregator_latency.py` | Standalone benchmark script (per Correction 3) — `--mtf-disabled` / `--mtf-recording`, outputs JSON, runs in CI as smoke artifact |
| `backend/tests/db/test_audit_whitelist_consistency.py` | Schema-vs-whitelist drift detector + unknown-table-raises test (per Correction 1) |
| `backend/tests/db/test_audit_replay_identity.py` | Re-hashes last 100 rows of each chained table, asserts match |
| `backend/tests/db/test_payload_builders.py` | Golden-dict fixtures per builder |
| `backend/tests/core/scoring/test_mtf_confluence.py` | `respx`-mocked LONG/SHORT/NEUTRAL paths (per Correction 4), fail-open, cache TTL, prewarm |
| `backend/tests/core/scoring/test_p_win_calibrator.py` | Fit ≥50 / <50 / round-trip / both directions |
| `backend/tests/core/scoring/test_vol_normalization.py` | Formula + MIN_VOL floor + None passthrough |
| `backend/tests/core/scoring/test_funding_directional.py` | ±0.10 threshold, deadband, both directions |
| `backend/tests/ops/test_mtf_startup_spawn.py` | Prewarm + TTL-refresh start/cancel lifecycle |

### MODIFIED files
| Path | Reason |
|---|---|
| `backend/app/db/audit.py` | Add `HASH_PAYLOAD_COLUMNS` + `NON_HASHED_ALLOW_LIST` constants; refactor `insert_with_chain` to whitelist-hash; **unknown table raises `ValueError`** (per Correction 1) |
| `backend/app/db/audit_verifier.py` (locate exact filename — see Task 1.3) | Use same whitelist when re-hashing rows |
| `backend/app/api/schemas.py` lines 123-148 | Add **7** new optional fields to `LivePredictionOut` (inline-fix count) |
| `backend/app/core/predictor.py` | Call new compute functions inside `build_prediction`, populate new fields |
| `backend/app/core/execution/persistence.py` | Use `build_predictions_payload`; pass through new keys (eventually) |
| `backend/app/ws/live_prediction.py` lines 128-144 | Replace inline dict with `build_predictions_payload(pred, user_id=...)` call |
| `backend/app/shadow/persistence.py` lines 124-148 | Replace inline dict in `persist_closed_trade` with `build_shadow_trade_payload(...)` call |
| `backend/app/trading/execution/dispatcher.py` lines 353-374 | Replace inline `live_trades` dict with `build_live_trade_payload(...)` call |
| `backend/app/ops/telegram_polling.py` lines 192-212 | Replace inline `live_trades` dict with `build_live_trade_payload(...)` call |
| `backend/app/ops/worker_registry.py` | **Register `mtf_cache_prewarm_task` + `mtf_cache_ttl_refresh_task`** (per Correction 2) |
| `backend/tests/unit/test_worker_registry_consistency.py` | Extend `WORKER_SOURCE_MODULES` (if present) with both new worker names mapped to `app.core.scoring.mtf_confluence` |
| `backend/app/main.py` (lifespan) | Spawn registered workers via `start_mtf_cache_prewarm_task()` + `start_mtf_cache_ttl_refresh_task()` |
| `.github/workflows/ci.yml` | Add benchmark smoke step + artifact upload (per Correction 3) |
| `docs/ARCHITECTURE.md` | Append new section "Math accuracy upgrades (PR1)" + update engine accountability matrix |

### DELETED files
None tracked. `tmp_bench/bench_pr1_latency.py` is throwaway (never committed); removed at the end of Task 7.2 after numbers transferred to the proper `backend/scripts/bench_aggregator_latency.py`.

---

## Phase 0 — Branch + setup

### Task 0: Create feature branch off dev

**Files:** none

- [ ] **Step 1: Ensure clean working tree on docs/architecture-review (current branch)**

Run: `git status --short`
Expected: only the operator's untracked scratch files (`HANDOVER.md`, `populate_universe.py`, `secrets.enc`, `tmp_screens/`, `tmp_screens_buttons/`, `tmp_smc/`, `tmp_bench/`). **Never commit these.**

- [ ] **Step 2: Fetch latest dev**

Run: `git fetch origin dev`
Expected: fast-forward fetch, no output if already up to date.

- [ ] **Step 3: Checkout dev**

Run: `git checkout dev && git reset --hard origin/dev`
Expected: `HEAD is now at <sha> ...` matching origin/dev tip.

- [ ] **Step 4: Create feature branch**

Run: `git checkout -b feat/pr1-record-only-foundation`
Expected: `Switched to a new branch 'feat/pr1-record-only-foundation'`.

- [ ] **Step 5: Confirm mypy baseline still clean**

Run: `cd backend && python -m mypy app 2>&1 | tail -1`
Expected: `Success: no issues found in 398 source files` (or higher count).

---

## Phase 1 — `audit.py` whitelist refactor (LANDS FIRST)

### Task 1.1: Derive current effectively-hashed columns

**Files:** none (research-only step that informs Task 1.2)

The whitelist must equal **exactly** the set of keys passed by current callers to `insert_with_chain`. Derived from the 4 inspected call sites:

```python
# backend/app/db/audit.py — DRAFT to be confirmed in Task 1.5 replay test
HASH_PAYLOAD_COLUMNS: dict[str, frozenset[str]] = {
    "predictions": frozenset({
        "user_id", "symbol", "timeframe", "ts", "layer_scores",
        "final_score", "direction", "confidence", "inputs_hash",
        "model_version", "cold_start",
        # ghost — conditionally present in payload; whitelist allows them
        "ghost_open", "ghost_high", "ghost_low", "ghost_close",
        "ghost_p5_low", "ghost_p95_high", "ghost_uncertainty",
        "model_checkpoint_id",
    }),
    "shadow_trades": frozenset({
        "user_id", "symbol", "timeframe", "direction",
        "entry_price", "stop_loss", "take_profit",
        "position_size_usdt", "entry_score", "entry_confidence",
        "layer_scores", "entry_atr",
        "exit_price", "exit_reason", "pnl_pct", "pnl_usdt",
        "bars_held", "opened_at", "closed_at", "inputs_hash",
        "model_version", "signal_id",
    }),
    "live_trades": frozenset({
        "user_id", "symbol", "direction",
        "margin_usdt", "leverage", "position_value_usdt",
        "entry_price", "stop_loss", "take_profit",
        "binance_order_id", "opened_at",
        "mode_at_open", "approved_via", "reasoning", "inputs_hash",
    }),
    "paper_trades": frozenset({
        "symbol", "direction",
        "entry_price", "exit_price", "stop_loss", "take_profit",
        "position_size", "opened_at", "closed_at",
        "pnl_pct", "max_drawdown_during", "bars_held",
        "exit_reason", "reasoning", "model_version",
    }),
}
```

- [ ] **Step 1: Verify each frozenset matches the actual call site keys**

Run these greps and compare output to the constants above:
```
grep -nA40 "await persist_prediction" backend/app/ws/live_prediction.py
grep -nA40 "await insert_with_chain" backend/app/shadow/persistence.py
grep -nA40 "await insert_with_chain" backend/app/trading/execution/dispatcher.py
grep -nA40 "await insert_with_chain" backend/app/ops/telegram_polling.py
grep -nA40 "await insert_with_chain" backend/app/core/execution/persistence.py
```

If any call site passes a key NOT in the frozenset above, OR is missing a key that's in the frozenset, **STOP and report** — the whitelist MUST equal exactly the current-call-site key union.

- [ ] **Step 2: Confirm no other tables get `insert_with_chain` calls in production code**

Run:
```
grep -rn "insert_with_chain" backend/app/ | grep -v "tests/"
```
Expected output: only references to `predictions`, `shadow_trades`, `live_trades`, `paper_trades`. If any other table appears, **STOP and report**.

### Task 1.2: Refactor `insert_with_chain` to use whitelist

**Files:**
- Modify: `backend/app/db/audit.py`
- Test: `backend/tests/db/test_audit_whitelist.py` (NEW)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/db/test_audit_whitelist.py`:

```python
"""Whitelist-aware insert_with_chain — keys outside the whitelist must not affect row_hash."""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.db.audit import (
    HASH_PAYLOAD_COLUMNS,
    compute_row_hash,
    insert_with_chain,
)


@pytest.fixture
async def sqlite_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, symbol TEXT, timeframe TEXT,
                ts TEXT, layer_scores TEXT, final_score REAL,
                direction TEXT, confidence REAL, inputs_hash TEXT,
                model_version TEXT, cold_start INTEGER,
                extra_recording_only_col REAL,
                prev_hash TEXT, row_hash TEXT
            )
        """))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_whitelist_excludes_recording_only_column_from_hash(sqlite_session):
    """A column not in HASH_PAYLOAD_COLUMNS['predictions'] must not alter row_hash."""
    base_payload = {
        "user_id": 1, "symbol": "BTCUSDT", "timeframe": "1h",
        "ts": "2026-05-16T10:00:00+00:00",
        "layer_scores": "{}", "final_score": 0.35,
        "direction": "LONG", "confidence": 0.6,
        "inputs_hash": "abc", "model_version": "sp-0",
        "cold_start": 0,
    }
    # Insert row 1 with no extra column
    hash_without = await insert_with_chain(
        sqlite_session, "predictions", base_payload,
    )

    # Reset for a fresh chain
    await sqlite_session.execute(sa.text("DELETE FROM predictions"))
    # Insert row 1 again with an extra non-whitelisted column
    payload_with_extra = {**base_payload, "extra_recording_only_col": 42.0}
    hash_with = await insert_with_chain(
        sqlite_session, "predictions", payload_with_extra,
    )
    assert hash_without == hash_with, (
        "row_hash must be identical when only a non-whitelisted column differs"
    )


async def test_whitelist_includes_whitelisted_column_in_hash(sqlite_session):
    """A column IN the whitelist MUST contribute to row_hash."""
    base = {
        "user_id": 1, "symbol": "BTCUSDT", "timeframe": "1h",
        "ts": "2026-05-16T10:00:00+00:00",
        "layer_scores": "{}", "final_score": 0.35,
        "direction": "LONG", "confidence": 0.6,
        "inputs_hash": "abc", "model_version": "sp-0",
        "cold_start": 0,
    }
    hash_a = await insert_with_chain(sqlite_session, "predictions", base)
    await sqlite_session.execute(sa.text("DELETE FROM predictions"))
    different = {**base, "final_score": 0.36}  # whitelisted column, different value
    hash_b = await insert_with_chain(sqlite_session, "predictions", different)
    assert hash_a != hash_b, (
        "row_hash must differ when a whitelisted column value differs"
    )


def test_hash_payload_columns_covers_expected_tables():
    expected = {"predictions", "shadow_trades", "live_trades", "paper_trades"}
    assert set(HASH_PAYLOAD_COLUMNS.keys()) == expected


async def test_insert_with_chain_raises_for_unknown_table(sqlite_session):
    """Per Correction 1 — fail-secure on the unknown-table branch.

    A caller passing an unregistered table name is a bug; we want it
    surfaced loudly, not silently hashed-and-forgotten.
    """
    with pytest.raises(ValueError, match="not in HASH_PAYLOAD_COLUMNS"):
        await insert_with_chain(
            sqlite_session, "some_unregistered_table", {"foo": "bar"},
        )
```

- [ ] **Step 2: Run test — verify it fails (HASH_PAYLOAD_COLUMNS not exported yet)**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_audit_whitelist.py -q --no-cov`
Expected: ImportError or AttributeError on `HASH_PAYLOAD_COLUMNS`.

- [ ] **Step 3: Implement whitelist refactor in audit.py**

Modify `backend/app/db/audit.py` to:

```python
import hashlib
import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

GENESIS_HASH: str = "0" * 64

# Per-table whitelist of columns that contribute to the audit hash chain.
# Adding a column to a chained table is a NO-OP for the chain UNLESS its
# name is also added below. New analytics/recording-only columns belong
# in NON_HASHED_ALLOW_LIST instead (forces conscious decision per column).
#
# Initial values MUST equal exactly the union of keys that current call
# sites pass to insert_with_chain. Verified by tests/db/test_audit_replay_identity.py.
HASH_PAYLOAD_COLUMNS: dict[str, frozenset[str]] = {
    "predictions": frozenset({
        "user_id", "symbol", "timeframe", "ts", "layer_scores",
        "final_score", "direction", "confidence", "inputs_hash",
        "model_version", "cold_start",
        "ghost_open", "ghost_high", "ghost_low", "ghost_close",
        "ghost_p5_low", "ghost_p95_high", "ghost_uncertainty",
        "model_checkpoint_id",
    }),
    "shadow_trades": frozenset({
        "user_id", "symbol", "timeframe", "direction",
        "entry_price", "stop_loss", "take_profit",
        "position_size_usdt", "entry_score", "entry_confidence",
        "layer_scores", "entry_atr",
        "exit_price", "exit_reason", "pnl_pct", "pnl_usdt",
        "bars_held", "opened_at", "closed_at", "inputs_hash",
        "model_version", "signal_id",
    }),
    "live_trades": frozenset({
        "user_id", "symbol", "direction",
        "margin_usdt", "leverage", "position_value_usdt",
        "entry_price", "stop_loss", "take_profit",
        "binance_order_id", "opened_at",
        "mode_at_open", "approved_via", "reasoning", "inputs_hash",
    }),
    "paper_trades": frozenset({
        "symbol", "direction",
        "entry_price", "exit_price", "stop_loss", "take_profit",
        "position_size", "opened_at", "closed_at",
        "pnl_pct", "max_drawdown_during", "bars_held",
        "exit_reason", "reasoning", "model_version",
    }),
}

# Per-table allowlist of columns that exist on the table but are NOT
# part of the audit hash chain. Recording-only analytics columns live
# here. Test test_audit_whitelist_consistency.py fails if a column
# appears on the schema but is in neither set.
NON_HASHED_ALLOW_LIST: dict[str, frozenset[str]] = {
    "predictions": frozenset({
        "id", "prev_hash", "row_hash",  # chain metadata + autoincrement PK
        # PR1 recording-only columns (added in alembic 2026_05_16_XXXX):
        "mtf_agreement", "mtf_dominant_tf", "mtf_directions_json",
        "p_win", "effective_score", "realized_vol_20d",
        "funding_directional_adj",
    }),
    "shadow_trades": frozenset({
        "id", "prev_hash", "row_hash",
        "mtf_agreement", "mtf_dominant_tf", "mtf_directions_json",
        "p_win", "effective_score", "realized_vol_20d",
        "funding_directional_adj",
    }),
    "live_trades": frozenset({
        "id", "prev_hash", "row_hash",
        "timeframe",  # added in PR1, not part of chain on live_trades
        "mtf_agreement", "mtf_dominant_tf", "mtf_directions_json",
        "p_win", "effective_score", "realized_vol_20d",
        "funding_directional_adj",
    }),
    "paper_trades": frozenset({
        "id", "prev_hash", "row_hash",
    }),
}


def canonical_row_json(row: dict[str, Any]) -> str:
    """Canonical JSON serialization for hashing.

    sort_keys=True and compact separators give a deterministic byte
    representation so the same row always hashes to the same value.
    """
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def compute_row_hash(prev_hash: str, row: dict[str, Any]) -> str:
    payload = (prev_hash + canonical_row_json(row)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _filter_for_hash(table: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Drop keys not in HASH_PAYLOAD_COLUMNS[table] before hashing.

    Per Correction 1 (fail-secure on BOTH branches): unknown table raises
    ValueError. Hash-chained tables must be explicitly registered — any
    caller for a non-whitelisted table is a bug we want surfaced, not
    silently hashed-and-forgotten.
    """
    whitelist = HASH_PAYLOAD_COLUMNS.get(table)
    if whitelist is None:
        raise ValueError(
            f"Table {table!r} not in HASH_PAYLOAD_COLUMNS. "
            f"Hash-chained tables must be explicitly registered."
        )
    return {k: v for k, v in payload.items() if k in whitelist}


async def _last_row_hash(session: AsyncSession, table: str) -> str:
    result = await session.execute(
        sa.text(f"SELECT row_hash FROM {table} ORDER BY id DESC LIMIT 1")
    )
    row = result.first()
    return row.row_hash if row else GENESIS_HASH


async def insert_with_chain(
    session: AsyncSession, table: str, payload: dict[str, Any]
) -> str:
    """Insert payload + computed prev_hash/row_hash. Returns row_hash.

    Only keys in ``HASH_PAYLOAD_COLUMNS[table]`` contribute to the row hash.
    Columns not in the whitelist (e.g. recording-only analytics) are
    written to the DB but excluded from the chain. Fail-secure contract:
      - forgotten column → "not tamper-evident", visible in consistency test
      - unknown table → raises ValueError (per Correction 1)
    """
    prev = await _last_row_hash(session, table)
    hashable = _filter_for_hash(table, payload)  # raises on unknown table
    new_hash = compute_row_hash(prev, hashable)
    full = {**payload, "prev_hash": prev, "row_hash": new_hash}
    cols = ", ".join(full.keys())
    params = ", ".join(f":{k}" for k in full.keys())
    await session.execute(
        sa.text(f"INSERT INTO {table} ({cols}) VALUES ({params})"), full
    )
    return new_hash
```

- [ ] **Step 4: Run test — verify it passes**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_audit_whitelist.py -q --no-cov`
Expected: 3 passed.

- [ ] **Step 5: Run full existing audit test suite to confirm no regression**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/ -q --no-cov -k "audit"`
Expected: all existing audit tests still pass — whitelist is bit-equivalent to current behavior because the whitelist exactly equals the union of currently-passed keys.

- [ ] **Step 6: Commit**

```
git add backend/app/db/audit.py backend/tests/db/test_audit_whitelist.py
git commit -m "refactor(audit): add HASH_PAYLOAD_COLUMNS whitelist + filter insert_with_chain (no behavior change)"
```

### Task 1.3: Update audit_verifier to use the same whitelist

**Files:**
- Locate + Modify: `backend/app/db/audit_verifier.py` OR wherever audit verification lives (run discovery step first)

- [ ] **Step 1: Locate the audit verifier module**

Run: `grep -rn "compute_row_hash\|audit_verifier\|prev_hash" backend/app/ --include="*.py" | grep -v "tests/" | head -20`
Expected: find the file that re-computes hashes for chain verification. Likely `backend/app/db/audit_verifier.py` or `backend/app/ops/audit_verifier.py`.

- [ ] **Step 2: Read the verifier file in full**

Run: `cat <path-from-step-1>`
Identify the function that computes `expected_hash = compute_row_hash(prev_hash, row_dict)` — that's the call site to patch.

- [ ] **Step 3: Write failing test**

Create `backend/tests/db/test_audit_verifier_uses_whitelist.py`:

```python
"""Verifier must use HASH_PAYLOAD_COLUMNS — otherwise a recording-only
column would falsely 'break' the chain on existing rows."""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.db.audit import insert_with_chain
# Adjust import to wherever verifier lives — discovered in Step 1
from app.db.audit_verifier import verify_chain  # placeholder import name


@pytest.fixture
async def session_with_recording_only_column():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, symbol TEXT, timeframe TEXT,
                ts TEXT, layer_scores TEXT, final_score REAL,
                direction TEXT, confidence REAL, inputs_hash TEXT,
                model_version TEXT, cold_start INTEGER,
                mtf_agreement INTEGER,
                prev_hash TEXT, row_hash TEXT
            )
        """))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_verifier_ignores_non_whitelisted_column(session_with_recording_only_column):
    s = session_with_recording_only_column
    payload = {
        "user_id": 1, "symbol": "BTCUSDT", "timeframe": "1h",
        "ts": "2026-05-16T10:00:00+00:00",
        "layer_scores": "{}", "final_score": 0.35,
        "direction": "LONG", "confidence": 0.6,
        "inputs_hash": "abc", "model_version": "sp-0",
        "cold_start": 0,
        "mtf_agreement": 4,  # NON-hashed
    }
    await insert_with_chain(s, "predictions", payload)
    result = await verify_chain(s, "predictions")
    assert result.broken_rows == [], (
        f"verifier reported false break — recording-only column "
        f"changed the verifier's expected hash: {result.broken_rows}"
    )
```

- [ ] **Step 4: Run test — verify it fails (verifier still hashes full row)**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_audit_verifier_uses_whitelist.py -q --no-cov`
Expected: FAIL with `result.broken_rows` non-empty.

- [ ] **Step 5: Patch verifier to use `_filter_for_hash`**

In the verifier file, replace any direct call like:
```python
expected = compute_row_hash(prev_hash, row_dict)
```
with:
```python
from app.db.audit import _filter_for_hash, compute_row_hash
expected = compute_row_hash(prev_hash, _filter_for_hash(table, row_dict))
```

If `_filter_for_hash` is private-by-underscore, promote it to public name `filter_payload_for_hash` and export it from `audit.py`.

- [ ] **Step 6: Run test — verify it passes**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_audit_verifier_uses_whitelist.py -q --no-cov`
Expected: 1 passed.

- [ ] **Step 7: Run any existing audit verifier tests**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/ -q --no-cov -k "verifier"`
Expected: all pass.

- [ ] **Step 8: Commit**

```
git add backend/app/db/audit.py backend/app/db/audit_verifier.py backend/tests/db/test_audit_verifier_uses_whitelist.py
git commit -m "refactor(audit): verifier honors HASH_PAYLOAD_COLUMNS (no behavior change for existing rows)"
```

### Task 1.4: Add whitelist consistency test

**Files:**
- Create: `backend/tests/db/test_audit_whitelist_consistency.py`

- [ ] **Step 1: Write the test**

```python
"""Schema-vs-whitelist drift detector.

Walks each chained table's actual column schema (introspected via
SQLAlchemy inspect) and asserts every column is in EITHER
HASH_PAYLOAD_COLUMNS[table] (tamper-evident) OR
NON_HASHED_ALLOW_LIST[table] (recording-only).

Fails if any column appears on the table without an explicit decision.
Forces every future PR adding a column to a chained table to
consciously decide its audit-chain status.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.audit import HASH_PAYLOAD_COLUMNS, NON_HASHED_ALLOW_LIST


@pytest.fixture(scope="module")
async def db_inspector():
    """Use a real session against the configured test DB.

    Uses the application's standard DATABASE_URL env var so this test
    runs against whatever schema is currently migrated.
    """
    import os
    url = os.environ["DATABASE_URL"]
    engine = create_async_engine(url)
    yield engine
    await engine.dispose()


@pytest.mark.parametrize("table", sorted(HASH_PAYLOAD_COLUMNS.keys()))
async def test_every_column_classified(db_inspector, table):
    """Every column on `table` must be in whitelist OR allow-list."""
    async with db_inspector.connect() as conn:
        def _inspect(sync_conn):
            insp = sa.inspect(sync_conn)
            return [c["name"] for c in insp.get_columns(table)]
        actual_cols = set(await conn.run_sync(_inspect))

    classified = HASH_PAYLOAD_COLUMNS[table] | NON_HASHED_ALLOW_LIST.get(
        table, frozenset()
    )
    unclassified = actual_cols - classified
    assert not unclassified, (
        f"Table `{table}` has columns not in HASH_PAYLOAD_COLUMNS or "
        f"NON_HASHED_ALLOW_LIST: {sorted(unclassified)}. "
        f"Decide for each: tamper-evident (add to HASH_PAYLOAD_COLUMNS) "
        f"or recording-only (add to NON_HASHED_ALLOW_LIST)."
    )


@pytest.mark.parametrize("table", sorted(HASH_PAYLOAD_COLUMNS.keys()))
def test_no_overlap_between_lists(table):
    """A column can't be both hashed and unhashed."""
    overlap = HASH_PAYLOAD_COLUMNS[table] & NON_HASHED_ALLOW_LIST.get(
        table, frozenset()
    )
    assert not overlap, (
        f"Column(s) {sorted(overlap)} on `{table}` are in BOTH "
        f"HASH_PAYLOAD_COLUMNS and NON_HASHED_ALLOW_LIST — pick one."
    )
```

- [ ] **Step 2: Run — should pass against current schema before migration**

Run: `cd backend && DATABASE_URL=<test-db-url> REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_audit_whitelist_consistency.py -q --no-cov`
Expected: all parametrized tests pass.

- [ ] **Step 3: Note for Phase 3** — this test will run again AFTER the alembic migration adds the 7 new columns. The new columns are already in `NON_HASHED_ALLOW_LIST`, so it should still pass.

- [ ] **Step 4: Commit**

```
git add backend/tests/db/test_audit_whitelist_consistency.py
git commit -m "test(audit): consistency check — every column classified hashed/non-hashed"
```

### Task 1.5: Replay-identity test against fixtures

**Files:**
- Create: `backend/tests/db/test_audit_replay_identity.py`

The operator's bound: "Verify by replaying audit_verifier on the last 100 rows after the refactor — should produce identical row_hash to what's stored. If even one diverges, the whitelist is wrong; stop and report."

This test uses synthetic fixtures (we can't ship prod data in tests). The production replay is performed as a manual verification step in Task 7.2.

- [ ] **Step 1: Write the test**

```python
"""Replay-identity: re-hash known fixture rows, assert match.

For the prod-data replay step, see manual verification in
docs/superpowers/plans/2026-05-16-pr1-record-only.md Task 7.2.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.db.audit import (
    GENESIS_HASH, compute_row_hash, _filter_for_hash, insert_with_chain,
)


@pytest.fixture
async def chain_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table, cols in [
            ("predictions", "user_id INTEGER, symbol TEXT, timeframe TEXT, "
                           "ts TEXT, layer_scores TEXT, final_score REAL, "
                           "direction TEXT, confidence REAL, inputs_hash TEXT, "
                           "model_version TEXT, cold_start INTEGER"),
        ]:
            await conn.execute(sa.text(
                f"CREATE TABLE {table} ("
                f"id INTEGER PRIMARY KEY AUTOINCREMENT, {cols}, "
                f"prev_hash TEXT, row_hash TEXT)"
            ))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_replay_3_row_chain_identity(chain_session):
    """Build a 3-row chain via insert_with_chain, re-compute each
    expected_hash by hand using _filter_for_hash, assert match."""
    s = chain_session
    rows = [
        {"user_id": 1, "symbol": "BTCUSDT", "timeframe": "1h",
         "ts": f"2026-05-16T1{i}:00:00+00:00",
         "layer_scores": "{}", "final_score": 0.30 + i * 0.05,
         "direction": "LONG", "confidence": 0.6, "inputs_hash": f"h{i}",
         "model_version": "sp-0", "cold_start": 0}
        for i in range(3)
    ]
    stored_hashes = []
    for row in rows:
        stored_hashes.append(await insert_with_chain(s, "predictions", row))

    # Re-read all rows + recompute
    db_rows = (await s.execute(sa.text(
        "SELECT * FROM predictions ORDER BY id"
    ))).all()
    prev = GENESIS_HASH
    for db_row, expected_stored in zip(db_rows, stored_hashes, strict=True):
        row_dict = dict(db_row._mapping)
        # Drop id + chain meta from the dict before filter, mimicking what
        # the prod verifier reads (it doesn't know about id).
        row_dict.pop("id", None)
        row_dict.pop("prev_hash", None)
        row_dict.pop("row_hash", None)
        filtered = _filter_for_hash("predictions", row_dict)
        recomputed = compute_row_hash(prev, filtered)
        assert recomputed == expected_stored, (
            f"replay mismatch on row id={db_row.id}: "
            f"stored={expected_stored} recomputed={recomputed}"
        )
        prev = expected_stored
```

- [ ] **Step 2: Run — verify passes**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_audit_replay_identity.py -q --no-cov`
Expected: 1 passed.

- [ ] **Step 3: Commit**

```
git add backend/tests/db/test_audit_replay_identity.py
git commit -m "test(audit): replay-identity on 3-row fixture chain"
```

---

## Phase 2 — `payload_builders.py` consolidation (LANDS SECOND, BEFORE new columns)

Operator bound (repeat): **mechanical extraction only. BIT-IDENTICAL output. Report any divergence; do NOT silently align.**

### Task 2.1: Divergence audit of the 4 call sites

**Files:** none (research-only, produces a divergence report)

- [ ] **Step 1: Read the predictions builder call site**

Read [backend/app/ws/live_prediction.py:128-144](backend/app/ws/live_prediction.py#L128-L144). The dict keys + value sources:

```python
{
    "user_id": BOOTSTRAP_ADMIN_USER_ID,
    "symbol": pred.symbol,
    "timeframe": pred.timeframe,
    "ts": pred.ts,                                # datetime, NOT isoformat
    "layer_scores": json.dumps(_layer_payload),   # _layer_payload = layer dicts + prediction_extras merged
    "final_score": pred.final.score,
    "direction": pred.final.direction,
    "confidence": pred.final.confidence,
    "inputs_hash": pred.inputs_hash,
    "model_version": "sp-0",
    "cold_start": pred.cold_start,
    **ghost_payload,  # 0 or 8 keys depending on active model
}
```

- [ ] **Step 2: Read the shadow_trades builder call site**

Read [backend/app/shadow/persistence.py:124-148](backend/app/shadow/persistence.py#L124-L148). The dict keys (22 total).

- [ ] **Step 3: Read both live_trades builder call sites and diff them**

Read [backend/app/trading/execution/dispatcher.py:353-374](backend/app/trading/execution/dispatcher.py#L353-L374) AND [backend/app/ops/telegram_polling.py:192-212](backend/app/ops/telegram_polling.py#L192-L212).

Build a divergence table:

| Key | dispatcher value | telegram_polling value | Status |
|---|---|---|---|
| `user_id` | `user.user_id` | `row.user_id or user_id` | semantic match (both = approving user) |
| `symbol` | `proposal.symbol` | `symbol` (local) | match |
| `direction` | `proposal.direction` | `direction` (local) | match |
| `margin_usdt` | `margin_usdt` | `margin_usdt` | match |
| `leverage` | `leverage` | `leverage` | match |
| `position_value_usdt` | `margin_usdt * leverage` | `margin_usdt * leverage` | match |
| `entry_price` | `float(order.avg_fill_price or proposal.entry_price)` | `float(order.avg_fill_price or entry_price)` | match (parameter rename only) |
| `stop_loss` | `proposal.stop_loss_price` | `stop_loss` (local) | match |
| `take_profit` | `proposal.take_profit_price` | `take_profit` (local) | match |
| `binance_order_id` | `order.binance_order_id` | `order.binance_order_id` | match |
| `opened_at` | `now` | `n` | match (both = datetime.now(UTC)) |
| `mode_at_open` | `user.mode` | `'telegram-approve'` literal | **DIVERGENCE — semantic** |
| `approved_via` | `'auto'` literal | `'telegram'` literal | **DIVERGENCE — semantic** |
| `reasoning` | `json.dumps({"confidence_pct": ..., "layer_summary": ..., "signal_id": ...})` | `json.dumps({"signal_id": ..., "confidence_pct": ...})` | **DIVERGENCE — dispatcher includes `layer_summary` key, polling omits it** |
| `inputs_hash` | `proposal.inputs_hash` | `payload.get("inputs_hash", "")` | match (semantic — defaults to "") |

- [ ] **Step 4: Report divergences in this file as inline comments — DO NOT silently align**

Append the following section to this plan file (or paste into a comment to the operator if they want a separate report):

```
DIVERGENCE REPORT — live_trades payload (2 call sites)
=======================================================
1. mode_at_open
   - dispatcher.py:365 = user.mode  (dynamic, normally 'fully-auto')
   - telegram_polling.py:204 = 'telegram-approve' (literal)
   Recommendation: BOTH are correct. The builder takes mode_at_open as
   a required parameter; each caller passes the right value.

2. approved_via
   - dispatcher.py:366 = 'auto'
   - telegram_polling.py:205 = 'telegram'
   Recommendation: same — builder takes approved_via as required param.

3. reasoning
   - dispatcher.py:367-371 includes {confidence_pct, layer_summary, signal_id}
   - telegram_polling.py:206-209 includes {signal_id, confidence_pct} only — NO layer_summary
   Recommendation: KEEP the divergence. The telegram path doesn't have
   layer_summary at hand (it's not in the row). Forcing it to fetch
   would expand scope. Make builder accept extra_reasoning: dict | None
   so each caller adds its available context; if None, reasoning is
   {signal_id, confidence_pct} only.
```

- [ ] **Step 5: PAUSE — wait for operator decision per divergence before proceeding**

Per operator's bound: "If the 4 call sites currently DIVERGE in any column or value, do NOT silently align them. Report each divergence with: (file:line, what differs, your recommendation for canonical behavior). Wait for my decision per divergence before consolidating."

Send the divergence report above to the operator. Wait for explicit per-item decision (likely: "all 3 recommendations approved — divergences preserved via builder parameters").

- [ ] **Step 6: Commit the divergence report**

The plan file (this document) already contains the report — no separate commit needed. The implementing engineer pastes the operator's decision back into this section as a `> Operator decision: ...` quote block before proceeding.

### Task 2.2: Create `build_predictions_payload` + golden test

**Files:**
- Create: `backend/app/db/payload_builders.py`
- Create: `backend/tests/db/test_payload_builders.py`

- [ ] **Step 1: Write the failing golden-dict test FIRST**

```python
"""Golden-dict tests for payload_builders.

Each builder is exercised with a frozen canonical input. The expected
output dict is hard-coded. Any future schema change to the chained
tables surfaces here as a test failure — forces conscious update.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.db.payload_builders import build_predictions_payload


def _make_prediction():
    """Canonical fixture: a minimal LivePredictionOut-shaped object."""
    return SimpleNamespace(
        symbol="BTC/USDT",
        timeframe="1h",
        ts=datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        final=SimpleNamespace(score=0.421, direction="LONG", confidence=0.62),
        inputs_hash="abc123",
        cold_start=False,
        prediction_extras={"trap_count": 2, "tier": "probable"},
        layer_scores={
            "1": SimpleNamespace(direction="LONG", strength=0.5, confidence=0.7, notes=""),
            "2": None,
        },
    )


def test_build_predictions_payload_golden_no_ghost():
    pred = _make_prediction()
    actual = build_predictions_payload(pred, user_id=1, ghost_payload=None)
    expected = {
        "user_id": 1,
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "ts": datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        "layer_scores": json.dumps({
            "1": {"direction": "LONG", "strength": 0.5, "confidence": 0.7, "notes": ""},
            "2": None,
            "trap_count": 2, "tier": "probable",  # extras merged
        }),
        "final_score": 0.421,
        "direction": "LONG",
        "confidence": 0.62,
        "inputs_hash": "abc123",
        "model_version": "sp-0",
        "cold_start": False,
    }
    assert actual == expected


def test_build_predictions_payload_golden_with_ghost():
    pred = _make_prediction()
    ghost = {
        "ghost_open": 80000.0, "ghost_high": 80500.0,
        "ghost_low": 79500.0, "ghost_close": 80100.0,
        "ghost_p5_low": 79000.0, "ghost_p95_high": 81000.0,
        "ghost_uncertainty": 0.02,
        "model_checkpoint_id": 7,
    }
    actual = build_predictions_payload(pred, user_id=1, ghost_payload=ghost)
    # Ghost keys are merged into the top-level dict
    assert actual["ghost_open"] == 80000.0
    assert actual["ghost_close"] == 80100.0
    assert actual["model_checkpoint_id"] == 7
    # Non-ghost keys unchanged
    assert actual["final_score"] == 0.421
```

- [ ] **Step 2: Run — verify it fails (module doesn't exist)**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_payload_builders.py -q --no-cov`
Expected: ModuleNotFoundError on `app.db.payload_builders`.

- [ ] **Step 3: Implement `build_predictions_payload`**

Create `backend/app/db/payload_builders.py`:

```python
"""Single source of truth for chained-table INSERT payloads.

Each function returns the exact dict `insert_with_chain` expects.
Centralising here means new columns (PR1 record-only fields and
beyond) get added in exactly one place per table.

Bound: mechanical extraction. Output is BIT-IDENTICAL to the
pre-refactor inline dicts at the 4 call sites. Divergence between
the two live_trades call sites is preserved via parameter passing
(see DIVERGENCE REPORT in PR1 plan Task 2.1).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal


def build_predictions_payload(
    pred: Any,                                  # LivePredictionOut-shaped
    *,
    user_id: int,
    ghost_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the dict that live_prediction.py currently inlines.

    Bit-identical to live_prediction.py:128-144 pre-refactor:
      - layer_scores is json.dumps(layer_dict + prediction_extras merged)
      - ts is the raw datetime (NOT isoformat — asyncpg binds it)
      - ghost_payload keys are merged at the top level when present
    """
    layer_payload: dict[str, Any] = {}
    for k, v in pred.layer_scores.items():
        if v is None:
            layer_payload[k] = None
        else:
            # LayerScore is a frozen dataclass; build a plain dict for
            # JSON serialization. Direction may be Enum or str.
            direction = v.direction
            if hasattr(direction, "value"):
                direction = direction.value
            layer_payload[k] = {
                "direction": direction,
                "strength": v.strength,
                "confidence": v.confidence,
                "notes": getattr(v, "notes", ""),
            }
    extras = getattr(pred, "prediction_extras", None)
    if extras:
        layer_payload.update(extras)

    payload: dict[str, Any] = {
        "user_id": user_id,
        "symbol": pred.symbol,
        "timeframe": pred.timeframe,
        "ts": pred.ts,
        "layer_scores": json.dumps(layer_payload),
        "final_score": pred.final.score,
        "direction": (
            pred.final.direction.value
            if hasattr(pred.final.direction, "value")
            else pred.final.direction
        ),
        "confidence": pred.final.confidence,
        "inputs_hash": pred.inputs_hash,
        "model_version": "sp-0",
        "cold_start": pred.cold_start,
    }
    if ghost_payload:
        payload.update(ghost_payload)
    return payload
```

- [ ] **Step 4: Run — verify both golden tests pass**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_payload_builders.py -q --no-cov`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```
git add backend/app/db/payload_builders.py backend/tests/db/test_payload_builders.py
git commit -m "feat(db): extract build_predictions_payload (golden-dict test, bit-identical)"
```

### Task 2.3: Migrate `live_prediction.py` to use the builder

**Files:**
- Modify: `backend/app/ws/live_prediction.py` lines 128-144

- [ ] **Step 1: Capture current behavior snapshot** (no test file — uses existing integration test)

Run: `cd backend && DATABASE_URL=<test-db> REDIS_URL=redis://x ENV=test python -m pytest tests/integration/test_ml_ghost_pipeline_e2e.py tests/unit/test_ws_live_prediction_ghost.py -q --no-cov`
Expected: all pass. Record any test names.

- [ ] **Step 2: Replace the inline dict at lines 128-144**

In [backend/app/ws/live_prediction.py:128-144](backend/app/ws/live_prediction.py#L128-L144), replace:

```python
                _layer_payload: dict[str, Any] = {
                    k: (v.model_dump() if v else None)
                    for k, v in pred.layer_scores.items()
                }
                if pred.prediction_extras is not None:
                    _layer_payload.update(pred.prediction_extras)
                await persist_prediction(session, {
                    "user_id": BOOTSTRAP_ADMIN_USER_ID,
                    "symbol": pred.symbol,
                    "timeframe": pred.timeframe,
                    "ts": pred.ts,
                    "layer_scores": json.dumps(_layer_payload),
                    "final_score": pred.final.score,
                    "direction": pred.final.direction,
                    "confidence": pred.final.confidence,
                    "inputs_hash": pred.inputs_hash,
                    "model_version": "sp-0",
                    "cold_start": pred.cold_start,
                    **ghost_payload,
                })
```

with:

```python
                from app.db.payload_builders import build_predictions_payload
                payload = build_predictions_payload(
                    pred,
                    user_id=BOOTSTRAP_ADMIN_USER_ID,
                    ghost_payload=ghost_payload if ghost_payload else None,
                )
                await persist_prediction(session, payload)
```

Move the import to module top with other imports.

- [ ] **Step 3: Run the same integration tests — verify still pass**

Run: `cd backend && DATABASE_URL=<test-db> REDIS_URL=redis://x ENV=test python -m pytest tests/integration/test_ml_ghost_pipeline_e2e.py tests/unit/test_ws_live_prediction_ghost.py -q --no-cov`
Expected: all pass with same row_hash values (assert added in next step).

- [ ] **Step 4: Add a bit-identity assertion test**

Create `backend/tests/db/test_payload_builders_bit_identity.py`:

```python
"""Bit-identity: builder output must equal hard-coded pre-refactor dict.

If this fails, the builder smuggled in a behavior change — STOP.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from app.db.payload_builders import build_predictions_payload


def test_predictions_payload_bit_identical_to_pre_refactor():
    pred = SimpleNamespace(
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        final=SimpleNamespace(score=0.5, direction="LONG", confidence=0.7),
        inputs_hash="h0",
        cold_start=False,
        prediction_extras={"k1": "v1"},
        layer_scores={
            "1": SimpleNamespace(
                direction="LONG", strength=0.5, confidence=0.7, notes="",
            ),
        },
    )
    actual = build_predictions_payload(pred, user_id=1, ghost_payload=None)

    # Reconstruct the EXACT pre-refactor dict (copied from live_prediction.py:128-144)
    _layer_payload = {
        "1": {"direction": "LONG", "strength": 0.5, "confidence": 0.7, "notes": ""},
    }
    _layer_payload.update({"k1": "v1"})
    expected = {
        "user_id": 1,
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "ts": datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        "layer_scores": json.dumps(_layer_payload),
        "final_score": 0.5,
        "direction": "LONG",
        "confidence": 0.7,
        "inputs_hash": "h0",
        "model_version": "sp-0",
        "cold_start": False,
    }
    assert actual == expected
```

- [ ] **Step 5: Run — verify passes**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_payload_builders_bit_identity.py -q --no-cov`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```
git add backend/app/ws/live_prediction.py backend/tests/db/test_payload_builders_bit_identity.py
git commit -m "refactor(live_prediction): use build_predictions_payload (bit-identical)"
```

### Task 2.4: Extract `build_shadow_trade_payload` and migrate

**Files:**
- Modify: `backend/app/db/payload_builders.py` (extend)
- Modify: `backend/app/shadow/persistence.py` lines 124-148
- Modify: `backend/tests/db/test_payload_builders.py` (extend)

- [ ] **Step 1: Write golden test (extend `test_payload_builders.py`)**

Append:

```python
from app.db.payload_builders import build_shadow_trade_payload


def _make_shadow_position():
    # Mirrors shadow.engine.ShadowPosition minimal shape
    return SimpleNamespace(
        symbol="ETHUSDT",
        direction=SimpleNamespace(value="LONG"),
        entry_price=3000.0, stop_loss=2950.0, take_profit=3150.0,
        position_size_usdt=30.0,
        entry_score=0.45, entry_confidence=0.6,
        layer_scores={"1": 0.5, "2": 0.4},
        entry_atr=15.5,
        opened_at=datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        signal_id="sig-abc",
    )


def test_build_shadow_trade_payload_golden_long_tp_hit():
    pos = _make_shadow_position()
    payload = build_shadow_trade_payload(
        pos, user_id=1,
        exit_price=3150.0,
        exit_reason=SimpleNamespace(value="TAKE_PROFIT"),
        closed_at=datetime(2026, 5, 16, 18, 0, 0, tzinfo=timezone.utc),
        bars_held=8,
        inputs_hash="ih-1",
    )
    # pnl_pct = (3150 - 3000) / 3000 * 100 = 5.0
    # pnl_usdt = 30 * 5.0 / 100 = 1.5
    assert payload["pnl_pct"] == 5.0
    assert payload["pnl_usdt"] == 1.5
    assert payload["direction"] == "LONG"
    assert payload["exit_reason"] == "TAKE_PROFIT"
    assert payload["timeframe"] == "1h"  # legacy literal


def test_build_shadow_trade_payload_golden_short_sl_hit():
    pos = _make_shadow_position()
    pos.direction = SimpleNamespace(value="SHORT")
    payload = build_shadow_trade_payload(
        pos, user_id=1,
        exit_price=3050.0,
        exit_reason=SimpleNamespace(value="STOP_LOSS"),
        closed_at=datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc),
        bars_held=2,
        inputs_hash="ih-2",
    )
    # SHORT: pnl_pct = (entry - exit) / entry * 100 = (3000 - 3050) / 3000 * 100 = -1.666...
    assert payload["pnl_pct"] == pytest.approx(-1.6666666666666667)
    assert payload["pnl_usdt"] == pytest.approx(-0.5)
    assert payload["direction"] == "SHORT"
```

- [ ] **Step 2: Run — verify fails (function missing)**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_payload_builders.py::test_build_shadow_trade_payload_golden_long_tp_hit -q --no-cov`
Expected: ImportError on `build_shadow_trade_payload`.

- [ ] **Step 3: Implement `build_shadow_trade_payload` in payload_builders.py**

Append to `backend/app/db/payload_builders.py`:

```python
def build_shadow_trade_payload(
    pos: Any,                          # ShadowPosition
    *,
    user_id: int,
    exit_price: float,
    exit_reason: Any,                  # ExitReason (.value)
    closed_at: datetime,
    bars_held: int,
    inputs_hash: str,
) -> dict[str, Any]:
    """Build the dict shadow/persistence.py:124-148 currently inlines.

    Bit-identical to pre-refactor: pnl_pct sign flip per direction,
    pnl_usdt = position_size_usdt * pnl_pct / 100, json.dumps on
    layer_scores, timeframe '1h' literal.
    """
    direction = pos.direction.value if hasattr(pos.direction, "value") else pos.direction
    if direction == "LONG":
        pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100.0
    else:
        pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100.0
    pnl_usdt = pos.position_size_usdt * pnl_pct / 100.0
    reason_value = exit_reason.value if hasattr(exit_reason, "value") else exit_reason

    return {
        "user_id": user_id,
        "symbol": pos.symbol,
        "timeframe": "1h",
        "direction": direction,
        "entry_price": pos.entry_price,
        "stop_loss": pos.stop_loss,
        "take_profit": pos.take_profit,
        "position_size_usdt": pos.position_size_usdt,
        "entry_score": pos.entry_score,
        "entry_confidence": pos.entry_confidence,
        "layer_scores": json.dumps(pos.layer_scores),
        "entry_atr": pos.entry_atr,
        "exit_price": exit_price,
        "exit_reason": reason_value,
        "pnl_pct": pnl_pct,
        "pnl_usdt": pnl_usdt,
        "bars_held": bars_held,
        "opened_at": pos.opened_at,
        "closed_at": closed_at,
        "inputs_hash": inputs_hash,
        "model_version": "sp-0.5",
        "signal_id": pos.signal_id,
    }
```

- [ ] **Step 4: Migrate `shadow/persistence.py`**

In [backend/app/shadow/persistence.py:102-149](backend/app/shadow/persistence.py#L102-L149), replace the inline `payload = {...}` dict with:

```python
async def persist_closed_trade(
    session: AsyncSession,
    pos: ShadowPosition,
    *,
    user_id: int,
    exit_price: float,
    exit_reason: ExitReason,
    closed_at: datetime,
    bars_held: int,
    inputs_hash: str,
) -> str:
    from app.db.payload_builders import build_shadow_trade_payload
    payload = build_shadow_trade_payload(
        pos, user_id=user_id, exit_price=exit_price,
        exit_reason=exit_reason, closed_at=closed_at,
        bars_held=bars_held, inputs_hash=inputs_hash,
    )
    return await insert_with_chain(session, "shadow_trades", payload)
```

- [ ] **Step 5: Run all golden + existing shadow tests**

Run: `cd backend && DATABASE_URL=<test-db> REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_payload_builders.py tests/unit/test_shadow_persistence.py tests/integration/test_shadow_worker.py -q --no-cov`
Expected: all pass.

- [ ] **Step 6: Commit**

```
git add backend/app/db/payload_builders.py backend/app/shadow/persistence.py backend/tests/db/test_payload_builders.py
git commit -m "refactor(shadow): use build_shadow_trade_payload (bit-identical, both directions)"
```

### Task 2.5: Extract `build_live_trade_payload` and migrate both call sites

**Files:**
- Modify: `backend/app/db/payload_builders.py` (extend)
- Modify: `backend/app/trading/execution/dispatcher.py` lines 353-374
- Modify: `backend/app/ops/telegram_polling.py` lines 192-212
- Modify: `backend/tests/db/test_payload_builders.py` (extend)

- [ ] **Step 1: Write golden tests for both call-site shapes**

Append to `tests/db/test_payload_builders.py`:

```python
from app.db.payload_builders import build_live_trade_payload


def _make_proposal_and_order():
    proposal = SimpleNamespace(
        symbol="BTCUSDT", direction="LONG",
        entry_price=80000.0, stop_loss_price=79000.0, take_profit_price=82000.0,
        confidence_pct=62.5,
        layer_summary={"L1": 0.5, "L3": 0.3},
        inputs_hash="ih-live-1",
    )
    order = SimpleNamespace(
        avg_fill_price=80001.0,
        binance_order_id="1013194660207",
    )
    return proposal, order


def test_build_live_trade_payload_dispatcher_path():
    proposal, order = _make_proposal_and_order()
    payload = build_live_trade_payload(
        proposal, order,
        user_id=1,
        approved_via="auto",
        mode_at_open="fully-auto",
        margin_usdt=8.0, leverage=20,
        opened_at=datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        extra_reasoning={"layer_summary": proposal.layer_summary,
                         "signal_id": "sig-1"},
    )
    assert payload["mode_at_open"] == "fully-auto"
    assert payload["approved_via"] == "auto"
    parsed = json.loads(payload["reasoning"])
    assert parsed["confidence_pct"] == 62.5
    assert parsed["signal_id"] == "sig-1"
    assert parsed["layer_summary"] == {"L1": 0.5, "L3": 0.3}
    assert payload["entry_price"] == 80001.0
    assert payload["position_value_usdt"] == 8.0 * 20


def test_build_live_trade_payload_telegram_path():
    proposal, order = _make_proposal_and_order()
    payload = build_live_trade_payload(
        proposal, order,
        user_id=1,
        approved_via="telegram",
        mode_at_open="telegram-approve",
        margin_usdt=8.0, leverage=10,
        opened_at=datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        extra_reasoning=None,  # telegram doesn't have layer_summary
    )
    assert payload["mode_at_open"] == "telegram-approve"
    assert payload["approved_via"] == "telegram"
    parsed = json.loads(payload["reasoning"])
    assert parsed["confidence_pct"] == 62.5
    assert "layer_summary" not in parsed   # divergence preserved
    assert "signal_id" not in parsed       # caller can add via extra_reasoning if it has one
```

- [ ] **Step 2: Run — verify fails**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_payload_builders.py::test_build_live_trade_payload_dispatcher_path tests/db/test_payload_builders.py::test_build_live_trade_payload_telegram_path -q --no-cov`
Expected: ImportError.

- [ ] **Step 3: Implement `build_live_trade_payload`**

Append to `backend/app/db/payload_builders.py`:

```python
def build_live_trade_payload(
    proposal: Any,
    order: Any,
    *,
    user_id: int,
    approved_via: Literal["auto", "telegram"],
    mode_at_open: str,
    margin_usdt: float,
    leverage: int,
    opened_at: datetime,
    extra_reasoning: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the dict that dispatcher.py and telegram_polling.py inline.

    Divergence preserved (per PR1 plan Task 2.1 operator decision):
      - mode_at_open: required param (user.mode vs literal)
      - approved_via: required param ('auto' vs 'telegram')
      - reasoning: always includes confidence_pct; extra_reasoning
        (e.g. {layer_summary, signal_id}) is merged if provided.
    """
    reasoning = {"confidence_pct": proposal.confidence_pct}
    if extra_reasoning:
        reasoning.update(extra_reasoning)
    entry_price = float(order.avg_fill_price or proposal.entry_price)
    direction = (
        proposal.direction.value if hasattr(proposal.direction, "value")
        else proposal.direction
    )

    return {
        "user_id": user_id,
        "symbol": proposal.symbol,
        "direction": direction,
        "margin_usdt": margin_usdt,
        "leverage": leverage,
        "position_value_usdt": margin_usdt * leverage,
        "entry_price": entry_price,
        "stop_loss": proposal.stop_loss_price,
        "take_profit": proposal.take_profit_price,
        "binance_order_id": order.binance_order_id,
        "opened_at": opened_at,
        "mode_at_open": mode_at_open,
        "approved_via": approved_via,
        "reasoning": json.dumps(reasoning),
        "inputs_hash": proposal.inputs_hash,
    }
```

- [ ] **Step 4: Migrate dispatcher.py:353-374**

Replace the inline dict at [backend/app/trading/execution/dispatcher.py:353-374](backend/app/trading/execution/dispatcher.py#L353-L374) with:

```python
    from app.db.payload_builders import build_live_trade_payload
    payload = build_live_trade_payload(
        proposal, order,
        user_id=user.user_id,
        approved_via="auto",
        mode_at_open=user.mode,
        margin_usdt=margin_usdt,
        leverage=leverage,
        opened_at=now,
        extra_reasoning={
            "layer_summary": proposal.layer_summary,
            "signal_id": sig_id,
        },
    )
    await insert_with_chain(session, "live_trades", payload)
```

- [ ] **Step 5: Migrate telegram_polling.py:192-212**

Replace the inline dict with:

```python
    from app.db.payload_builders import build_live_trade_payload
    # Synthesize a proposal-shaped object from the row + payload
    proposal_shim = SimpleNamespace(
        symbol=symbol, direction=direction,
        entry_price=entry_price,
        stop_loss_price=stop_loss, take_profit_price=take_profit,
        confidence_pct=payload.get("confidence_pct"),
        inputs_hash=payload.get("inputs_hash", ""),
    )
    trade_payload = build_live_trade_payload(
        proposal_shim, order,
        user_id=row.user_id or user_id,
        approved_via="telegram",
        mode_at_open="telegram-approve",
        margin_usdt=margin_usdt,
        leverage=leverage,
        opened_at=n,
        extra_reasoning={"signal_id": signal_id},
    )
    await insert_with_chain(session, "live_trades", trade_payload)
```

Add `from types import SimpleNamespace` to imports at top of `telegram_polling.py`.

- [ ] **Step 6: Run all golden + existing dispatcher/telegram tests**

Run: `cd backend && DATABASE_URL=<test-db> REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_payload_builders.py tests/unit/test_telegram_polling.py -q --no-cov`
Expected: all pass.

- [ ] **Step 7: Commit**

```
git add backend/app/db/payload_builders.py backend/app/trading/execution/dispatcher.py backend/app/ops/telegram_polling.py backend/tests/db/test_payload_builders.py
git commit -m "refactor(live_trades): use build_live_trade_payload, divergence preserved via params"
```

### Task 2.6: Full test suite + mypy check — Phase 2 gate

**Files:** none

- [ ] **Step 1: Run full mypy**

Run: `cd backend && python -m mypy app 2>&1 | tail -3`
Expected: `Success: no issues found in 399+ source files` (counts go up by ~1 for payload_builders.py).

- [ ] **Step 2: Run full unit + integration tests**

Run: `cd backend && DATABASE_URL=<test-db> REDIS_URL=redis://x ENV=test python -m pytest tests/ -q --no-cov 2>&1 | tail -10`
Expected: all green. The only failures should be tests that explicitly check pre-refactor row_hash values — Phase 2 doesn't change row_hash because builders produce bit-identical dicts.

- [ ] **Step 3: Grep for any remaining inline payload dicts in production code**

Run: `grep -rnB2 -A30 "insert_with_chain" backend/app/ | grep -v "tests/"`
Expected: every production call to `insert_with_chain` is preceded by a `build_*_payload(...)` call. If any inline dict remains, **STOP and migrate it before proceeding to Phase 3**.

- [ ] **Step 3b: Verify `paper_trades` has no active production callers (inline fix from operator)**

Run: `grep -rn "insert_with_chain.*paper_trades\|persist_trade(" backend/app/ | grep -v "^backend.app.core.execution.persistence.py:.*def persist_trade"`
Expected: **zero matches.** `persist_trade` in `app/core/execution/persistence.py` is defined but only called from `tests/unit/test_paper_persistence.py` (unit test). `paper_trades` stays in `HASH_PAYLOAD_COLUMNS` for legacy-row verification by `audit_verifier_task`, but no builder is needed because there's no active production writer.

If any active caller is found: **STOP and add a 4th builder (`build_paper_trade_payload`) following the same pattern as the other 3**.

- [ ] **Step 4: Commit checkpoint marker**

```
git commit --allow-empty -m "phase2: payload_builders extraction complete (4 call sites migrated, bit-identical)"
```

---

## Phase 3 — Alembic migration

### Task 3.1: Write the migration file

**Files:**
- Create: `backend/alembic/versions/2026_05_16_HHMM_pr1_record_only_columns.py`
  - Replace `HHMM` with actual time at creation (e.g. `1545`)

- [ ] **Step 1: Identify the latest existing revision ID**

Run: `ls backend/alembic/versions/ | sort | tail -3`
Expected: latest is the migration with the highest YYYY-MM-DD-NNNN prefix. Open the latest file, note `revision: str = "NNNN_descriptive"` — this becomes `down_revision`.

- [ ] **Step 2: Create the migration file**

Create `backend/alembic/versions/2026_05_16_HHMM_pr1_record_only_columns.py` (replace HHMM with current 24h time):

```python
"""PR1 record-only columns + timeframe NOT NULL

Adds 7 nullable analytics columns to predictions, shadow_trades,
live_trades. Enforces timeframe NOT NULL DEFAULT '1h' on all 3 tables.

Recording-only contract: these columns are NOT in the audit hash chain
(see HASH_PAYLOAD_COLUMNS in app/db/audit.py — they live in
NON_HASHED_ALLOW_LIST). PR1 callers may still set them to None on
every insert; future PRs populate them.

Reversible. Row counts at migration time:
  predictions=95, shadow_trades=20, live_trades=1.
No chunking needed.

Revision ID: 0020_pr1_record_only
Revises: <down_revision_id>
Create Date: 2026-05-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0020_pr1_record_only"
down_revision: Union[str, None] = "<FILL IN FROM STEP 1>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS: tuple[tuple[str, sa.Column], ...] = (
    ("mtf_agreement",          sa.Column("mtf_agreement",          sa.SmallInteger(), nullable=True)),
    ("mtf_dominant_tf",        sa.Column("mtf_dominant_tf",        sa.String(length=8), nullable=True)),
    ("mtf_directions_json",    sa.Column("mtf_directions_json",    sa.JSON(), nullable=True)),
    ("p_win",                  sa.Column("p_win",                  sa.Float(), nullable=True)),
    ("effective_score",        sa.Column("effective_score",        sa.Float(), nullable=True)),
    ("realized_vol_20d",       sa.Column("realized_vol_20d",       sa.Float(), nullable=True)),
    ("funding_directional_adj",sa.Column("funding_directional_adj",sa.Float(), nullable=True)),
)

_TABLES = ("predictions", "shadow_trades", "live_trades")


def upgrade() -> None:
    # --- Step 1: predictions.timeframe + shadow_trades.timeframe ---
    # Both columns already exist (nullable). Backfill any NULLs, flip NOT NULL.
    op.execute("UPDATE predictions SET timeframe='1h' WHERE timeframe IS NULL")
    op.alter_column(
        "predictions", "timeframe",
        existing_type=sa.String(length=8),
        nullable=False, server_default="1h",
    )
    op.execute("UPDATE shadow_trades SET timeframe='1h' WHERE timeframe IS NULL")
    op.alter_column(
        "shadow_trades", "timeframe",
        existing_type=sa.String(length=8),
        nullable=False, server_default="1h",
    )

    # --- Step 2: live_trades — column does NOT exist yet ---
    op.add_column("live_trades",
                  sa.Column("timeframe", sa.String(length=8), nullable=True))
    op.execute("UPDATE live_trades SET timeframe='1h' WHERE timeframe IS NULL")
    op.alter_column(
        "live_trades", "timeframe",
        existing_type=sa.String(length=8),
        nullable=False, server_default="1h",
    )

    # --- Step 3: 7 new nullable analytics columns × 3 tables ---
    for table in _TABLES:
        for col_name, col_def in _NEW_COLUMNS:
            op.add_column(table, col_def.copy())


def downgrade() -> None:
    # Reverse order: drop new columns first, then drop live_trades.timeframe,
    # then restore nullability on predictions/shadow_trades.
    for table in _TABLES:
        for col_name, _ in reversed(_NEW_COLUMNS):
            op.drop_column(table, col_name)

    op.drop_column("live_trades", "timeframe")

    op.alter_column(
        "shadow_trades", "timeframe",
        existing_type=sa.String(length=8),
        nullable=True, server_default=None,
    )
    op.alter_column(
        "predictions", "timeframe",
        existing_type=sa.String(length=8),
        nullable=True, server_default=None,
    )
```

- [ ] **Step 3: Fill in `<FILL IN FROM STEP 1>` with actual down_revision**

Open the latest existing alembic file from Step 1, copy its `revision` value into the new file's `down_revision`.

- [ ] **Step 4: Verify alembic dry-run sees the new migration**

Run: `cd backend && DATABASE_URL=<test-db> python -m alembic check 2>&1 | tail -5`
Expected: no errors, the new revision listed.

### Task 3.2: Test migration up + down on a fresh DB

**Files:** none (uses alembic CLI)

- [ ] **Step 1: Test upgrade**

Run: `cd backend && DATABASE_URL=<test-db-fresh> python -m alembic upgrade head 2>&1 | tail -5`
Expected: `Running upgrade <prev> -> 0020_pr1_record_only, PR1 record-only columns + timeframe NOT NULL`.

- [ ] **Step 2: Verify new columns exist + are nullable**

Run via psql/sqlite query:
```
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name IN ('predictions', 'shadow_trades', 'live_trades')
  AND column_name IN ('timeframe', 'mtf_agreement', 'mtf_dominant_tf',
                      'mtf_directions_json', 'p_win', 'effective_score',
                      'realized_vol_20d', 'funding_directional_adj')
ORDER BY table_name, column_name;
```
Expected: 24 rows (3 tables × 8 cols); `timeframe` is NOT NULL on all 3; the 7 analytics cols are NULLABLE on all 3.

- [ ] **Step 3: Test downgrade**

Run: `cd backend && DATABASE_URL=<test-db> python -m alembic downgrade -1 2>&1 | tail -5`
Expected: clean reversal. All 24 columns removed (or `timeframe` reverted to nullable on predictions/shadow_trades, dropped entirely from live_trades).

- [ ] **Step 4: Re-upgrade for downstream tests**

Run: `cd backend && DATABASE_URL=<test-db> python -m alembic upgrade head 2>&1 | tail -2`
Expected: head reached cleanly.

- [ ] **Step 5: Re-run audit whitelist consistency test against new schema**

Run: `cd backend && DATABASE_URL=<test-db> REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_audit_whitelist_consistency.py -q --no-cov`
Expected: all parametrized tests pass — the 7 new columns are already in `NON_HASHED_ALLOW_LIST` in `audit.py`.

- [ ] **Step 6: Commit**

```
git add backend/alembic/versions/2026_05_16_HHMM_pr1_record_only_columns.py
git commit -m "migration: PR1 record-only columns + timeframe NOT NULL (3-step, reversible)"
```

---

## Phase 4 — New scoring modules

### Task 4.1: `vol_normalization.py` — realized_vol_20d + effective_score

**Files:**
- Create: `backend/app/core/scoring/vol_normalization.py`
- Create: `backend/tests/core/scoring/test_vol_normalization.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/core/scoring/test_vol_normalization.py`:

```python
"""Vol normalization — pure math, both directions, MIN_VOL floor."""

import numpy as np
import pandas as pd
import pytest

from app.core.scoring.vol_normalization import (
    MIN_VOL, VOL_NORM_TARGET,
    compute_realized_vol_20d, compute_effective_score,
)


def _make_hourly_bars(n_hours: int, daily_log_return_std: float = 0.02):
    """Generate n_hours of 1h bars whose daily log-returns have given stdev."""
    rng = np.random.default_rng(42)
    # daily vol = sqrt(24) * hourly vol — invert
    hourly_std = daily_log_return_std / np.sqrt(24)
    hourly_returns = rng.normal(0, hourly_std, n_hours)
    closes = 80000.0 * np.exp(np.cumsum(hourly_returns))
    ts = pd.date_range("2026-04-01", periods=n_hours, freq="1H", tz="UTC")
    return pd.DataFrame({"close": closes}, index=ts)


def test_realized_vol_20d_close_to_target_daily_vol():
    bars = _make_hourly_bars(24 * 30, daily_log_return_std=0.02)
    vol = compute_realized_vol_20d(bars)
    assert vol is not None
    # Generated stdev should round-trip within 30% (small sample noise)
    assert 0.014 <= vol <= 0.027


def test_realized_vol_20d_returns_none_when_insufficient_history():
    bars = _make_hourly_bars(24 * 5)  # only 5 days
    vol = compute_realized_vol_20d(bars)
    assert vol is None  # needs ≥20 daily samples


def test_effective_score_long_positive_score():
    # vol=0.02 (target) → multiplier = 1.0 → effective_score = final_score
    eff = compute_effective_score(final_score=0.5, realized_vol_20d=0.02)
    assert eff == pytest.approx(0.5)


def test_effective_score_short_negative_score():
    eff = compute_effective_score(final_score=-0.5, realized_vol_20d=0.02)
    assert eff == pytest.approx(-0.5)


def test_effective_score_high_vol_dampens():
    # vol = 0.04 (double target) → multiplier = 0.5 → eff_score = final * 0.5
    eff = compute_effective_score(final_score=0.5, realized_vol_20d=0.04)
    assert eff == pytest.approx(0.25)


def test_effective_score_low_vol_clamped_to_min():
    # vol = 0.005 → clamped to MIN_VOL=0.01 → multiplier = 2.0 → eff = 1.0
    eff = compute_effective_score(final_score=0.5, realized_vol_20d=0.005)
    assert eff == pytest.approx(1.0)


def test_effective_score_none_vol_returns_none():
    eff = compute_effective_score(final_score=0.5, realized_vol_20d=None)
    assert eff is None
```

- [ ] **Step 2: Run — verify fails**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/core/scoring/test_vol_normalization.py -q --no-cov`
Expected: ImportError.

- [ ] **Step 3: Implement vol_normalization.py**

Create `backend/app/core/scoring/vol_normalization.py`:

```python
"""Volatility normalization helpers (PR1).

VOL_NORM_TARGET=0.02 corresponds to a 2% daily-log-return stdev — a
typical BTC value. effective_score scales final_score so a 2%-vol day
neutralizes (multiplier=1.0); higher vol shrinks the score; lower vol
amplifies it, clamped at MIN_VOL to prevent runaway multipliers.

Recording-only in PR1 — does NOT feed back into the score or gating.
"""

from __future__ import annotations

import math
import numpy as np
import pandas as pd


VOL_NORM_TARGET: float = 0.02   # 2% daily log-return stdev = neutral
MIN_VOL: float = 0.01           # floor to clamp the multiplier ≤ 2.0
MIN_DAILY_SAMPLES: int = 20     # require ≥20 daily closes


def compute_realized_vol_20d(bars: pd.DataFrame) -> float | None:
    """Return 20-day stdev of daily log-returns.

    `bars` is the in-memory 1h-OHLCV frame the workers already
    maintain (must have a UTC DatetimeIndex). We resample to daily
    close, take log-returns, return stdev. None when <20 daily
    closes available.
    """
    if bars is None or len(bars) == 0:
        return None
    if "close" not in bars.columns:
        return None
    daily_close = bars["close"].resample("1D").last().dropna()
    if len(daily_close) < MIN_DAILY_SAMPLES + 1:
        return None
    log_returns = np.log(daily_close / daily_close.shift(1)).dropna()
    # Take last 20 daily returns for the rolling window
    last_20 = log_returns.tail(MIN_DAILY_SAMPLES)
    if len(last_20) < MIN_DAILY_SAMPLES:
        return None
    return float(last_20.std())


def compute_effective_score(
    final_score: float,
    realized_vol_20d: float | None,
) -> float | None:
    """Volatility-normalized signed score.

    formula: final_score × (VOL_NORM_TARGET / max(realized_vol_20d, MIN_VOL))

    Returns None when realized_vol_20d is None. Symmetric for LONG/SHORT.
    """
    if realized_vol_20d is None:
        return None
    denom = max(realized_vol_20d, MIN_VOL)
    return final_score * (VOL_NORM_TARGET / denom)


__all__ = [
    "MIN_DAILY_SAMPLES", "MIN_VOL", "VOL_NORM_TARGET",
    "compute_effective_score", "compute_realized_vol_20d",
]
```

- [ ] **Step 4: Run tests — verify pass**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/core/scoring/test_vol_normalization.py -q --no-cov`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```
git add backend/app/core/scoring/vol_normalization.py backend/tests/core/scoring/test_vol_normalization.py
git commit -m "feat(scoring): vol_normalization (realized_vol_20d + effective_score, both directions)"
```

### Task 4.2: `funding_directional.py` — signed funding-rate adjustment

**Files:**
- Create: `backend/app/core/scoring/funding_directional.py`
- Create: `backend/tests/core/scoring/test_funding_directional.py`

- [ ] **Step 1: Write failing test**

```python
"""Funding-directional adj — signed, symmetric, deadband 0."""

from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.scoring.funding_directional import (
    FUNDING_BOOST_MAGNITUDE,
    FUNDING_THRESHOLD,
    compute_funding_directional_adj,
)


@pytest.fixture
async def session_with_intermarket():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE intermarket_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                funding_rate REAL,
                mark_price REAL,
                open_interest REAL,
                source TEXT
            )
        """))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_funding(session, symbol, funding_rate):
    await session.execute(sa.text(
        "INSERT INTO intermarket_snapshots "
        "(symbol, captured_at, funding_rate, mark_price, open_interest, source) "
        "VALUES (:s, :t, :f, 80000, 1000, 'binance_futures')"
    ), {"s": symbol, "t": datetime.now(timezone.utc).isoformat(), "f": funding_rate})
    await session.commit()


async def test_high_positive_funding_returns_short_boost(session_with_intermarket):
    """funding > +0.05%/8h → -0.10 (boost SHORT, suppress LONG)."""
    s = session_with_intermarket
    await _seed_funding(s, "BTCUSDT", 0.001)  # 0.1%/8h, well above threshold
    adj = await compute_funding_directional_adj(s, "BTCUSDT")
    assert adj == pytest.approx(-FUNDING_BOOST_MAGNITUDE)


async def test_high_negative_funding_returns_long_boost(session_with_intermarket):
    """funding < -0.05%/8h → +0.10 (boost LONG, suppress SHORT)."""
    s = session_with_intermarket
    await _seed_funding(s, "BTCUSDT", -0.001)
    adj = await compute_funding_directional_adj(s, "BTCUSDT")
    assert adj == pytest.approx(+FUNDING_BOOST_MAGNITUDE)


async def test_deadband_returns_zero(session_with_intermarket):
    """|funding| < 0.05%/8h → 0."""
    s = session_with_intermarket
    await _seed_funding(s, "BTCUSDT", 0.0001)  # 0.01%/8h, in deadband
    adj = await compute_funding_directional_adj(s, "BTCUSDT")
    assert adj == 0.0


async def test_no_snapshot_returns_none(session_with_intermarket):
    s = session_with_intermarket
    adj = await compute_funding_directional_adj(s, "ETHUSDT")  # not seeded
    assert adj is None


async def test_threshold_is_symmetric(session_with_intermarket):
    s = session_with_intermarket
    await _seed_funding(s, "BTCUSDT", -FUNDING_THRESHOLD - 0.0001)
    adj_neg = await compute_funding_directional_adj(s, "BTCUSDT")
    await s.execute(sa.text("DELETE FROM intermarket_snapshots"))
    await _seed_funding(s, "BTCUSDT", FUNDING_THRESHOLD + 0.0001)
    adj_pos = await compute_funding_directional_adj(s, "BTCUSDT")
    assert adj_neg == -adj_pos
```

- [ ] **Step 2: Run — verify fails**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/core/scoring/test_funding_directional.py -q --no-cov`
Expected: ImportError.

- [ ] **Step 3: Implement funding_directional.py**

Create `backend/app/core/scoring/funding_directional.py`:

```python
"""Funding-rate directional adjustment (PR1, record-only).

Spec PART E: |funding| > 0.05%/8h adds ±0.10 to the LONG/SHORT side.

Sign convention (per D2 in design):
  +0.10 = LONG boost (negative funding — shorts paying longs)
  -0.10 = SHORT boost (positive funding — longs paying shorts)
   0    = deadband
   None = no intermarket snapshot for this symbol

In PR1 this is recorded only — does NOT feed back into final_score.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


FUNDING_THRESHOLD: float = 0.0005      # 0.05%/8h
FUNDING_BOOST_MAGNITUDE: float = 0.10


async def compute_funding_directional_adj(
    session: AsyncSession,
    symbol: str,
) -> float | None:
    """Look up the most recent intermarket_snapshot for `symbol`,
    return the signed boost. None when no snapshot exists.
    """
    row = (await session.execute(sa.text(
        "SELECT funding_rate FROM intermarket_snapshots "
        "WHERE symbol = :s ORDER BY captured_at DESC LIMIT 1"
    ), {"s": symbol})).first()
    if row is None or row.funding_rate is None:
        return None
    funding = float(row.funding_rate)
    if funding > FUNDING_THRESHOLD:
        return -FUNDING_BOOST_MAGNITUDE   # SHORT-boost
    if funding < -FUNDING_THRESHOLD:
        return +FUNDING_BOOST_MAGNITUDE   # LONG-boost
    return 0.0


__all__ = [
    "FUNDING_BOOST_MAGNITUDE", "FUNDING_THRESHOLD",
    "compute_funding_directional_adj",
]
```

- [ ] **Step 4: Run tests — verify pass**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/core/scoring/test_funding_directional.py -q --no-cov`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```
git add backend/app/core/scoring/funding_directional.py backend/tests/core/scoring/test_funding_directional.py
git commit -m "feat(scoring): funding_directional adj (symmetric, intermarket lookup)"
```

### Task 4.3: `p_win_calibrator.py` — fit + predict, no worker

**Files:**
- Create: `backend/app/core/scoring/p_win_calibrator.py`
- Create: `backend/tests/core/scoring/test_p_win_calibrator.py`
- Create: `backend/app/data/p_win_models/.gitkeep` (empty file so directory exists)
- Update: `.gitignore` to exclude `backend/app/data/p_win_models/*.pkl`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/core/scoring/test_p_win_calibrator.py`:

```python
"""p_win calibrator — IsotonicRegression per direction, lazy load."""

import shutil
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.scoring.p_win_calibrator import (
    P_WIN_MIN_TRADES_TO_FIT,
    fit_p_win_models,
    predict_p_win,
)


@pytest.fixture
def tmp_model_dir(tmp_path, monkeypatch):
    """Redirect P_WIN_MODEL_DIR to a tmp path for the test."""
    from app.core.scoring import p_win_calibrator as mod
    monkeypatch.setattr(mod, "P_WIN_MODEL_DIR", tmp_path)
    # Clear lazy cache for clean state
    monkeypatch.setattr(mod, "_LOADED_MODELS", {})
    yield tmp_path


@pytest.fixture
async def session_with_shadow_trades():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE shadow_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, symbol TEXT, direction TEXT,
                entry_score REAL, pnl_usdt REAL, closed_at TEXT
            )
        """))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_trades(session, n_long_wins, n_long_losses, n_short_wins, n_short_losses):
    rng = np.random.default_rng(42)
    rows = []
    # Wins: higher score → higher hit rate
    for _ in range(n_long_wins):
        rows.append(("LONG", float(rng.uniform(0.4, 0.9)), 1.5))
    for _ in range(n_long_losses):
        rows.append(("LONG", float(rng.uniform(0.3, 0.5)), -1.0))
    for _ in range(n_short_wins):
        rows.append(("SHORT", float(rng.uniform(-0.9, -0.4)), 1.5))
    for _ in range(n_short_losses):
        rows.append(("SHORT", float(rng.uniform(-0.5, -0.3)), -1.0))
    for direction, score, pnl in rows:
        await session.execute(sa.text(
            "INSERT INTO shadow_trades (user_id, symbol, direction, entry_score, pnl_usdt, closed_at) "
            "VALUES (1, 'BTCUSDT', :d, :s, :p, '2026-05-15T10:00:00+00:00')"
        ), {"d": direction, "s": score, "p": pnl})
    await session.commit()


async def test_fit_returns_none_when_below_min_trades(tmp_model_dir, session_with_shadow_trades):
    s = session_with_shadow_trades
    await _seed_trades(s, n_long_wins=10, n_long_losses=10, n_short_wins=0, n_short_losses=0)
    models = await fit_p_win_models(s)
    assert models["LONG"] is None  # 20 < 50
    assert models["SHORT"] is None  # 0 < 50


async def test_fit_succeeds_above_min_trades(tmp_model_dir, session_with_shadow_trades):
    s = session_with_shadow_trades
    await _seed_trades(s, n_long_wins=35, n_long_losses=20, n_short_wins=35, n_short_losses=20)
    models = await fit_p_win_models(s)
    assert models["LONG"] is not None
    assert models["SHORT"] is not None
    # Models persisted to disk
    assert (tmp_model_dir / "long.pkl").exists()
    assert (tmp_model_dir / "short.pkl").exists()


async def test_predict_returns_none_when_no_model(tmp_model_dir):
    p = await predict_p_win(final_score=0.5, direction="LONG")
    assert p is None


async def test_predict_returns_probability_after_fit(tmp_model_dir, session_with_shadow_trades):
    s = session_with_shadow_trades
    await _seed_trades(s, n_long_wins=35, n_long_losses=20, n_short_wins=35, n_short_losses=20)
    await fit_p_win_models(s)
    p_long = await predict_p_win(final_score=0.6, direction="LONG")
    p_short = await predict_p_win(final_score=-0.6, direction="SHORT")
    assert p_long is not None and 0.0 <= p_long <= 1.0
    assert p_short is not None and 0.0 <= p_short <= 1.0
```

- [ ] **Step 2: Run — verify fails**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/core/scoring/test_p_win_calibrator.py -q --no-cov`
Expected: ImportError.

- [ ] **Step 3: Implement p_win_calibrator.py**

Create `backend/app/core/scoring/p_win_calibrator.py`:

```python
"""p_win isotonic-regression calibrator (PR1).

PART C of upgrade plan. Per-direction sklearn IsotonicRegression
fit on closed shadow_trades. Persisted to disk; lazy-loaded on
first predict call. In PR1 there's no worker calling fit yet
(scheduled for PR5 nightly cron) — so predict returns None in
production until a model file exists.

Recording-only contract: returned p_win is attached to predictions
column but does NOT feed back into final_score or gating.
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Literal

import sqlalchemy as sa
from sklearn.isotonic import IsotonicRegression
from sqlalchemy.ext.asyncio import AsyncSession


log = logging.getLogger(__name__)

P_WIN_MIN_TRADES_TO_FIT: int = 50
P_WIN_ROLLING_WINDOW: int = 500
P_WIN_MODEL_DIR: Path = Path(__file__).resolve().parent.parent.parent / "data" / "p_win_models"

# Lazy in-memory cache of fitted models keyed by direction
_LOADED_MODELS: dict[str, IsotonicRegression | None] = {}


async def fit_p_win_models(
    session: AsyncSession,
) -> dict[Literal["LONG", "SHORT"], IsotonicRegression | None]:
    """Pull last P_WIN_ROLLING_WINDOW closed trades per direction;
    fit IsotonicRegression(score → win_prob) on each. Persist .pkl
    files. Returns dict with None values for directions below the
    min-trades threshold.
    """
    out: dict[str, IsotonicRegression | None] = {"LONG": None, "SHORT": None}
    for direction in ("LONG", "SHORT"):
        rows = (await session.execute(sa.text(
            "SELECT entry_score, pnl_usdt FROM shadow_trades "
            "WHERE closed_at IS NOT NULL AND direction = :d "
            "AND pnl_usdt IS NOT NULL "
            "ORDER BY closed_at DESC LIMIT :lim"
        ), {"d": direction, "lim": P_WIN_ROLLING_WINDOW})).all()

        if len(rows) < P_WIN_MIN_TRADES_TO_FIT:
            log.info(
                "p_win: skip fit for %s — only %d closed trades (need %d)",
                direction, len(rows), P_WIN_MIN_TRADES_TO_FIT,
            )
            continue

        scores = [float(r.entry_score) for r in rows]
        wins = [1.0 if float(r.pnl_usdt) > 0 else 0.0 for r in rows]
        # Isotonic regression needs monotonically-ordered x. For SHORT,
        # higher (less negative) entry_score corresponds to weaker signal,
        # so fit on |score| direction-corrected as positive.
        x = [abs(s) for s in scores]
        model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        model.fit(x, wins)
        out[direction] = model

        # Persist to disk
        P_WIN_MODEL_DIR.mkdir(parents=True, exist_ok=True)
        with (P_WIN_MODEL_DIR / f"{direction.lower()}.pkl").open("wb") as f:
            pickle.dump(model, f)
        log.info("p_win: fitted %s model on %d trades, saved", direction, len(rows))

    # Reset lazy cache so next predict_p_win re-loads fresh
    _LOADED_MODELS.clear()
    return out  # type: ignore[return-value]


def _load_model(direction: str) -> IsotonicRegression | None:
    if direction in _LOADED_MODELS:
        return _LOADED_MODELS[direction]
    path = P_WIN_MODEL_DIR / f"{direction.lower()}.pkl"
    if not path.exists():
        _LOADED_MODELS[direction] = None
        return None
    try:
        with path.open("rb") as f:
            model = pickle.load(f)
        _LOADED_MODELS[direction] = model
        return model
    except (pickle.UnpicklingError, EOFError, OSError) as e:
        log.warning("p_win: failed to load %s model from %s: %s", direction, path, e)
        _LOADED_MODELS[direction] = None
        return None


async def predict_p_win(
    final_score: float,
    direction: str,
) -> float | None:
    """Return calibrated win probability, or None when no model exists.

    PR1: returns None in production (no worker fits yet). PR5 wires the
    nightly recalibrate worker.
    """
    if direction not in ("LONG", "SHORT"):
        return None
    model = _load_model(direction)
    if model is None:
        return None
    x = [abs(final_score)]
    p = float(model.predict(x)[0])
    return max(0.0, min(1.0, p))


__all__ = [
    "P_WIN_MIN_TRADES_TO_FIT", "P_WIN_MODEL_DIR", "P_WIN_ROLLING_WINDOW",
    "fit_p_win_models", "predict_p_win",
]
```

- [ ] **Step 4: Add .gitignore entry + create model dir**

Append to `.gitignore`:
```
backend/app/data/p_win_models/*.pkl
```

Create `backend/app/data/p_win_models/.gitkeep`:
```
(empty)
```

- [ ] **Step 5: Run tests — verify pass**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/core/scoring/test_p_win_calibrator.py -q --no-cov`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```
git add backend/app/core/scoring/p_win_calibrator.py backend/tests/core/scoring/test_p_win_calibrator.py backend/app/data/p_win_models/.gitkeep .gitignore
git commit -m "feat(scoring): p_win_calibrator (isotonic, both directions, lazy load, no worker yet)"
```

### Task 4.4: `mtf_confluence.py` — 6-TF SPOT-REST + cache + gather + prewarm + TTL-refresh

This is the largest module. Breaking into 4 sub-tasks: (a) cache + types, (b) per-TF compute, (c) gathered top-level, (d) prewarm + TTL-refresh.

#### Task 4.4a: Cache + types + per-TF compute

**Files:**
- Create: `backend/app/core/scoring/mtf_confluence.py` (initial — types, cache, _vote_for_tf)
- Create: `backend/tests/core/scoring/test_mtf_confluence.py` (initial — vote + cache tests)

- [ ] **Step 1: Write failing tests for the per-TF vote function**

```python
"""MTF confluence — per-TF vote on EMA20/50 + ADX14."""

import asyncio
from unittest.mock import AsyncMock

import numpy as np
import pytest

from app.core.scoring.mtf_confluence import (
    CACHE_TTL_S, MtfConfluence, TIMEFRAMES,
    _vote_for_tf, _cache_get, _cache_set, _KLINE_CACHE,
)


def _make_uptrend_klines(n=200):
    """Generate klines where EMA20 > EMA50 and ADX is high."""
    closes = np.linspace(80000.0, 90000.0, n)
    highs = closes * 1.005
    lows = closes * 0.995
    # Return Binance-style [[open_ts, o, h, l, c, vol, close_ts, ...]] rows
    return [
        [i * 60_000, c * 0.999, h, l, c, 100.0, i * 60_000 + 59999, 0, 0, 0, 0, 0]
        for i, (c, h, l) in enumerate(zip(closes, highs, lows))
    ]


def _make_downtrend_klines(n=200):
    closes = np.linspace(90000.0, 80000.0, n)
    highs = closes * 1.005
    lows = closes * 0.995
    return [
        [i * 60_000, c * 1.001, h, l, c, 100.0, i * 60_000 + 59999, 0, 0, 0, 0, 0]
        for i, (c, h, l) in enumerate(zip(closes, highs, lows))
    ]


def test_vote_for_tf_uptrend_returns_long_with_adx():
    klines = _make_uptrend_klines()
    vote, adx = _vote_for_tf(klines)
    assert vote == +1
    assert adx >= 20.0  # uptrend → ADX above the trend floor


def test_vote_for_tf_downtrend_returns_short_with_adx():
    klines = _make_downtrend_klines()
    vote, adx = _vote_for_tf(klines)
    assert vote == -1
    assert adx >= 20.0


def test_vote_for_tf_chop_returns_neutral_with_low_adx():
    # Sideways close → EMA20 ≈ EMA50, ADX low
    rng = np.random.default_rng(42)
    closes = 80000.0 + rng.normal(0, 50, 200)
    highs = closes * 1.0001
    lows = closes * 0.9999
    klines = [
        [i * 60_000, c, h, l, c, 100.0, i * 60_000 + 59999, 0, 0, 0, 0, 0]
        for i, (c, h, l) in enumerate(zip(closes, highs, lows))
    ]
    vote, adx = _vote_for_tf(klines)
    assert vote == 0
    # ADX may be any value here; the trend floor in _vote_for_tf clamps vote to 0


def test_cache_ttl_get_returns_none_when_expired():
    _KLINE_CACHE.clear()
    klines = _make_uptrend_klines()
    _cache_set("BTCUSDT", "1h", klines, fetched_at=0.0)
    # _cache_get with current time well past TTL returns None
    result = _cache_get("BTCUSDT", "1h", now=1e9)
    assert result is None


def test_cache_ttl_get_returns_value_within_ttl():
    _KLINE_CACHE.clear()
    klines = _make_uptrend_klines()
    _cache_set("BTCUSDT", "1h", klines, fetched_at=100.0)
    result = _cache_get("BTCUSDT", "1h", now=100.0 + CACHE_TTL_S["1h"] - 1)
    assert result == klines
```

- [ ] **Step 2: Run — verify fails**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/core/scoring/test_mtf_confluence.py -q --no-cov`
Expected: ImportError.

- [ ] **Step 3: Implement initial mtf_confluence.py**

Create `backend/app/core/scoring/mtf_confluence.py`:

```python
"""Multi-timeframe (MTF) confluence — 6-TF SPOT REST vote (PR1).

Per spec PART A. In PR1, results are RECORD-ONLY: the aggregator
attaches mtf_agreement / mtf_dominant_tf / mtf_directions_json to
the prediction row, but does NOT use them in any gate. PR2 adds
the dispatcher gate.

Source: Binance SPOT REST /api/v3/klines — geoblock-safe from Hetzner.
Cache: module-level dict, TTL per TF tier (5m=60s, 15m=60s, 1h=300s,
       4h/1d/1w=3600s).
Concurrency: asyncio.gather(return_exceptions=True) — any per-TF
             timeout or HTTP failure degrades that TF only.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np


log = logging.getLogger(__name__)


TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "1h", "4h", "1d", "1w")

CACHE_TTL_S: dict[str, int] = {
    "5m": 60, "15m": 60, "1h": 300,
    "4h": 3600, "1d": 3600, "1w": 3600,
}

KLINE_LIMIT: int = 200
TF_FETCH_TIMEOUT_S: float = 2.0
ADX_PERIOD: int = 14
EMA_SHORT: int = 20
EMA_LONG: int = 50
ADX_TREND_FLOOR: float = 20.0


@dataclass(frozen=True)
class MtfConfluence:
    """Result of a multi-timeframe vote.

    Per Correction 4:
      agreement:   count of TFs voting same direction as signal (LONG/SHORT);
                   None for NEUTRAL signals (avoids "agreement with what?" ambiguity).
      dominant_tf: TF with highest |vote × ADX| across all 6; None if all
                   six TFs voted 0 (no trending TF).
      directions:  per-TF vote map { "5m": +1, "15m": -1, "1h": 0, ... } —
                   always populated fully even for NEUTRAL signals.
    """
    agreement: int | None
    dominant_tf: str | None
    directions: dict[str, int] = field(default_factory=dict)


@dataclass
class _CacheEntry:
    klines: list[list[Any]]
    fetched_at: float


_KLINE_CACHE: dict[tuple[str, str], _CacheEntry] = {}


def _cache_get(symbol: str, tf: str, *, now: float | None = None) -> list[list[Any]] | None:
    n = now if now is not None else time.time()
    entry = _KLINE_CACHE.get((symbol, tf))
    if entry is None:
        return None
    if (n - entry.fetched_at) >= CACHE_TTL_S[tf]:
        return None
    return entry.klines


def _cache_set(symbol: str, tf: str, klines: list[list[Any]], *, fetched_at: float | None = None) -> None:
    _KLINE_CACHE[(symbol, tf)] = _CacheEntry(
        klines=klines,
        fetched_at=fetched_at if fetched_at is not None else time.time(),
    )


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1)
    out = np.empty_like(arr)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = ADX_PERIOD) -> float:
    if len(close) < period + 2:
        return 0.0
    tr = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - close[:-1]),
        np.abs(low[1:] - close[:-1]),
    ])
    dm_plus = np.where(
        (high[1:] - high[:-1]) > (low[:-1] - low[1:]),
        np.maximum(high[1:] - high[:-1], 0.0), 0.0,
    )
    dm_minus = np.where(
        (low[:-1] - low[1:]) > (high[1:] - high[:-1]),
        np.maximum(low[:-1] - low[1:], 0.0), 0.0,
    )
    atr = _ema(tr, period)
    safe_atr = np.where(atr == 0, 1.0, atr)
    di_plus = 100 * _ema(dm_plus, period) / safe_atr
    di_minus = 100 * _ema(dm_minus, period) / safe_atr
    denom = np.where((di_plus + di_minus) == 0, 1.0, (di_plus + di_minus))
    dx = 100 * np.abs(di_plus - di_minus) / denom
    return float(_ema(dx, period)[-1])


def _vote_for_tf(klines: list[list[Any]]) -> tuple[int, float]:
    """Compute +1/-1/0 vote + ADX(14) magnitude from EMA20/50 cross.

    Returns ``(vote, adx)`` — ADX is returned even when vote=0 so that
    ``_compute_agreement_and_dominant`` can rank TFs by ``|vote × ADX|``
    per Correction 4 (dominant_tf semantics).

    Binance kline row format: [open_time, o, h, l, c, vol, close_time, ...].
    We use the closed-candle close as the trailing value.
    """
    if len(klines) < max(EMA_LONG, ADX_PERIOD + 2):
        return 0, 0.0
    closes = np.asarray([float(k[4]) for k in klines])
    highs = np.asarray([float(k[2]) for k in klines])
    lows = np.asarray([float(k[3]) for k in klines])
    ema_short = _ema(closes, EMA_SHORT)[-1]
    ema_long = _ema(closes, EMA_LONG)[-1]
    adx = _adx(highs, lows, closes)
    if adx < ADX_TREND_FLOOR:
        return 0, adx
    if ema_short > ema_long:
        return +1, adx
    if ema_short < ema_long:
        return -1, adx
    return 0, adx


__all__ = [
    "ADX_PERIOD", "ADX_TREND_FLOOR", "CACHE_TTL_S",
    "EMA_LONG", "EMA_SHORT", "KLINE_LIMIT", "TIMEFRAMES",
    "TF_FETCH_TIMEOUT_S",
    "MtfConfluence",
    "_cache_get", "_cache_set", "_KLINE_CACHE",
    "_vote_for_tf",
]
```

- [ ] **Step 4: Run — verify passes**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/core/scoring/test_mtf_confluence.py -q --no-cov`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```
git add backend/app/core/scoring/mtf_confluence.py backend/tests/core/scoring/test_mtf_confluence.py
git commit -m "feat(mtf): per-TF vote + cache primitives + types"
```

#### Task 4.4b: Per-TF fetch + asyncio.gather top-level

- [ ] **Step 1: Add failing tests for the fetcher + gather**

Append to `tests/core/scoring/test_mtf_confluence.py`:

```python
import respx
from httpx import Response

from app.core.scoring.mtf_confluence import (
    compute_mtf_confluence, _fetch_one_tf,
)
from app.core.scoring.types import Direction


@respx.mock
async def test_fetch_one_tf_caches_on_success():
    _KLINE_CACHE.clear()
    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=_make_uptrend_klines())
    )
    async with __import__("httpx").AsyncClient() as http:
        klines = await _fetch_one_tf(http, "BTCUSDT", "1h")
    assert len(klines) == 200
    # Second call uses cache (mock count stays at 1)
    async with __import__("httpx").AsyncClient() as http:
        klines2 = await _fetch_one_tf(http, "BTCUSDT", "1h")
    assert klines2 == klines
    assert respx.calls.call_count == 1


@respx.mock
async def test_fetch_one_tf_timeout_returns_none():
    _KLINE_CACHE.clear()
    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        side_effect=__import__("httpx").TimeoutException("timeout")
    )
    async with __import__("httpx").AsyncClient() as http:
        klines = await _fetch_one_tf(http, "BTCUSDT", "5m")
    assert klines is None


@respx.mock
async def test_compute_mtf_confluence_uptrend_long():
    _KLINE_CACHE.clear()
    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=_make_uptrend_klines())
    )
    result = await compute_mtf_confluence("BTCUSDT", Direction.LONG)
    assert result is not None
    assert result.agreement == 6   # all 6 TFs voted +1 matching LONG signal
    assert result.dominant_tf in TIMEFRAMES   # one of the 6 was picked
    # All TF directions are +1
    assert set(result.directions.values()) == {+1}


@respx.mock
async def test_compute_mtf_confluence_neutral_signal_agreement_is_none():
    """Per Correction 4 — NEUTRAL signal returns agreement=None;
    dominant_tf is the TF with highest |vote × ADX|; directions still
    populated fully."""
    _KLINE_CACHE.clear()
    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=_make_uptrend_klines())
    )
    result = await compute_mtf_confluence("BTCUSDT", Direction.NEUTRAL)
    assert result is not None
    assert result.agreement is None
    assert result.dominant_tf in TIMEFRAMES   # uptrend → at least one non-zero
    assert len(result.directions) == 6        # full per-TF map


@respx.mock
async def test_compute_mtf_confluence_all_chop_dominant_is_none():
    """When every TF returns 0-vote (chop), dominant_tf is None."""
    _KLINE_CACHE.clear()
    # Generate chop klines (sideways, low ADX → 0 vote)
    rng = np.random.default_rng(42)
    closes = 80000.0 + rng.normal(0, 50, 200)
    chop = [
        [i * 60_000, c, c * 1.0001, c * 0.9999, c, 100.0, i * 60_000 + 59999, 0, 0, 0, 0, 0]
        for i, c in enumerate(closes)
    ]
    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=chop)
    )
    result = await compute_mtf_confluence("BTCUSDT", Direction.LONG)
    assert result is not None
    assert result.dominant_tf is None
    assert result.agreement == 0


@respx.mock
async def test_compute_mtf_confluence_partial_failure_fail_open():
    """gather(return_exceptions=True): one TF timeout doesn't kill result."""
    _KLINE_CACHE.clear()
    # Mock with timeouts for "5m" but uptrend for the rest
    def _route(request):
        if "interval=5m" in str(request.url):
            raise __import__("httpx").TimeoutException("5m timeout")
        return Response(200, json=_make_uptrend_klines())
    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(side_effect=_route)
    result = await compute_mtf_confluence("BTCUSDT", Direction.LONG)
    assert result is not None  # partial result returned, not None
    assert result.directions["5m"] == 0   # failed TF degrades to 0-vote
    assert result.agreement >= 4          # at least 4 other TFs still +1
```

- [ ] **Step 2: Run — verify fails**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/core/scoring/test_mtf_confluence.py -q --no-cov`
Expected: ImportError on `compute_mtf_confluence` / `_fetch_one_tf`.

- [ ] **Step 3: Extend mtf_confluence.py with fetcher + gather**

Append to `backend/app/core/scoring/mtf_confluence.py`:

```python
from app.core.scoring.types import Direction

_BASE_URL = "https://api.binance.com"


async def _fetch_one_tf(
    http: httpx.AsyncClient,
    symbol: str,
    tf: str,
    *,
    cache_get=_cache_get,
    cache_set=_cache_set,
) -> list[list[Any]] | None:
    """Fetch klines for one TF; cache-hit returns immediately.

    On HTTP failure or timeout, returns None — caller treats as a
    zero-vote degradation per the fail-open contract.
    """
    cached = cache_get(symbol, tf)
    if cached is not None:
        return cached
    try:
        resp = await http.get(
            f"{_BASE_URL}/api/v3/klines",
            params={"symbol": symbol, "interval": tf, "limit": KLINE_LIMIT},
            timeout=TF_FETCH_TIMEOUT_S,
        )
        resp.raise_for_status()
        klines = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        log.warning("mtf fetch %s/%s failed: %s", symbol, tf, e)
        return None
    cache_set(symbol, tf, klines)
    return klines


def _compute_agreement_and_dominant(
    direction: Direction,
    tf_data: dict[str, tuple[int, float]],
) -> tuple[int | None, str | None]:
    """Reduce per-TF (vote, adx) data to (agreement, dominant_tf).

    Per Correction 4:
      - LONG/SHORT signal: agreement = count of TFs matching direction
      - NEUTRAL signal:    agreement = None (no direction to compare against)
      - dominant_tf:       TF with highest |vote × adx| across all 6;
                           None if all 6 TFs voted 0.
    """
    count_long = sum(1 for v, _ in tf_data.values() if v > 0)
    count_short = sum(1 for v, _ in tf_data.values() if v < 0)

    # Dominant TF: highest |vote × adx| among non-zero-voting TFs.
    candidates: list[tuple[str, float]] = [
        (tf, abs(v) * adx) for tf, (v, adx) in tf_data.items() if v != 0
    ]
    dominant: str | None = max(candidates, key=lambda x: x[1])[0] if candidates else None

    agreement: int | None
    if direction is Direction.LONG:
        agreement = count_long
    elif direction is Direction.SHORT:
        agreement = count_short
    else:  # NEUTRAL — per Correction 4
        agreement = None
    return agreement, dominant


async def compute_mtf_confluence(
    symbol: str,
    signal_direction: Direction,
    *,
    _http: httpx.AsyncClient | None = None,
) -> MtfConfluence | None:
    """Top-level: fetch + vote across all 6 TFs in parallel.

    Returns None ONLY if ALL 6 TFs failed. Otherwise returns a
    partial result with failed TFs as (0, 0.0) entries (fail-open).
    """
    own_http = _http is None
    http = _http or httpx.AsyncClient(timeout=TF_FETCH_TIMEOUT_S)
    try:
        results = await asyncio.gather(
            *[_fetch_one_tf(http, symbol, tf) for tf in TIMEFRAMES],
            return_exceptions=True,
        )
    finally:
        if own_http:
            await http.aclose()

    tf_data: dict[str, tuple[int, float]] = {}
    failures = 0
    for tf, klines_or_exc in zip(TIMEFRAMES, results, strict=True):
        if isinstance(klines_or_exc, BaseException) or klines_or_exc is None:
            tf_data[tf] = (0, 0.0)
            failures += 1
            continue
        tf_data[tf] = _vote_for_tf(klines_or_exc)

    if failures == len(TIMEFRAMES):
        return None  # total failure

    agreement, dominant = _compute_agreement_and_dominant(signal_direction, tf_data)
    votes_only: dict[str, int] = {tf: v for tf, (v, _) in tf_data.items()}
    return MtfConfluence(
        agreement=agreement,
        dominant_tf=dominant,
        directions=votes_only,
    )


# Extend __all__
__all__ += ["compute_mtf_confluence", "_fetch_one_tf"]
```

- [ ] **Step 4: Run tests — verify pass**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/core/scoring/test_mtf_confluence.py -q --no-cov`
Expected: 11 passed (5 prior in 4.4a + 6 new in 4.4b: fetch_caches, fetch_timeout, compute_uptrend_long, compute_neutral_signal, compute_all_chop, compute_partial_failure).

- [ ] **Step 5: Commit**

```
git add backend/app/core/scoring/mtf_confluence.py backend/tests/core/scoring/test_mtf_confluence.py
git commit -m "feat(mtf): compute_mtf_confluence — gather, fail-open per TF, NEUTRAL=None per Correction 4"
```

#### Task 4.4c: Prewarm + TTL-refresh loop

- [ ] **Step 1: Write failing test**

Append:

```python
import time


@respx.mock
async def test_prewarm_cache_populates_all_tfs():
    _KLINE_CACHE.clear()
    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=_make_uptrend_klines())
    )
    from app.core.scoring.mtf_confluence import prewarm_cache, TIMEFRAMES
    n = await prewarm_cache(["BTCUSDT", "ETHUSDT"], deadline_seconds=30.0)
    assert n == 2 * len(TIMEFRAMES)  # 12 entries


@respx.mock
async def test_prewarm_cache_respects_deadline(monkeypatch):
    _KLINE_CACHE.clear()
    # Make every fetch take 2s so 12 fetches × 2s = 24s exceeds 1s deadline
    async def _slow_route(request):
        await asyncio.sleep(2.0)
        return Response(200, json=_make_uptrend_klines())
    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        side_effect=_slow_route
    )
    from app.core.scoring.mtf_confluence import prewarm_cache
    n = await prewarm_cache(["BTCUSDT"], deadline_seconds=1.0)
    # Got AT MOST some entries; deadline cancelled the rest
    assert n < 6


async def test_refresh_loop_refreshes_near_expiry(monkeypatch):
    """Cache entry within 20% of expiry gets refreshed in the next loop tick."""
    _KLINE_CACHE.clear()
    klines = _make_uptrend_klines()
    # Seed entry that's 90% of TTL old (within the 20% window)
    fake_now = 1000.0
    _cache_set("BTCUSDT", "1h", klines, fetched_at=fake_now - CACHE_TTL_S["1h"] * 0.85)
    # The refresh routine just checks staleness — assert it identifies the entry
    from app.core.scoring.mtf_confluence import _entries_due_for_refresh
    due = _entries_due_for_refresh(now=fake_now, expiry_threshold_pct=0.20)
    assert ("BTCUSDT", "1h") in due
```

- [ ] **Step 2: Run — verify fails**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/core/scoring/test_mtf_confluence.py -q --no-cov`
Expected: ImportError on `prewarm_cache` / `_entries_due_for_refresh`.

- [ ] **Step 3: Append prewarm + refresh implementation**

Append to `backend/app/core/scoring/mtf_confluence.py`:

```python
async def prewarm_cache(
    symbols: list[str],
    *,
    deadline_seconds: float = 60.0,
    _http: httpx.AsyncClient | None = None,
) -> int:
    """Populate cache for symbols × TIMEFRAMES. Stops at deadline.

    Returns count of entries successfully cached. Logs start/end with
    duration. Background-task-friendly: uses same _fetch_one_tf path
    as production, so any bug surfaces in tests.
    """
    start = time.time()
    log.info("mtf_prewarm: start symbols=%d tfs=%d deadline=%.1fs",
             len(symbols), len(TIMEFRAMES), deadline_seconds)
    own_http = _http is None
    http = _http or httpx.AsyncClient(timeout=TF_FETCH_TIMEOUT_S)
    cached_count = 0
    try:
        for sym in symbols:
            if time.time() - start >= deadline_seconds:
                log.info("mtf_prewarm: deadline reached at sym=%s", sym)
                break
            results = await asyncio.gather(
                *[_fetch_one_tf(http, sym, tf) for tf in TIMEFRAMES],
                return_exceptions=True,
            )
            for tf, k in zip(TIMEFRAMES, results):
                if isinstance(k, BaseException) or k is None:
                    continue
                cached_count += 1
    finally:
        if own_http:
            await http.aclose()
    duration = time.time() - start
    log.info("mtf_prewarm: done duration=%.2fs entries=%d", duration, cached_count)
    return cached_count


def _entries_due_for_refresh(
    *,
    now: float | None = None,
    expiry_threshold_pct: float = 0.20,
) -> list[tuple[str, str]]:
    """Find cache entries within `expiry_threshold_pct` of their TTL expiry."""
    n = now if now is not None else time.time()
    due: list[tuple[str, str]] = []
    for (symbol, tf), entry in _KLINE_CACHE.items():
        age = n - entry.fetched_at
        ttl = CACHE_TTL_S[tf]
        time_to_expiry = ttl - age
        if 0 < time_to_expiry <= ttl * expiry_threshold_pct:
            due.append((symbol, tf))
    return due


async def run_mtf_cache_refresh_loop(
    session_factory,  # async_sessionmaker — needed for heartbeat
    *,
    interval_s: int = 30,
    expiry_threshold_pct: float = 0.20,
    _http: httpx.AsyncClient | None = None,
) -> None:
    """Background loop: every interval_s, refresh entries within 20% of expiry.

    Operator bound (PR1):
      - simple: no stampede protection, no coalescing, no Redis
      - respects existing per-TF TTLs (no adaptive)
      - on refresh failure: entry expires normally, next read pays cold-cache cost
      - heartbeats every loop iteration so the watchdog sees us (per Correction 2)
    """
    from app.ops.heartbeat import record_heartbeat
    log.info("mtf_cache_refresh: starting (interval=%ds)", interval_s)
    own_http = _http is None
    http = _http or httpx.AsyncClient(timeout=TF_FETCH_TIMEOUT_S)
    try:
        while True:
            await record_heartbeat(
                session_factory, WORKER_NAME_REFRESH,
                status="ok",
                details={"cache_entries": len(_KLINE_CACHE)},
            )
            await asyncio.sleep(interval_s)
            due = _entries_due_for_refresh(expiry_threshold_pct=expiry_threshold_pct)
            if not due:
                continue
            log.info("mtf_cache_refresh: tick — refreshing %d entries", len(due))
            await asyncio.gather(
                *[_fetch_one_tf(http, sym, tf) for sym, tf in due],
                return_exceptions=True,
            )
    finally:
        if own_http:
            await http.aclose()


# Worker names used by worker_registry.py (per Correction 2).
WORKER_NAME_PREWARM: str = "mtf_cache_prewarm_task"
WORKER_NAME_REFRESH: str = "mtf_cache_ttl_refresh_task"


def start_mtf_cache_prewarm_task(session_factory) -> asyncio.Task[None]:
    """Spawn the single-shot prewarm as an asyncio.Task.

    Loads the current universe, runs prewarm_cache, returns. Task
    completes naturally — watchdog's pending_heartbeat=True flag skips
    the staleness check (single-shot by design).
    """
    async def _runner():
        from app.shadow.universe import load_current_universe
        try:
            async with session_factory() as session:
                entries = await load_current_universe(session)
            symbols = [e.symbol for e in entries[:30]]
            await prewarm_cache(symbols, deadline_seconds=60.0)
        except Exception as e:  # noqa: BLE001 — fail-open per spec
            log.warning("mtf_cache_prewarm task failed: %s", e)
    return asyncio.create_task(_runner(), name=WORKER_NAME_PREWARM)


def start_mtf_cache_ttl_refresh_task(session_factory) -> asyncio.Task[None]:
    """Spawn the long-running TTL-refresh loop as an asyncio.Task."""
    return asyncio.create_task(
        run_mtf_cache_refresh_loop(session_factory, interval_s=30),
        name=WORKER_NAME_REFRESH,
    )


__all__ += [
    "WORKER_NAME_PREWARM", "WORKER_NAME_REFRESH",
    "_entries_due_for_refresh",
    "prewarm_cache",
    "run_mtf_cache_refresh_loop",
    "start_mtf_cache_prewarm_task",
    "start_mtf_cache_ttl_refresh_task",
]
```

- [ ] **Step 4: Run all mtf tests**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/core/scoring/test_mtf_confluence.py -q --no-cov`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```
git add backend/app/core/scoring/mtf_confluence.py backend/tests/core/scoring/test_mtf_confluence.py
git commit -m "feat(mtf): prewarm + TTL-refresh loop (simple, no stampede protection)"
```

---

## Phase 5 — Aggregator hook + LivePredictionOut + builders extension

### Task 5.1: Add new optional fields to `LivePredictionOut`

**Files:**
- Modify: `backend/app/api/schemas.py` lines 123-148

- [ ] **Step 1: Write failing test**

Create `backend/tests/api/test_live_prediction_out_new_fields.py`:

```python
"""LivePredictionOut: new PR1 fields are optional (default None) — extra='ignore'."""

from datetime import datetime, timezone

from app.api.schemas import (
    FinalScoreOut, LayerScoreOut, LivePredictionOut,
    MomentumPanelOut, TradeSetupOut,
)


def _minimal_out_kwargs():
    return dict(
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        price=80000.0,
        final=FinalScoreOut(score=0.5, direction="LONG", confidence=0.6,
                            contributing_layers=[1, 3]),
        layer_scores={},
        trade_setup=TradeSetupOut(direction="LONG", entry=80000.0,
                                  stop_loss=79000.0, take_profit=82000.0,
                                  risk_reward=2.0),
        momentum=MomentumPanelOut(rsi=55.0, macd_hist=0.5, adx=22.0),
        inputs_hash="abc",
    )


def test_new_fields_default_to_none():
    out = LivePredictionOut(**_minimal_out_kwargs())
    assert out.mtf_agreement is None
    assert out.mtf_dominant_tf is None
    assert out.mtf_directions_json is None
    assert out.p_win is None
    assert out.effective_score is None
    assert out.realized_vol_20d is None
    assert out.funding_directional_adj is None


def test_new_fields_round_trip():
    kw = _minimal_out_kwargs()
    kw.update(dict(
        mtf_agreement=4, mtf_dominant_tf="1h",
        mtf_directions_json={"5m": 1, "15m": 1, "1h": 1, "4h": 0, "1d": 1, "1w": -1},
        p_win=0.62, effective_score=0.5,
        realized_vol_20d=0.020, funding_directional_adj=-0.10,
    ))
    out = LivePredictionOut(**kw)
    d = out.model_dump(mode="json")
    assert d["mtf_agreement"] == 4
    assert d["funding_directional_adj"] == -0.10


def test_extra_ignore_no_explicit_forbid():
    """Confirm no 'extra=forbid' on the model — frontend WS payloads
    with unknown fields must not raise."""
    out = LivePredictionOut(**_minimal_out_kwargs(), unknown_future_field="hi")  # type: ignore[call-arg]
    # If model_config sets extra='forbid', the line above raises ValidationError.
    # Reaching here means we're safe.
    assert out is not None
```

- [ ] **Step 2: Run — verify fails (fields don't exist)**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/api/test_live_prediction_out_new_fields.py -q --no-cov`
Expected: ValidationError on unknown fields OR AttributeError.

- [ ] **Step 3: Add fields to `LivePredictionOut`**

In [backend/app/api/schemas.py:123-148](backend/app/api/schemas.py#L123-L148), inside the `LivePredictionOut` class body, append (right before the closing bracket of fields):

```python
    # PR1 record-only analytics fields (default None — feature gates land in PR2+).
    mtf_agreement: int | None = None
    mtf_dominant_tf: str | None = None
    mtf_directions_json: dict[str, int] | None = None
    p_win: float | None = None
    effective_score: float | None = None
    realized_vol_20d: float | None = None
    funding_directional_adj: float | None = None
```

Do NOT add `model_config = ConfigDict(extra='forbid')`. Leave Pydantic default (extra='ignore').

- [ ] **Step 4: Run — verify passes**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/api/test_live_prediction_out_new_fields.py -q --no-cov`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```
git add backend/app/api/schemas.py backend/tests/api/test_live_prediction_out_new_fields.py
git commit -m "feat(schemas): add 7 PR1 record-only optional fields to LivePredictionOut"
```

### Task 5.2: Wire compute calls into `build_prediction`

**Files:**
- Modify: `backend/app/core/predictor.py` (find return statement of `build_prediction`)

- [ ] **Step 1: Locate `build_prediction` return statement**

Run: `grep -nB1 -A3 "return LivePredictionOut" backend/app/core/predictor.py`
Note the line range.

- [ ] **Step 2: Write integration-style failing test (uses real predictor, mocked deps)**

Create `backend/tests/core/test_build_prediction_with_pr1_fields.py`:

```python
"""build_prediction must populate the 7 new fields when its compute path runs.

We mock the HTTP layer (no Binance) and the intermarket_snapshot lookup;
the math functions run real.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import asyncio

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.predictor import build_prediction


@pytest.fixture
async def session_with_intermarket():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE intermarket_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                funding_rate REAL, mark_price REAL,
                open_interest REAL, source TEXT
            )
        """))
        await conn.execute(sa.text(
            "INSERT INTO intermarket_snapshots "
            "(symbol, captured_at, funding_rate, mark_price, open_interest, source) "
            "VALUES ('BTCUSDT', '2026-05-16T09:00:00+00:00', 0.0008, 80000, 1000, 'binance_futures')"
        ))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _make_bars(n=500):
    rng = np.random.default_rng(42)
    closes = 80000.0 * np.exp(np.cumsum(rng.normal(0, 0.005, n)))
    highs = closes * 1.002
    lows = closes * 0.998
    opens = np.roll(closes, 1); opens[0] = 80000.0
    volumes = rng.uniform(100, 1000, n)
    ts = pd.date_range("2026-04-01", periods=n, freq="1H", tz="UTC")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=ts,
    )


async def test_build_prediction_populates_pr1_fields(session_with_intermarket):
    """Vol, funding, effective_score are populated. MTF and p_win are
    None in this test (MTF mocked off, p_win has no model loaded)."""
    bars = _make_bars(500)
    with patch(
        "app.core.scoring.mtf_confluence.compute_mtf_confluence",
        new=AsyncMock(return_value=None),
    ):
        pred = await build_prediction(
            symbol="BTC/USDT", timeframe="1h", bars=bars,
            pattern_stats_lookup=None,
            session=session_with_intermarket,
        )

    # Vol normalization populated (we have ~20 daily bars)
    assert pred.realized_vol_20d is not None
    assert pred.effective_score is not None
    # Funding adj: seeded 0.0008 > 0.0005 threshold → SHORT-boost = -0.10
    assert pred.funding_directional_adj == pytest.approx(-0.10)
    # p_win is None (no model files)
    assert pred.p_win is None
    # MTF is None (mocked out)
    assert pred.mtf_agreement is None
    assert pred.mtf_dominant_tf is None
    assert pred.mtf_directions_json is None
```

- [ ] **Step 3: Run — verify fails**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/core/test_build_prediction_with_pr1_fields.py -q --no-cov`
Expected: assertions fail — fields are None because compute hasn't been wired in.

- [ ] **Step 4: Wire compute calls into `build_prediction`**

In `backend/app/core/predictor.py`, near the bottom of `build_prediction` (just before the final `return LivePredictionOut(...)`), insert the compute calls and pass to the constructor:

```python
    # --- PR1 record-only analytics (no behavior change to final_score) ---
    from app.core.scoring.mtf_confluence import compute_mtf_confluence
    from app.core.scoring.p_win_calibrator import predict_p_win
    from app.core.scoring.vol_normalization import (
        compute_effective_score, compute_realized_vol_20d,
    )
    from app.core.scoring.funding_directional import compute_funding_directional_adj

    binance_symbol = symbol.replace("/", "")
    mtf = await compute_mtf_confluence(binance_symbol, final.direction)
    p_win_val = await predict_p_win(final.score, final.direction.value)
    vol_20d = compute_realized_vol_20d(bars)
    eff_score = compute_effective_score(final.score, vol_20d)
    funding_adj = None
    if session is not None:
        try:
            funding_adj = await compute_funding_directional_adj(session, binance_symbol)
        except Exception as e:  # noqa: BLE001 — recording-only, never block scoring
            log.warning("compute_funding_directional_adj failed: %s", e)

    return LivePredictionOut(
        # ... existing kwargs ...
        # NEW PR1 record-only fields:
        mtf_agreement=mtf.agreement if mtf else None,
        mtf_dominant_tf=mtf.dominant_tf if mtf else None,
        mtf_directions_json=mtf.directions if mtf else None,
        p_win=p_win_val,
        effective_score=eff_score,
        realized_vol_20d=vol_20d,
        funding_directional_adj=funding_adj,
    )
```

NOTE: the engineer must merge these kwargs with the existing `LivePredictionOut(...)` arg list — keep all the existing kwargs unchanged, just append the 7 new ones.

- [ ] **Step 5: Run — verify passes**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/core/test_build_prediction_with_pr1_fields.py -q --no-cov`
Expected: 1 passed.

- [ ] **Step 6: Mypy check + commit**

Run: `cd backend && python -m mypy app 2>&1 | tail -3`
Expected: `Success: no issues found ...`.

```
git add backend/app/core/predictor.py backend/tests/core/test_build_prediction_with_pr1_fields.py
git commit -m "feat(predictor): wire PR1 record-only compute (mtf/p_win/vol/funding) into build_prediction"
```

### Task 5.3: Extend payload_builders to pass through new fields

**Files:**
- Modify: `backend/app/db/payload_builders.py` — all 3 builders

- [ ] **Step 1: Write failing test (extend `test_payload_builders.py`)**

Append:

```python
def test_build_predictions_payload_includes_pr1_fields():
    pred = _make_prediction()
    pred.mtf_agreement = 4
    pred.mtf_dominant_tf = "1h"
    pred.mtf_directions_json = {"5m": 1, "1h": 1, "1w": -1}
    pred.p_win = 0.62
    pred.effective_score = 0.5
    pred.realized_vol_20d = 0.020
    pred.funding_directional_adj = -0.10
    payload = build_predictions_payload(pred, user_id=1, ghost_payload=None)
    assert payload["mtf_agreement"] == 4
    assert payload["mtf_dominant_tf"] == "1h"
    assert payload["mtf_directions_json"] == {"5m": 1, "1h": 1, "1w": -1}
    assert payload["p_win"] == 0.62
    assert payload["effective_score"] == 0.5
    assert payload["realized_vol_20d"] == 0.020
    assert payload["funding_directional_adj"] == -0.10


def test_build_predictions_payload_handles_none_pr1_fields():
    """Each new field defaults to None when not present on the pred object."""
    pred = _make_prediction()
    # Explicitly leave new attrs unset → SimpleNamespace returns AttributeError
    # so the builder should `getattr(..., None)` gracefully.
    payload = build_predictions_payload(pred, user_id=1, ghost_payload=None)
    assert payload["mtf_agreement"] is None
    assert payload["p_win"] is None
    assert payload["effective_score"] is None
```

- [ ] **Step 2: Run — verify fails**

Expected: KeyError or AttributeError.

- [ ] **Step 3: Extend `build_predictions_payload` (and similarly the other two)**

In `backend/app/db/payload_builders.py`, modify each of the 3 builders to append the 7 new keys at the bottom of their dict. Use `getattr(pred, "mtf_agreement", None)` since the input may be `SimpleNamespace` in tests:

```python
def build_predictions_payload(pred, *, user_id, ghost_payload=None):
    # ... existing code unchanged ...
    payload: dict[str, Any] = {
        # ... existing keys unchanged ...
        # PR1 record-only fields (None pass-through):
        "mtf_agreement": getattr(pred, "mtf_agreement", None),
        "mtf_dominant_tf": getattr(pred, "mtf_dominant_tf", None),
        "mtf_directions_json": getattr(pred, "mtf_directions_json", None),
        "p_win": getattr(pred, "p_win", None),
        "effective_score": getattr(pred, "effective_score", None),
        "realized_vol_20d": getattr(pred, "realized_vol_20d", None),
        "funding_directional_adj": getattr(pred, "funding_directional_adj", None),
    }
    if ghost_payload:
        payload.update(ghost_payload)
    return payload
```

For `build_shadow_trade_payload`: the new fields come from the `pos: ShadowPosition` object (which must carry them post-evaluator). Add `pos.mtf_*` etc. with `getattr(..., None)` fallbacks — the values are populated when the dispatcher signs off the signal. For PR1, the shadow worker doesn't compute these fields, so they fall through as None.

For `build_live_trade_payload`: the proposal object now needs to carry these fields. Add the parameters to the builder signature OR pull from `proposal` attrs with `getattr(..., None)`:

```python
def build_live_trade_payload(
    proposal, order, *, user_id, approved_via, mode_at_open,
    margin_usdt, leverage, opened_at, extra_reasoning=None,
):
    # ... existing code ...
    payload = {
        # ... existing keys ...
        "timeframe": getattr(proposal, "timeframe", "1h"),
        "mtf_agreement": getattr(proposal, "mtf_agreement", None),
        "mtf_dominant_tf": getattr(proposal, "mtf_dominant_tf", None),
        "mtf_directions_json": getattr(proposal, "mtf_directions_json", None),
        "p_win": getattr(proposal, "p_win", None),
        "effective_score": getattr(proposal, "effective_score", None),
        "realized_vol_20d": getattr(proposal, "realized_vol_20d", None),
        "funding_directional_adj": getattr(proposal, "funding_directional_adj", None),
    }
    return payload
```

- [ ] **Step 4: Run — verify pass**

Run: `cd backend && DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test python -m pytest tests/db/test_payload_builders.py -q --no-cov`
Expected: all tests pass (golden + bit-identity + pr1-fields).

- [ ] **Step 5: Commit**

```
git add backend/app/db/payload_builders.py backend/tests/db/test_payload_builders.py
git commit -m "feat(builders): pass through PR1 record-only fields (None default, getattr fallback)"
```

### Task 5.4: End-to-end integration test

**Files:**
- Create: `backend/tests/integration/test_pr1_e2e_record_only.py`

- [ ] **Step 1: Write the failing test**

```python
"""E2E: build a prediction, persist, read back. Verify new columns
are written as expected and DON'T break the audit chain."""

from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from app.db.audit import insert_with_chain
from app.db.payload_builders import build_predictions_payload


async def test_predictions_row_has_pr1_columns_and_chain_intact(db_session_with_migration):
    """Uses a real Postgres test session that has the migration applied.

    Asserts:
      1. INSERT with new fields succeeds
      2. row_hash is computed from whitelist only (omits new cols)
      3. Re-fetch returns the persisted values
    """
    s = db_session_with_migration
    from types import SimpleNamespace
    pred = SimpleNamespace(
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        final=SimpleNamespace(score=0.45, direction="LONG", confidence=0.62),
        inputs_hash="ih-e2e", cold_start=False,
        prediction_extras=None,
        layer_scores={},
        mtf_agreement=5, mtf_dominant_tf="1h",
        mtf_directions_json={"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": 1, "1w": 0},
        p_win=None,
        effective_score=0.45,
        realized_vol_20d=0.020,
        funding_directional_adj=-0.10,
    )
    payload = build_predictions_payload(pred, user_id=1, ghost_payload=None)
    row_hash = await insert_with_chain(s, "predictions", payload)
    await s.commit()

    row = (await s.execute(sa.text(
        "SELECT * FROM predictions WHERE inputs_hash = 'ih-e2e'"
    ))).first()
    assert row.mtf_agreement == 5
    assert row.mtf_dominant_tf == "1h"
    assert row.effective_score == pytest.approx(0.45)
    assert row.realized_vol_20d == pytest.approx(0.020)
    assert row.funding_directional_adj == pytest.approx(-0.10)
    assert row.p_win is None
    assert row.row_hash == row_hash
```

- [ ] **Step 2: Set up the `db_session_with_migration` fixture**

Add to `backend/tests/conftest.py` (or a closer conftest):

```python
@pytest.fixture
async def db_session_with_migration():
    """Postgres session with all migrations applied (incl. PR1 0020)."""
    # Use the test DATABASE_URL; alembic upgrade head must have run.
    from app.db.session import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        yield session
        await session.rollback()
```

- [ ] **Step 3: Run — verify passes**

Run: `cd backend && DATABASE_URL=<test-postgres-with-pr1-migration-applied> REDIS_URL=redis://x ENV=test python -m pytest tests/integration/test_pr1_e2e_record_only.py -q --no-cov`
Expected: 1 passed.

- [ ] **Step 4: Commit**

```
git add backend/tests/integration/test_pr1_e2e_record_only.py backend/tests/conftest.py
git commit -m "test(integration): PR1 e2e — persist record-only fields, audit chain intact"
```

---

## Phase 6 — Register both tasks in worker_registry + wire into lifespan (per Correction 2)

### Task 6.1: Register `mtf_cache_prewarm_task` + `mtf_cache_ttl_refresh_task` in worker_registry

**Files:**
- Modify: `backend/app/ops/worker_registry.py` (extend `WORKER_REGISTRY` tuple)
- Modify: `backend/tests/unit/test_worker_registry_consistency.py` if `WORKER_SOURCE_MODULES` map exists (extend it)

- [ ] **Step 1: Read the current registry to see the exact pattern**

Run: `grep -nA20 "WORKER_REGISTRY" backend/app/ops/worker_registry.py | head -40`
Confirm `WorkerSpec` dataclass shape (name, description, liveness_query, max_staleness_seconds, stateful, required_env, pending_heartbeat).

- [ ] **Step 2: Add 2 new entries to `WORKER_REGISTRY` tuple**

In `backend/app/ops/worker_registry.py`, append to the `WORKER_REGISTRY` tuple (before the closing parenthesis):

```python
    # PR1 — MTF cache subsystem (Correction 2: not orphaned).
    # Single-shot prewarm: completes after universe × 6 TFs cached or 60s
    # deadline. liveness_query=None tells the watchdog to skip staleness
    # checks (single-shot by design). pending_heartbeat=True keeps the
    # CI consistency check satisfied.
    WorkerSpec(
        name="mtf_cache_prewarm_task",
        description="Startup-once: prewarm MTF kline cache, universe × 6 TFs, 60s deadline",
        liveness_query=None,
        max_staleness_seconds=0,
        stateful=False,
        pending_heartbeat=True,
    ),
    # Long-running TTL-refresh loop. Records heartbeat on every iteration
    # (~30s cadence). Auto-restart safe (non-stateful — just a refresh
    # loop over an in-memory cache that can rebuild on demand).
    WorkerSpec(
        name="mtf_cache_ttl_refresh_task",
        description="30s loop: refresh MTF cache entries within 20% of TTL expiry",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=5 * 60,
        stateful=False,
    ),
```

- [ ] **Step 3: Update `WORKER_SOURCE_MODULES` in the consistency test**

Run: `grep -n "WORKER_SOURCE_MODULES" backend/tests/unit/test_worker_registry_consistency.py`
If the map exists, add entries:
```python
"mtf_cache_prewarm_task": "app.core.scoring.mtf_confluence",
"mtf_cache_ttl_refresh_task": "app.core.scoring.mtf_confluence",
```

- [ ] **Step 4: Run consistency test — verify pass**

Run: `cd backend && DATABASE_URL=<test-db> REDIS_URL=redis://x ENV=test python -m pytest tests/unit/test_worker_registry_consistency.py -v --no-cov`
Expected: PASS — both new entries have `log.info(...)` in their source module (`mtf_confluence.py`), and Step 5 will wire the spawn calls.

If `test_worker_registry_consistency.py` requires a `start_<name>` reference in `main.py`, Step 5 below adds those references; the consistency test passes after Step 5.

- [ ] **Step 5: Wire `start_*` calls into `app/main.py` lifespan**

Locate the worker-spawn region in `backend/app/main.py` lifespan (where other `start_*_task(...)` calls happen). Add:

```python
    # --- PR1 MTF cache subsystem (registered workers per Correction 2) ---
    from app.core.scoring.mtf_confluence import (
        start_mtf_cache_prewarm_task,
        start_mtf_cache_ttl_refresh_task,
    )
    mtf_prewarm_task = start_mtf_cache_prewarm_task(session_factory)
    mtf_refresh_task = start_mtf_cache_ttl_refresh_task(session_factory)
```

Replace `session_factory` with the actual reference already in main.py.

Ensure the task handles get added to whatever shutdown-cleanup list main.py uses (likely a list of tasks to cancel at lifespan teardown).

- [ ] **Step 6: Write a spawn-and-cancel unit test**

Create `backend/tests/ops/test_mtf_startup_spawn.py`:

```python
"""mtf prewarm + refresh-loop are spawnable and cancellable."""

import asyncio
from unittest.mock import MagicMock

import pytest

from app.core.scoring.mtf_confluence import (
    start_mtf_cache_prewarm_task,
    start_mtf_cache_ttl_refresh_task,
)


async def test_prewarm_task_completes_cleanly_on_empty_universe(monkeypatch):
    """Prewarm: empty universe → 0 entries cached, task completes."""
    from app.core.scoring import mtf_confluence as mod
    async def _empty_universe(session):
        return []
    monkeypatch.setattr("app.shadow.universe.load_current_universe", _empty_universe)
    session_factory_mock = MagicMock()
    session_factory_mock.return_value.__aenter__.return_value = MagicMock()
    session_factory_mock.return_value.__aexit__.return_value = None
    task = start_mtf_cache_prewarm_task(session_factory_mock)
    await asyncio.wait_for(task, timeout=5.0)
    assert task.done()


async def test_refresh_task_cancellable():
    """Long-running TTL-refresh task must cancel cleanly."""
    from unittest.mock import AsyncMock
    sf = MagicMock()
    sf.return_value.__aenter__.return_value = AsyncMock()
    sf.return_value.__aexit__.return_value = None
    # Patch record_heartbeat so the loop doesn't need a real DB
    from app.ops import heartbeat
    original = heartbeat.record_heartbeat
    async def _noop_heartbeat(*a, **k):
        return None
    heartbeat.record_heartbeat = _noop_heartbeat  # type: ignore[assignment]
    try:
        task = start_mtf_cache_ttl_refresh_task(sf)
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        heartbeat.record_heartbeat = original  # type: ignore[assignment]
```

- [ ] **Step 7: Run — verify pass**

Run: `cd backend && DATABASE_URL=<test-db> REDIS_URL=redis://x ENV=test python -m pytest tests/ops/test_mtf_startup_spawn.py tests/unit/test_worker_registry_consistency.py -q --no-cov`
Expected: all pass.

- [ ] **Step 8: Mypy + commit**

Run: `cd backend && python -m mypy app 2>&1 | tail -3`
Expected: clean.

```
git add backend/app/ops/worker_registry.py backend/app/main.py backend/tests/ops/test_mtf_startup_spawn.py backend/tests/unit/test_worker_registry_consistency.py
git commit -m "feat(workers): register mtf_cache_prewarm + ttl_refresh in worker_registry (Correction 2)"
```

---

## Phase 7 — Verification, latency gate, ARCHITECTURE.md, PR

### Task 7.1: Full mypy + test suite green check

**Files:** none

- [ ] **Step 1: Mypy**

Run: `cd backend && python -m mypy app 2>&1 | tail -3`
Expected: `Success: no issues found in N source files` where N >= 403 (4 new modules added).
If failures: fix them inline, then re-run.

- [ ] **Step 2: Unit tests**

Run: `cd backend && DATABASE_URL=<test-db> REDIS_URL=redis://x ENV=test python -m pytest tests/unit/ -q --no-cov 2>&1 | tail -5`
Expected: all green.

- [ ] **Step 3: Integration tests**

Run: `cd backend && DATABASE_URL=<test-db> REDIS_URL=redis://x ENV=test python -m pytest tests/integration/ -q --no-cov 2>&1 | tail -5`
Expected: all green.

- [ ] **Step 4: PR1-specific test groups**

Run: `cd backend && DATABASE_URL=<test-db> REDIS_URL=redis://x ENV=test python -m pytest tests/db/ tests/core/scoring/test_mtf_confluence.py tests/core/scoring/test_p_win_calibrator.py tests/core/scoring/test_vol_normalization.py tests/core/scoring/test_funding_directional.py tests/api/test_live_prediction_out_new_fields.py -v --no-cov 2>&1 | tail -30`
Expected: every test passes; count matches what was written across Phases 1-6.

### Task 7.2: Latency check gate (per Correction 3 — concrete, mechanically verifiable)

Operator bound: **Re-run latency comparison AFTER implementing Phases 1-6. PR1 merge gate: `(p50_recording - p50_baseline) ≤ 50ms` AND `(p99_recording - p99_baseline) ≤ 200ms`. Numbers go in PR description.**

**Files:**
- Create: `backend/scripts/bench_aggregator_latency.py` (proper benchmark, replaces throwaway `tmp_bench/`)
- Create: `backend/scripts/__init__.py` if `scripts/` isn't a package yet
- Modify: `.github/workflows/ci.yml` (add benchmark smoke step that captures JSON as artifact)

- [ ] **Step 1: Create `backend/scripts/bench_aggregator_latency.py`**

```python
"""PR1 aggregator-latency benchmark.

Runs N=500 score-computations on a fixed symbol (BTCUSDT) with fixed
synthetic bar fixtures. Two CLI modes:
  --mtf-disabled    baseline: aggregate() pure math only
  --mtf-recording   with PR1 record-only MTF compute (cache-hit path)

Output (stdout, one line JSON):
  {"p50_ms": float, "p95_ms": float, "p99_ms": float,
   "n_samples": int, "mode": str}

CI integration: runs as a smoke step in backend CI (60s timeout), JSON
captured as workflow artifact. Not a CI gate. PR1 merge gate is operator
review of the reported numbers vs thresholds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@h/d")
os.environ.setdefault("REDIS_URL", "redis://x")
os.environ.setdefault("ENV", "test")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret")
os.environ.setdefault("VAULT_KEY", "0" * 64)

from app.core.scoring.aggregator import aggregate
from app.core.scoring.mtf_confluence import (
    _KLINE_CACHE, _cache_set, _vote_for_tf,
    TIMEFRAMES, KLINE_LIMIT,
)
from app.core.scoring.types import Direction, LayerScore


N_SAMPLES: int = 500


def _make_layer_results() -> dict[int, LayerScore | None]:
    return {
        1: LayerScore(direction=Direction.LONG, strength=0.55, confidence=0.70),
        2: LayerScore(direction=Direction.LONG, strength=0.40, confidence=0.65),
        3: LayerScore(direction=Direction.SHORT, strength=0.30, confidence=0.55),
        4: LayerScore(direction=Direction.LONG, strength=0.45, confidence=0.60),
        5: LayerScore(direction=Direction.LONG, strength=0.50, confidence=0.65),
        6: LayerScore(direction=Direction.NEUTRAL, strength=0.20, confidence=0.45),
        7: None,
        8: None,
        9: LayerScore(direction=Direction.LONG, strength=0.35, confidence=0.50),
        10: None,
    }


def _make_synthetic_klines(n: int = KLINE_LIMIT) -> list[list]:
    """Fixed-seed klines so the benchmark is reproducible."""
    rng = np.random.default_rng(42)
    closes = 80000.0 * np.exp(np.cumsum(rng.normal(0, 0.005, n)))
    highs = closes * 1.002
    lows = closes * 0.998
    return [
        [i * 60_000, float(c * 0.999), float(h), float(l), float(c),
         100.0, i * 60_000 + 59999, 0, 0, 0, 0, 0]
        for i, (c, h, l) in enumerate(zip(closes, highs, lows))
    ]


def _prepopulate_cache_with_synthetic_klines() -> None:
    """Seed _KLINE_CACHE for BTCUSDT × all 6 TFs at t=now so every
    benchmark iteration takes the cache-hit branch (no network)."""
    klines = _make_synthetic_klines()
    for tf in TIMEFRAMES:
        _cache_set("BTCUSDT", tf, klines)


async def _bench_baseline(layer_results) -> list[float]:
    samples: list[float] = []
    for _ in range(N_SAMPLES):
        t0 = time.perf_counter_ns()
        aggregate(layer_results)
        samples.append((time.perf_counter_ns() - t0) / 1e6)
    return samples


async def _bench_recording(layer_results) -> list[float]:
    """Cache-hit path: 6 TFs already populated, MTF compute is pure CPU."""
    from app.core.scoring.mtf_confluence import compute_mtf_confluence
    samples: list[float] = []
    for _ in range(N_SAMPLES):
        t0 = time.perf_counter_ns()
        aggregate(layer_results)
        await compute_mtf_confluence("BTCUSDT", Direction.LONG)
        samples.append((time.perf_counter_ns() - t0) / 1e6)
    return samples


def _percentile(samples: list[float], p: float) -> float:
    s = sorted(samples)
    idx = int(len(s) * p)
    return s[min(idx, len(s) - 1)]


async def main() -> int:
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--mtf-disabled", action="store_true",
                   help="Baseline: aggregate() only")
    g.add_argument("--mtf-recording", action="store_true",
                   help="With PR1 record-only MTF compute (cache-hit path)")
    args = parser.parse_args()

    layer_results = _make_layer_results()
    _KLINE_CACHE.clear()

    if args.mtf_recording:
        _prepopulate_cache_with_synthetic_klines()
        # Warmup: 50 iterations to settle JIT-ish numpy compilation
        for _ in range(50):
            aggregate(layer_results)
            from app.core.scoring.mtf_confluence import compute_mtf_confluence
            await compute_mtf_confluence("BTCUSDT", Direction.LONG)
        samples = await _bench_recording(layer_results)
        mode = "mtf-recording"
    else:
        for _ in range(50):
            aggregate(layer_results)
        samples = await _bench_baseline(layer_results)
        mode = "mtf-disabled"

    result = {
        "p50_ms": round(_percentile(samples, 0.50), 4),
        "p95_ms": round(_percentile(samples, 0.95), 4),
        "p99_ms": round(_percentile(samples, 0.99), 4),
        "n_samples": len(samples),
        "mode": mode,
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: Run both modes locally + record numbers**

Run from `a:/v5_Trade_bot`:

```
cd backend && python scripts/bench_aggregator_latency.py --mtf-disabled
cd backend && python scripts/bench_aggregator_latency.py --mtf-recording
```

Record both JSON lines. Example expected output (numbers will vary by host):
```
{"p50_ms": 0.009, "p95_ms": 0.012, "p99_ms": 0.016, "n_samples": 500, "mode": "mtf-disabled"}
{"p50_ms": 7.71, "p95_ms": 8.53, "p99_ms": 11.39, "n_samples": 500, "mode": "mtf-recording"}
```

Compute deltas:
- p50 added = `p50_recording - p50_baseline`
- p99 added = `p99_recording - p99_baseline`

- [ ] **Step 3: Compare against operator's PR-merge gates**

| Metric | Threshold | Actual delta | Verdict |
|---|---|---|---|
| p50 added | ≤ 50ms | ___ ms | ✅ / ❌ |
| p99 added | ≤ 200ms | ___ ms | ✅ / ❌ |

If BOTH ✅: paste both JSON lines + deltas into the PR description (Task 7.4 Step 2). Proceed to Task 7.3.
If EITHER ❌: STOP, report to operator. Likely fix: investigate why cache-hit regressed vs pre-impl bench (~7.7ms p50 / ~11.4ms p99 from V-7).

- [ ] **Step 4: Add CI smoke step to capture JSON as artifact**

Locate the backend CI job in `.github/workflows/ci.yml` (the job named `backend` per the existing CI structure). Add a step AFTER the pytest step:

```yaml
      - name: Benchmark — aggregator latency (smoke, non-gating)
        if: ${{ always() && success() }}
        timeout-minutes: 1
        working-directory: backend
        env:
          DATABASE_URL: postgresql+asyncpg://x:x@h/d
          REDIS_URL: redis://x
          ENV: test
          JWT_SECRET: test_jwt_secret
          VAULT_KEY: "0000000000000000000000000000000000000000000000000000000000000000"
        run: |
          python scripts/bench_aggregator_latency.py --mtf-disabled > /tmp/bench-baseline.json
          python scripts/bench_aggregator_latency.py --mtf-recording > /tmp/bench-recording.json
          echo "=== baseline ==="
          cat /tmp/bench-baseline.json
          echo "=== recording ==="
          cat /tmp/bench-recording.json

      - name: Upload benchmark JSON artifact
        if: ${{ always() && success() }}
        uses: actions/upload-artifact@v4
        with:
          name: pr1-latency-bench
          path: /tmp/bench-*.json
          retention-days: 30
```

- [ ] **Step 5: Verify CI smoke step runs (local sanity)**

Push the branch to a remote to trigger CI, OR run the commands locally to verify nothing breaks. Confirm artifact appears in the workflow run page.

- [ ] **Step 6: Remove the throwaway `tmp_bench/` directory**

If `tmp_bench/bench_pr1_latency.py` exists locally:
```
rm -rf tmp_bench/
```
It was never committed (`?? tmp_bench/` in `git status`); no `git rm` needed.

- [ ] **Step 7: Commit**

```
git add backend/scripts/bench_aggregator_latency.py .github/workflows/ci.yml
git commit -m "test(bench): scripts/bench_aggregator_latency.py + CI smoke (per Correction 3)"
```

### Task 7.3: Update `docs/ARCHITECTURE.md`

**Files:**
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Add new section "Math accuracy upgrades (PR1)"**

Append a new section after `## 10. Binance filters module`, before `## 11. Self-healing supervisor`:

```markdown
## 10b. Math accuracy upgrades (PR1)

**Files**: `app/core/scoring/mtf_confluence.py`, `p_win_calibrator.py`,
`vol_normalization.py`, `funding_directional.py` — added 2026-05-16.

PR1 attaches 4 new record-only metrics to every `LivePredictionOut`.
None feed back into `final_score` — gating lands in PR2+.

| Metric | Source | Recorded as |
|---|---|---|
| MTF agreement (5m/15m/1h/4h/1d/1w EMA+ADX vote, asyncio.gather) | SPOT REST klines, cached in-memory | `mtf_agreement`, `mtf_dominant_tf`, `mtf_directions_json` |
| p_win (calibrated win probability) | Isotonic regression on shadow_trades; None until PR5 worker fits | `p_win` |
| realized_vol_20d (20-day stdev of daily log-returns) | Resample 1h bars to daily | `realized_vol_20d` |
| effective_score (vol-normalized signed score) | `final_score × 0.02 / max(vol, 0.01)` | `effective_score` |
| funding_directional_adj (signed ±0.10 boost) | Latest `intermarket_snapshots` row | `funding_directional_adj` |

**Cache**: module-level dict in `mtf_confluence.py`. TTLs 5m=60s,
15m=60s, 1h=300s, 4h/1d/1w=3600s. Pre-warmed at startup (60s deadline,
fail-open). TTL-refresh background task every 30s refreshes entries
within 20% of expiry. Neither task is in `worker_registry.py` (PR1
spec: "(none new)"); both spawn from `app/main.py` lifespan.
```

- [ ] **Step 2: Update Cadence map**

Append rows to the cadence map at section "Cadence map":

```
30s   ┃ mtf_cache_refresh           ┃ refresh cache entries near expiry (PR1)
EVENT ┃ mtf_prewarm (startup-once)  ┃ universe × 6 TFs, 60s deadline (PR1)
```

- [ ] **Step 3: Update Engine accountability matrix**

Add rows:

```
| **mtf_confluence** | SPOT klines × 6 TFs | agreement+dominant+directions | (in-memory cache) | per-TF None on failure |
| **p_win_calibrator** | shadow_trades | isotonic model (.pkl) | filesystem | None when no model |
| **vol_normalization** | in-memory bars | realized_vol_20d, effective_score | (computed) | None when insufficient history |
| **funding_directional** | intermarket_snapshots | signed adj | (computed) | None when no snapshot |
```

- [ ] **Step 4: Commit**

```
git add docs/ARCHITECTURE.md
git commit -m "docs(architecture): PR1 math accuracy upgrades section + cadence + matrix"
```

### Task 7.4: Open PR to dev

**Files:** none

- [ ] **Step 1: Push the branch**

```
git push -u origin feat/pr1-record-only-foundation
```

- [ ] **Step 2: Open PR with full body**

```bash
gh pr create --base dev --head feat/pr1-record-only-foundation \
  --title "feat(pr1): record-only foundation — MTF / p_win / vol-norm / funding-adj + audit whitelist + payload builders" \
  --body "$(cat <<'EOF'
## Summary

PR1 of the 9-PR upgrade plan. Lands the analytics foundation:

1. **Audit whitelist refactor** — `HASH_PAYLOAD_COLUMNS` constant in `audit.py`. Adding new columns to chained tables is now a no-op for the hash chain unless explicitly whitelisted. Fail-secure (whitelist) vs fail-open (excluded-set).
2. **`payload_builders.py` consolidation** — 3 shared functions (\`build_predictions_payload\`, \`build_shadow_trade_payload\`, \`build_live_trade_payload\`) replace the 4 inline dicts at \`live_prediction.py\`, \`shadow/persistence.py\`, \`dispatcher.py\`, \`telegram_polling.py\`. Bit-identical to pre-refactor.
3. **4 new scoring modules** (record-only, no gating):
   - \`mtf_confluence.py\` — 6-TF EMA+ADX vote, asyncio.gather, in-memory cache, pre-warm + TTL-refresh
   - \`p_win_calibrator.py\` — isotonic per direction; predicts None until PR5 worker fits
   - \`vol_normalization.py\` — realized_vol_20d + effective_score
   - \`funding_directional.py\` — signed ±0.10 adj from intermarket_snapshots
4. **Alembic migration** — 7 new nullable cols × 3 tables + \`timeframe NOT NULL DEFAULT '1h'\` (3-step backfill).
5. **\`LivePredictionOut\`** gains 5 optional fields. \`extra='ignore'\` preserved.

**Zero behavior change.** All new fields recorded only; gating lands in PR2+.

### Latency check gate result
- Baseline p50 / p99: \_\_\_ ms / \_\_\_ ms
- With-MTF p50 / p99: \_\_\_ ms / \_\_\_ ms
- Added p50 / p99: \_\_\_ ms / \_\_\_ ms (thresholds: ≤50ms / ≤200ms)

### One-line rollback
\`git revert <merge-commit>\` then \`alembic downgrade -1\`.

## Test plan
- [ ] Backend CI green (mypy + pytest)
- [ ] Frontend CI green (no schema impact expected)
- [ ] docker-compose-smoke CI green
- [ ] Manual prod-data replay (operator runs): \`gh workflow run ops-debug.yml -f probe=watchdog-audit\` post-deploy; spot-check last 10 \`predictions\` row_hashes match recomputed (operator action)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Wait for CI green**

Run: `gh pr checks <PR_NUMBER> --watch`
Expected: all 3 checks (backend, frontend, docker-compose-smoke) green within ~7 min.

- [ ] **Step 4: Report PR number to operator + STOP**

The operator must:
1. Review the PR
2. Approve merge to dev (CI green is necessary but not sufficient)
3. After dev merge + staging soak, separately authorize dev → main with a free-text "yes" / "ship it" per the `dev-prod-branch-workflow` memory.

DO NOT auto-merge to dev. DO NOT auto-promote to main. Both gates require explicit operator action in the current turn.

---

## Self-review checklist (for the implementing engineer)

Before opening the PR, verify each:

- [ ] Audit whitelist (Task 1.2): every key your call sites pass to `insert_with_chain` is in `HASH_PAYLOAD_COLUMNS[table]`. Run the bit-identity tests one more time.
- [ ] Payload builders (Tasks 2.2-2.5): no inline payload dicts remain in production code. `grep -rn "insert_with_chain" backend/app/ | grep -v "build_.*_payload"` returns nothing.
- [ ] Migration (Task 3): upgrade-then-downgrade-then-upgrade leaves the schema in the expected state. Audit whitelist consistency test passes against the migrated schema.
- [ ] MTF (Task 4.4): `asyncio.gather(return_exceptions=True)` is the actual call (not `asyncio.gather` without that flag). One TF failure does not break the result.
- [ ] LivePredictionOut (Task 5.1): no `model_config = ConfigDict(extra='forbid')` was added. Round-trip with unknown field doesn't raise.
- [ ] Lifespan (Task 6.1): prewarm uses `asyncio.create_task` (background, not awaited). 60s deadline is enforced inside `prewarm_cache`.
- [ ] Latency gate (Task 7.2): both p50 and p99 added cost are within thresholds. If not, stop — do not open PR.
- [ ] No `--no-verify` git commits anywhere.
- [ ] No direct pushes to `main`. All commits on `feat/pr1-record-only-foundation`.
- [ ] Untracked files (`HANDOVER.md`, `populate_universe.py`, `secrets.enc`, `tmp_screens/`, `tmp_screens_buttons/`, `tmp_smc/`) NOT in any commit.

---

## Out-of-scope reminders (do NOT add)

- ❌ Dispatcher MTF gate (PR2)
- ❌ SHORT-specific safety flags (PR2)
- ❌ p_win_recalibrate_task worker (PR5)
- ❌ Redis or any external cache
- ❌ Cache stats / Prometheus
- ❌ Adaptive TTL
- ❌ Async-rewriting other aggregator parts
- ❌ Modifying L1-L10 layer internals
- ❌ Enabling any feature for live/telegram-approve modes

