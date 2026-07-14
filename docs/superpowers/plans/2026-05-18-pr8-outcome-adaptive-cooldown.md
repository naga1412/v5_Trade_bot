# PR8 — Outcome-adaptive cooldown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `live_trades.exit_reason` at close time, add `live_cooldowns` table + dispatcher cooldown gate, and apply outcome-adaptive cooldown durations (SL=8h+fresh-MTF, TP=1h, TIMEOUT=4h, MANUAL/EXTERNAL=0h, LIQ_BUFFER=24h). Default-OFF in prod via `LIVE_COOLDOWN_ENABLED=False`.

**Architecture:** Three intertwined deliverables in one PR. (1) `live_exit_monitor` worker polls open `live_trades` every 30s, classifies outcome on close, writes `live_trades.exit_reason` + upserts `live_cooldowns`. (2) `_apply_cooldown_gate` pre-condition in dispatcher reads `live_cooldowns` for `(user_id, symbol)`, blocks if `cooldown_until > now` (with SL-fresh-MTF override). (3) Pure-function `compute_cooldown_duration` reads `LIVE_COOLDOWN_HOURS_BY_OUTCOME` dict. Fail-open contract on cooldown errors. No regime-aware behavior — flag added as forward-compat hook only.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 async / Pydantic v2 BaseSettings / Alembic / pytest + pytest-asyncio.

**Source spec:** [`docs/superpowers/specs/2026-05-18-pr8-outcome-adaptive-cooldown-design.md`](../specs/2026-05-18-pr8-outcome-adaptive-cooldown-design.md)

**Branch:** `feat/pr8-impl-outcome-adaptive-cooldown` off `dev` (which now contains PR3). NEVER push to `main`.

**Behavior change classification:** YES — adds a new pre-condition gate to the live dispatcher. Default-OFF (`LIVE_COOLDOWN_ENABLED=False`) so the deploy is bit-identical to pre-PR8 until operator flips. Per operator's policy: 24h+criteria soak after operator flips ON in staging, before flipping ON in prod.

---

## File Structure (locked in via design)

### NEW files

| Path | Responsibility |
|---|---|
| `backend/alembic/versions/2026_05_18_0022_pr8_live_cooldowns.py` | Create `live_cooldowns` table + partial active-only index. 2-step pattern. |
| `backend/app/trading/cooldown_compute.py` | Pure functions: `compute_cooldown_duration(exit_reason, settings) -> timedelta`, `is_cooldown_blocked(now, cooldown_row, new_mtf, settings) -> tuple[bool, str]`. No DB. |
| `backend/app/trading/execution/cooldown_gate.py` | `_apply_cooldown_gate(proposal, session, settings) -> DispatchResult | None`. Mirrors `_apply_mtf_gate`. Fail-open on DB error. |
| `backend/app/trading/execution/live_exit_monitor.py` | 30s polling worker: read open `live_trades` from DB, fetch live position from Binance, classify outcome on close, write `live_trades.exit_reason` + upsert `live_cooldowns`. Heartbeat per poll. |
| `backend/app/db/live_cooldowns.py` | Persistence: `load_cooldown(uid, sym)`, `upsert_cooldown(...)`, `delete_cooldown(...)`. Mirrors `shadow.persistence` patterns. |
| `backend/app/db/live_exit_reasons.py` | `LiveExitReason` StrEnum (TAKE_PROFIT / STOP_LOSS / TIMEOUT / MANUAL_CLOSE / EXTERNAL_CLOSE / LIQUIDATION_BUFFER_BREACH). |
| `backend/tests/db/test_pr8_migration.py` | Postgres introspection: table exists, PK shape, columns, index, downgrade round-trip. |
| `backend/tests/db/test_pr8_migration_downgrade.py` | Round-trip upgrade → downgrade → upgrade; assert clean restore. |
| `backend/tests/unit/test_pr8_settings_defaults.py` | All 4 new settings have correct defaults. |
| `backend/tests/unit/test_cooldown_compute.py` | `compute_cooldown_duration` per outcome; `is_cooldown_blocked` matrix. |
| `backend/tests/trading/test_cooldown_gate.py` | Dispatcher pre-conditions integration (gate enabled vs disabled, fail-open). |
| `backend/tests/trading/test_live_exit_monitor.py` | Outcome classification (TP/SL/TIMEOUT/EXTERNAL); exit_reason write-back; cooldown upsert. |
| `backend/tests/integration/test_pr8_e2e_sl_blocks_then_clears.py` | E2E: SL closes → 8h cooldown set → next signal blocked → calendar expires → stale MTF still blocks → fresh MTF clears. |
| `backend/tests/integration/test_pr8_liquidation_buffer_path.py` | Liquidation auto-close writes `exit_reason="liquidation_buffer_breach"` + 24h cooldown. |
| `backend/tests/ops/test_live_exit_monitor_registry.py` | Worker registry has `live_exit_monitor` with `max_staleness_seconds=2*60`, `pending_heartbeat=False`. |
| `backend/tests/api/test_cooldowns_endpoint.py` | `/bot-status/cooldowns` returns active cooldowns; correct schema. |
| `backend/scripts/bench_dispatcher_preconditions.py` | V-7 microbench: gate-disabled vs gate-enabled. `delta_p50 ≤ 5ms`, `delta_p99 ≤ 20ms`. |

### MODIFIED files

| Path | Reason |
|---|---|
| `backend/app/config.py` | Add 4 PR8 settings: `LIVE_COOLDOWN_ENABLED`, `LIVE_COOLDOWN_HOURS_BY_OUTCOME`, `LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF`, `LIVE_COOLDOWN_REGIME_AWARE`. |
| `backend/app/trading/execution/dispatcher.py` | Wire `_apply_cooldown_gate` into pre-conditions block (between funding and MTF — cheapest check). |
| `backend/app/trading/execution/liquidation_monitor.py` | Auto-close path writes `exit_reason="liquidation_buffer_breach"` + upserts `live_cooldowns`. |
| `backend/app/api/routes/bot_status.py` | New `/cooldowns` endpoint. |
| `backend/app/api/schemas.py` | `LiveCooldownOut` schema. |
| `backend/app/ops/worker_registry.py` | Register `live_exit_monitor` (max_staleness=2*60). |
| `backend/app/main.py` | Spawn `live_exit_monitor` task alongside `liquidation_monitor`. |
| `backend/app/db/audit.py` | If needed: confirm `live_trades.exit_reason` is in `NON_HASHED_ALLOW_LIST` (it should be — column existed pre-PR8). |
| `docs/ARCHITECTURE.md` | New §12 — Outcome-adaptive cooldown. |
| `docs/superpowers/specs/2026-05-17-master-rollout-plan-option-d.md` | Update PR8 section to reflect actual landed scope. |

---

## Phase 1: Alembic migration

**Files:**
- Create: `backend/alembic/versions/2026_05_18_0022_pr8_live_cooldowns.py`
- Create: `backend/tests/db/test_pr8_migration.py`
- Create: `backend/tests/db/test_pr8_migration_downgrade.py`

- [ ] **Step 1.1: Write the failing migration test** (`tests/db/test_pr8_migration.py`)

```python
"""PR8 migration: live_cooldowns table introspection (Postgres-only)."""
from __future__ import annotations

import os
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def _is_postgres() -> bool:
    url = os.environ.get("DATABASE_URL", "")
    return url.startswith(("postgresql", "postgres"))


pytestmark = pytest.mark.skipif(
    not _is_postgres(), reason="PR8 schema is Postgres-only"
)


@pytest.mark.asyncio
async def test_live_cooldowns_table_exists() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        rows = await conn.execute(sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'live_cooldowns'"
        ))
        assert rows.first() is not None


@pytest.mark.asyncio
async def test_live_cooldowns_pk_composite() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        rows = (await conn.execute(sa.text("""
            SELECT a.attname FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid
                              AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'live_cooldowns'::regclass AND i.indisprimary
            ORDER BY a.attname
        """))).all()
        cols = {r.attname for r in rows}
        assert cols == {"user_id", "symbol"}


@pytest.mark.asyncio
async def test_live_cooldowns_active_partial_index() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        rows = (await conn.execute(sa.text("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'live_cooldowns'
              AND indexname = 'ix_live_cooldowns_active'
        """))).all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_live_cooldowns_columns() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        rows = (await conn.execute(sa.text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'live_cooldowns'
        """))).all()
        cols = {r.column_name: (r.data_type, r.is_nullable) for r in rows}
        assert "cooldown_until" in cols
        assert "last_exit_reason" in cols
        assert "last_mtf_agreement" in cols
        assert cols["last_mtf_agreement"][1] == "YES"  # nullable
        assert "updated_at" in cols
```

- [ ] **Step 1.2: Run test, verify it fails** (live_cooldowns table doesn't exist yet)

```
cd backend && pytest tests/db/test_pr8_migration.py -v --no-cov
```
Expected: All 4 tests FAIL with relation does not exist OR alembic must have applied this migration (PostgresOnly skip if no DATABASE_URL).

- [ ] **Step 1.3: Write the migration** (`alembic/versions/2026_05_18_0022_pr8_live_cooldowns.py`)

```python
"""pr8 — live_cooldowns table

Revision ID: 0022_pr8_live_cooldowns
Revises: 0021_pr3_shadow_per_tf
Create Date: 2026-05-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0022_pr8_live_cooldowns"
down_revision = "0021_pr3_shadow_per_tf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_cooldowns",
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_exit_reason", sa.Text, nullable=False),
        sa.Column("last_mtf_agreement", sa.SmallInteger, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("user_id", "symbol"),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_live_cooldowns_active "
            "ON live_cooldowns (cooldown_until) "
            "WHERE cooldown_until > NOW()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_live_cooldowns_active")
    op.drop_table("live_cooldowns")
```

- [ ] **Step 1.4: Apply migration + re-run tests**

```
cd backend && alembic upgrade head && pytest tests/db/test_pr8_migration.py -v --no-cov
```
Expected: All 4 tests PASS.

- [ ] **Step 1.5: Write downgrade round-trip test** (`tests/db/test_pr8_migration_downgrade.py`)

```python
"""PR8 downgrade round-trip — FU-10 anticipation."""
from __future__ import annotations

import os
import pytest
from alembic import command
from alembic.config import Config


def _is_postgres() -> bool:
    return os.environ.get("DATABASE_URL", "").startswith(("postgresql", "postgres"))


pytestmark = pytest.mark.skipif(
    not _is_postgres(), reason="PR8 downgrade is Postgres-only"
)


def _alembic_cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"].replace(
        "postgresql+asyncpg", "postgresql+psycopg2"
    ))
    return cfg


def test_pr8_downgrade_round_trip() -> None:
    cfg = _alembic_cfg()
    command.downgrade(cfg, "0021_pr3_shadow_per_tf")
    command.upgrade(cfg, "0022_pr8_live_cooldowns")
    # Re-run forward — assert table exists again
    import sqlalchemy as sa
    eng = sa.create_engine(os.environ["DATABASE_URL"].replace(
        "postgresql+asyncpg", "postgresql+psycopg2"
    ))
    with eng.connect() as conn:
        assert conn.execute(sa.text(
            "SELECT to_regclass('live_cooldowns')"
        )).scalar() is not None
```

- [ ] **Step 1.6: Run downgrade test**

```
cd backend && pytest tests/db/test_pr8_migration_downgrade.py -v --no-cov
```
Expected: PASS.

- [ ] **Step 1.7: Commit**

```bash
git add backend/alembic/versions/2026_05_18_0022_pr8_live_cooldowns.py \
        backend/tests/db/test_pr8_migration.py \
        backend/tests/db/test_pr8_migration_downgrade.py
git commit -m "feat(pr8): alembic — live_cooldowns table + active-only index (Phase 1)"
```

---

## Phase 2: Settings + ExitReason enum

**Files:**
- Modify: `backend/app/config.py`
- Create: `backend/app/db/live_exit_reasons.py`
- Create: `backend/tests/unit/test_pr8_settings_defaults.py`

- [ ] **Step 2.1: Write the failing settings test** (`tests/unit/test_pr8_settings_defaults.py`)

```python
"""PR8 settings defaults — all default-OFF for prod safety."""
from __future__ import annotations

from app.config import get_settings


def test_live_cooldown_enabled_default_false() -> None:
    s = get_settings()
    assert s.LIVE_COOLDOWN_ENABLED is False


def test_live_cooldown_hours_by_outcome_defaults() -> None:
    s = get_settings()
    table = s.LIVE_COOLDOWN_HOURS_BY_OUTCOME
    assert table["stop_loss"] == 8.0
    assert table["take_profit"] == 1.0
    assert table["timeout"] == 4.0
    assert table["manual_close"] == 0.0
    assert table["external_close"] == 0.0
    assert table["liquidation_buffer_breach"] == 24.0


def test_live_cooldown_sl_requires_fresh_mtf_default_true() -> None:
    s = get_settings()
    assert s.LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF is True


def test_live_cooldown_regime_aware_default_false() -> None:
    s = get_settings()
    assert s.LIVE_COOLDOWN_REGIME_AWARE is False
```

- [ ] **Step 2.2: Run test, verify it fails**

```
cd backend && pytest tests/unit/test_pr8_settings_defaults.py -v --no-cov
```
Expected: FAIL with AttributeError on missing settings.

- [ ] **Step 2.3: Add settings to `app/config.py`**

In `Settings(BaseSettings)`:

```python
# --- PR8 outcome-adaptive cooldown -------------------------------------
# Default-OFF for prod safety. Operator flips per env once soak verifies.
LIVE_COOLDOWN_ENABLED: bool = False

# Per-outcome cooldown duration (hours).
LIVE_COOLDOWN_HOURS_BY_OUTCOME: dict[str, float] = Field(
    default_factory=lambda: {
        "stop_loss": 8.0,
        "take_profit": 1.0,
        "timeout": 4.0,
        "manual_close": 0.0,
        "external_close": 0.0,
        "liquidation_buffer_breach": 24.0,
    }
)

# After SL: require strictly-greater mtf_agreement on the new signal to
# clear the cooldown (even after calendar time elapsed).
LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF: bool = True

# Forward-compat hook for regime-aware classifier (no detector exists yet).
LIVE_COOLDOWN_REGIME_AWARE: bool = False
```

- [ ] **Step 2.4: Re-run test**

```
cd backend && pytest tests/unit/test_pr8_settings_defaults.py -v --no-cov
```
Expected: PASS.

- [ ] **Step 2.5: Create the ExitReason enum** (`app/db/live_exit_reasons.py`)

```python
"""Live trade exit-reason enum — PR8.

Persisted as TEXT to live_trades.exit_reason. Any other value is an
invariant violation (caught in tests).
"""
from __future__ import annotations

from enum import StrEnum


class LiveExitReason(StrEnum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TIMEOUT = "timeout"
    MANUAL_CLOSE = "manual_close"
    EXTERNAL_CLOSE = "external_close"
    LIQUIDATION_BUFFER_BREACH = "liquidation_buffer_breach"
```

- [ ] **Step 2.6: Commit**

```bash
git add backend/app/config.py backend/app/db/live_exit_reasons.py \
        backend/tests/unit/test_pr8_settings_defaults.py
git commit -m "feat(pr8): 4 settings + LiveExitReason enum (Phase 2)"
```

---

## Phase 3: Pure-function cooldown compute

**Files:**
- Create: `backend/app/trading/cooldown_compute.py`
- Create: `backend/tests/unit/test_cooldown_compute.py`

- [ ] **Step 3.1: Write the failing tests** (`tests/unit/test_cooldown_compute.py`)

```python
"""PR8 cooldown_compute — pure-function tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.trading.cooldown_compute import (
    compute_cooldown_duration, is_cooldown_blocked,
)


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _settings(
    enabled: bool = True, sl_fresh_mtf: bool = True,
    table: dict[str, float] | None = None,
):
    return SimpleNamespace(
        LIVE_COOLDOWN_ENABLED=enabled,
        LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF=sl_fresh_mtf,
        LIVE_COOLDOWN_HOURS_BY_OUTCOME=table or {
            "stop_loss": 8.0, "take_profit": 1.0, "timeout": 4.0,
            "manual_close": 0.0, "external_close": 0.0,
            "liquidation_buffer_breach": 24.0,
        },
    )


def test_compute_cooldown_duration_stop_loss() -> None:
    assert compute_cooldown_duration("stop_loss", _settings()) == timedelta(hours=8)


def test_compute_cooldown_duration_take_profit() -> None:
    assert compute_cooldown_duration("take_profit", _settings()) == timedelta(hours=1)


def test_compute_cooldown_duration_unknown_outcome_falls_back_to_timeout() -> None:
    """Defensive: unknown outcome strings get the 4h timeout default."""
    assert compute_cooldown_duration("future_outcome_we_havent_named", _settings()) == timedelta(hours=4)


def test_compute_cooldown_duration_zero_outcomes_yield_zero() -> None:
    assert compute_cooldown_duration("manual_close", _settings()) == timedelta(0)
    assert compute_cooldown_duration("external_close", _settings()) == timedelta(0)


# ---------- is_cooldown_blocked matrix --------------------------------


def test_blocked_when_gate_disabled_returns_false() -> None:
    row = SimpleNamespace(
        cooldown_until=_NOW + timedelta(hours=2),
        last_exit_reason="stop_loss", last_mtf_agreement=3,
    )
    blocked, reason = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=5, settings=_settings(enabled=False),
    )
    assert not blocked
    assert reason == "cooldown_disabled"


def test_blocked_when_no_row_returns_false() -> None:
    blocked, _ = is_cooldown_blocked(
        now=_NOW, cooldown_row=None,
        new_mtf_agreement=5, settings=_settings(),
    )
    assert not blocked


def test_blocked_when_calendar_active() -> None:
    row = SimpleNamespace(
        cooldown_until=_NOW + timedelta(hours=2),
        last_exit_reason="stop_loss", last_mtf_agreement=3,
    )
    blocked, reason = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=5, settings=_settings(),
    )
    assert blocked
    assert reason.startswith("calendar_until_")


def test_blocked_after_sl_with_stale_mtf() -> None:
    """Calendar expired but SL + stale-or-equal MTF still blocks."""
    row = SimpleNamespace(
        cooldown_until=_NOW - timedelta(hours=1),
        last_exit_reason="stop_loss", last_mtf_agreement=4,
    )
    blocked, reason = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=4, settings=_settings(),  # SAME mtf
    )
    assert blocked
    assert "sl_stale_mtf" in reason


def test_cleared_after_sl_with_fresh_mtf() -> None:
    row = SimpleNamespace(
        cooldown_until=_NOW - timedelta(hours=1),
        last_exit_reason="stop_loss", last_mtf_agreement=4,
    )
    blocked, reason = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=5, settings=_settings(),  # HIGHER mtf
    )
    assert not blocked
    assert reason == "cleared"


def test_cleared_after_tp_calendar_expired() -> None:
    """TP doesn't require fresh MTF — calendar alone gates."""
    row = SimpleNamespace(
        cooldown_until=_NOW - timedelta(minutes=1),
        last_exit_reason="take_profit", last_mtf_agreement=4,
    )
    blocked, reason = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=3, settings=_settings(),  # LOWER mtf, doesn't matter
    )
    assert not blocked


def test_sl_fresh_mtf_disabled_via_flag() -> None:
    """LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF=False — calendar alone gates."""
    row = SimpleNamespace(
        cooldown_until=_NOW - timedelta(hours=1),
        last_exit_reason="stop_loss", last_mtf_agreement=4,
    )
    blocked, _ = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=4, settings=_settings(sl_fresh_mtf=False),
    )
    assert not blocked
```

- [ ] **Step 3.2: Run tests, verify they fail** (module doesn't exist yet)

```
cd backend && pytest tests/unit/test_cooldown_compute.py -v --no-cov
```
Expected: ImportError.

- [ ] **Step 3.3: Implement** (`app/trading/cooldown_compute.py`)

```python
"""PR8 pure-function cooldown logic. No DB access here.

`compute_cooldown_duration` and `is_cooldown_blocked` are testable in
isolation — the DB-touching path lives in cooldown_gate.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol


_DEFAULT_TIMEOUT_HOURS = 4.0  # fall-back for unrecognized exit reasons


class _SettingsProto(Protocol):
    LIVE_COOLDOWN_ENABLED: bool
    LIVE_COOLDOWN_HOURS_BY_OUTCOME: dict[str, float]
    LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF: bool


class _CooldownRowProto(Protocol):
    cooldown_until: datetime
    last_exit_reason: str
    last_mtf_agreement: int | None


def compute_cooldown_duration(
    exit_reason: str, settings: _SettingsProto,
) -> timedelta:
    """Look up the configured duration for an outcome.

    Falls back to the 'timeout' baseline when the outcome string isn't
    in the dict — defensive against future enum extensions that ship
    before the config dict is updated.
    """
    hours = settings.LIVE_COOLDOWN_HOURS_BY_OUTCOME.get(
        exit_reason, _DEFAULT_TIMEOUT_HOURS,
    )
    return timedelta(hours=hours)


def is_cooldown_blocked(
    *, now: datetime, cooldown_row: _CooldownRowProto | None,
    new_mtf_agreement: int | None, settings: _SettingsProto,
) -> tuple[bool, str]:
    """Decide whether the dispatcher should block this signal on cooldown.

    Returns (blocked, reason_tag). reason_tag is logged for ops; it is
    NOT user-facing.
    """
    if not settings.LIVE_COOLDOWN_ENABLED:
        return False, "cooldown_disabled"
    if cooldown_row is None:
        return False, "no_cooldown"
    if now < cooldown_row.cooldown_until:
        return True, f"calendar_until_{cooldown_row.cooldown_until.isoformat()}"
    # Calendar expired — check the SL fresh-MTF override.
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

- [ ] **Step 3.4: Re-run tests**

```
cd backend && pytest tests/unit/test_cooldown_compute.py -v --no-cov
```
Expected: All 9 PASS.

- [ ] **Step 3.5: Commit**

```bash
git add backend/app/trading/cooldown_compute.py backend/tests/unit/test_cooldown_compute.py
git commit -m "feat(pr8): cooldown_compute pure-function logic (Phase 3)"
```

---

## Phase 4: Persistence layer (live_cooldowns)

**Files:**
- Create: `backend/app/db/live_cooldowns.py`
- Create: `backend/tests/db/test_live_cooldowns_persistence.py`

- [ ] **Step 4.1: Write the failing tests** (`tests/db/test_live_cooldowns_persistence.py`)

Tests use in-memory SQLite with a CREATE TABLE shim (no migration loading) to test the persistence helpers.

```python
"""PR8 live_cooldowns persistence — upsert + load + delete."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.live_cooldowns import (
    delete_cooldown, load_cooldown, upsert_cooldown,
)


async def _mk_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE live_cooldowns ("
            "user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, "
            "cooldown_until TEXT NOT NULL, "
            "last_exit_reason TEXT NOT NULL, "
            "last_mtf_agreement INTEGER, "
            "updated_at TEXT NOT NULL, "
            "PRIMARY KEY (user_id, symbol))"
        ))
    return engine


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_upsert_insert_then_read_back() -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await upsert_cooldown(
            s, user_id=1, symbol="BTCUSDT",
            cooldown_until=_NOW + timedelta(hours=8),
            last_exit_reason="stop_loss", last_mtf_agreement=4,
        )
        await s.commit()
    async with factory() as s:
        row = await load_cooldown(s, user_id=1, symbol="BTCUSDT")
    assert row is not None
    assert row.last_exit_reason == "stop_loss"
    assert row.last_mtf_agreement == 4


@pytest.mark.asyncio
async def test_upsert_updates_existing_row() -> None:
    """Same PK → UPSERT overwrites the cooldown_until + last_exit_reason."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await upsert_cooldown(
            s, user_id=1, symbol="BTCUSDT",
            cooldown_until=_NOW + timedelta(hours=8),
            last_exit_reason="stop_loss", last_mtf_agreement=3,
        )
        await s.commit()
    async with factory() as s:
        await upsert_cooldown(
            s, user_id=1, symbol="BTCUSDT",
            cooldown_until=_NOW + timedelta(hours=1),  # shorter
            last_exit_reason="take_profit", last_mtf_agreement=5,
        )
        await s.commit()
    async with factory() as s:
        row = await load_cooldown(s, user_id=1, symbol="BTCUSDT")
    assert row.last_exit_reason == "take_profit"
    assert row.last_mtf_agreement == 5


@pytest.mark.asyncio
async def test_load_cooldown_missing_returns_none() -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        assert await load_cooldown(s, user_id=1, symbol="ETHUSDT") is None


@pytest.mark.asyncio
async def test_delete_cooldown() -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await upsert_cooldown(
            s, user_id=1, symbol="BTCUSDT",
            cooldown_until=_NOW + timedelta(hours=8),
            last_exit_reason="stop_loss", last_mtf_agreement=4,
        )
        await s.commit()
    async with factory() as s:
        await delete_cooldown(s, user_id=1, symbol="BTCUSDT")
        await s.commit()
    async with factory() as s:
        assert await load_cooldown(s, user_id=1, symbol="BTCUSDT") is None
```

- [ ] **Step 4.2: Run tests, verify they fail** (module doesn't exist)

- [ ] **Step 4.3: Implement** (`app/db/live_cooldowns.py`)

```python
"""PR8 live_cooldowns persistence — UPSERT / LOAD / DELETE.

Mirrors shadow.persistence patterns. Postgres uses ON CONFLICT;
SQLite uses INSERT OR REPLACE (test-only path).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class LiveCooldown:
    user_id: int
    symbol: str
    cooldown_until: datetime
    last_exit_reason: str
    last_mtf_agreement: int | None
    updated_at: datetime


def _is_pg(s: AsyncSession) -> bool:
    return s.bind.dialect.name == "postgresql"


async def upsert_cooldown(
    session: AsyncSession, *, user_id: int, symbol: str,
    cooldown_until: datetime, last_exit_reason: str,
    last_mtf_agreement: int | None,
) -> None:
    now = datetime.now(tz=cooldown_until.tzinfo)
    if _is_pg(session):
        sql = sa.text("""
            INSERT INTO live_cooldowns
                (user_id, symbol, cooldown_until, last_exit_reason,
                 last_mtf_agreement, updated_at)
            VALUES (:uid, :sym, :cu, :reason, :mtf, :upd)
            ON CONFLICT (user_id, symbol)
            DO UPDATE SET
                cooldown_until = EXCLUDED.cooldown_until,
                last_exit_reason = EXCLUDED.last_exit_reason,
                last_mtf_agreement = EXCLUDED.last_mtf_agreement,
                updated_at = EXCLUDED.updated_at
        """)
    else:
        sql = sa.text("""
            INSERT OR REPLACE INTO live_cooldowns
                (user_id, symbol, cooldown_until, last_exit_reason,
                 last_mtf_agreement, updated_at)
            VALUES (:uid, :sym, :cu, :reason, :mtf, :upd)
        """)
    await session.execute(sql, {
        "uid": user_id, "sym": symbol, "cu": cooldown_until,
        "reason": last_exit_reason, "mtf": last_mtf_agreement,
        "upd": now,
    })


async def load_cooldown(
    session: AsyncSession, *, user_id: int, symbol: str,
) -> LiveCooldown | None:
    row = (await session.execute(sa.text(
        "SELECT user_id, symbol, cooldown_until, last_exit_reason, "
        "       last_mtf_agreement, updated_at "
        "FROM live_cooldowns WHERE user_id = :uid AND symbol = :sym"
    ), {"uid": user_id, "sym": symbol})).first()
    if row is None:
        return None
    return LiveCooldown(
        user_id=row.user_id, symbol=row.symbol,
        cooldown_until=_to_aware(row.cooldown_until),
        last_exit_reason=row.last_exit_reason,
        last_mtf_agreement=row.last_mtf_agreement,
        updated_at=_to_aware(row.updated_at),
    )


async def delete_cooldown(
    session: AsyncSession, *, user_id: int, symbol: str,
) -> None:
    await session.execute(sa.text(
        "DELETE FROM live_cooldowns WHERE user_id = :uid AND symbol = :sym"
    ), {"uid": user_id, "sym": symbol})


def _to_aware(v: Any) -> datetime:
    """SQLite stringifies datetimes; Postgres returns naive UTC. Normalize."""
    if isinstance(v, str):
        return datetime.fromisoformat(v)
    return v
```

- [ ] **Step 4.4: Re-run tests**

Expected: All 4 PASS.

- [ ] **Step 4.5: Commit**

```bash
git add backend/app/db/live_cooldowns.py backend/tests/db/test_live_cooldowns_persistence.py
git commit -m "feat(pr8): live_cooldowns persistence layer (Phase 4)"
```

---

## Phase 5: Dispatcher cooldown gate

**Files:**
- Create: `backend/app/trading/execution/cooldown_gate.py`
- Modify: `backend/app/trading/execution/dispatcher.py`
- Create: `backend/tests/trading/test_cooldown_gate.py`

- [ ] **Step 5.1: Write the failing gate tests** (`tests/trading/test_cooldown_gate.py`)

```python
"""PR8 cooldown gate — dispatcher pre-conditions integration."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.trading.execution.cooldown_gate import _apply_cooldown_gate


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _proposal(symbol: str = "BTCUSDT", mtf: int | None = 5):
    return MagicMock(symbol=symbol, user_id=1, mtf_agreement=mtf)


def _settings(enabled: bool = True):
    return MagicMock(
        LIVE_COOLDOWN_ENABLED=enabled,
        LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF=True,
        LIVE_COOLDOWN_HOURS_BY_OUTCOME={
            "stop_loss": 8.0, "take_profit": 1.0, "timeout": 4.0,
            "manual_close": 0.0, "external_close": 0.0,
            "liquidation_buffer_breach": 24.0,
        },
    )


@pytest.mark.asyncio
async def test_gate_disabled_returns_none() -> None:
    session = MagicMock()
    result = await _apply_cooldown_gate(
        proposal=_proposal(), session=session, settings=_settings(enabled=False),
        now_fn=lambda: _NOW,
    )
    assert result is None


@pytest.mark.asyncio
async def test_gate_no_cooldown_row_returns_none() -> None:
    session = MagicMock()
    with patch("app.trading.execution.cooldown_gate.load_cooldown",
               new=AsyncMock(return_value=None)):
        result = await _apply_cooldown_gate(
            proposal=_proposal(), session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is None


@pytest.mark.asyncio
async def test_gate_calendar_active_blocks() -> None:
    row = MagicMock(
        cooldown_until=_NOW + timedelta(hours=2),
        last_exit_reason="stop_loss", last_mtf_agreement=3,
    )
    session = MagicMock()
    with patch("app.trading.execution.cooldown_gate.load_cooldown",
               new=AsyncMock(return_value=row)):
        result = await _apply_cooldown_gate(
            proposal=_proposal(mtf=5), session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is not None
    assert result.outcome == "blocked_cooldown"


@pytest.mark.asyncio
async def test_gate_fails_open_on_db_error() -> None:
    """A DB read failure must NOT block trading — fail-open contract."""
    session = MagicMock()
    with patch("app.trading.execution.cooldown_gate.load_cooldown",
               new=AsyncMock(side_effect=RuntimeError("db blip"))):
        result = await _apply_cooldown_gate(
            proposal=_proposal(), session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is None  # fail-open
```

- [ ] **Step 5.2: Run tests, verify they fail**

- [ ] **Step 5.3: Implement** (`app/trading/execution/cooldown_gate.py`)

```python
"""PR8 cooldown gate — dispatcher pre-condition.

Reads live_cooldowns for (user_id, symbol). Emits
DispatchResult(blocked_cooldown) if the cooldown is active.

**Fail-open contract:** any error from the DB read path is logged and
returns None (let the trade proceed). A broken cooldown gate that errors
to-blocked would shut down trading indefinitely on a single DB blip.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.live_cooldowns import load_cooldown
from app.trading.cooldown_compute import is_cooldown_blocked
from app.trading.execution.dispatcher import DispatchResult, SignalProposal


log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _apply_cooldown_gate(
    *, proposal: SignalProposal, session: AsyncSession, settings,
    now_fn: Callable[[], datetime] = _utc_now,
) -> DispatchResult | None:
    """Return DispatchResult to block; None to let the trade proceed.

    None on: gate disabled, no cooldown row, calendar expired + fresh
    MTF (if applicable), OR any DB error (fail-open).
    """
    try:
        row = await load_cooldown(
            session, user_id=proposal.user_id, symbol=proposal.symbol,
        )
    except Exception as e:  # noqa: BLE001
        log.warning(
            "cooldown_gate DB read failed for %s/%s; failing open: %s",
            proposal.user_id, proposal.symbol, e,
        )
        return None

    blocked, reason = is_cooldown_blocked(
        now=now_fn(), cooldown_row=row,
        new_mtf_agreement=getattr(proposal, "mtf_agreement", None),
        settings=settings,
    )
    if not blocked:
        return None
    return DispatchResult(
        outcome="blocked_cooldown",
        detail=f"cooldown: {reason}",
    )
```

- [ ] **Step 5.4: Re-run tests**

Expected: All 4 PASS.

- [ ] **Step 5.5: Wire into dispatcher.py pre-conditions**

In `dispatcher.dispatch()` pre-conditions block (around line 615-650): add the cooldown gate call BETWEEN funding and MTF gates (cheapest check first).

```python
# Existing funding check ...
if funding_blocked:
    return DispatchResult(outcome="blocked_funding", ...)

# PR8: cooldown gate (cheapest DB check, run early)
cooldown_block = await _apply_cooldown_gate(
    proposal=proposal, session=session, settings=settings,
)
if cooldown_block is not None:
    return cooldown_block

# Existing MTF gate ...
mtf_block = _apply_mtf_gate(proposal, settings)
```

- [ ] **Step 5.6: Run dispatcher tests + new gate tests**

```
cd backend && pytest tests/trading/ -v --no-cov
```
Expected: existing dispatcher tests still pass + 4 new gate tests pass.

- [ ] **Step 5.7: Commit**

```bash
git add backend/app/trading/execution/cooldown_gate.py \
        backend/app/trading/execution/dispatcher.py \
        backend/tests/trading/test_cooldown_gate.py
git commit -m "feat(pr8): wire _apply_cooldown_gate in dispatcher pre-conditions (Phase 5)"
```

---

## Phase 6: live_exit_monitor worker

**Files:**
- Create: `backend/app/trading/execution/live_exit_monitor.py`
- Create: `backend/tests/trading/test_live_exit_monitor.py`
- Modify: `backend/app/trading/execution/liquidation_monitor.py`
- Create: `backend/tests/integration/test_pr8_liquidation_buffer_path.py`

- [ ] **Step 6.1: Write the failing classifier tests**

```python
"""PR8 live_exit_monitor — outcome classification + write-back."""
# Test cases:
# - test_classify_tp_when_price_at_or_above_take_profit_long
# - test_classify_sl_when_price_at_or_below_stop_loss_long
# - test_classify_timeout_when_position_age_exceeds_baseline
# - test_classify_external_close_when_binance_returns_no_position
# - test_close_writes_exit_reason_to_live_trades
# - test_close_upserts_cooldown_with_correct_duration
# - test_close_reads_mtf_agreement_from_live_trades_row
# - test_monitor_heartbeat_fires_per_poll
```

(Full code per spec §4.1 + 6.2; see commit 5.4 pattern.)

- [ ] **Step 6.2: Run tests, verify they fail**

- [ ] **Step 6.3: Implement** (`app/trading/execution/live_exit_monitor.py`)

Structure:
- `class LiveExitMonitor: async def run()` — 30s loop
- Per iteration: list open `live_trades`, fetch Binance position for each, classify exit, on close path: write `exit_reason` + upsert `live_cooldowns` + emit `record_heartbeat("live_exit_monitor", status="ok", details={...})`
- Outcome classification: TP if `pos.entry_dir == LONG and current_price >= pos.take_profit` (and mirror for SHORT); SL similarly; TIMEOUT if `now - opened_at > settings.LIVE_HOLD_TIMEOUT_HOURS`; EXTERNAL_CLOSE if `binance.get_position` returns None for an open `live_trades` row

- [ ] **Step 6.4: Re-run classifier tests** — all PASS

- [ ] **Step 6.5: Modify `liquidation_monitor.py` auto-close path**

Add: when `buffer < _AUTO_CLOSE_BUFFER_PCT` AND `binance.close_position` succeeds:
```python
await session.execute(sa.text(
    "UPDATE live_trades SET exit_reason = :r, closed_at = :ts WHERE id = :id"
), {"r": "liquidation_buffer_breach", "ts": _utc_now(), "id": pos.trade_id})
await upsert_cooldown(
    session, user_id=pos.user_id, symbol=pos.symbol,
    cooldown_until=_utc_now() + compute_cooldown_duration(
        "liquidation_buffer_breach", settings,
    ),
    last_exit_reason="liquidation_buffer_breach",
    last_mtf_agreement=pos.mtf_agreement,  # from live_trades row
)
```

- [ ] **Step 6.6: Write + pass liquidation buffer integration test**

- [ ] **Step 6.7: Commit**

```bash
git add backend/app/trading/execution/live_exit_monitor.py \
        backend/app/trading/execution/liquidation_monitor.py \
        backend/tests/trading/test_live_exit_monitor.py \
        backend/tests/integration/test_pr8_liquidation_buffer_path.py
git commit -m "feat(pr8): live_exit_monitor + liquidation buffer cooldown wiring (Phase 6)"
```

---

## Phase 7: Worker registry + main.py wiring

**Files:**
- Modify: `backend/app/ops/worker_registry.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/ops/test_live_exit_monitor_registry.py`

- [ ] **Step 7.1: Write the failing registry test**

```python
def test_live_exit_monitor_registered_with_2min_staleness() -> None:
    from app.ops.worker_registry import by_name
    spec = by_name("live_exit_monitor")
    assert spec is not None
    assert spec.max_staleness_seconds == 2 * 60  # 30s poll + 1.5x slack
    assert spec.pending_heartbeat is False
```

- [ ] **Step 7.2: Add registry entry** in `app/ops/worker_registry.py`:

```python
WorkerSpec(
    name="live_exit_monitor",
    description="30s poll of open live_trades; writes exit_reason + cooldown on close (PR8)",
    max_staleness_seconds=2 * 60,
    pending_heartbeat=False,
),
```

- [ ] **Step 7.3: Wire startup task** in `app/main.py` (next to liquidation_monitor):

```python
asyncio.create_task(LiveExitMonitor(...).run())
```

- [ ] **Step 7.4: Test pass + commit**

```bash
git commit -m "feat(pr8): register live_exit_monitor + main.py spawn (Phase 7)"
```

---

## Phase 8: /cooldowns API endpoint

**Files:**
- Modify: `backend/app/api/routes/bot_status.py`
- Modify: `backend/app/api/schemas.py`
- Create: `backend/tests/api/test_cooldowns_endpoint.py`

- [ ] **Step 8.1: Write the failing endpoint test**

```python
def test_cooldowns_endpoint_returns_active_cooldowns() -> None:
    # Seed 2 cooldown rows: one active, one expired
    # Hit /bot-status/cooldowns
    # Assert only active row in response + correct schema
```

- [ ] **Step 8.2: Add `LiveCooldownOut` schema**

```python
class LiveCooldownOut(BaseModel):
    user_id: int
    symbol: str
    cooldown_until: datetime
    last_exit_reason: str
    last_mtf_agreement: int | None
    blocked_until_fresh_mtf: bool  # computed: SL + flag on
```

- [ ] **Step 8.3: Add `/cooldowns` route** (parametrized SQL, no string interpolation)

- [ ] **Step 8.4: Test pass + commit**

---

## Phase 9: V-7 latency bench

**Files:**
- Create: `backend/scripts/bench_dispatcher_preconditions.py`
- Create: `backend/tests/scripts/test_bench_dispatcher_preconditions.py`

- [ ] **Step 9.1: Write the bench script** (N=200 dispatches, gate-off vs gate-on, measure Δp50/Δp99)

- [ ] **Step 9.2: Run locally, confirm Δp50 ≤ 5ms, Δp99 ≤ 20ms**

- [ ] **Step 9.3: Test pass + commit**

---

## Phase 10: docs/ARCHITECTURE.md + master rollout doc

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/superpowers/specs/2026-05-17-master-rollout-plan-option-d.md`

- [ ] **Step 10.1: Add §12 to ARCHITECTURE.md** — Outcome-adaptive cooldown surface

- [ ] **Step 10.2: Update master rollout PR8 section** — replace "Spec to be drafted after PR3" with actual landed scope summary

- [ ] **Step 10.3: Commit**

```bash
git commit -m "docs(arch): section 12 — PR8 outcome-adaptive cooldown (Phase 10)"
```

---

## Self-review checklist (run before opening PR)

- [ ] All 17+ new test files pass; lint + mypy clean
- [ ] V-7 bench gate passes (Δp50 ≤ 5ms, Δp99 ≤ 20ms)
- [ ] Default-OFF in prod (LIVE_COOLDOWN_ENABLED=False) — verified by running test suite with default config and observing all dispatcher pre-conditions tests still pass with the new gate present but gated off
- [ ] Audit chain `NON_HASHED_ALLOW_LIST` covers `live_trades.exit_reason` (it should — column existed pre-PR8)
- [ ] Migration applies cleanly + downgrade round-trips
- [ ] No regression in existing dispatcher tests
- [ ] Architecture doc §12 published
- [ ] Master rollout doc updated

---

## Execution handoff

Plan complete. Execution: **Subagent-Driven Development** (per operator's continuous-rollout authorization). Fresh subagent per phase + spec compliance reviewer + code quality reviewer per phase.
