# PR3 — Multi-resolution shadow (15m lane + prewarm + narrow universe) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 15m lane to the shadow worker alongside 1h. Reuse PR1's MTF kline cache for prewarm. Per-TF cooldowns + open positions. Heartbeat wiring + watchdog window tightening (FU-1 partial close). Frontend deep-link fix. Recording-mixed-TF / promoting-1h-only by default.

**Architecture:** Single `ShadowWorker` instance owns N `MultiStreamReader`s (one per TF), keys `bars` by `(symbol, tf)`, routes per-candle through a TF-aware `_handle_candle`. Persistence (`shadow_cooldowns`, `shadow_open_positions`) and exit-timeout (`TIMEOUT_BARS_PER_TF`) become TF-keyed. Alembic migration extends PKs / UNIQUE constraints to include `timeframe`, with the 4-step add/backfill/NOT-NULL/DEFAULT pattern that PR1 used for `live_trades.timeframe`. Heartbeat fires once per `_handle_candle` invocation; watchdog window drops from 2h to 30min.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 async / Pydantic v2 BaseSettings / Alembic / pytest + pytest-asyncio / React + TanStack Query (frontend single-file tweak).

**Source spec:** [`docs/superpowers/specs/2026-05-17-pr3-multi-resolution-shadow-design.md`](../specs/2026-05-17-pr3-multi-resolution-shadow-design.md)

**Branch:** `feat/pr3-impl-multi-resolution-shadow` off `dev` (which now contains FU-1 + PR2). NEVER push to `main`.

**Behavior change classification:** YES — shadow-only behavior flip (`SHADOW_TIMEFRAMES=["1h","15m"]` default) that 4× signal rate. Live trading paths unchanged. Per operator's 2026-05-17 policy override: 24h+criteria soak (criteria audited per-PR), NOT 3-day default.

---

## File Structure (locked in via design)

### NEW files

| Path | Responsibility |
|---|---|
| `backend/alembic/versions/2026_05_18_0021_pr3_shadow_per_tf.py` | Add `timeframe` to `shadow_cooldowns` (+ extend PK) and `shadow_open_positions` (+ replace UNIQUE). 4-step add/backfill/NOT-NULL/DEFAULT pattern. |
| `backend/tests/db/test_pr3_migration.py` | Postgres-only schema introspection: PK + UNIQUE shape post-migration. Mirrors `test_pr1_migration.py`. |
| `backend/tests/db/test_pr3_migration_downgrade.py` | FU-10 anticipation: round-trip upgrade → downgrade → upgrade; assert clean restore. |
| `backend/tests/shadow/test_worker_multi_tf.py` | 2 TFs → 2 readers, per-(sym, tf) bars, TF-aware `_handle_candle` routing. |
| `backend/tests/shadow/test_universe_narrow.py` | Empty → full; non-empty intersects; no-overlap → WARN + fallback. |
| `backend/tests/shadow/test_cooldown_per_tf.py` | 1h cooldown does not block 15m on same symbol; same-TF blocks same-TF. |
| `backend/tests/shadow/test_open_positions_per_tf.py` | 1h open does not block 15m open; same-TF blocks. |
| `backend/tests/shadow/test_exit_monitor_per_tf.py` | 15m position expires at 96 bars; 1h at 24 bars. |
| `backend/tests/integration/test_pr3_e2e_dual_lane.py` | WS frames on both TFs → 1 row per TF on entry; cross-TF same-symbol works. |
| `backend/tests/integration/test_pr3_promotion_gate_breakdown.py` | `/promotion-gate` includes `per_timeframe`; combined excludes 15m when `SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False`. |
| `backend/tests/ops/test_shadow_worker_heartbeat.py` | `record_heartbeat("shadow_worker")` fires in `_handle_candle`; max_staleness 30 min enforced. |
| `backend/scripts/bench_shadow_handle_candle.py` | V-7 microbench: 1h-only baseline vs 1h+15m. `delta_p50 ≤ 50ms`, `delta_p99 ≤ 200ms`. |

### MODIFIED files

| Path | Reason |
|---|---|
| `backend/app/config.py` | Add 5 PR3 settings: `SHADOW_TIMEFRAMES`, `SHADOW_PREWARM_BARS`, `SHADOW_COOLDOWN_HOURS` (dict), `SHADOW_NARROW_UNIVERSE`, `SHADOW_15M_ELIGIBLE_FOR_PROMOTION`. |
| `backend/app/shadow/worker.py` | TF-aware refactor: per-TF readers + per-(sym, tf) bars + TF-arg on `_handle_candle`; setup() reuses `_KLINE_CACHE`; heartbeat call. |
| `backend/app/shadow/engine.py` | `ShadowPosition.timeframe` field. `PositionGate.is_blocked` consults per-(sym, tf) state. No threshold or score changes (§6.6 hard bound). |
| `backend/app/shadow/exit_monitor.py` | Replace `TIMEOUT_BARS=24` with `TIMEOUT_BARS_PER_TF={"1h":24, "15m":96}`; `check_exit` reads per-TF. |
| `backend/app/shadow/persistence.py` | `set_cooldown` takes `timeframe` arg; `get_cooldowns` returns `dict[(str,str), datetime]`; `persist_closed_trade` threads `timeframe`. |
| `backend/app/shadow/universe.py` | New `load_shadow_universe(session, narrow)` helper applies narrow filter with fallback. |
| `backend/app/db/payload_builders.py` | `build_shadow_trade_payload` accepts `timeframe` kwarg (default `"1h"` for PR1 compat); emits the column. |
| `backend/app/ops/worker_registry.py` | `shadow_worker` entry: `max_staleness_seconds=30*60`, `pending_heartbeat=False` (B6 + B7 together — §6.3). |
| `backend/app/api/routes/bot_status.py` | `/promotion-gate` response adds `per_timeframe` block; combined block filters per `SHADOW_15M_ELIGIBLE_FOR_PROMOTION`. |
| `backend/app/main.py` | Spawn `start_shadow_worker()` with `timeframes=settings.SHADOW_TIMEFRAMES` (was hardcoded). |
| `backend/tests/db/test_payload_builders.py` | New + updated cases: `build_shadow_trade_payload` with `timeframe='15m'`; existing `timeframe='1h'` cases stay green. |
| `frontend/src/tabs/BotStatus/OpenPositions.tsx` | 3-line fix at lines 36-38: read `pos.timeframe` with `?? "1h"` fallback. |
| `backend/docs/KNOWN_ISSUES.md` | Mark FU-1's `shadow_worker` row CLOSED; note FU-10 PR3-local anticipation done. |

### DELETED files

None.

---

## Phase 0 — Branch + baseline

### Task 0: Create feature branch off dev (after PR3 plan merges)

**Files:** none

- [ ] **Step 1: Verify dev tip contains PR2 (`fb2621c`) and the PR3 plan**

```
gh api repos/naga1412/v5_Trade_bot/branches/dev --jq '.commit.sha'
```
Expected: dev HEAD references a squash that includes PR2 + this plan. If not present, STOP — implementation can't start until plan merges.

- [ ] **Step 2: Fetch + branch**

```
git fetch origin dev
git checkout -b feat/pr3-impl-multi-resolution-shadow origin/dev
```

- [ ] **Step 3: Confirm ruff + mypy + bench baseline**

```
cd backend && python -m ruff check app/ tests/ scripts/ && python -m mypy app 2>&1 | tail -1
python scripts/bench_aggregator_latency.py --mtf-gate-disabled --n 500   # baseline OK
```
Expected: clean lint/typecheck; gate bench p50 sub-1ms (PR2 baseline).

- [ ] **Step 4: Confirm PR1 + PR2 fixtures load cleanly**

```
python -c "from tests.integration.test_pr1_full_pipeline import _make_hourly_bars; print(_make_hourly_bars(10).shape)"
python -c "from tests.trading.execution.test_dispatcher_pr2_gate_uniformity import _proposal; print(_proposal(direction='LONG').direction)"
```

---

## Phase 1 — Alembic migration (LANDS FIRST — schema must exist before code)

Rationale: code in later phases reads/writes the new `timeframe` column; the migration must be on every dev DB before that code lands.

### Task 1.1: Write the failing migration test (introspection)

**Files:**
- Test: `backend/tests/db/test_pr3_migration.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
"""PR3 alembic migration introspection — Postgres only.

Mirrors `test_pr1_migration.py` pattern. Skipped when DATABASE_URL
is sqlite (the migration uses Postgres-specific ALTER TABLE).
"""
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine


_DSN = os.environ.get("DATABASE_URL", "")


@pytest.mark.skipif(
    not _DSN.startswith("postgresql"),
    reason="Postgres DATABASE_URL not set — migration tests are CI-only.",
)
@pytest.mark.asyncio
async def test_shadow_cooldowns_pk_extended_with_timeframe() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        row = (await conn.execute(sa.text(
            "SELECT a.attname "
            "FROM pg_index i JOIN pg_attribute a "
            "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'shadow_cooldowns'::regclass AND i.indisprimary "
            "ORDER BY array_position(i.indkey, a.attnum);"
        ))).all()
    cols = [r.attname for r in row]
    assert cols == ["user_id", "symbol", "timeframe"]
    await engine.dispose()


@pytest.mark.skipif(
    not _DSN.startswith("postgresql"),
    reason="Postgres DATABASE_URL not set.",
)
@pytest.mark.asyncio
async def test_shadow_open_positions_unique_is_symbol_timeframe() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT conname, pg_get_constraintdef(oid) AS def "
            "FROM pg_constraint WHERE conrelid = 'shadow_open_positions'::regclass "
            "AND contype = 'u';"
        ))).all()
    assert any(
        "(symbol, timeframe)" in r.def_ for r in rows
    ), f"Expected (symbol, timeframe) UNIQUE; got {rows}"
    await engine.dispose()


@pytest.mark.skipif(
    not _DSN.startswith("postgresql"),
    reason="Postgres DATABASE_URL not set.",
)
@pytest.mark.asyncio
async def test_shadow_cooldowns_timeframe_default_is_1h() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        row = (await conn.execute(sa.text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name='shadow_cooldowns' AND column_name='timeframe';"
        ))).first()
    assert row is not None
    assert "1h" in (row.column_default or ""), (
        f"Expected DEFAULT '1h'; got {row.column_default!r}"
    )
    await engine.dispose()
```

- [ ] **Step 2: Run + verify fail**

```
DATABASE_URL=postgresql+asyncpg://postgres:testpw@localhost:5432/trading_radar \
  python -m pytest tests/db/test_pr3_migration.py -v
```
Expected: failure or skip-when-no-Postgres. In CI with Postgres: tests fail because columns don't exist yet.

### Task 1.2: Write the alembic migration

**Files:**
- Migration: `backend/alembic/versions/2026_05_18_0021_pr3_shadow_per_tf.py` (NEW)

- [ ] **Step 1: Author the migration**

```python
"""PR3: shadow_cooldowns + shadow_open_positions become TF-aware.

Revision ID: 0021_pr3_shadow_per_tf
Revises: 0020_pr1_record_only_columns
Create Date: 2026-05-18

4-step pattern per PR1's track record (add nullable → backfill → NOT NULL
→ DEFAULT) so the migration is fully online for any concurrent writers.
Raw SQL because alembic's autogenerate doesn't handle PK/UNIQUE rewrites.
"""
from alembic import op

revision = "0021_pr3_shadow_per_tf"
down_revision = "0020_pr1_record_only_columns"  # adjust to actual prior head
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- shadow_cooldowns: extend PK to include timeframe -----------------
    op.execute("ALTER TABLE shadow_cooldowns ADD COLUMN timeframe VARCHAR(8) NULL;")
    op.execute("UPDATE shadow_cooldowns SET timeframe = '1h' WHERE timeframe IS NULL;")
    op.execute("ALTER TABLE shadow_cooldowns ALTER COLUMN timeframe SET NOT NULL;")
    op.execute("ALTER TABLE shadow_cooldowns ALTER COLUMN timeframe SET DEFAULT '1h';")
    op.execute("ALTER TABLE shadow_cooldowns DROP CONSTRAINT shadow_cooldowns_pkey;")
    op.execute(
        "ALTER TABLE shadow_cooldowns "
        "ADD CONSTRAINT shadow_cooldowns_pkey PRIMARY KEY (user_id, symbol, timeframe);"
    )
    # --- shadow_open_positions: replace symbol UNIQUE with (symbol, tf) ---
    op.execute("ALTER TABLE shadow_open_positions ADD COLUMN timeframe VARCHAR(8) NULL;")
    op.execute("UPDATE shadow_open_positions SET timeframe = '1h' WHERE timeframe IS NULL;")
    op.execute("ALTER TABLE shadow_open_positions ALTER COLUMN timeframe SET NOT NULL;")
    op.execute("ALTER TABLE shadow_open_positions ALTER COLUMN timeframe SET DEFAULT '1h';")
    op.execute(
        "ALTER TABLE shadow_open_positions "
        "DROP CONSTRAINT shadow_open_positions_symbol_key;"
    )
    op.execute(
        "ALTER TABLE shadow_open_positions "
        "ADD CONSTRAINT shadow_open_positions_symbol_tf_key UNIQUE (symbol, timeframe);"
    )


def downgrade() -> None:
    # Reverse order. Drops the timeframe column entirely; any 15m rows are lost.
    op.execute(
        "ALTER TABLE shadow_open_positions "
        "DROP CONSTRAINT shadow_open_positions_symbol_tf_key;"
    )
    op.execute(
        "ALTER TABLE shadow_open_positions "
        "ADD CONSTRAINT shadow_open_positions_symbol_key UNIQUE (symbol);"
    )
    op.execute("ALTER TABLE shadow_open_positions DROP COLUMN timeframe;")

    op.execute("ALTER TABLE shadow_cooldowns DROP CONSTRAINT shadow_cooldowns_pkey;")
    op.execute(
        "ALTER TABLE shadow_cooldowns "
        "ADD CONSTRAINT shadow_cooldowns_pkey PRIMARY KEY (user_id, symbol);"
    )
    op.execute("ALTER TABLE shadow_cooldowns DROP COLUMN timeframe;")
```

- [ ] **Step 2: Verify the prior alembic head**

```
cd backend && python -m alembic heads
```
If the prior head is NOT `0020_pr1_record_only_columns`, adjust `down_revision` to match.

- [ ] **Step 3: Apply migration to test Postgres**

```
DATABASE_URL=postgresql+asyncpg://postgres:testpw@localhost:5432/trading_radar \
  python -m alembic upgrade head
```

- [ ] **Step 4: Re-run the introspection test — green**

Expected: 3 tests in `test_pr3_migration.py` pass.

- [ ] **Step 5: Commit Phase 1**

```
git add backend/alembic/versions/2026_05_18_0021_pr3_shadow_per_tf.py backend/tests/db/test_pr3_migration.py
git commit -m "feat(pr3): alembic — shadow_cooldowns + shadow_open_positions TF-aware (Phase 1)"
```

### Task 1.3: FU-10 anticipation — downgrade round-trip test

**Files:**
- Test: `backend/tests/db/test_pr3_migration_downgrade.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
"""PR3 FU-10 anticipation: upgrade → downgrade → upgrade round-trip.

PR3's migration is non-trivial (PK extensions, raw SQL constraint changes).
FU-10 (untested downgrades) hasn't shipped yet, but per spec §6.4 PR3
exercises the round-trip locally. Postgres only.
"""
from __future__ import annotations

import os
import subprocess

import pytest


_DSN = os.environ.get("DATABASE_URL", "")
_REV = "0021_pr3_shadow_per_tf"
_PRIOR = "0020_pr1_record_only_columns"


@pytest.mark.skipif(
    not _DSN.startswith("postgresql"),
    reason="Postgres DATABASE_URL not set.",
)
def test_pr3_migration_round_trip() -> None:
    """Upgrade to PR3 → downgrade to prior → upgrade again. Assert no errors."""
    env = {**os.environ, "DATABASE_URL": _DSN}
    for cmd in [
        ["python", "-m", "alembic", "upgrade", _REV],
        ["python", "-m", "alembic", "downgrade", _PRIOR],
        ["python", "-m", "alembic", "upgrade", _REV],
    ]:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd="backend")
        assert r.returncode == 0, f"{cmd!r} failed: stderr={r.stderr}"
```

- [ ] **Step 2: Run + verify**

```
DATABASE_URL=postgresql+asyncpg://postgres:testpw@localhost:5432/trading_radar \
  python -m pytest tests/db/test_pr3_migration_downgrade.py -v
```
Expected: pass. If downgrade leaves orphan constraints, fix the `downgrade()` body before continuing.

- [ ] **Step 3: Commit**

```
git add backend/tests/db/test_pr3_migration_downgrade.py
git commit -m "test(pr3): alembic downgrade round-trip (FU-10 anticipation, Phase 1)"
```

---

## Phase 2 — Settings + `ShadowPosition.timeframe`

Rationale: the rest of the worker reads from settings + carries `timeframe` on every position object; ship these early so later phases can rely on them.

### Task 2.1: Failing test for new settings defaults

**Files:**
- Test: `backend/tests/unit/test_pr3_settings_defaults.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
"""PR3 settings — §6.1 hard bounds."""
from __future__ import annotations

from app.config import Settings


def _s(**kw):
    return Settings(
        database_url="postgresql://x", redis_url="redis://x", **kw,
    )


def test_shadow_timeframes_default_is_1h_and_15m() -> None:
    assert _s().SHADOW_TIMEFRAMES == ["1h", "15m"]


def test_shadow_prewarm_bars_default_200() -> None:
    assert _s().SHADOW_PREWARM_BARS == 200


def test_shadow_cooldown_hours_default_dict() -> None:
    s = _s()
    assert s.SHADOW_COOLDOWN_HOURS == {"1h": 0.5, "15m": 0.5}


def test_shadow_narrow_universe_default_empty() -> None:
    assert _s().SHADOW_NARROW_UNIVERSE == []


def test_shadow_15m_eligible_default_false() -> None:
    assert _s().SHADOW_15M_ELIGIBLE_FOR_PROMOTION is False


def test_env_var_override_shadow_timeframes(monkeypatch) -> None:
    monkeypatch.setenv("SHADOW_TIMEFRAMES", '["1h"]')  # rollback path
    assert _s().SHADOW_TIMEFRAMES == ["1h"]


def test_env_var_override_narrow_universe(monkeypatch) -> None:
    monkeypatch.setenv("SHADOW_NARROW_UNIVERSE", '["BTCUSDT", "ETHUSDT"]')
    assert _s().SHADOW_NARROW_UNIVERSE == ["BTCUSDT", "ETHUSDT"]
```

- [ ] **Step 2: Run + verify red**

### Task 2.2: Add the 5 settings

**Files:**
- Modify: `backend/app/config.py`

- [ ] **Step 1: Add the fields to `Settings(BaseSettings)`**

```python
    # --- PR3: Multi-resolution shadow ------------------------------------
    # SHADOW_TIMEFRAMES default ["1h", "15m"] — the one explicit behavior
    # flip from PR2's effective ["1h"]. Rollback: set to ["1h"] in env.
    SHADOW_TIMEFRAMES: list[str] = ["1h", "15m"]
    SHADOW_PREWARM_BARS: int = 200
    # Per-TF cooldown in hours. Both default 0.5h (30 min) — matches the
    # pre-PR3 COOLDOWN_MINUTES=30 module constant. Dict shape future-proofs
    # asymmetric values without API churn.
    SHADOW_COOLDOWN_HOURS: dict[str, float] = {"1h": 0.5, "15m": 0.5}
    # Non-empty list = intersect with top-30 universe. Empty = use full.
    SHADOW_NARROW_UNIVERSE: list[str] = []
    # Excludes 15m from the promotion-gate combined aggregate when False.
    # Records 15m, just doesn't gamble promotion on it until win-rate proven.
    SHADOW_15M_ELIGIBLE_FOR_PROMOTION: bool = False
```

- [ ] **Step 2: Test green + ruff + mypy**

- [ ] **Step 3: Commit**

```
git commit -m "feat(pr3): settings — 5 shadow-multi-tf fields (Phase 2.1)"
```

### Task 2.3: `ShadowPosition.timeframe` field

**Files:**
- Modify: `backend/app/shadow/engine.py`
- Test: `backend/tests/shadow/test_engine_position_tf_field.py` (NEW)

- [ ] **Step 1: Failing test**

```python
"""ShadowPosition gains timeframe field (default '1h' for PR1 compat)."""
from app.shadow.engine import ShadowPosition, Direction
from datetime import datetime, timezone


def test_position_timeframe_default() -> None:
    p = ShadowPosition(
        symbol="BTCUSDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=98.0, take_profit=104.0,
        position_size_usdt=10.0, entry_score=0.4, entry_confidence=0.6,
        entry_atr=1.5, layer_scores={},
        bars_held=0, opened_at=datetime.now(timezone.utc),
        last_check_at=datetime.now(timezone.utc),
        signal_id="abc",
    )
    assert p.timeframe == "1h"


def test_position_timeframe_explicit() -> None:
    p = ShadowPosition(
        symbol="BTCUSDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=98.0, take_profit=104.0,
        position_size_usdt=10.0, entry_score=0.4, entry_confidence=0.6,
        entry_atr=1.5, layer_scores={},
        bars_held=0, opened_at=datetime.now(timezone.utc),
        last_check_at=datetime.now(timezone.utc),
        signal_id="abc", timeframe="15m",
    )
    assert p.timeframe == "15m"
```

- [ ] **Step 2: Add `timeframe: str = "1h"` to the dataclass**

- [ ] **Step 3: Verify nothing in existing tests breaks (default preserves PR1 behavior)**

```
cd backend && python -m pytest tests/shadow/ -v --no-cov
```

- [ ] **Step 4: Commit**

```
git commit -m "feat(pr3): ShadowPosition.timeframe field (default '1h', Phase 2.2)"
```

---

## Phase 3 — Persistence threading (`set_cooldown`, `get_cooldowns`, `persist_closed_trade`, payload builder)

Rationale: code in worker.py (Phase 4) reads/writes through these functions; thread the TF through here first.

### Task 3.1: Failing tests for persistence per-TF semantics

**Files:**
- Test: `backend/tests/shadow/test_persistence_per_tf.py` (NEW)

- [ ] **Step 1: Tests cover**

- `set_cooldown(user_id=1, symbol="BTC", timeframe="1h", until=t1)` and a separate `(timeframe="15m", until=t2)` produce 2 rows.
- `get_cooldowns(user_id=1)` returns `{("BTC", "1h"): t1, ("BTC", "15m"): t2}`.
- `persist_closed_trade(pos)` with `pos.timeframe="15m"` writes `timeframe='15m'` to `shadow_trades`.

Use the in-memory SQLite engine pattern from `test_dispatcher_e2e.py` (apply alembic head before tests OR use raw `CREATE TABLE` with the post-migration schema).

- [ ] **Step 2: Run + verify red**

### Task 3.2: Update persistence functions

**Files:**
- Modify: `backend/app/shadow/persistence.py`

- [ ] **Step 1: Add `timeframe` arg to `set_cooldown`**

```python
async def set_cooldown(
    session: AsyncSession, *,
    user_id: int, symbol: str, timeframe: str, until: datetime,
) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO shadow_cooldowns (user_id, symbol, timeframe, until) "
            "VALUES (:u, :s, :t, :until) "
            "ON CONFLICT (user_id, symbol, timeframe) "
            "DO UPDATE SET until = EXCLUDED.until"
        ),
        {"u": user_id, "s": symbol, "t": timeframe, "until": until},
    )
```

- [ ] **Step 2: Update `get_cooldowns` return shape**

```python
async def get_cooldowns(
    session: AsyncSession, *, user_id: int,
) -> dict[tuple[str, str], datetime]:
    rows = (await session.execute(
        sa.text(
            "SELECT symbol, timeframe, until FROM shadow_cooldowns "
            "WHERE user_id = :u"
        ),
        {"u": user_id},
    )).all()
    return {(r.symbol, r.timeframe): r.until for r in rows}
```

- [ ] **Step 3: Update `persist_closed_trade` to pass `pos.timeframe` to the builder**

- [ ] **Step 4: Test green; broader shadow + dispatcher regression clean**

```
cd backend && python -m pytest tests/shadow/ tests/unit/test_dispatcher_e2e.py --no-cov -q
```

- [ ] **Step 5: Commit**

```
git commit -m "feat(pr3): persistence — set_cooldown/get_cooldowns/persist threading timeframe (Phase 3)"
```

### Task 3.3: `build_shadow_trade_payload` accepts `timeframe`

**Files:**
- Modify: `backend/app/db/payload_builders.py`
- Modify: `backend/tests/db/test_payload_builders.py`

- [ ] **Step 1: Failing tests in `test_payload_builders.py`**

Existing `build_shadow_trade_payload` tests assert key count and timeframe='1h' (PR1 column default). New cases:

```python
def test_shadow_payload_timeframe_15m_threads_through() -> None:
    result = build_shadow_trade_payload(**_baseline_kwargs(), timeframe="15m")
    assert result["timeframe"] == "15m"


def test_shadow_payload_timeframe_default_1h() -> None:
    """Pre-PR3 call sites that omit timeframe get '1h' (PR1 compat)."""
    result = build_shadow_trade_payload(**_baseline_kwargs())
    assert result["timeframe"] == "1h"
```

- [ ] **Step 2: Add `timeframe: str = "1h"` kwarg to `build_shadow_trade_payload`**

- [ ] **Step 3: Test green + commit**

```
git commit -m "feat(pr3): build_shadow_trade_payload accepts timeframe (default '1h', Phase 3.3)"
```

---

## Phase 4 — `ShadowWorker` TF-aware refactor (the core)

Rationale: this is the largest single phase. Done in TDD slices.

### Task 4.1: Failing test for TF-keyed bar buffer

**Files:**
- Test: `backend/tests/shadow/test_worker_multi_tf.py` (NEW)

- [ ] **Step 1: Write tests**

- ShadowWorker constructed with `timeframes=["1h", "15m"]` exposes `self.bars` keyed by `(symbol, tf)`.
- `_handle_candle(candle, tf="15m")` appends to `(candle.symbol, "15m")` buffer, not `(symbol, "1h")`.
- Each TF has its own `MultiStreamReader`.

Use the existing `_StubReader` / fixture pattern from `test_shadow_worker.py` if present; otherwise mock via `MagicMock`.

- [ ] **Step 2: Run + verify red**

### Task 4.2: Refactor `ShadowWorker.__init__`

**Files:**
- Modify: `backend/app/shadow/worker.py`

- [ ] **Step 1: Update signature**

```python
class ShadowWorker:
    def __init__(
        self,
        symbols: list[str],
        session_factory: async_sessionmaker[AsyncSession],
        *,
        timeframes: list[str] | None = None,
    ) -> None:
        self.symbols = symbols
        self.session_factory = session_factory
        self.timeframes = timeframes if timeframes else ["1h"]
        self.bars: dict[tuple[str, str], pd.DataFrame] = {}
        self.readers: dict[str, MultiStreamReader] = {
            tf: MultiStreamReader(symbols, timeframe=tf)
            for tf in self.timeframes
        }
        self.open_positions: dict[tuple[str, str], ShadowPosition] = {}
        ...
```

- [ ] **Step 2: Update internal call sites**

Per the spec call-graph trace: `worker.py:59, 96, 121, 273, 288, 444, 466`. Each touches `SHADOW_TIMEFRAME` (the module constant). Replace with TF-aware accesses.

- [ ] **Step 3: Tests pass; broader shadow tests still green**

- [ ] **Step 4: Commit**

```
git commit -m "feat(pr3): ShadowWorker — TF-keyed bar buffer + per-TF readers (Phase 4.2)"
```

### Task 4.3: TF-aware `run()` + `_consume_one_tf()`

- [ ] **Step 1: Failing test** — assert `asyncio.gather` spawns N tasks (one per TF) and cancels cleanly.

- [ ] **Step 2: Implement**

```python
async def run(self) -> None:
    await self.setup()
    log.info(
        "shadow_worker: entering stream loop (%d TFs: %s)",
        len(self.timeframes), self.timeframes,
    )
    tasks = [
        asyncio.create_task(self._consume_one_tf(tf, self.readers[tf]))
        for tf in self.timeframes
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()


async def _consume_one_tf(self, tf: str, reader: MultiStreamReader) -> None:
    async for candle in reader.stream():
        try:
            await self._handle_candle(candle, tf)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception(
                "shadow worker handler failed for %s/%s: %s",
                candle.symbol, tf, e,
            )
            await record_heartbeat(
                self.session_factory, "shadow_worker",
                status="error",
                details={"symbol": candle.symbol, "tf": tf, "error": str(e)[:200]},
            )
```

- [ ] **Step 3: Commit**

```
git commit -m "feat(pr3): ShadowWorker.run spawns one task per TF (Phase 4.3)"
```

### Task 4.4: `_handle_candle(candle, tf)` TF-aware

- [ ] **Step 1: Failing tests** — _handle_candle records to `(symbol, tf)` bar buffer; consults per-TF cooldowns + per-TF open positions; heartbeats once per invocation (B6 contract).

- [ ] **Step 2: Implement the signature change + threading**

- Every internal call inside `_handle_candle` that previously used `SHADOW_TIMEFRAME` constant now uses the `tf` arg.
- The success-path heartbeat: `await record_heartbeat(self.session_factory, "shadow_worker", status="ok", details={"tf": tf})`.
- The paused-path heartbeat: `details={"paused": True}` (NO symbol/tf in paused payload per FU-1 follow-up's lesson).

- [ ] **Step 3: Commit**

```
git commit -m "feat(pr3): _handle_candle TF-aware + heartbeat per invocation (Phase 4.4)"
```

### Task 4.5: `setup()` reuses `_KLINE_CACHE`

- [ ] **Step 1: Failing test** — when MTF cache has 200 klines for (BTCUSDT, 15m), `setup()` does NOT call `client.fetch_klines`; uses cached klines.

- [ ] **Step 2: Implement**

```python
async def setup(self) -> None:
    from app.core.scoring.mtf_confluence import _cache_get, _cache_set
    settings = get_settings()
    for tf in self.timeframes:
        for sym in self.symbols:
            cache_entry = _cache_get(sym, tf)
            if cache_entry is not None and len(cache_entry.klines) >= settings.SHADOW_PREWARM_BARS:
                klines = cache_entry.klines
            else:
                klines = await self.client.fetch_klines(
                    sym, tf, limit=settings.SHADOW_PREWARM_BARS,
                )
                try:
                    _cache_set(sym, tf, klines, fetched_at=time.time())
                except Exception:
                    pass  # best-effort populate
            self.bars[(sym, tf)] = _klines_to_dataframe(klines)
```

- [ ] **Step 3: Commit**

```
git commit -m "feat(pr3): setup() reuses _KLINE_CACHE for prewarm (Phase 4.5)"
```

### Task 4.6: Narrow universe loader

**Files:**
- Modify: `backend/app/shadow/universe.py`
- Test: `backend/tests/shadow/test_universe_narrow.py` (NEW)

- [ ] **Step 1: Failing tests** — empty → full; non-empty intersects; no overlap → WARN + fallback.

- [ ] **Step 2: Implement `load_shadow_universe(session, narrow)`** per spec §4.4.

- [ ] **Step 3: Commit**

```
git commit -m "feat(pr3): shadow universe narrowing with fail-loud-then-open fallback (Phase 4.6)"
```

---

## Phase 5 — Per-TF exit timeout

### Task 5.1: Failing tests for per-TF expiry

**Files:**
- Test: `backend/tests/shadow/test_exit_monitor_per_tf.py` (NEW)

- [ ] **Step 1: Write tests**

- `check_exit(pos_1h_with_24_bars, ...)` returns `TIMEOUT`.
- `check_exit(pos_15m_with_95_bars, ...)` returns `None` (still inside 96-bar window).
- `check_exit(pos_15m_with_96_bars, ...)` returns `TIMEOUT`.
- `check_exit(pos_5m, ...)` raises `KeyError` (unknown TF — programming-error fail-loud).

### Task 5.2: Replace `TIMEOUT_BARS` constant with per-TF dict

**Files:**
- Modify: `backend/app/shadow/exit_monitor.py`

- [ ] **Step 1: Implement**

```python
# was: TIMEOUT_BARS = 24
TIMEOUT_BARS_PER_TF: dict[str, int] = {"1h": 24, "15m": 96}

def check_exit(
    pos: ShadowPosition, *, bar_high: float, bar_low: float, bar_close: float,
) -> ExitDecision | None:
    limit = TIMEOUT_BARS_PER_TF[pos.timeframe]  # KeyError on unknown TF
    if pos.bars_held >= limit:
        return ExitDecision(reason=ExitReason.TIMEOUT, exit_price=bar_close)
    ...
```

- [ ] **Step 2: Verify existing 1h tests still pass (TIMEOUT_BARS_PER_TF['1h']=24 matches old constant)**

- [ ] **Step 3: Commit**

```
git commit -m "feat(pr3): TIMEOUT_BARS_PER_TF dict — 1h=24, 15m=96 (Phase 5)"
```

---

## Phase 5.5 — Hold/TP scaling by `mtf_agreement` (G1)

Originally PR4 scope (smart-position v1 trio). G2/G3 stay deferred (need 30+ days of MTF shadow data); G1 has no such dependency since `mtf_agreement` is already on `predictions` from PR1. Added to PR3 per operator scope audit 2026-05-18.

**Net add**: ~80 LOC + 5 tests. Spec §4.6b.

### Task 5.5.1: Migration extension — add scaling columns to shadow_trades + live_trades

**Files:**
- Modify: `backend/alembic/versions/2026_05_18_0021_pr3_shadow_per_tf.py` (the existing PR3 migration from Phase 1)
- Modify: `backend/tests/db/test_pr3_migration.py`
- Modify: `backend/app/db/audit.py` — extend `NON_HASHED_ALLOW_LIST` for `shadow_trades` and `live_trades` with the two new columns

- [ ] **Step 1: Extend migration `upgrade()` body**

Append to the existing PR3 migration (do NOT create a new revision — this is in-flight scope expansion):

```python
    # --- G1: Hold/TP scaling recording-only columns ----------------------
    # shadow_trades (PR3 populates from worker)
    op.execute("ALTER TABLE shadow_trades ADD COLUMN hold_scaling_factor REAL NULL;")
    op.execute("ALTER TABLE shadow_trades ADD COLUMN hold_timeout_bars   SMALLINT NULL;")
    # live_trades (PR3 reserves the columns; future PR wires the auto path)
    op.execute("ALTER TABLE live_trades   ADD COLUMN hold_scaling_factor REAL NULL;")
    op.execute("ALTER TABLE live_trades   ADD COLUMN hold_timeout_bars   SMALLINT NULL;")
```

- [ ] **Step 2: Extend `downgrade()` body** (reverse order — drop these BEFORE undoing the PK/UNIQUE changes)

```python
    op.execute("ALTER TABLE live_trades   DROP COLUMN hold_timeout_bars;")
    op.execute("ALTER TABLE live_trades   DROP COLUMN hold_scaling_factor;")
    op.execute("ALTER TABLE shadow_trades DROP COLUMN hold_timeout_bars;")
    op.execute("ALTER TABLE shadow_trades DROP COLUMN hold_scaling_factor;")
    # ... then the existing PR3 downgrade body ...
```

- [ ] **Step 3: Failing migration introspection tests in `test_pr3_migration.py`**

```python
@pytest.mark.skipif(not _DSN.startswith("postgresql"), reason="Postgres only.")
@pytest.mark.asyncio
async def test_shadow_trades_has_hold_scaling_columns() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name='shadow_trades' "
            "AND column_name IN ('hold_scaling_factor', 'hold_timeout_bars');"
        ))).all()
    cols = {r.column_name: (r.data_type, r.is_nullable) for r in rows}
    assert "hold_scaling_factor" in cols
    assert "hold_timeout_bars" in cols
    assert cols["hold_scaling_factor"][1] == "YES"  # nullable
    assert cols["hold_timeout_bars"][1] == "YES"
    await engine.dispose()


@pytest.mark.skipif(not _DSN.startswith("postgresql"), reason="Postgres only.")
@pytest.mark.asyncio
async def test_live_trades_has_hold_scaling_columns() -> None:
    # ... symmetric assertion on live_trades ...
```

- [ ] **Step 4: Extend `NON_HASHED_ALLOW_LIST` in `app/db/audit.py`**

The 4 new columns (2 on shadow_trades, 2 on live_trades) MUST go in the allow-list so the audit verifier doesn't flag them as missing hash inputs. They are recording-only — never in `HASH_PAYLOAD_COLUMNS`. Mirrors PR1's pattern for the analytics columns.

- [ ] **Step 5: Run upgrade + downgrade round-trip — green**

```
DATABASE_URL=postgresql+asyncpg://... python -m pytest tests/db/test_pr3_migration.py tests/db/test_pr3_migration_downgrade.py -v
```

- [ ] **Step 6: Commit**

```
git commit -m "feat(pr3): G1 — alembic adds hold_scaling_factor + hold_timeout_bars (shadow + live, Phase 5.5.1)"
```

### Task 5.5.2: Settings — `HOLD_TP_SCALING_ENABLED` + `HOLD_TP_SCALING_TABLE`

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/tests/unit/test_pr3_settings_defaults.py`

- [ ] **Step 1: Failing tests**

```python
def test_hold_tp_scaling_default_off() -> None:
    assert _s().HOLD_TP_SCALING_ENABLED is False


def test_hold_tp_scaling_table_default() -> None:
    s = _s()
    assert s.HOLD_TP_SCALING_TABLE == {
        3: (24, 1.0),
        4: (48, 1.25),
        5: (96, 1.5),
        6: (168, 2.0),
    }


def test_hold_tp_scaling_env_var_override(monkeypatch) -> None:
    monkeypatch.setenv("HOLD_TP_SCALING_ENABLED", "true")
    assert _s().HOLD_TP_SCALING_ENABLED is True
```

- [ ] **Step 2: Add fields to `Settings(BaseSettings)`**

```python
    # --- PR3 G1: Hold/TP scaling by mtf_agreement ------------------------
    # Default OFF: scaling does NOT apply; positions use the per-TF baseline
    # timeout (TIMEOUT_BARS_PER_TF) and the engine's computed TP. Operator
    # flips per-env after staging confirms scaled trades behave (per-TF
    # win-rate, hold-time distribution). G2 (IC auto-weighting) and G3
    # (regime-conditional weights) stay deferred — need 30+ days of MTF
    # shadow data which only starts accruing post-PR3 deploy.
    HOLD_TP_SCALING_ENABLED: bool = False
    # Lookup: mtf_agreement -> (timeout_bars, tp_multiplier).
    # The timeout_bars value here is the 1h-baseline; for 15m positions
    # the worker applies the multiplier (entry's_table_bars / 24) against
    # the per-TF baseline (TIMEOUT_BARS_PER_TF[tf]) — see scaling.py.
    # Stop-loss is INVARIANT under scaling.
    HOLD_TP_SCALING_TABLE: dict[int, tuple[int, float]] = {
        3: (24, 1.0),
        4: (48, 1.25),
        5: (96, 1.5),
        6: (168, 2.0),
    }
```

- [ ] **Step 3: Test green; commit**

```
git commit -m "feat(pr3): G1 settings — HOLD_TP_SCALING_ENABLED (default OFF) + scaling table (Phase 5.5.2)"
```

### Task 5.5.3: `app/shadow/scaling.py` — pure lookup helper

**Files:**
- New: `backend/app/shadow/scaling.py`
- New: `backend/tests/shadow/test_hold_tp_scaling.py`

- [ ] **Step 1: Failing tests**

```python
"""G1 — Hold/TP scaling lookup helper. Pure function, no I/O."""
from __future__ import annotations

import pytest

from app.shadow.scaling import effective_hold_tp


_TABLE = {3: (24, 1.0), 4: (48, 1.25), 5: (96, 1.5), 6: (168, 2.0)}


def test_lookup_per_agreement_returns_table_entry() -> None:
    assert effective_hold_tp(timeframe="1h", mtf_agreement=4, table=_TABLE) == (48, 1.25)
    assert effective_hold_tp(timeframe="1h", mtf_agreement=6, table=_TABLE) == (168, 2.0)


def test_lookup_agreement_3_returns_baseline() -> None:
    """agreement=3 maps to (24, 1.0) — baseline for 1h."""
    assert effective_hold_tp(timeframe="1h", mtf_agreement=3, table=_TABLE) == (24, 1.0)


def test_lookup_15m_scales_against_per_tf_baseline() -> None:
    """For 15m, multiplier is relative to the per-TF baseline (96 bars).
    agreement=4 → 48/24 = 2× multiplier → 192 bars on 15m."""
    bars, mult = effective_hold_tp(timeframe="15m", mtf_agreement=4, table=_TABLE)
    assert bars == 192   # 96 × (48/24)
    assert mult == 1.25  # tp multiplier unchanged across TFs


def test_lookup_agreement_below_table_returns_baseline_and_warns(caplog) -> None:
    """agreement=2 shouldn't reach this lookup (PR2 gate blocks), but if
    it ever does — fail-open to baseline + WARN."""
    import logging
    caplog.set_level(logging.WARNING, logger="app.shadow.scaling")
    bars, mult = effective_hold_tp(timeframe="1h", mtf_agreement=2, table=_TABLE)
    assert (bars, mult) == (24, 1.0)
    assert any("below table" in r.message for r in caplog.records)


def test_lookup_none_agreement_returns_baseline() -> None:
    """mtf_agreement is None (PR1 fail-open) → baseline + no scaling."""
    assert effective_hold_tp(timeframe="1h", mtf_agreement=None, table=_TABLE) == (24, 1.0)


def test_lookup_unknown_tf_raises() -> None:
    """Unknown TF in TIMEOUT_BARS_PER_TF is a programming error — fail-loud."""
    with pytest.raises(KeyError):
        effective_hold_tp(timeframe="5m", mtf_agreement=4, table=_TABLE)
```

- [ ] **Step 2: Implement `scaling.py`**

```python
"""G1 — Hold/TP scaling lookup. Pure, no I/O.

Per spec §4.6b: at trade-open time, the worker calls effective_hold_tp(...)
to convert (timeframe, mtf_agreement) into (timeout_bars, tp_multiplier).
The result is recording-only when HOLD_TP_SCALING_ENABLED=False — the
worker uses the per-TF baseline instead in that case. When ON, both the
ShadowPosition and the persisted shadow_trades / live_trades rows reflect
the scaled values.
"""
from __future__ import annotations

import logging

from app.shadow.exit_monitor import TIMEOUT_BARS_PER_TF

log = logging.getLogger(__name__)


def effective_hold_tp(
    *,
    timeframe: str,
    mtf_agreement: int | None,
    table: dict[int, tuple[int, float]],
) -> tuple[int, float]:
    """Look up `(timeout_bars, tp_multiplier)` for this signal.

    Multiplier semantics: the table is indexed against the 1h baseline
    (24 bars). For non-1h TFs, the timeout multiplier is applied against
    that TF's baseline (TIMEOUT_BARS_PER_TF[tf]). TP multiplier is TF-
    invariant — same multiplier whether the position is 1h or 15m.

    Fail-open contract:
      - mtf_agreement is None (PR1 cold cache / fetch fail) → baseline +
        1.0× (no scaling). PR2 gate has already passed at this point.
      - mtf_agreement below the lowest table key (shouldn't happen — PR2
        MTF_MIN_AGREEMENT_1H gate blocks) → baseline + 1.0× + WARN.
      - mtf_agreement above the highest table key → cap at the highest key.

    Raises:
      - KeyError if `timeframe` isn't in TIMEOUT_BARS_PER_TF (programming
        error — fail-loud; new TFs require explicit TIMEOUT_BARS_PER_TF +
        scaling-table entries).
    """
    tf_baseline_bars = TIMEOUT_BARS_PER_TF[timeframe]  # KeyError → fail-loud

    if mtf_agreement is None:
        return (tf_baseline_bars, 1.0)

    sorted_keys = sorted(table.keys())
    lowest, highest = sorted_keys[0], sorted_keys[-1]
    if mtf_agreement < lowest:
        log.warning(
            "effective_hold_tp: mtf_agreement=%d below table (min=%d); "
            "falling back to baseline. PR2 MTF gate should have blocked.",
            mtf_agreement, lowest,
        )
        return (tf_baseline_bars, 1.0)
    if mtf_agreement > highest:
        agreement = highest
    else:
        agreement = mtf_agreement

    table_bars, tp_mult = table[agreement]
    # The table_bars is the 1h-baseline; scale by TF ratio for non-1h TFs.
    bars_baseline_1h = table[lowest][0]  # 24 (lowest key's bars = baseline)
    bars = int(tf_baseline_bars * (table_bars / bars_baseline_1h))
    return (bars, tp_mult)
```

- [ ] **Step 3: Test green + commit**

```
git commit -m "feat(pr3): G1 scaling.py — effective_hold_tp pure lookup (Phase 5.5.3)"
```

### Task 5.5.4: `ShadowPosition` fields + worker open-trade hook

**Files:**
- Modify: `backend/app/shadow/engine.py`
- Modify: `backend/app/shadow/worker.py`
- Modify: `backend/tests/shadow/test_hold_tp_scaling.py`

- [ ] **Step 1: Failing tests for the position hook**

```python
@pytest.mark.asyncio
async def test_hold_tp_scaling_applies_on_open_when_enabled(
    monkeypatch,
) -> None:
    """Flag ON + signal with mtf_agreement=5 + tf=1h → opened position has
    hold_timeout_bars=96, hold_scaling_factor=1.5, take_profit at 1.5×
    baseline distance from entry."""
    monkeypatch.setenv("HOLD_TP_SCALING_ENABLED", "true")
    # ... fixture: signal with mtf_agreement=5, baseline TP 4% above entry ...
    # ... drive _handle_candle ...
    # ... assert pos.hold_timeout_bars == 96, pos.hold_scaling_factor == 1.5 ...
    # ... assert pos.take_profit_price == entry + 1.5 * 0.04 * entry ...


@pytest.mark.asyncio
async def test_hold_tp_scaling_off_keeps_baseline(monkeypatch) -> None:
    """Flag OFF (default) → opened position has hold_* fields = None,
    take_profit at engine-computed baseline (PR1/PR2 behavior)."""
    # ... assert pos.hold_timeout_bars is None ...
    # ... assert pos.hold_scaling_factor is None ...
    # ... assert pos.take_profit_price == engine_baseline_tp ...


@pytest.mark.asyncio
async def test_hold_tp_scaling_neutral_signal_never_scales() -> None:
    """NEUTRAL direction never opens a position, so scaling lookup never
    fires. Lock-in test against future refactors that might call
    effective_hold_tp on NEUTRAL signals."""
    # ... ShadowWorker._handle_candle with NEUTRAL signal ...
    # ... assert effective_hold_tp NOT called (mock + assert_not_called) ...
```

- [ ] **Step 2: Add fields to `ShadowPosition`**

```python
@dataclass(frozen=True)
class ShadowPosition:
    ...
    # G1 (Phase 5.5): NULL when HOLD_TP_SCALING_ENABLED=False; populated
    # with the table-lookup values when scaling is ON.
    hold_scaling_factor: float | None = None
    hold_timeout_bars: int | None = None
```

- [ ] **Step 3: Hook into the worker's open-trade path**

In the LONG/SHORT entry branch of `_handle_candle` (post-PR3 Phase 4 refactor, the call site is TF-aware):

```python
from app.shadow.scaling import effective_hold_tp

if settings.HOLD_TP_SCALING_ENABLED:
    scaled_bars, tp_mult = effective_hold_tp(
        timeframe=tf,
        mtf_agreement=signal.mtf_agreement,
        table=settings.HOLD_TP_SCALING_TABLE,
    )
    # Recompute TP at scaled distance, keep SL invariant.
    baseline_tp_distance = abs(baseline_tp - entry_price)
    sign = 1 if direction == Direction.LONG else -1
    new_tp = entry_price + (sign * tp_mult * baseline_tp_distance)
    pos = ShadowPosition(
        ...,
        take_profit=new_tp,
        hold_scaling_factor=tp_mult,
        hold_timeout_bars=scaled_bars,
    )
else:
    # Flag OFF — bit-identical to pre-G1 behavior.
    pos = ShadowPosition(
        ...,
        take_profit=baseline_tp,
        # hold_* default None
    )
```

- [ ] **Step 4: `exit_monitor.py` reads `position.hold_timeout_bars` when set, else falls back to `TIMEOUT_BARS_PER_TF[position.timeframe]`**

```python
def check_exit(
    pos: ShadowPosition, *, bar_high: float, bar_low: float, bar_close: float,
) -> ExitDecision | None:
    # G1 (Phase 5.5): per-position override takes precedence over per-TF
    # default. None falls back to the per-TF baseline (PR1/PR2 behavior).
    limit = pos.hold_timeout_bars if pos.hold_timeout_bars is not None \
        else TIMEOUT_BARS_PER_TF[pos.timeframe]
    if pos.bars_held >= limit:
        return ExitDecision(reason=ExitReason.TIMEOUT, exit_price=bar_close)
    ...
```

- [ ] **Step 5: Tests green + commit**

```
git commit -m "feat(pr3): G1 worker + exit_monitor honor scaled hold/TP per ShadowPosition (Phase 5.5.4)"
```

### Task 5.5.5: Persistence — `build_shadow_trade_payload` + `build_live_trade_payload` accept scaling kwargs

**Files:**
- Modify: `backend/app/db/payload_builders.py`
- Modify: `backend/app/shadow/persistence.py` — pass `pos.hold_scaling_factor` and `pos.hold_timeout_bars` to the builder
- Modify: `backend/tests/db/test_payload_builders.py`

- [ ] **Step 1: Failing tests**

```python
def test_shadow_payload_hold_scaling_populated() -> None:
    result = build_shadow_trade_payload(
        **_baseline_kwargs(),
        hold_scaling_factor=1.5,
        hold_timeout_bars=96,
    )
    assert result["hold_scaling_factor"] == 1.5
    assert result["hold_timeout_bars"] == 96


def test_shadow_payload_hold_scaling_default_none() -> None:
    """Pre-G1 callers (no kwargs) get NULL on both columns — bit-identical
    to pre-PR3 shadow_trades row contract."""
    result = build_shadow_trade_payload(**_baseline_kwargs())
    assert result["hold_scaling_factor"] is None
    assert result["hold_timeout_bars"] is None


def test_live_trade_payload_hold_scaling_columns_present() -> None:
    """Live_trades gets the columns now (PR3 reserves; future PR wires
    auto path). Builder accepts kwargs, default None."""
    result = build_live_trade_payload(**_baseline_live_kwargs())
    assert result["hold_scaling_factor"] is None
    assert result["hold_timeout_bars"] is None
```

- [ ] **Step 2: Add `hold_scaling_factor` + `hold_timeout_bars` kwargs (default None) to both builders**

Update the key count assertions in existing tests: 18 → 20 for `build_live_trade_payload`; matching update for `build_shadow_trade_payload`.

- [ ] **Step 3: Wire `pos.hold_*` through `persist_closed_trade` to the builder**

- [ ] **Step 4: Test green + commit**

```
git commit -m "feat(pr3): G1 persistence — payload builders + persist_closed_trade thread scaling (Phase 5.5.5)"
```

---

## Phase 6 — Heartbeat + watchdog hygiene (FU-1 partial close)

Rationale: per spec §6.3 these ship together in one commit.

### Task 6.1: Failing test for the registry shape

**Files:**
- Test: `backend/tests/ops/test_shadow_worker_heartbeat.py` (NEW)

- [ ] **Step 1: Tests cover**

- `WORKER_REGISTRY` entry for `shadow_worker` has `max_staleness_seconds == 30 * 60`.
- `pending_heartbeat` attribute is `False` (not `True`).
- Driving `_handle_candle` once produces a row in `worker_heartbeats` with `worker_name='shadow_worker'`.

### Task 6.2: Update worker_registry.py + verify heartbeat already wired

**Files:**
- Modify: `backend/app/ops/worker_registry.py`

- [ ] **Step 1: Update `shadow_worker` entry**

```python
WorkerSpec(
    name="shadow_worker",
    description="1h+15m shadow paper-trade engine across the asset universe",  # update wording
    liveness_query=HEARTBEAT,
    max_staleness_seconds=30 * 60,  # PR3: was 2*60*60 (sized for 15m cadence)
    stateful=True,
    # PR3: pending_heartbeat removed (was True post-FU-1 partial); heartbeat is now wired
    # in _handle_candle (B6). The CI test `test_no_pending_heartbeat_after_fu1`
    # enforces this at the suite level.
),
```

- [ ] **Step 2: Confirm `record_heartbeat` is wired (already done in Phase 4.4)**

- [ ] **Step 3: Tests + registry-consistency suite green**

```
cd backend && python -m pytest tests/ops/test_shadow_worker_heartbeat.py tests/unit/test_worker_registry_consistency.py -v
```

- [ ] **Step 4: Commit**

```
git commit -m "feat(pr3): shadow_worker max_staleness 30 min + heartbeat wired (FU-1 partial, Phase 6)"
```

### Task 6.3: Update KNOWN_ISSUES to note FU-1 shadow_worker closure

**Files:**
- Modify: `backend/docs/KNOWN_ISSUES.md`

- [ ] **Step 1: Update the FU-1 closure note**

Add a one-line addendum to FU-1's entry (already CLOSED on 2026-05-17) noting that PR3 also tightened `shadow_worker` max_staleness_seconds from 2h to 30 min for the 15m cadence. Do NOT re-open the FU-1 entry; just append context.

- [ ] **Step 2: Commit**

```
git commit -m "docs(known-issues): FU-1 addendum — PR3 tightens shadow_worker staleness budget"
```

---

## Phase 7 — `/promotion-gate` per-TF breakdown

### Task 7.1: Failing integration test

**Files:**
- Test: `backend/tests/integration/test_pr3_promotion_gate_breakdown.py` (NEW)

- [ ] **Step 1: Tests cover**

- `/promotion-gate` JSON includes `per_timeframe` block with `"1h"` and `"15m"` keys.
- `per_timeframe["15m"]` has `trades_total >= 0` (may be zero before 15m signals fire).
- Combined block's `trades_total` EXCLUDES 15m when `SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False`.
- Combined INCLUDES 15m when override flips to True.

### Task 7.2: Implement per-TF aggregation

**Files:**
- Modify: `backend/app/api/routes/bot_status.py`

- [ ] **Step 1: Add per-TF query + aggregation per spec §4.8**

- [ ] **Step 2: Test green**

- [ ] **Step 3: Commit**

```
git commit -m "feat(pr3): /promotion-gate per_timeframe breakdown (Phase 7)"
```

---

## Phase 8 — Frontend hyperlink fix

### Task 8.1: Read pos.timeframe via the API response model

**Files:**
- Check: `backend/app/api/schemas.py` or wherever `OpenPosition` Pydantic model lives

- [ ] **Step 1: Verify `OpenPosition` API model exposes `timeframe`. Add if missing.**

### Task 8.2: Frontend 3-line change

**Files:**
- Modify: `frontend/src/tabs/BotStatus/OpenPositions.tsx`

- [ ] **Step 1: Change the hardcoded `"1h"` to `pos.timeframe ?? "1h"` at lines 36-38**

- [ ] **Step 2: Frontend `npm run typecheck` and `npm run lint` (or whichever is wired)**

- [ ] **Step 3: Commit**

```
git commit -m "fix(frontend): OpenPositions deep-link reads pos.timeframe with fallback (Phase 8)"
```

---

## Phase 9 — V-7 bench

### Task 9.1: Author `bench_shadow_handle_candle.py`

**Files:**
- New: `backend/scripts/bench_shadow_handle_candle.py`

- [ ] **Step 1: Author the script**

Mirror `bench_aggregator_latency.py` structure. Two modes:

- `--mode=baseline`: TFs=["1h"], drives `_handle_candle` N=200 times with the canned BTCUSDT bars fixture, mocks DB writes.
- `--mode=multi-tf`: TFs=["1h", "15m"], drives `_handle_candle` for alternating 1h + 15m candles.

Same JSON output shape as the aggregator bench. Same V-7 budget: `delta_p50 ≤ 50ms`, `delta_p99 ≤ 200ms`.

- [ ] **Step 2: Run both modes, capture deltas, paste numbers into commit body**

```
python backend/scripts/bench_shadow_handle_candle.py --mode=baseline --n 200
python backend/scripts/bench_shadow_handle_candle.py --mode=multi-tf --n 200
```

- [ ] **Step 3: Commit**

```
git commit -m "bench(pr3): bench_shadow_handle_candle.py — V-7 PASS Δp50=Xms Δp99=Yms"
```

---

## Phase 10 — `main.py` spawn + integration test + ARCHITECTURE update + PR

### Task 10.1: Wire `start_shadow_worker` to use the settings TF list

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Pass `timeframes=settings.SHADOW_TIMEFRAMES` to the worker spawn**

- [ ] **Step 2: Commit**

```
git commit -m "feat(pr3): main spawn uses SHADOW_TIMEFRAMES from settings (Phase 10.1)"
```

### Task 10.2: E2E dual-lane integration test

**Files:**
- Test: `backend/tests/integration/test_pr3_e2e_dual_lane.py` (NEW)

- [ ] **Step 1: Drive a fake MultiStreamReader fixture that yields candles on both 1h + 15m streams. Assert the worker writes two distinct `shadow_trades` rows (one per TF) when both TFs cross the entry threshold near-simultaneously.**

- [ ] **Step 2: Commit**

```
git commit -m "test(pr3): e2e dual-lane — same-symbol cross-TF produces 2 trade rows"
```

### Task 10.3: Update `docs/ARCHITECTURE.md`

**Files:**
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Add a new section 11b (after PR2's 9b) describing**

- 15m lane motivation (4× signal rate, no live trading impact)
- Per-TF cooldown + open positions design
- Heartbeat + watchdog tightening (FU-1 partial)
- Rollback path (env var `SHADOW_TIMEFRAMES=["1h"]`)
- V-7 bench result numbers

- [ ] **Step 2: Commit**

```
git commit -m "docs(arch): section 11b — PR3 multi-resolution shadow"
```

### Task 10.4: Self-review + reviewer subagents

- [ ] **Step 1: Run the full local suite under bare env (no DATABASE_URL exported — conftest setdefault kicks in)**

```
cd backend && python -m pytest tests/ --no-cov --ignore=tests/unit/test_ml_checkpoints.py --ignore=tests/unit/test_ratelimit_client.py
```
Expected: 2200+ pass, 17 skipped (Postgres-only), 0 fail. (Approximate; PR3 adds ~15 tests on top of PR2's count.)

- [ ] **Step 2: Run V-7 bench one more time — confirm PASS in commit message**

- [ ] **Step 3: Dispatch spec-compliance reviewer subagent**

Per the subagent-driven-development skill template. Have it verify each §-numbered requirement in the spec (§1–§9) is met, scope creep didn't sneak in (no score knob changes, no live-trading touch, only the 3-line frontend tweak, etc.).

- [ ] **Step 4: Dispatch code-quality reviewer subagent**

Per the code-reviewer-prompt template. Have it focus on:
- TF-aware refactor correctness (no stale `SHADOW_TIMEFRAME` constant references)
- Migration `downgrade()` correctness
- Heartbeat + watchdog wiring (one of B6/B7 without the other is a bug)
- Cross-TF duplicate handling (R2)
- Schema migration edge cases (backfill correctness, constraint name collisions)
- V-7 bench microbench fairness (does the bench actually measure what it claims?)

- [ ] **Step 5: Address Important findings inline; loop reviewer until both APPROVED**

### Task 10.5: Push branch + open PR vs dev

- [ ] **Step 1: Push**

```
git push -u origin feat/pr3-impl-multi-resolution-shadow
```

- [ ] **Step 2: Open the PR**

```
gh pr create --base dev --head feat/pr3-impl-multi-resolution-shadow \
  --title "feat(pr3): multi-resolution shadow — 15m lane + per-TF state + heartbeat" \
  --body-file <prepared body>
```

Body should include:
- Scope summary (10 phases)
- Spec section references
- Settings list with defaults
- Migration summary + downgrade tested
- V-7 numbers
- Reviewer outcomes
- 24h+criteria soak plan: `SHADOW_TIMEFRAMES=["1h", "15m"]` enabled on staging; verify per-TF trade rows accrue; `per_timeframe.15m.trades_total > 0` within first hour
- Rollback: single env var `SHADOW_TIMEFRAMES=["1h"]`

- [ ] **Step 3: Wait CI green; manual squash-merge per the auto-merge-repo-disabled pattern (cherry-pick-prod-promotion-pattern memory)**

---

## Exit criteria (PR3 ships to dev when)

1. ✅ All CI green (backend + frontend + docker-smoke).
2. ✅ ruff + mypy clean on full source.
3. ✅ Full local pytest: 2200+ passed, 17 skipped, 0 failed (matching PR2's baseline + ~15 new PR3 tests).
4. ✅ Migration upgrade + downgrade round-trip passes on Postgres.
5. ✅ V-7 bench: `delta_p50 ≤ 50ms`, `delta_p99 ≤ 200ms`.
6. ✅ Spec-compliance reviewer: APPROVED.
7. ✅ Code-quality reviewer: 0 Critical findings.
8. ✅ Cherry-pick prod-promotion pattern ready (per memory).

## Exit criteria (PR3 ships to prod when — operator-triggered, post 24h+criteria soak)

1. ✅ FU-1 + PR2 already in prod (confirmed; both landed 2026-05-17).
2. ✅ 24h staging soak with `SHADOW_TIMEFRAMES=["1h", "15m"]` enabled.
3. ✅ `per_timeframe.15m.trades_total > 0` (proves 15m lane fires).
4. ✅ Zero new chain breaks in staging during soak.
5. ✅ Zero new predictor/dispatcher errors in staging logs.
6. ✅ shadow_worker heartbeat appearing every `_handle_candle` invocation (max_staleness < 30 min always).
7. ✅ Operator review of full diff — explicit `ship it` for dev → main merge.
8. ✅ Cherry-pick `chore/pr3-prod-promotion` off `main`; PR vs main; CI green; squash-merge per the standard pattern.

## References

- Parent spec: [`docs/superpowers/specs/2026-05-17-pr3-multi-resolution-shadow-design.md`](../specs/2026-05-17-pr3-multi-resolution-shadow-design.md)
- Master rollout: [`docs/superpowers/specs/2026-05-17-master-rollout-plan-option-d.md`](../specs/2026-05-17-master-rollout-plan-option-d.md)
- PR1 plan + spec: `docs/superpowers/plans/2026-05-16-pr1-record-only.md`, `docs/superpowers/specs/2026-05-16-pr1-record-only-design.md`
- PR2 plan + spec: `docs/superpowers/plans/2026-05-17-pr2-mtf-gate-and-short-safety.md`, `docs/superpowers/specs/2026-05-17-pr2-mtf-gate-and-short-safety-design.md`
- KNOWN_ISSUES: `backend/docs/KNOWN_ISSUES.md` (FU-1 partial close on shadow_worker; FU-10 PR3-local anticipation in Task 1.3)
- Memories: `cherry-pick-prod-promotion-pattern`, `merge-authorization`, `dev-prod-branch-workflow`, `complete-modules-before-merge`, `shadow-entry-thresholds`, `worker-watchdog-system`
- Migration patterns: `backend/alembic/versions/2026_05_17_0020_pr1_record_only_columns.py` (3-step add/backfill/flip)
