# PR10 — Symbol allowlist + stablecoin filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an automatic per-symbol allowlist (rolling Sharpe + grace window) + dispatcher-side stablecoin filter. Both gated by a single `SYMBOL_ALLOWLIST_ENABLED` flag (default-OFF) so deploy is bit-identical to pre-PR10.

**Architecture:** New 8th hash-chained table `symbol_performance_snapshots` written daily by a new background worker. Pure-function helpers (`_parse_base_asset`, `is_stablecoin_pair`, `is_symbol_allowed`) feed a `_apply_symbol_allowlist_gate` dispatcher pre-condition. Process-local cache (TTL 1h) on the hot path. Two distinct outcome literals (`blocked_stablecoin` / `blocked_low_sharpe`) matching PR2's pattern. Fail-open on DB error.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 async / Pydantic v2 BaseSettings / Alembic / pytest + pytest-asyncio.

**Source spec:** [`docs/superpowers/specs/2026-05-19-pr10-symbol-allowlist-and-stablecoin-filter-design.md`](../specs/2026-05-19-pr10-symbol-allowlist-and-stablecoin-filter-design.md)

**Branch:** `feat/pr10-impl-symbol-allowlist-stablecoin-filter` off `dev`.

**Behavior change:** NO at deploy. Default-OFF flag. Operator flips after observing allowlist via `/symbol-allowlist` endpoint.

---

## File Structure (locked in via spec §4)

### NEW files

| Path | Responsibility |
|---|---|
| `backend/alembic/versions/2026_05_19_0024_pr10_symbol_performance_snapshots.py` | Create table + index. Register `symbol_performance_snapshots` as 8th chained table. |
| `backend/app/trading/symbol_allowlist.py` | Pure helpers: `_parse_base_asset`, `is_stablecoin_pair`, `is_symbol_allowed`, `compute_per_symbol_stats`, `_AllowlistCache`. |
| `backend/app/trading/execution/symbol_allowlist_gate.py` | `_apply_symbol_allowlist_gate(proposal, user_id, session, settings, cache) -> DispatchResult \| None`. |
| `backend/app/db/symbol_performance_snapshots.py` | `SymbolSnapshot` dataclass; `load_latest_snapshots_per_symbol`; `insert_snapshot_row` via `insert_with_chain`. |
| `backend/app/workers/symbol_allowlist_refresh.py` | Daily worker loop. |
| `backend/tests/db/test_pr10_migration.py` | Postgres introspection. |
| `backend/tests/db/test_pr10_migration_downgrade.py` | Round-trip upgrade → downgrade → upgrade → head. |
| `backend/tests/unit/test_pr10_settings_defaults.py` | All 6 settings defaults. |
| `backend/tests/unit/test_symbol_allowlist.py` | Pure-function tests for `_parse_base_asset`, `is_stablecoin_pair`, `is_symbol_allowed`, cache freshness. |
| `backend/tests/unit/test_compute_per_symbol_stats.py` | Aggregation logic. |
| `backend/tests/db/test_symbol_performance_snapshots_persistence.py` | Round-trip insert + load. |
| `backend/tests/trading/test_symbol_allowlist_gate.py` | Dispatcher gate matrix. |
| `backend/tests/integration/test_pr10_dispatcher_e2e.py` | E2E excluded/allowed/stablecoin. |
| `backend/tests/integration/test_pr10_allowlist_endpoint.py` | API endpoint tests. |
| `backend/tests/workers/test_symbol_allowlist_refresh.py` | Worker writes + heartbeats. |
| `backend/scripts/bench_dispatcher_allowlist.py` | V-7 microbench. |

### MODIFIED files

| Path | Reason |
|---|---|
| `backend/app/config.py` | 6 settings: `SYMBOL_ALLOWLIST_ENABLED`, `SHADOW_STABLECOIN_EXCLUDE_LIST`, `SYMBOL_ALLOWLIST_GRACE_TRADES`, `SYMBOL_ALLOWLIST_WINDOW_TRADES`, `SYMBOL_ALLOWLIST_WINDOW_DAYS`, `SYMBOL_ALLOWLIST_CACHE_TTL_SECONDS`. |
| `backend/app/trading/execution/dispatcher.py` | Add `"blocked_stablecoin"` + `"blocked_low_sharpe"` literals to `DispatchOutcome`. Wire `_apply_symbol_allowlist_gate` FIRST in pre-conditions. |
| `backend/app/db/audit.py` | Add `"symbol_performance_snapshots": frozenset({...})` entry to `HASH_PAYLOAD_COLUMNS`. |
| `backend/app/api/routes/bot_status.py` | New `/symbol-allowlist` endpoint. |
| `backend/app/api/schemas.py` | `SymbolAllowlistOut` + `SymbolAllowlistResponseOut` schemas. |
| `backend/app/ops/worker_registry.py` | Register `symbol_allowlist_refresh`. |
| `backend/app/main.py` | Spawn `symbol_allowlist_refresh` task in lifespan (unconditional — not gated by AUTONOMOUS_TRADING). |
| `backend/tests/unit/test_worker_registry_consistency.py` | Add new worker to WORKER_SOURCE_MODULES map. |
| `docs/ARCHITECTURE.md` | New §11e — Symbol allowlist + stablecoin filter. |
| `docs/superpowers/specs/2026-05-17-master-rollout-plan-option-d.md` | Add strategic-replan PR10-PR19 section. |

---

## Phase 1: Alembic migration + audit chain registration

**Files:**
- Create: `backend/alembic/versions/2026_05_19_0024_pr10_symbol_performance_snapshots.py`
- Create: `backend/tests/db/test_pr10_migration.py`
- Create: `backend/tests/db/test_pr10_migration_downgrade.py`
- Modify: `backend/app/db/audit.py`

- [ ] **Step 1.1: Write failing migration introspection test**

```python
# backend/tests/db/test_pr10_migration.py
"""Migration tests for 0024_pr10_symbol_performance_snapshots."""
from __future__ import annotations

import os
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

_DSN = os.environ.get("DATABASE_URL", "")
_IS_PG = _DSN.startswith("postgresql")

pytestmark = pytest.mark.skipif(not _IS_PG, reason="Postgres-only")


@pytest.mark.asyncio
async def test_symbol_performance_snapshots_table_exists() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'symbol_performance_snapshots'"
        ))).all()
    assert len(rows) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_symbol_performance_snapshots_columns() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'symbol_performance_snapshots'"
        ))).all()
    cols = {r.column_name: r.is_nullable for r in rows}
    assert "id" in cols
    assert "symbol" in cols and cols["symbol"] == "NO"
    assert "window_start" in cols and cols["window_start"] == "NO"
    assert "window_end" in cols and cols["window_end"] == "NO"
    assert "trades_count" in cols and cols["trades_count"] == "NO"
    assert "win_rate" in cols and cols["win_rate"] == "YES"
    assert "sharpe" in cols and cols["sharpe"] == "YES"
    assert "allowed" in cols and cols["allowed"] == "NO"
    assert "computed_at" in cols and cols["computed_at"] == "NO"
    assert "prev_hash" in cols and cols["prev_hash"] == "NO"
    assert "row_hash" in cols and cols["row_hash"] == "NO"
    await engine.dispose()


@pytest.mark.asyncio
async def test_symbol_performance_snapshots_index() -> None:
    engine = create_async_engine(_DSN)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'symbol_performance_snapshots' "
            "AND indexname = 'ix_symbol_perf_symbol_computed'"
        ))).all()
    assert len(rows) == 1
    await engine.dispose()
```

- [ ] **Step 1.2: Run test, verify it fails**

```bash
cd backend && pytest tests/db/test_pr10_migration.py -v --no-cov
```
Expected: FAIL with relation does not exist.

- [ ] **Step 1.3: Write migration**

```python
# backend/alembic/versions/2026_05_19_0024_pr10_symbol_performance_snapshots.py
"""PR10: symbol_performance_snapshots — daily per-symbol Sharpe + allowlist snapshot.

8th hash-chained audit table. Append-only. Single writer (daily worker)
means FU-24's concurrent-insert race doesn't fire here.

Revision ID: 0024_pr10_symbol_performance_snapshots
Revises: 0023_pr9_users_balance_tier
Create Date: 2026-05-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_pr10_symbol_performance_snapshots"
down_revision: str | None = "0023_pr9_users_balance_tier"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "symbol_performance_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trades_count", sa.Integer, nullable=False),
        sa.Column("win_rate", sa.Float, nullable=True),
        sa.Column("sharpe", sa.Float, nullable=True),
        sa.Column("allowed", sa.Boolean, nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("prev_hash", sa.Text, nullable=False),
        sa.Column("row_hash", sa.Text, nullable=False, unique=True),
        sa.Column("inputs_hash", sa.Text, nullable=True),
        sa.CheckConstraint("trades_count >= 0", name="ck_trades_count_nonneg"),
    )
    op.create_index(
        "ix_symbol_perf_symbol_computed",
        "symbol_performance_snapshots",
        ["symbol", sa.text("computed_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_symbol_perf_symbol_computed",
        table_name="symbol_performance_snapshots",
    )
    op.drop_table("symbol_performance_snapshots")
```

- [ ] **Step 1.4: Apply migration + re-run tests**

```bash
cd backend && alembic upgrade head && pytest tests/db/test_pr10_migration.py -v --no-cov
```
Expected: all 3 PASS.

- [ ] **Step 1.5: Write downgrade round-trip test**

```python
# backend/tests/db/test_pr10_migration_downgrade.py
"""FU-10 anticipation: PR10 upgrade → downgrade → upgrade → head round-trip."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_DSN = os.environ.get("DATABASE_URL", "")
_IS_PG = _DSN.startswith("postgresql")

_REV = "0024_pr10_symbol_performance_snapshots"
_PRIOR = "0023_pr9_users_balance_tier"

_BACKEND_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(not _IS_PG, reason="Postgres-only")


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python", "-m", "alembic", *args],
        capture_output=True, text=True,
        env=os.environ.copy(), cwd=str(_BACKEND_DIR), check=False,
    )


def test_pr10_migration_round_trip() -> None:
    r = _alembic("upgrade", _REV)
    assert r.returncode == 0, f"upgrade failed: {r.stderr}"
    r = _alembic("downgrade", _PRIOR)
    assert r.returncode == 0, f"downgrade failed: {r.stderr}"
    r = _alembic("upgrade", "head")
    assert r.returncode == 0, f"final upgrade to head failed: {r.stderr}"


def test_pr10_downgrade_drops_table() -> None:
    import asyncio
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _check() -> None:
        r = _alembic("downgrade", _PRIOR)
        assert r.returncode == 0
        engine = create_async_engine(_DSN)
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(sa.text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'symbol_performance_snapshots'"
                ))).all()
                assert rows == []
        finally:
            await engine.dispose()
        r = _alembic("upgrade", "head")
        assert r.returncode == 0

    asyncio.run(_check())
```

- [ ] **Step 1.6: Add audit chain registration to `backend/app/db/audit.py`**

Find the `HASH_PAYLOAD_COLUMNS` dict (top of the file). Add:

```python
"symbol_performance_snapshots": frozenset({
    "symbol", "window_start", "window_end",
    "trades_count", "win_rate", "sharpe", "allowed",
    "computed_at",
}),
```

- [ ] **Step 1.7: Run downgrade tests**

```bash
cd backend && pytest tests/db/test_pr10_migration_downgrade.py -v --no-cov
```
Expected: 2 PASS.

- [ ] **Step 1.8: Commit**

```bash
git checkout -b feat/pr10-impl-symbol-allowlist-stablecoin-filter
git add backend/alembic/versions/2026_05_19_0024_pr10_symbol_performance_snapshots.py \
        backend/tests/db/test_pr10_migration.py \
        backend/tests/db/test_pr10_migration_downgrade.py \
        backend/app/db/audit.py
git commit -m "feat(pr10): alembic — symbol_performance_snapshots + audit chain registration (Phase 1)"
```

---

## Phase 2: 6 settings

**Files:**
- Modify: `backend/app/config.py`
- Create: `backend/tests/unit/test_pr10_settings_defaults.py`

- [ ] **Step 2.1: Write failing settings test**

```python
# backend/tests/unit/test_pr10_settings_defaults.py
"""PR10 settings defaults — flag default-OFF; rolling window 100/30; grace 50."""
from __future__ import annotations

from app.config import get_settings


def test_symbol_allowlist_enabled_default_false() -> None:
    assert get_settings().SYMBOL_ALLOWLIST_ENABLED is False


def test_stablecoin_exclude_list_defaults() -> None:
    s = get_settings().SHADOW_STABLECOIN_EXCLUDE_LIST
    assert "USDC" in s
    assert "FDUSD" in s
    assert "USD1" in s
    assert "BUSD" in s
    assert "TUSD" in s
    assert "DAI" in s


def test_grace_trades_default_50() -> None:
    assert get_settings().SYMBOL_ALLOWLIST_GRACE_TRADES == 50


def test_window_trades_default_100() -> None:
    assert get_settings().SYMBOL_ALLOWLIST_WINDOW_TRADES == 100


def test_window_days_default_30() -> None:
    assert get_settings().SYMBOL_ALLOWLIST_WINDOW_DAYS == 30


def test_cache_ttl_default_3600() -> None:
    assert get_settings().SYMBOL_ALLOWLIST_CACHE_TTL_SECONDS == 3600
```

- [ ] **Step 2.2: Run test, verify it fails**

```bash
cd backend && pytest tests/unit/test_pr10_settings_defaults.py -v --no-cov
```
Expected: FAIL with AttributeError.

- [ ] **Step 2.3: Add settings to `backend/app/config.py`**

Append after the PR9 settings block:

```python
    # --- PR10 symbol allowlist + stablecoin filter -----------------------
    # Default-OFF for safe deploy. Operator flips after observing the
    # `/api/v1/bot-status/symbol-allowlist` endpoint for ~24h.
    SYMBOL_ALLOWLIST_ENABLED: bool = False
    # Quote-stripped base asset names excluded from real-money dispatch.
    # Shadow trading on these symbols continues (controlled by
    # SHADOW_NARROW_UNIVERSE) so per-symbol stats keep accruing.
    SHADOW_STABLECOIN_EXCLUDE_LIST: list[str] = [
        "USDC", "FDUSD", "USD1", "BUSD", "TUSD", "DAI",
    ]
    # New-symbol grace: < this many closed trades → allowlisted regardless
    # of Sharpe. Prevents excluding new symbols before meaningful data.
    SYMBOL_ALLOWLIST_GRACE_TRADES: int = 50
    # Rolling window: Sharpe over min(WINDOW_TRADES most-recent closed,
    # trades in last WINDOW_DAYS days) — whichever set is smaller.
    SYMBOL_ALLOWLIST_WINDOW_TRADES: int = 100
    SYMBOL_ALLOWLIST_WINDOW_DAYS: int = 30
    # In-memory allowlist cache TTL. Comfortably faster than daily refresh
    # so cache rebuilds read fresh snapshot data.
    SYMBOL_ALLOWLIST_CACHE_TTL_SECONDS: int = 3600
```

- [ ] **Step 2.4: Re-run + commit**

```bash
cd backend && pytest tests/unit/test_pr10_settings_defaults.py -v --no-cov
git add backend/app/config.py backend/tests/unit/test_pr10_settings_defaults.py
git commit -m "feat(pr10): 6 settings — allowlist flag + stablecoin list + window/grace (Phase 2)"
```

---

## Phase 3: Pure-function helpers

**Files:**
- Create: `backend/app/trading/symbol_allowlist.py`
- Create: `backend/tests/unit/test_symbol_allowlist.py`

- [ ] **Step 3.1: Write failing tests**

```python
# backend/tests/unit/test_symbol_allowlist.py
"""PR10 pure-function helpers: _parse_base_asset, is_stablecoin_pair, is_symbol_allowed."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.trading.symbol_allowlist import (
    _AllowlistCache,
    _parse_base_asset,
    is_stablecoin_pair,
    is_symbol_allowed,
)


def _settings(*, excludes: list[str] | None = None):
    return SimpleNamespace(
        SHADOW_STABLECOIN_EXCLUDE_LIST=excludes or ["USDC", "FDUSD", "USD1", "BUSD", "TUSD", "DAI"],
        SYMBOL_ALLOWLIST_GRACE_TRADES=50,
    )


# --- _parse_base_asset ---------------------------------------------------


def test_parse_base_btcusdt_no_slash() -> None:
    assert _parse_base_asset("BTCUSDT") == "BTC"


def test_parse_base_btc_slash_usdt() -> None:
    assert _parse_base_asset("BTC/USDT") == "BTC"


def test_parse_base_fdusdusdt_longest_quote_first() -> None:
    # FDUSDUSDT → strip USDT (len 4) → "FDUSD" — correctly identifies stablecoin base
    assert _parse_base_asset("FDUSDUSDT") == "FDUSD"


def test_parse_base_busdusdt() -> None:
    assert _parse_base_asset("BUSDUSDT") == "BUSD"


def test_parse_base_dash_separator() -> None:
    assert _parse_base_asset("BTC-USDT") == "BTC"


def test_parse_base_lowercase_normalized() -> None:
    assert _parse_base_asset("btc/usdt") == "BTC"


# --- is_stablecoin_pair --------------------------------------------------


def test_stablecoin_fdusdusdt_excluded() -> None:
    assert is_stablecoin_pair("FDUSDUSDT", _settings()) is True


def test_stablecoin_usdcusdt_excluded() -> None:
    assert is_stablecoin_pair("USDC/USDT", _settings()) is True


def test_non_stablecoin_btc_not_excluded() -> None:
    assert is_stablecoin_pair("BTCUSDT", _settings()) is False


def test_non_stablecoin_eth_not_excluded() -> None:
    assert is_stablecoin_pair("ETH/USDT", _settings()) is False


def test_stablecoin_env_override_adds_more() -> None:
    # Operator adds "USDP" to env list
    custom = _settings(excludes=["USDC", "FDUSD", "USDP"])
    assert is_stablecoin_pair("USDPUSDT", custom) is True
    assert is_stablecoin_pair("BUSDUSDT", custom) is False  # BUSD no longer in list


# --- is_symbol_allowed ---------------------------------------------------


@dataclass(frozen=True)
class _Snapshot:
    trades_count: int
    sharpe: float | None


def test_allowed_new_symbol_grace_window() -> None:
    """trades_count < grace_trades → allowed regardless of Sharpe."""
    assert is_symbol_allowed(_Snapshot(trades_count=10, sharpe=-5.0)) is True
    assert is_symbol_allowed(_Snapshot(trades_count=49, sharpe=-5.0)) is True


def test_allowed_grace_boundary_50_means_judged_by_sharpe() -> None:
    """trades_count == grace_trades → no longer in grace window."""
    assert is_symbol_allowed(_Snapshot(trades_count=50, sharpe=1.0)) is True
    assert is_symbol_allowed(_Snapshot(trades_count=50, sharpe=-1.0)) is False


def test_allowed_positive_sharpe() -> None:
    assert is_symbol_allowed(_Snapshot(trades_count=200, sharpe=0.5)) is True


def test_allowed_zero_sharpe_excluded() -> None:
    """Sharpe must be strictly positive (> 0, not >=)."""
    assert is_symbol_allowed(_Snapshot(trades_count=200, sharpe=0.0)) is False


def test_allowed_negative_sharpe_excluded() -> None:
    assert is_symbol_allowed(_Snapshot(trades_count=200, sharpe=-2.0)) is False


def test_allowed_none_sharpe_treated_as_zero_excluded() -> None:
    """Sharpe=None (insufficient data) → not allowed past grace window."""
    assert is_symbol_allowed(_Snapshot(trades_count=200, sharpe=None)) is False


# --- _AllowlistCache.is_fresh -------------------------------------------


def test_cache_fresh_within_ttl() -> None:
    now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
    cache = _AllowlistCache(
        snapshot_map={}, last_refresh=now - timedelta(minutes=30),
    )
    assert cache.is_fresh(now) is True


def test_cache_stale_past_ttl() -> None:
    now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
    cache = _AllowlistCache(
        snapshot_map={}, last_refresh=now - timedelta(hours=2),
    )
    assert cache.is_fresh(now) is False
```

- [ ] **Step 3.2: Run tests, verify they fail**

```bash
cd backend && pytest tests/unit/test_symbol_allowlist.py -v --no-cov
```
Expected: ImportError.

- [ ] **Step 3.3: Implement helpers in `backend/app/trading/symbol_allowlist.py`**

```python
"""PR10 — symbol allowlist + stablecoin filter pure helpers.

Pure functions, no DB / no I/O. The DB-touching path lives in
`app/db/symbol_performance_snapshots.py`; the dispatcher integration
lives in `app/trading/execution/symbol_allowlist_gate.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol


# Order matters: longest first so FDUSD strips before USD, BUSD before USD, etc.
_QUOTE_SUFFIXES_LONGEST_FIRST: tuple[str, ...] = (
    "FDUSD", "USDT", "USDC", "BUSD", "TUSD", "USD",
)


def _parse_base_asset(symbol: str) -> str:
    """Extract base from BTC/USDT, BTCUSDT, BTC-USDT (case-insensitive).

    Strips known quote suffixes longest-first. Falls back to entire
    string if no recognized quote pattern.
    """
    s = symbol.replace("/", "").replace("-", "").upper()
    for quote in _QUOTE_SUFFIXES_LONGEST_FIRST:
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)]
    return s


class _StablecoinSettingsProto(Protocol):
    SHADOW_STABLECOIN_EXCLUDE_LIST: list[str]


def is_stablecoin_pair(symbol: str, settings: _StablecoinSettingsProto) -> bool:
    """True if base asset of `symbol` is in the stablecoin exclude list.

    Operator-tunable via SHADOW_STABLECOIN_EXCLUDE_LIST env override.
    """
    base = _parse_base_asset(symbol)
    excludes_upper = {b.upper() for b in settings.SHADOW_STABLECOIN_EXCLUDE_LIST}
    return base in excludes_upper


class _SnapshotProto(Protocol):
    trades_count: int
    sharpe: float | None


def is_symbol_allowed(
    snapshot: _SnapshotProto, *, grace_trades: int = 50,
) -> bool:
    """Allowlist inclusion rule.

    Allowed if either:
      - trades_count < grace_trades (new-symbol grace window), OR
      - Sharpe is strictly > 0 (positive edge demonstrated).

    Negative or zero or None Sharpe past grace → excluded. The None case
    arises when compute_sharpe_annualized returns None for < 2 trades;
    rare in practice because the grace window covers that condition.
    """
    if snapshot.trades_count < grace_trades:
        return True
    return (snapshot.sharpe or 0.0) > 0.0


@dataclass
class _AllowlistCache:
    """Process-local cache of latest snapshot per symbol.

    Single instance keyed externally on user_id. Caller holds an asyncio
    lock around rebuild to prevent thundering-herd queries.
    """
    snapshot_map: dict[str, "_SnapshotProto"] = field(default_factory=dict)
    last_refresh: datetime | None = None
    ttl: timedelta = field(default_factory=lambda: timedelta(hours=1))

    def is_fresh(self, now: datetime) -> bool:
        if self.last_refresh is None:
            return False
        return (now - self.last_refresh) < self.ttl
```

- [ ] **Step 3.4: Re-run tests + commit**

```bash
cd backend && pytest tests/unit/test_symbol_allowlist.py -v --no-cov
git add backend/app/trading/symbol_allowlist.py backend/tests/unit/test_symbol_allowlist.py
git commit -m "feat(pr10): pure helpers — _parse_base_asset + is_stablecoin_pair + is_symbol_allowed + cache (Phase 3)"
```

---

## Phase 4: `compute_per_symbol_stats` aggregator

**Files:**
- Modify: `backend/app/trading/symbol_allowlist.py`
- Create: `backend/tests/unit/test_compute_per_symbol_stats.py`

- [ ] **Step 4.1: Write failing tests**

```python
# backend/tests/unit/test_compute_per_symbol_stats.py
"""PR10 compute_per_symbol_stats — rolling-window per-symbol aggregation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.trading.symbol_allowlist import compute_per_symbol_stats


_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _Trade:
    symbol: str
    closed_at: datetime
    pnl_usdt: float
    pnl_pct: float


def _settings(window_trades: int = 100, window_days: int = 30, grace: int = 50):
    return SimpleNamespace(
        SYMBOL_ALLOWLIST_WINDOW_TRADES=window_trades,
        SYMBOL_ALLOWLIST_WINDOW_DAYS=window_days,
        SYMBOL_ALLOWLIST_GRACE_TRADES=grace,
    )


def test_per_symbol_grouping() -> None:
    trades = [
        _Trade("BTCUSDT", _NOW - timedelta(hours=1), 1.0, 0.01),
        _Trade("BTCUSDT", _NOW - timedelta(hours=2), -0.5, -0.005),
        _Trade("ETHUSDT", _NOW - timedelta(hours=1), 2.0, 0.02),
    ]
    result = compute_per_symbol_stats(trades, _settings(), now=_NOW)
    symbols = {r.symbol for r in result}
    assert symbols == {"BTCUSDT", "ETHUSDT"}


def test_per_symbol_trades_count() -> None:
    trades = [
        _Trade("BTCUSDT", _NOW - timedelta(hours=i), 1.0, 0.01)
        for i in range(5)
    ]
    result = compute_per_symbol_stats(trades, _settings(), now=_NOW)
    btc = next(r for r in result if r.symbol == "BTCUSDT")
    assert btc.trades_count == 5


def test_rolling_window_trades_cutoff() -> None:
    """100-trade window: only most-recent 100 per symbol contribute."""
    trades = [
        _Trade("BTCUSDT", _NOW - timedelta(hours=i), 0.0, 0.0)
        for i in range(150)
    ]
    result = compute_per_symbol_stats(trades, _settings(window_trades=100), now=_NOW)
    btc = next(r for r in result if r.symbol == "BTCUSDT")
    assert btc.trades_count == 100


def test_rolling_window_days_cutoff() -> None:
    """30-day window: only trades within last 30 days contribute."""
    trades = [
        _Trade("BTCUSDT", _NOW - timedelta(days=20), 1.0, 0.01),
        _Trade("BTCUSDT", _NOW - timedelta(days=40), 1.0, 0.01),
    ]
    result = compute_per_symbol_stats(trades, _settings(window_days=30), now=_NOW)
    btc = next(r for r in result if r.symbol == "BTCUSDT")
    assert btc.trades_count == 1


def test_empty_input_returns_empty_list() -> None:
    assert compute_per_symbol_stats([], _settings(), now=_NOW) == []


def test_allowed_flag_set_per_rule() -> None:
    """trades_count < grace (50) → allowed True regardless of sharpe."""
    trades = [
        _Trade("BTCUSDT", _NOW - timedelta(hours=i), -1.0, -0.01)
        for i in range(10)
    ]
    result = compute_per_symbol_stats(trades, _settings(), now=_NOW)
    btc = next(r for r in result if r.symbol == "BTCUSDT")
    assert btc.allowed is True  # grace window


def test_window_uses_smaller_of_trades_or_days() -> None:
    """If 30 days contains 200 trades but window_trades=100, cap at 100."""
    trades = [
        _Trade("BTCUSDT", _NOW - timedelta(hours=i), 0.0, 0.0)
        for i in range(200)
    ]
    result = compute_per_symbol_stats(
        trades, _settings(window_trades=100, window_days=30), now=_NOW,
    )
    btc = next(r for r in result if r.symbol == "BTCUSDT")
    assert btc.trades_count == 100
```

- [ ] **Step 4.2: Run tests, verify they fail**

```bash
cd backend && pytest tests/unit/test_compute_per_symbol_stats.py -v --no-cov
```

- [ ] **Step 4.3: Implement `compute_per_symbol_stats` in `backend/app/trading/symbol_allowlist.py`**

Append:

```python
from dataclasses import asdict
from typing import Iterable

from app.shadow.stats import Trade as ShadowTrade, compute_sharpe_annualized, compute_win_rate


@dataclass(frozen=True)
class SymbolSnapshotComputed:
    """Result of computing per-symbol stats — pre-DB-insert shape."""
    symbol: str
    window_start: datetime
    window_end: datetime
    trades_count: int
    win_rate: float | None
    sharpe: float | None
    allowed: bool


class _TradeProto(Protocol):
    symbol: str
    closed_at: datetime
    pnl_usdt: float
    pnl_pct: float


class _AggregatorSettingsProto(Protocol):
    SYMBOL_ALLOWLIST_WINDOW_TRADES: int
    SYMBOL_ALLOWLIST_WINDOW_DAYS: int
    SYMBOL_ALLOWLIST_GRACE_TRADES: int


def compute_per_symbol_stats(
    trades: Iterable[_TradeProto],
    settings: _AggregatorSettingsProto,
    *,
    now: datetime,
) -> list[SymbolSnapshotComputed]:
    """Aggregate closed trades per symbol over rolling window.

    Window: min(WINDOW_TRADES most-recent closed for the symbol,
                trades in last WINDOW_DAYS days). Smaller set wins.

    Returns one SymbolSnapshotComputed per distinct symbol present.
    Empty input → empty list.
    """
    window_start = now - timedelta(days=settings.SYMBOL_ALLOWLIST_WINDOW_DAYS)

    by_symbol: dict[str, list[_TradeProto]] = {}
    for t in trades:
        if t.closed_at < window_start:
            continue
        by_symbol.setdefault(t.symbol, []).append(t)

    out: list[SymbolSnapshotComputed] = []
    for symbol, sym_trades in by_symbol.items():
        # Sort newest first, truncate to window_trades cap
        sym_trades.sort(key=lambda t: t.closed_at, reverse=True)
        sym_trades = sym_trades[: settings.SYMBOL_ALLOWLIST_WINDOW_TRADES]

        # Reuse existing stats helpers (shadow/stats.py); they take a list
        # of Trade objects with .pnl_pct and .pnl_usdt; our _TradeProto
        # matches that contract.
        win_rate = compute_win_rate(sym_trades) if sym_trades else None
        sharpe = compute_sharpe_annualized(
            sym_trades, settings.SYMBOL_ALLOWLIST_WINDOW_DAYS,
        ) if sym_trades else None

        # Pre-compute allowed flag for the snapshot row
        @dataclass(frozen=True)
        class _SnapForRule:
            trades_count: int
            sharpe: float | None
        allowed = is_symbol_allowed(
            _SnapForRule(trades_count=len(sym_trades), sharpe=sharpe),
            grace_trades=settings.SYMBOL_ALLOWLIST_GRACE_TRADES,
        )

        out.append(SymbolSnapshotComputed(
            symbol=symbol,
            window_start=window_start,
            window_end=now,
            trades_count=len(sym_trades),
            win_rate=win_rate,
            sharpe=sharpe,
            allowed=allowed,
        ))
    return out
```

- [ ] **Step 4.4: Re-run + commit**

```bash
cd backend && pytest tests/unit/test_compute_per_symbol_stats.py -v --no-cov
git add backend/app/trading/symbol_allowlist.py backend/tests/unit/test_compute_per_symbol_stats.py
git commit -m "feat(pr10): compute_per_symbol_stats rolling-window aggregator (Phase 4)"
```

---

## Phase 5: Persistence layer

**Files:**
- Create: `backend/app/db/symbol_performance_snapshots.py`
- Create: `backend/tests/db/test_symbol_performance_snapshots_persistence.py`

- [ ] **Step 5.1: Write failing tests**

```python
# backend/tests/db/test_symbol_performance_snapshots_persistence.py
"""PR10 persistence — insert via audit chain + load latest per symbol."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.symbol_performance_snapshots import (
    SymbolSnapshot,
    insert_snapshot_row,
    load_latest_snapshots_per_symbol,
)


_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


async def _mk_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE symbol_performance_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "window_start TEXT NOT NULL, "
            "window_end TEXT NOT NULL, "
            "trades_count INTEGER NOT NULL, "
            "win_rate REAL, sharpe REAL, "
            "allowed INTEGER NOT NULL, "
            "computed_at TEXT NOT NULL, "
            "prev_hash TEXT NOT NULL, "
            "row_hash TEXT NOT NULL UNIQUE, "
            "inputs_hash TEXT)"
        ))
    return engine


@pytest.mark.asyncio
async def test_insert_and_load_back() -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await insert_snapshot_row(
            s, symbol="BTCUSDT",
            window_start=_NOW - timedelta(days=30), window_end=_NOW,
            trades_count=42, win_rate=0.45, sharpe=1.2,
            allowed=True, computed_at=_NOW,
        )
        await s.commit()
    async with factory() as s:
        rows = await load_latest_snapshots_per_symbol(s)
    assert "BTCUSDT" in rows
    assert rows["BTCUSDT"].trades_count == 42
    assert abs(rows["BTCUSDT"].sharpe - 1.2) < 1e-9
    assert rows["BTCUSDT"].allowed is True


@pytest.mark.asyncio
async def test_load_returns_latest_only() -> None:
    """Two snapshots for same symbol → load returns the newer one."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    older = _NOW - timedelta(days=1)
    async with factory() as s:
        await insert_snapshot_row(
            s, symbol="BTCUSDT",
            window_start=older - timedelta(days=30), window_end=older,
            trades_count=10, win_rate=0.30, sharpe=-1.0,
            allowed=False, computed_at=older,
        )
        await insert_snapshot_row(
            s, symbol="BTCUSDT",
            window_start=_NOW - timedelta(days=30), window_end=_NOW,
            trades_count=20, win_rate=0.55, sharpe=1.5,
            allowed=True, computed_at=_NOW,
        )
        await s.commit()
    async with factory() as s:
        rows = await load_latest_snapshots_per_symbol(s)
    assert rows["BTCUSDT"].trades_count == 20  # newer one


@pytest.mark.asyncio
async def test_empty_table_returns_empty_dict() -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        rows = await load_latest_snapshots_per_symbol(s)
    assert rows == {}
```

- [ ] **Step 5.2: Run tests, verify they fail**

```bash
cd backend && pytest tests/db/test_symbol_performance_snapshots_persistence.py -v --no-cov
```

- [ ] **Step 5.3: Implement `backend/app/db/symbol_performance_snapshots.py`**

```python
"""PR10 persistence — symbol_performance_snapshots round-trip.

Append-only writes via `insert_with_chain` (hash-chained per audit
convention). Reads via `load_latest_snapshots_per_symbol` — returns
dict keyed on symbol holding the row with the newest `computed_at`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import insert_with_chain


@dataclass(frozen=True)
class SymbolSnapshot:
    """One row from symbol_performance_snapshots, post-load."""
    symbol: str
    window_start: datetime
    window_end: datetime
    trades_count: int
    win_rate: float | None
    sharpe: float | None
    allowed: bool
    computed_at: datetime


def _to_dt(value: Any) -> datetime:
    """SQLite stringifies datetimes; Postgres returns native. Normalize."""
    from datetime import datetime as _dt
    if isinstance(value, _dt):
        return value
    return _dt.fromisoformat(str(value).replace("Z", "+00:00"))


async def insert_snapshot_row(
    session: AsyncSession,
    *,
    symbol: str,
    window_start: datetime,
    window_end: datetime,
    trades_count: int,
    win_rate: float | None,
    sharpe: float | None,
    allowed: bool,
    computed_at: datetime,
) -> str:
    """Append one snapshot row via insert_with_chain. Returns row_hash."""
    payload = {
        "symbol": symbol,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "trades_count": trades_count,
        "win_rate": win_rate,
        "sharpe": sharpe,
        "allowed": allowed,
        "computed_at": computed_at.isoformat(),
        "inputs_hash": None,
    }
    return await insert_with_chain(
        session, "symbol_performance_snapshots", payload,
    )


async def load_latest_snapshots_per_symbol(
    session: AsyncSession,
) -> dict[str, SymbolSnapshot]:
    """Return the most-recent snapshot per symbol.

    Uses a window function on Postgres; on SQLite (tests), uses an
    equivalent correlated subquery. Returns dict keyed on symbol.
    """
    sql = sa.text(
        "SELECT symbol, window_start, window_end, trades_count, "
        "       win_rate, sharpe, allowed, computed_at "
        "  FROM symbol_performance_snapshots t1 "
        " WHERE computed_at = ( "
        "       SELECT MAX(computed_at) "
        "         FROM symbol_performance_snapshots t2 "
        "        WHERE t2.symbol = t1.symbol "
        "     )"
    )
    rows = (await session.execute(sql)).all()
    out: dict[str, SymbolSnapshot] = {}
    for r in rows:
        out[r.symbol] = SymbolSnapshot(
            symbol=r.symbol,
            window_start=_to_dt(r.window_start),
            window_end=_to_dt(r.window_end),
            trades_count=int(r.trades_count),
            win_rate=float(r.win_rate) if r.win_rate is not None else None,
            sharpe=float(r.sharpe) if r.sharpe is not None else None,
            allowed=bool(r.allowed),
            computed_at=_to_dt(r.computed_at),
        )
    return out
```

- [ ] **Step 5.4: Re-run + commit**

```bash
cd backend && pytest tests/db/test_symbol_performance_snapshots_persistence.py -v --no-cov
git add backend/app/db/symbol_performance_snapshots.py backend/tests/db/test_symbol_performance_snapshots_persistence.py
git commit -m "feat(pr10): symbol_performance_snapshots persistence (Phase 5)"
```

---

## Phase 6: Dispatcher allowlist gate

**Files:**
- Create: `backend/app/trading/execution/symbol_allowlist_gate.py`
- Modify: `backend/app/trading/execution/dispatcher.py`
- Create: `backend/tests/trading/test_symbol_allowlist_gate.py`

- [ ] **Step 6.1: Write failing tests**

```python
# backend/tests/trading/test_symbol_allowlist_gate.py
"""PR10 dispatcher gate — pre-condition integration."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.trading.execution.symbol_allowlist_gate import (
    _apply_symbol_allowlist_gate,
)


_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


def _proposal(symbol: str):
    return SimpleNamespace(symbol=symbol)


def _settings(enabled: bool = True):
    return SimpleNamespace(
        SYMBOL_ALLOWLIST_ENABLED=enabled,
        SHADOW_STABLECOIN_EXCLUDE_LIST=["USDC", "FDUSD", "USD1", "BUSD", "TUSD", "DAI"],
        SYMBOL_ALLOWLIST_GRACE_TRADES=50,
        SYMBOL_ALLOWLIST_CACHE_TTL_SECONDS=3600,
    )


@pytest.mark.asyncio
async def test_gate_disabled_returns_none() -> None:
    session = MagicMock()
    result = await _apply_symbol_allowlist_gate(
        proposal=_proposal("BTCUSDT"), user_id=1,
        session=session, settings=_settings(enabled=False),
        now_fn=lambda: _NOW,
    )
    assert result is None


@pytest.mark.asyncio
async def test_gate_stablecoin_returns_blocked_stablecoin() -> None:
    session = MagicMock()
    with patch(
        "app.trading.execution.symbol_allowlist_gate.load_latest_snapshots_per_symbol",
        new=AsyncMock(return_value={}),
    ):
        result = await _apply_symbol_allowlist_gate(
            proposal=_proposal("FDUSDUSDT"), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is not None
    assert result.outcome == "blocked_stablecoin"


@pytest.mark.asyncio
async def test_gate_no_snapshot_falls_open_allows() -> None:
    """When the symbol has NO snapshot row → allow (defensive default)."""
    session = MagicMock()
    with patch(
        "app.trading.execution.symbol_allowlist_gate.load_latest_snapshots_per_symbol",
        new=AsyncMock(return_value={}),
    ):
        result = await _apply_symbol_allowlist_gate(
            proposal=_proposal("BTCUSDT"), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is None


@pytest.mark.asyncio
async def test_gate_low_sharpe_returns_blocked_low_sharpe() -> None:
    snap = SimpleNamespace(trades_count=200, sharpe=-2.0)
    session = MagicMock()
    with patch(
        "app.trading.execution.symbol_allowlist_gate.load_latest_snapshots_per_symbol",
        new=AsyncMock(return_value={"BTCUSDT": snap}),
    ):
        result = await _apply_symbol_allowlist_gate(
            proposal=_proposal("BTCUSDT"), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is not None
    assert result.outcome == "blocked_low_sharpe"


@pytest.mark.asyncio
async def test_gate_positive_sharpe_passes() -> None:
    snap = SimpleNamespace(trades_count=200, sharpe=1.5)
    session = MagicMock()
    with patch(
        "app.trading.execution.symbol_allowlist_gate.load_latest_snapshots_per_symbol",
        new=AsyncMock(return_value={"BTCUSDT": snap}),
    ):
        result = await _apply_symbol_allowlist_gate(
            proposal=_proposal("BTCUSDT"), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is None


@pytest.mark.asyncio
async def test_gate_grace_window_passes_negative_sharpe() -> None:
    """trades_count < grace → allowed even with negative Sharpe."""
    snap = SimpleNamespace(trades_count=10, sharpe=-5.0)
    session = MagicMock()
    with patch(
        "app.trading.execution.symbol_allowlist_gate.load_latest_snapshots_per_symbol",
        new=AsyncMock(return_value={"BTCUSDT": snap}),
    ):
        result = await _apply_symbol_allowlist_gate(
            proposal=_proposal("BTCUSDT"), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is None


@pytest.mark.asyncio
async def test_gate_fails_open_on_db_error() -> None:
    """DB read failure → return None (let trade proceed). Critical."""
    session = MagicMock()
    with patch(
        "app.trading.execution.symbol_allowlist_gate.load_latest_snapshots_per_symbol",
        new=AsyncMock(side_effect=RuntimeError("db blip")),
    ):
        result = await _apply_symbol_allowlist_gate(
            proposal=_proposal("BTCUSDT"), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is None
```

- [ ] **Step 6.2: Run tests, verify they fail**

```bash
cd backend && pytest tests/trading/test_symbol_allowlist_gate.py -v --no-cov
```

- [ ] **Step 6.3: Implement `backend/app/trading/execution/symbol_allowlist_gate.py`**

```python
"""PR10 symbol allowlist gate — dispatcher pre-condition.

Reads latest snapshots, applies stablecoin filter + Sharpe rule.
Two distinct outcomes: blocked_stablecoin / blocked_low_sharpe.

Fail-open contract: any DB error returns None (let trade proceed).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.symbol_performance_snapshots import (
    SymbolSnapshot,
    load_latest_snapshots_per_symbol,
)
from app.trading.symbol_allowlist import (
    _AllowlistCache,
    is_stablecoin_pair,
    is_symbol_allowed,
)


log = logging.getLogger(__name__)


# Process-local cache per (user_id,). One asyncio.Lock per user to
# serialize cache rebuilds (thundering-herd protection).
_CACHE: dict[int, _AllowlistCache] = {}
_LOCKS: dict[int, asyncio.Lock] = {}


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _get_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _LOCKS:
        _LOCKS[user_id] = asyncio.Lock()
    return _LOCKS[user_id]


async def _get_cached_snapshots(
    *,
    user_id: int,
    session: AsyncSession,
    settings,
    now_fn: Callable[[], datetime],
) -> dict[str, SymbolSnapshot]:
    """Read-through cache for latest snapshots per symbol."""
    now = now_fn()
    cache = _CACHE.get(user_id)
    if cache is not None and cache.is_fresh(now):
        return cache.snapshot_map  # type: ignore[return-value]

    async with _get_lock(user_id):
        # Re-check after acquiring lock (another task may have refreshed)
        cache = _CACHE.get(user_id)
        if cache is not None and cache.is_fresh(now):
            return cache.snapshot_map  # type: ignore[return-value]

        from datetime import timedelta
        snaps = await load_latest_snapshots_per_symbol(session)
        ttl_seconds = settings.SYMBOL_ALLOWLIST_CACHE_TTL_SECONDS
        new_cache = _AllowlistCache(
            snapshot_map=snaps,  # type: ignore[arg-type]
            last_refresh=now,
            ttl=timedelta(seconds=ttl_seconds),
        )
        _CACHE[user_id] = new_cache
        return snaps


async def _apply_symbol_allowlist_gate(
    *,
    proposal,
    user_id: int,
    session: AsyncSession,
    settings,
    now_fn: Callable[[], datetime] = _utc_now,
):
    """Return DispatchResult to block; None to let the trade proceed.

    Decision order:
      1. Flag disabled → None (no-op; entire filter is opt-in).
      2. Stablecoin pair (base in SHADOW_STABLECOIN_EXCLUDE_LIST) →
         blocked_stablecoin.
      3. Snapshot missing for this symbol → None (defensive — no data
         means no decision; allow until data arrives).
      4. is_symbol_allowed(snapshot) is False → blocked_low_sharpe.
      5. Otherwise → None.

    Fail-open: any exception from the DB read OR rule evaluation returns
    None with a WARNING log. A stuck gate that errored to-blocked would
    shut down all trading on a single DB blip.
    """
    from app.trading.execution.dispatcher import DispatchResult

    if not settings.SYMBOL_ALLOWLIST_ENABLED:
        return None

    if is_stablecoin_pair(proposal.symbol, settings):
        return DispatchResult(
            outcome="blocked_stablecoin",
            detail=f"{proposal.symbol} base in stablecoin exclude list",
        )

    try:
        snaps = await _get_cached_snapshots(
            user_id=user_id, session=session, settings=settings, now_fn=now_fn,
        )
    except Exception as e:  # noqa: BLE001 — fail-open
        log.warning(
            "symbol_allowlist_gate snapshot read failed for user=%s symbol=%s; "
            "failing open: %s",
            user_id, proposal.symbol, e,
        )
        return None

    snap = snaps.get(proposal.symbol)
    if snap is None:
        # No snapshot row yet → defensive allow. Daily worker will
        # eventually backfill.
        return None

    try:
        allowed = is_symbol_allowed(
            snap, grace_trades=settings.SYMBOL_ALLOWLIST_GRACE_TRADES,
        )
    except Exception as e:  # noqa: BLE001 — fail-open
        log.warning(
            "symbol_allowlist_gate rule eval failed for %s; failing open: %s",
            proposal.symbol, e,
        )
        return None

    if allowed:
        return None
    return DispatchResult(
        outcome="blocked_low_sharpe",
        detail=f"{proposal.symbol}: sharpe={snap.sharpe} trades={snap.trades_count}",
    )
```

- [ ] **Step 6.4: Add `blocked_stablecoin` + `blocked_low_sharpe` to `DispatchOutcome` in `backend/app/trading/execution/dispatcher.py`**

Find the existing literals:

```python
DispatchOutcome = Literal[
    "emitted",
    "sent_telegram",
    "placed",
    "blocked_killswitch",
    "blocked_cooldown",
    "blocked_max_positions",
    "blocked_funding",
    "error",
    "blocked_mtf_low_agreement",
    "blocked_mtf_higher_tf_veto",
    "blocked_short_high_borrow",
]
```

Add 2 entries at end:

```python
    "blocked_stablecoin",
    "blocked_low_sharpe",
```

- [ ] **Step 6.5: Wire `_apply_symbol_allowlist_gate` FIRST in dispatcher pre-conditions**

In `dispatcher.dispatch()`, find the pre-conditions block (right after the manual-mode short-circuit and BEFORE the funding check). Add:

```python
    # ---- PR10 symbol allowlist gate (cheapest check, run first) ----
    # Default-OFF in prod via SYMBOL_ALLOWLIST_ENABLED=False — when
    # disabled, _apply_symbol_allowlist_gate short-circuits to None
    # without touching the DB.
    from app.config import get_settings as _get_pr10_settings
    from app.trading.execution.symbol_allowlist_gate import _apply_symbol_allowlist_gate
    pr10_settings = _get_pr10_settings()
    allowlist_block = await _apply_symbol_allowlist_gate(
        proposal=proposal, user_id=user.user_id,
        session=session, settings=pr10_settings,
        now_fn=lambda: n,
    )
    if allowlist_block is not None:
        return allowlist_block
    # ---- end PR10 ---------------------------------------------------
```

- [ ] **Step 6.6: Re-run + commit**

```bash
cd backend && pytest tests/trading/test_symbol_allowlist_gate.py tests/trading/ -v --no-cov
git add backend/app/trading/execution/symbol_allowlist_gate.py \
        backend/app/trading/execution/dispatcher.py \
        backend/tests/trading/test_symbol_allowlist_gate.py
git commit -m "feat(pr10): wire _apply_symbol_allowlist_gate as first dispatcher pre-condition (Phase 6)"
```

---

## Phase 7: Daily snapshot worker

**Files:**
- Create: `backend/app/workers/symbol_allowlist_refresh.py`
- Create: `backend/tests/workers/test_symbol_allowlist_refresh.py`
- Modify: `backend/app/ops/worker_registry.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/unit/test_worker_registry_consistency.py`

- [ ] **Step 7.1: Write failing test**

```python
# backend/tests/workers/test_symbol_allowlist_refresh.py
"""PR10 symbol_allowlist_refresh worker — writes 1 snapshot per symbol."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.workers.symbol_allowlist_refresh import run_one_refresh_cycle


_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


async def _mk_engine_with_shadow_trades():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, "
            "direction TEXT NOT NULL, "
            "closed_at TEXT, "
            "pnl_usdt REAL NOT NULL DEFAULT 0, "
            "pnl_pct REAL NOT NULL DEFAULT 0, "
            "prev_hash TEXT NOT NULL DEFAULT '', "
            "row_hash TEXT NOT NULL DEFAULT '')"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE symbol_performance_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "window_start TEXT NOT NULL, "
            "window_end TEXT NOT NULL, "
            "trades_count INTEGER NOT NULL, "
            "win_rate REAL, sharpe REAL, "
            "allowed INTEGER NOT NULL, "
            "computed_at TEXT NOT NULL, "
            "prev_hash TEXT NOT NULL, "
            "row_hash TEXT NOT NULL UNIQUE, "
            "inputs_hash TEXT)"
        ))
        # Seed 2 BTC trades, 1 ETH trade
        for sym, pnl in (("BTCUSDT", 1.0), ("BTCUSDT", -0.5), ("ETHUSDT", 2.0)):
            await conn.execute(sa.text(
                "INSERT INTO shadow_trades "
                "(user_id, symbol, direction, closed_at, pnl_usdt, pnl_pct) "
                "VALUES (1, :sym, 'LONG', :ts, :pnl, :pct)"
            ), {"sym": sym, "ts": _NOW.isoformat(), "pnl": pnl, "pct": pnl * 0.01})
    return engine


def _settings():
    return SimpleNamespace(
        SYMBOL_ALLOWLIST_WINDOW_TRADES=100,
        SYMBOL_ALLOWLIST_WINDOW_DAYS=30,
        SYMBOL_ALLOWLIST_GRACE_TRADES=50,
    )


@pytest.mark.asyncio
async def test_refresh_cycle_writes_one_row_per_symbol() -> None:
    engine = await _mk_engine_with_shadow_trades()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    with patch("app.workers.symbol_allowlist_refresh.record_heartbeat",
               new=AsyncMock(return_value=None)):
        await run_one_refresh_cycle(
            session_factory=factory, settings=_settings(), now_fn=lambda: _NOW,
        )

    async with factory() as s:
        rows = (await s.execute(sa.text(
            "SELECT symbol, trades_count FROM symbol_performance_snapshots "
            "ORDER BY symbol"
        ))).all()
    symbols = {r.symbol for r in rows}
    assert "BTCUSDT" in symbols
    assert "ETHUSDT" in symbols


@pytest.mark.asyncio
async def test_refresh_cycle_heartbeats() -> None:
    engine = await _mk_engine_with_shadow_trades()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    hb_mock = AsyncMock(return_value=None)

    with patch("app.workers.symbol_allowlist_refresh.record_heartbeat", new=hb_mock):
        await run_one_refresh_cycle(
            session_factory=factory, settings=_settings(), now_fn=lambda: _NOW,
        )

    hb_mock.assert_awaited()
    args, kwargs = hb_mock.call_args
    assert args[1] == "symbol_allowlist_refresh"
    assert kwargs.get("status") == "ok"
```

- [ ] **Step 7.2: Run tests, verify they fail**

```bash
cd backend && pytest tests/workers/test_symbol_allowlist_refresh.py -v --no-cov
```

- [ ] **Step 7.3: Implement `backend/app/workers/symbol_allowlist_refresh.py`**

```python
"""PR10 daily symbol_allowlist_refresh worker.

Reads closed shadow_trades, computes per-symbol stats over rolling
window, writes one symbol_performance_snapshots row per symbol via
insert_with_chain. Heartbeats per cycle.

Single-writer worker → FU-24's concurrent-insert race doesn't fire
against symbol_performance_snapshots.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.symbol_performance_snapshots import insert_snapshot_row
from app.ops.heartbeat import record_heartbeat
from app.shadow.stats import Trade as ShadowTrade
from app.trading.symbol_allowlist import compute_per_symbol_stats


log = logging.getLogger(__name__)


_POLL_INTERVAL_SECONDS = 86400.0  # 24h


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _load_closed_trades_for_window(
    session: AsyncSession, *, window_start: datetime,
) -> list[ShadowTrade]:
    """Read all closed shadow_trades in the window. Aggregates across users
    (allowlist is per-symbol, not per-user-per-symbol)."""
    rows = (await session.execute(sa.text(
        "SELECT symbol, closed_at, pnl_usdt, pnl_pct, direction "
        "  FROM shadow_trades "
        " WHERE closed_at IS NOT NULL "
        "   AND closed_at >= :since"
    ), {"since": window_start})).all()
    out: list[ShadowTrade] = []
    for r in rows:
        out.append(ShadowTrade(
            symbol=r.symbol,
            direction=r.direction,
            entry_price=0.0, stop_loss=0.0, take_profit=0.0,
            position_size_usdt=0.0, pnl_pct=float(r.pnl_pct or 0),
            pnl_usdt=float(r.pnl_usdt or 0),
            closed_at=r.closed_at if isinstance(r.closed_at, datetime)
                      else datetime.fromisoformat(str(r.closed_at).replace("Z", "+00:00")),
        ))
    return out


async def run_one_refresh_cycle(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings,
    now_fn: Callable[[], datetime] = _utc_now,
) -> int:
    """One cycle: read trades → compute → insert one row per symbol.

    Returns count of snapshots written. Heartbeats on success/error.
    """
    now = now_fn()
    window_start = now - timedelta(days=settings.SYMBOL_ALLOWLIST_WINDOW_DAYS)
    try:
        async with session_factory() as session:
            trades = await _load_closed_trades_for_window(
                session, window_start=window_start,
            )
            stats = compute_per_symbol_stats(trades, settings, now=now)
            for s in stats:
                await insert_snapshot_row(
                    session,
                    symbol=s.symbol,
                    window_start=s.window_start, window_end=s.window_end,
                    trades_count=s.trades_count,
                    win_rate=s.win_rate, sharpe=s.sharpe,
                    allowed=s.allowed, computed_at=now,
                )
            await session.commit()
        await record_heartbeat(
            session_factory, "symbol_allowlist_refresh",
            status="ok", details={"snapshots_written": len(stats)},
        )
        return len(stats)
    except Exception as e:  # noqa: BLE001
        log.error("symbol_allowlist_refresh cycle failed: %s", e)
        try:
            await record_heartbeat(
                session_factory, "symbol_allowlist_refresh",
                status="error", details={"error": str(e)[:200]},
            )
        except Exception:  # noqa: BLE001
            pass
        return 0


async def run_symbol_allowlist_refresh_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings_factory: Callable[[], object],
    poll_interval_s: float = _POLL_INTERVAL_SECONDS,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Forever-loop. Fires one cycle per day."""
    log.info(
        "symbol_allowlist_refresh: starting (interval=%.0fs)", poll_interval_s,
    )
    while True:
        try:
            await _sleep(poll_interval_s)
        except asyncio.CancelledError:
            raise
        try:
            await run_one_refresh_cycle(
                session_factory=session_factory,
                settings=settings_factory(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("symbol_allowlist_refresh outer-loop error: %s", e)


def start_symbol_allowlist_refresh(
    session_factory: async_sessionmaker[AsyncSession],
    settings_factory: Callable[[], object],
) -> asyncio.Task[None]:
    return asyncio.create_task(run_symbol_allowlist_refresh_loop(
        session_factory=session_factory, settings_factory=settings_factory,
    ))


__all__ = [
    "run_one_refresh_cycle",
    "run_symbol_allowlist_refresh_loop",
    "start_symbol_allowlist_refresh",
]
```

- [ ] **Step 7.4: Register worker in `backend/app/ops/worker_registry.py`**

Append a new WorkerSpec (after the `live_exit_monitor` entry):

```python
    WorkerSpec(
        name="symbol_allowlist_refresh",
        description=(
            "Daily compute of per-symbol Sharpe + allowlist snapshot (PR10). "
            "Writes one symbol_performance_snapshots row per symbol."
        ),
        liveness_query=HEARTBEAT,
        max_staleness_seconds=2 * 86400,  # 2-day budget (1 missed run allowed)
        pending_heartbeat=False,
        stateful=False,  # safe to auto-restart
    ),
```

- [ ] **Step 7.5: Add WORKER_SOURCE_MODULES entry**

In `backend/tests/unit/test_worker_registry_consistency.py`:

```python
    "symbol_allowlist_refresh": "app/workers/symbol_allowlist_refresh.py",
```

(Insert in alphabetical position.)

- [ ] **Step 7.6: Spawn task in `backend/app/main.py` lifespan**

Find the worker-spawn block (near other unconditional workers). Add:

```python
    # PR10: daily symbol allowlist refresh (writes snapshots; not gated
    # by AUTONOMOUS_TRADING — snapshots are useful in all modes).
    from app.workers.symbol_allowlist_refresh import (
        start_symbol_allowlist_refresh,
    )
    from app.config import get_settings as _get_pr10_for_loop
    symbol_allowlist_task = start_symbol_allowlist_refresh(
        session_factory, _get_pr10_for_loop,
    )
```

Then in the shutdown block:

```python
    if symbol_allowlist_task is not None:
        symbol_allowlist_task.cancel()
```

And declare `symbol_allowlist_task = None` in the variable-init block.

- [ ] **Step 7.7: Re-run + commit**

```bash
cd backend && pytest tests/workers/test_symbol_allowlist_refresh.py tests/unit/test_worker_registry_consistency.py tests/ops/test_record_heartbeat_per_worker.py -v --no-cov
git add backend/app/workers/symbol_allowlist_refresh.py \
        backend/app/ops/worker_registry.py \
        backend/app/main.py \
        backend/tests/workers/test_symbol_allowlist_refresh.py \
        backend/tests/unit/test_worker_registry_consistency.py
git commit -m "feat(pr10): symbol_allowlist_refresh daily worker (Phase 7)"
```

---

## Phase 8: `/symbol-allowlist` API endpoint

**Files:**
- Modify: `backend/app/api/routes/bot_status.py`
- Modify: `backend/app/api/schemas.py`
- Create: `backend/tests/integration/test_pr10_allowlist_endpoint.py`

- [ ] **Step 8.1: Write failing test**

```python
# backend/tests/integration/test_pr10_allowlist_endpoint.py
"""PR10 /bot-status/symbol-allowlist endpoint."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
import sqlalchemy as sa


_NOW = datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_symbol_allowlist_endpoint_empty(
    bot_status_client: Any,
) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/symbol-allowlist")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_symbol_allowlist_endpoint_returns_sorted_by_sharpe_desc(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    async with bot_status_factory() as s:
        for sym, sharpe in (("TONUSDT", 5.84), ("FDUSDUSDT", -18.72), ("XRPUSDT", 2.93)):
            await s.execute(sa.text(
                "INSERT INTO symbol_performance_snapshots "
                "(symbol, window_start, window_end, trades_count, "
                " win_rate, sharpe, allowed, computed_at, prev_hash, row_hash) "
                "VALUES (:sym, :ws, :we, 100, 0.5, :sh, :al, :now, '0', :rh)"
            ), {
                "sym": sym, "ws": _NOW.isoformat(), "we": _NOW.isoformat(),
                "sh": sharpe, "al": sharpe > 0, "now": _NOW.isoformat(),
                "rh": f"hash_{sym}",
            })
        await s.commit()

    r = await bot_status_client.get("/api/v1/bot-status/symbol-allowlist")
    body = r.json()
    assert len(body) == 3
    symbols_ordered = [row["symbol"] for row in body]
    assert symbols_ordered == ["TONUSDT", "XRPUSDT", "FDUSDUSDT"]
```

- [ ] **Step 8.2: Run test, verify it fails**

```bash
cd backend && pytest tests/integration/test_pr10_allowlist_endpoint.py -v --no-cov
```

- [ ] **Step 8.3: Add `SymbolAllowlistOut` schema in `backend/app/api/schemas.py`**

After `SizingPreviewOut`:

```python
class SymbolAllowlistOut(BaseModel):
    """PR10 — one latest snapshot per symbol for /bot-status/symbol-allowlist."""
    symbol: str
    trades_count: int
    win_rate: float | None
    sharpe: float | None
    allowed: bool
    computed_at: datetime
```

- [ ] **Step 8.4: Add `/symbol-allowlist` route in `backend/app/api/routes/bot_status.py`**

Append after `/sizing` endpoint. First add to schema imports near other `*Out` imports:

```python
from app.api.schemas import (
    ...
    SymbolAllowlistOut,
    ...
)
```

Then the route:

```python
@router.get("/symbol-allowlist", response_model=list[SymbolAllowlistOut])
async def symbol_allowlist(
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SymbolAllowlistOut]:
    """PR10 — current per-symbol allowlist snapshot. Latest row per symbol,
    sorted by sharpe descending (winners on top, losers at bottom)."""
    rows = (await session.execute(sa.text(
        "SELECT symbol, trades_count, win_rate, sharpe, allowed, computed_at "
        "  FROM symbol_performance_snapshots t1 "
        " WHERE computed_at = ( "
        "       SELECT MAX(computed_at) FROM symbol_performance_snapshots t2 "
        "        WHERE t2.symbol = t1.symbol "
        "     )"
    ))).all()

    out: list[SymbolAllowlistOut] = []
    for r in rows:
        ca = r.computed_at
        if isinstance(ca, str):
            ca = datetime.fromisoformat(ca)
        out.append(SymbolAllowlistOut(
            symbol=r.symbol,
            trades_count=int(r.trades_count),
            win_rate=float(r.win_rate) if r.win_rate is not None else None,
            sharpe=float(r.sharpe) if r.sharpe is not None else None,
            allowed=bool(r.allowed),
            computed_at=ca,
        ))
    # Sort: positive sharpe first (winners), then by sharpe desc; None last.
    out.sort(
        key=lambda e: (e.sharpe is None, -(e.sharpe or 0)),
    )
    return out
```

- [ ] **Step 8.5: Add `symbol_performance_snapshots` table to integration conftest**

In `backend/tests/integration/conftest.py`, in `_create_shadow_tables`, append:

```python
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS symbol_performance_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "window_start TEXT NOT NULL, "
            "window_end TEXT NOT NULL, "
            "trades_count INTEGER NOT NULL, "
            "win_rate REAL, sharpe REAL, "
            "allowed INTEGER NOT NULL, "
            "computed_at TEXT NOT NULL, "
            "prev_hash TEXT NOT NULL, "
            "row_hash TEXT NOT NULL UNIQUE, "
            "inputs_hash TEXT)"
        ))
```

- [ ] **Step 8.6: Re-run + commit**

```bash
cd backend && pytest tests/integration/test_pr10_allowlist_endpoint.py -v --no-cov
git add backend/app/api/routes/bot_status.py \
        backend/app/api/schemas.py \
        backend/tests/integration/test_pr10_allowlist_endpoint.py \
        backend/tests/integration/conftest.py
git commit -m "feat(pr10): /bot-status/symbol-allowlist endpoint (Phase 8)"
```

---

## Phase 9: V-7 latency bench

**Files:**
- Create: `backend/scripts/bench_dispatcher_allowlist.py`

- [ ] **Step 9.1: Write the bench script**

```python
"""PR10 Phase 9 — V-7 microbench for the symbol allowlist gate.

Measures dispatcher pre-condition latency with allowlist gate
disabled / cache-warm / cache-cold. V-7 budget:
  delta_p50_cache_hit  ≤ 2ms
  delta_p99_cache_miss ≤ 10ms
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


P50_HIT_BUDGET_MS = 2.0
P99_MISS_BUDGET_MS = 10.0
DEFAULT_N = 500


def _percentile(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _settings(enabled: bool):
    return SimpleNamespace(
        SYMBOL_ALLOWLIST_ENABLED=enabled,
        SHADOW_STABLECOIN_EXCLUDE_LIST=["USDC", "FDUSD"],
        SYMBOL_ALLOWLIST_GRACE_TRADES=50,
        SYMBOL_ALLOWLIST_CACHE_TTL_SECONDS=3600,
    )


async def _run_mode(mode: str, n: int) -> dict[str, Any]:
    import asyncio
    from app.trading.execution import symbol_allowlist_gate as gate_mod

    # Clear cache between modes
    gate_mod._CACHE.clear()
    gate_mod._LOCKS.clear()

    snap = SimpleNamespace(trades_count=200, sharpe=1.5)
    snaps = {"BTCUSDT": snap}

    proposal = MagicMock(symbol="BTCUSDT")
    session = MagicMock()
    enabled = (mode != "baseline")

    times: list[float] = []
    with patch(
        "app.trading.execution.symbol_allowlist_gate.load_latest_snapshots_per_symbol",
        new=AsyncMock(return_value=snaps),
    ):
        for i in range(n):
            if mode == "cache-cold-every-call":
                gate_mod._CACHE.clear()
            t0 = time.perf_counter()
            await gate_mod._apply_symbol_allowlist_gate(
                proposal=proposal, user_id=1, session=session,
                settings=_settings(enabled=enabled),
                now_fn=lambda: datetime.now(tz=timezone.utc),
            )
            times.append((time.perf_counter() - t0) * 1000.0)

    return {
        "mode": mode, "n": n,
        "p50_ms": _percentile(times, 0.50),
        "p99_ms": _percentile(times, 0.99),
        "mean_ms": statistics.mean(times),
    }


async def _amain(args: argparse.Namespace) -> int:
    baseline = await _run_mode("baseline", args.samples)
    warm = await _run_mode("cache-warm", args.samples)
    cold = await _run_mode("cache-cold-every-call", args.samples)
    delta_p50_hit = warm["p50_ms"] - baseline["p50_ms"]
    delta_p99_miss = cold["p99_ms"] - baseline["p99_ms"]
    result = {
        "platform": platform.platform(),
        "python_version": sys.version.split()[0],
        "baseline": baseline, "cache_warm": warm, "cache_cold": cold,
        "delta_p50_cache_hit_ms": delta_p50_hit,
        "delta_p99_cache_miss_ms": delta_p99_miss,
        "p50_hit_budget_ms": P50_HIT_BUDGET_MS,
        "p99_miss_budget_ms": P99_MISS_BUDGET_MS,
        "pass": (delta_p50_hit <= P50_HIT_BUDGET_MS) and (delta_p99_miss <= P99_MISS_BUDGET_MS),
    }
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


def main() -> int:
    import asyncio
    p = argparse.ArgumentParser()
    p.add_argument("--samples", type=int, default=DEFAULT_N)
    args = p.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(_amain(args))
    return 130


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 9.2: Run bench**

```bash
cd backend && python scripts/bench_dispatcher_allowlist.py --samples 200
```
Expected: `"pass": true`, delta_p50_cache_hit ≤ 2ms, delta_p99_cache_miss ≤ 10ms.

- [ ] **Step 9.3: Commit**

```bash
git add backend/scripts/bench_dispatcher_allowlist.py
git commit -m "bench(pr10): bench_dispatcher_allowlist V-7 gate (Phase 9)"
```

---

## Phase 10: ARCHITECTURE.md §11e + master rollout doc

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/superpowers/specs/2026-05-17-master-rollout-plan-option-d.md`

- [ ] **Step 10.1: Add §11e to ARCHITECTURE.md**

After the §11d (PR9) section, before `## 11. Self-healing supervisor`, insert:

```markdown
## 11e. Symbol allowlist + stablecoin filter (PR10)

**Files**: `app/trading/symbol_allowlist.py` (pure helpers — new),
`app/trading/execution/symbol_allowlist_gate.py` (dispatcher pre-condition — new),
`app/db/symbol_performance_snapshots.py` (persistence — new),
`app/workers/symbol_allowlist_refresh.py` (daily worker — new),
`app/trading/execution/dispatcher.py` (wire as first pre-condition),
`app/api/routes/bot_status.py` (`/symbol-allowlist` endpoint),
`app/config.py` (6 new settings), alembic
`2026_05_19_0024_pr10_symbol_performance_snapshots.py` — added 2026-05-19.

### Strategic motivation

Shadow stats show bimodal performance: winners (TON, EDEN, XRP, SUI —
positive Sharpe) vs systematic losers (FDUSD, TRX, BNB — negative Sharpe)
plus stablecoin pairs that don't move. Aggregate stats hide the skill
split. Excluding losers + stablecoins should flip overall P&L positive.

### Allowlist rule

A symbol is allowed if **either**:
1. `trades_count < SYMBOL_ALLOWLIST_GRACE_TRADES` (default 50) — new
   symbols are unconditionally allowed until they accumulate evidence
2. `sharpe > 0` over the rolling window (default min(100 trades, 30 days))

Negative or zero or None Sharpe past grace → excluded.

### Daily snapshot worker

`symbol_allowlist_refresh` runs every 86400s. Reads closed shadow_trades,
computes per-symbol stats, writes one snapshot row per symbol. Single-
writer → FU-24's concurrent-insert race doesn't fire.

### Dispatcher gate

Slots **FIRST** in pre-conditions (cheapest after cache fill). Returns
`None` immediately when `SYMBOL_ALLOWLIST_ENABLED=False` — no DB read,
no behavior change at deploy. When flag is True:
- Stablecoin base → `blocked_stablecoin`
- Snapshot absent → defensive allow
- Sharpe rule fails → `blocked_low_sharpe`

Fail-open contract on DB error.

### Hot-path cache

Process-local cache keyed on user_id; TTL 1h. Per-user asyncio.Lock
prevents thundering herd on rebuild. First dispatch after restart or
TTL expiry pays one DB read; subsequent dispatches hit the dict.

### Rollback

Single env var: `SYMBOL_ALLOWLIST_ENABLED=False` reverts to pre-PR10
dispatch behavior. Process restart needed (lru_cache on get_settings).
Stage-2 rollback: alembic downgrade -1 drops the table; round-trip
tested.
```

- [ ] **Step 10.2: Update master rollout doc**

Append a new section to `docs/superpowers/specs/2026-05-17-master-rollout-plan-option-d.md`:

```markdown
## Strategic replan addendum (2026-05-19)

After Option D rollout completion, operator's reassessment of shadow
stats surfaced bimodal symbol performance + missing real-money gates.
Defer real-money trading until shadow Sharpe > 0.5 (2-week window).
New PR sequence:

| PR | Title | Status |
|----|-------|--------|
| 10 | Symbol allowlist + stablecoin filter | spec 2026-05-19; impl in progress |
| 11 | Exit improvements (timeout scaled, TP ≥ 2× SL) | queued |
| 12 | Spread + liquidity filter | queued |
| 13 | Bug bundle: FU-26 + FU-27 | queued |
| 14 | Trailing stops + partial profit | queued |
| 15 | FU-24 audit chain advisory lock | queued |
| 16+ | Tier 2 batches | queued |

Real-money fully-auto re-attempt: NOT before PR13 ships AND operator
fixes Binance Futures-Trade permission (separate operator-side work).
```

- [ ] **Step 10.3: Commit**

```bash
git add docs/ARCHITECTURE.md docs/superpowers/specs/2026-05-17-master-rollout-plan-option-d.md
git commit -m "docs(arch): section 11e — PR10 symbol allowlist + stablecoin filter (Phase 10)"
```

---

## Self-review checklist (run before opening PR)

- [ ] All 30+ new test files pass; lint + mypy clean
- [ ] V-7 bench gate passes (Δp50 cache-hit ≤ 2ms; Δp99 cache-miss ≤ 10ms)
- [ ] Default-OFF in prod confirmed (SYMBOL_ALLOWLIST_ENABLED=False) — verified by running test suite with default config and observing dispatcher tests still pass with the gate present but gated off
- [ ] Migration applies cleanly + downgrade round-trips
- [ ] No regression in existing dispatcher tests
- [ ] Audit chain `HASH_PAYLOAD_COLUMNS` covers the new `symbol_performance_snapshots` table (8 columns)
- [ ] Worker registry consistency tests pass (new worker has source-module mapping + heartbeat call)
- [ ] Architecture doc §11e published

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-19-pr10-symbol-allowlist-and-stablecoin-filter.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — Fresh subagent per task + two-stage review (spec compliance + code quality). Matches PR8/PR9 pattern.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch with checkpoints.

**Which approach?**
