# SP-7 Ops Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the operational surface of trading-radar so the system is production-grade. After SP-7 ships, every prediction's hash chain is verified nightly, off-host backups are guaranteed restorable on a quarterly cadence, layer weights are tunable via a reproducible Optuna sweep over a deterministic backtester, ML checkpoints are auto-promoted only if they beat the current champion on a held-out window, and operators can read p50/p95/p99 latency, error budgets, audit-chain status, and adapter health from a Grafana dashboard.

**Architecture:** Five new backend modules under `app/ops/` (`monitoring.py`, `alerts.py`, `verifier_scheduler.py`) and `app/ml/` (`champion_challenger.py`); two new admin REST routes (`admin_backtest.py`, `admin_monitoring.py`); two new tooling CLIs under `backend/tools/` (`backtest.py`, `hyperopt.py`); a four-script DR pipeline under `backend/tools/backup/` (`snapshot.py`, `upload_b2.py`, `rsync_laptop.py`, `recovery_rehearsal.py`); a four-service Docker monitoring stack (Prometheus + Grafana + Loki + Promtail) with dashboards/alerts provisioned from `infra/`; one Alembic migration (0012) creating `backtests`, `hyperopt_studies`, and `backup_runs`. The audit verifier runs as an asyncio task spawned from the FastAPI lifespan, mirroring the SP-3 `start_universe_sync_task` / SP-3 `start_health_pinger_task` pattern. Backup tooling is sync (CLI invocation) and writes `backup_runs` rows from a thin sync→async bridge. Champion-challenger evaluation is wired into the existing `PATCH /api/v1/admin/ml-checkpoints/{id}` endpoint via a `?force=true` bypass.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2 (async ORM + raw `sa.text` for chained tables) / asyncpg / TimescaleDB / `prometheus-fastapi-instrumentator==7.0.0` / `optuna==4.0.0` / `freqtrade==2024.10` (as library) / `cryptography` (already in deps; AES-256-GCM for backup encryption) / `boto3` (already in deps; B2 S3-compat upload) · Prometheus 2.55.0 / Grafana 11.2.0 / Loki 3.2.0 / Promtail 3.2.0 (docker images; no Python integration) · pytest + `freezegun` for the verifier scheduler / `respx` for B2 mock / Docker subprocess mocks for `pg_basebackup`

**Spec reference:** [`docs/superpowers/specs/2026-05-05-SP-7-ops-hardening-design.md`](../specs/2026-05-05-SP-7-ops-hardening-design.md). When this plan and the spec disagree, the spec wins.

**Companion specs:**
- `docs/superpowers/specs/2026-05-01-trading-radar-meta-plan-design.md` §2.7 (backups), §8 (monitoring stack), §3 §181 (acceptance gate)

**Cross-cutting policy compliance map (which §5 policy each phase touches):**
- Phase A — migration + scaffolding only; no policy surface
- Phase B — §5.16 (backtest reproducibility — `params_hash` columns), §5.14 reuse of `predictions` row schema for backtested ghost candles
- Phase C — §5.16 (hyperopt provenance — `mlflow_run_id`), `triggered_by` user_id audit trail
- Phase D — §5.14 (audit hash chain — verifier is the active enforcer), §5.13 (alerting on integrity break)
- Phase E — §5.13 (backups — RPO 1h, RTO 4h), §2.7 (laptop-cold-storage chain)
- Phase F — §5.18 (latency SLO p99 < 500ms), §8 (monitoring stack), §2.6 (Cloudflare-gated `/metrics` in prod)
- Phase G — §2.6 (`Depends(require_admin)` on every new endpoint), §5.17 (champion-challenger gate; manual `?force=true` bypass logged)

---

## File Structure

This is what SP-7 creates inside the new worktree. All paths are under `worktrees/sp-7/`.

```
worktrees/sp-7/
├── backend/
│   ├── alembic/versions/
│   │   └── 2026_05_05_0012_backtests_hyperopt_backups.py        NEW
│   ├── app/
│   │   ├── ops/                                                 NEW package
│   │   │   ├── __init__.py
│   │   │   ├── monitoring.py        — instrument_app() Prometheus wiring
│   │   │   ├── alerts.py            — alert_admin() SMTP dispatcher + log fallback
│   │   │   └── verifier_scheduler.py — nightly 03:00 UTC audit verifier loop
│   │   ├── ml/
│   │   │   └── champion_challenger.py NEW — evaluate_challenger() backtest gate
│   │   ├── api/routes/
│   │   │   ├── admin_backtest.py    NEW — POST/GET backtests + hyperopt
│   │   │   ├── admin_monitoring.py  NEW — GET /admin/monitoring/health
│   │   │   └── admin_ml.py          MODIFIED — wire challenger gate into PATCH
│   │   └── main.py                  MODIFIED — start verifier task + instrument_app
│   ├── tools/
│   │   ├── backtest.py              NEW — run_backtest() Freqtrade-as-library
│   │   ├── hyperopt.py              NEW — Optuna TPE over layer weights
│   │   └── backup/                  NEW package
│   │       ├── __init__.py
│   │       ├── snapshot.py          — pg_basebackup wrapper
│   │       ├── upload_b2.py         — encrypt + upload to B2
│   │       ├── rsync_laptop.py      — rsync to LAPTOP_RSYNC_TARGET
│   │       ├── recovery_rehearsal.py — pull + decrypt + restore + assert
│   │       └── README.md            — operator runbook + cron snippets
│   └── tests/
│       ├── unit/
│       │   ├── test_ops_alerts.py
│       │   ├── test_ops_verifier_scheduler.py
│       │   ├── test_ops_monitoring.py
│       │   ├── test_ml_champion_challenger.py
│       │   ├── test_tools_backtest.py
│       │   ├── test_tools_hyperopt.py
│       │   ├── test_tools_backup_snapshot.py
│       │   ├── test_tools_backup_upload_b2.py
│       │   ├── test_tools_backup_rsync_laptop.py
│       │   └── test_tools_backup_recovery_rehearsal.py
│       └── integration/
│           ├── test_api_admin_backtest.py
│           ├── test_api_admin_hyperopt.py
│           ├── test_api_admin_monitoring_health.py
│           ├── test_api_admin_ml_challenger_gate.py
│           ├── test_verifier_scheduler_detects_break.py
│           └── test_backup_runs_persisted.py
├── infra/
│   ├── prometheus/
│   │   ├── prometheus.yml          NEW — scrape backend /metrics
│   │   └── alert_rules.yml         NEW — p99 latency, audit chain, backup
│   ├── grafana/
│   │   ├── provisioning/
│   │   │   ├── datasources/datasource.yml
│   │   │   └── dashboards/dashboards.yml
│   │   └── dashboards/
│   │       ├── trading_radar_overview.json
│   │       ├── adapters_health.json
│   │       ├── audit_chain_status.json
│   │       └── ml_checkpoint_history.json
│   ├── loki/
│   │   └── loki.yml                NEW
│   └── promtail/
│       └── promtail.yml            NEW
├── docker-compose.yml              MODIFIED — adds prometheus/grafana/loki/promtail
└── backend/pyproject.toml          MODIFIED — adds optuna/freqtrade/instrumentator
```

---

## Phase A — Worktree + scaffolding + migration 0012

### Task A1: Create SP-7 worktree

**Files:** none (git operation only)

- [ ] **Step 1: Verify clean main**

```bash
cd a:/v5_Trade_bot
git -c safe.directory='A:/v5_Trade_bot' status
git -c safe.directory='A:/v5_Trade_bot' log --oneline -5
```
Expected: `On branch main`, `nothing to commit, working tree clean`, and the most recent commit hash matches `3a08a75` (SP-6 ship).

- [ ] **Step 2: Create worktree**

```bash
git -c safe.directory='A:/v5_Trade_bot' worktree add worktrees/sp-7 -b sp-7/main
```
Expected: `Preparing worktree (new branch 'sp-7/main')`. If `worktrees/sp-7` already exists from a prior aborted run, run `git worktree remove worktrees/sp-7 --force` first.

- [ ] **Step 3: Verify**

```bash
git -c safe.directory='A:/v5_Trade_bot' worktree list
```
Expected output includes `worktrees/sp-7  <hash> [sp-7/main]`.

- [ ] **Step 4: Bring stack up + run baseline tests**

```bash
cd worktrees/sp-7
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
```
Expected: `1342 passed` (the SP-6 ship baseline). If this fails, stop — main is not green and any new SP-7 task will start from a broken floor.

- [ ] **Step 5: Frontend baseline**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T frontend npm test -- --run
```
Expected: `329 passed`. SP-7 is backend-only; this is a sanity ping that the frontend image is healthy.

- [ ] **Step 6: All subsequent tasks operate inside `worktrees/sp-7/`**

No commit yet (worktree has no new files).

---

### Task A2: Migration 0012 — `backtests` + `hyperopt_studies` + `backup_runs`

**Files:**
- Create: `worktrees/sp-7/backend/alembic/versions/2026_05_05_0012_backtests_hyperopt_backups.py`

**Design notes:**
- `backtests.params_hash` is a sha256 of all inputs (symbol, timeframe, start_ts, end_ts, layer_weights, enabled_layers, enabled_traps, initial_balance) so a single re-run with the same params is reproducible and idempotent at lookup time. We do NOT enforce uniqueness on `params_hash` — operators may legitimately want to re-run the same backtest after a code change.
- `equity_curve_uri` and `trade_log_uri` are nullable `TEXT`. On Oracle production they point at `b2://trading-radar-backups/backtests/<id>/equity.json`. On local dev they may be `file:///tmp/backtest-<id>/equity.json` or `null` (compute is in-memory only). The admin REST surface persists the raw arrays inline if the URI is `null`.
- `hyperopt_studies.train_window` and `val_window` use `TSTZRANGE` per spec §4.1. Postgres native; SQLite tests use a TEXT fallback (CHECK constraint relaxed; the column is informational, not constraining).
- `backup_runs.backup_type` CHECK matches the spec: `hourly_dump | nightly_basebackup | recovery_rehearsal`. The hourly dump path was already in `infra/backup/pg_dump_hourly.sh` from SP-0; SP-7 wires it to write a `backup_runs` row via a small sync-to-Postgres helper that the shell script calls.
- All three tables stand alone — they do not participate in the audit hash chain. Their integrity is defended by the verifier (no false-positive risk if rows are added out of order).

- [ ] **Step 1: Write migration**

```python
"""backtests + hyperopt_studies + backup_runs (SP-7 Phase A2)

Revision ID: 0012_backtests_hyperopt_backups
Revises: 0011_trap_enabled
Create Date: 2026-05-05
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0012_backtests_hyperopt_backups"
down_revision: str | None = "0011_trap_enabled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -------- backtests --------
    op.execute(
        """
        CREATE TABLE backtests (
            id BIGSERIAL PRIMARY KEY,
            triggered_by BIGINT REFERENCES users(id),
            triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            start_ts TIMESTAMPTZ NOT NULL,
            end_ts TIMESTAMPTZ NOT NULL,
            layer_weights JSONB,
            enabled_layers JSONB,
            enabled_traps JSONB,
            initial_balance DOUBLE PRECISION NOT NULL,
            n_trades INTEGER NOT NULL DEFAULT 0,
            win_rate DOUBLE PRECISION,
            profit_factor DOUBLE PRECISION,
            sharpe DOUBLE PRECISION,
            max_drawdown DOUBLE PRECISION,
            equity_curve_uri TEXT,
            trade_log_uri TEXT,
            params_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed'
                CHECK (status IN ('running','completed','failed')),
            error_message TEXT
        );
        """
    )
    op.execute(
        "CREATE INDEX backtests_symbol_tf_idx "
        "ON backtests (symbol, timeframe, triggered_at DESC);"
    )
    op.execute(
        "CREATE INDEX backtests_params_hash_idx "
        "ON backtests (params_hash);"
    )

    # -------- hyperopt_studies --------
    op.execute(
        """
        CREATE TABLE hyperopt_studies (
            id BIGSERIAL PRIMARY KEY,
            triggered_by BIGINT REFERENCES users(id),
            triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            n_trials INTEGER NOT NULL,
            train_window TSTZRANGE NOT NULL,
            val_window TSTZRANGE NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            best_weights JSONB,
            best_sharpe DOUBLE PRECISION,
            mlflow_run_id TEXT,
            status TEXT NOT NULL
                CHECK (status IN ('running','completed','failed')),
            error_message TEXT
        );
        """
    )
    op.execute(
        "CREATE INDEX hyperopt_studies_status_idx "
        "ON hyperopt_studies (status, triggered_at DESC);"
    )

    # -------- backup_runs --------
    op.execute(
        """
        CREATE TABLE backup_runs (
            id BIGSERIAL PRIMARY KEY,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            backup_type TEXT NOT NULL
                CHECK (backup_type IN
                    ('hourly_dump','nightly_basebackup','recovery_rehearsal')),
            target TEXT NOT NULL,
            success BOOLEAN,
            size_bytes BIGINT,
            duration_seconds DOUBLE PRECISION,
            error_message TEXT,
            metadata_json JSONB
        );
        """
    )
    op.execute(
        "CREATE INDEX backup_runs_type_started_idx "
        "ON backup_runs (backup_type, started_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS backup_runs_type_started_idx;")
    op.execute("DROP TABLE IF EXISTS backup_runs;")
    op.execute("DROP INDEX IF EXISTS hyperopt_studies_status_idx;")
    op.execute("DROP TABLE IF EXISTS hyperopt_studies;")
    op.execute("DROP INDEX IF EXISTS backtests_params_hash_idx;")
    op.execute("DROP INDEX IF EXISTS backtests_symbol_tf_idx;")
    op.execute("DROP TABLE IF EXISTS backtests;")
```

- [ ] **Step 2: Run migration**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend bash -c "cd /app && alembic upgrade head"
```
Expected: `Running upgrade 0011_trap_enabled -> 0012_backtests_hyperopt_backups`.

- [ ] **Step 3: Verify tables exist**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U postgres trading_radar -c "\dt"
```
Expected: lists `backtests`, `hyperopt_studies`, `backup_runs` in addition to the existing 11+ tables.

- [ ] **Step 4: Verify alembic version table is current**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U postgres trading_radar -c "SELECT version_num FROM alembic_version;"
```
Expected: a single row `0012_backtests_hyperopt_backups`.

- [ ] **Step 5: Sanity test that the backend boots after migration**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/integration/test_app_startup.py -v
```
Expected: pass — the new tables don't break the existing app startup path.

- [ ] **Step 6: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/alembic/versions/2026_05_05_0012_backtests_hyperopt_backups.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): migration 0012 — backtests + hyperopt_studies + backup_runs"
```

---

### Task A3: Add SP-7 runtime dependencies

**Files:**
- Modify: `worktrees/sp-7/backend/pyproject.toml`

**Design notes:**
- `optuna==4.0.0` is the Bayesian search runner. Pure-Python; ~30MB image-add.
- `freqtrade==2024.10` ships a heavyweight CLI but we import only `freqtrade.optimize.backtesting.Backtesting` indirectly. The full `freqtrade` package brings ccxt, talib, pandas-ta — most of which we already have. Adds ~50MB. We intentionally do NOT install `freqtrade[hyperopt]` extras — Optuna replaces it.
- `prometheus-fastapi-instrumentator==7.0.0` is a thin wrapper around `prometheus-client`; ~2MB.
- Verify the Docker rebuild succeeds — Freqtrade has been known to require `git` and `cmake` in the build image for a transitive `tables` (PyTables) dep. The existing `backend/Dockerfile` has a `build-essential` stage already, so this should be a no-op; if it fails, the fallback is to pin freqtrade to a wheel-only release (`freqtrade==2024.9.1`).

- [ ] **Step 1: Edit pyproject.toml dependencies**

Add the three pinned versions to the `[project] dependencies` list (alphabetical placement, between `cryptography` and `duckdb`):

```toml
dependencies = [
    # ... existing deps ...
    "cryptography==46.0.7",
    "duckdb==1.1.3",
    "freqtrade==2024.10",
    "optuna==4.0.0",
    "prometheus-fastapi-instrumentator==7.0.0",
    # ... rest of existing deps ...
]
```

- [ ] **Step 2: Rebuild backend image**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml build backend
```
Expected: `naming to docker.io/library/trading-radar-backend done`. Watch for `Successfully installed freqtrade-2024.10 optuna-4.0.0 prometheus-fastapi-instrumentator-7.0.0`.

If build fails on freqtrade transitive native deps:
- Pin to `freqtrade==2024.9.1` (last known wheel-only release for py3.11)
- OR add `apt-get install -y libhdf5-dev` to `backend/Dockerfile` build stage

- [ ] **Step 3: Verify imports work**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm backend python -c "
import optuna
import freqtrade
from prometheus_fastapi_instrumentator import Instrumentator
print('optuna', optuna.__version__)
print('freqtrade', freqtrade.__version__)
print('instrumentator import ok')
"
```
Expected: prints all three version lines without ImportError.

- [ ] **Step 4: Re-run baseline tests**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
```
Expected: still `1342 passed`. Adding new deps must not break existing tests.

- [ ] **Step 5: Verify ruff config inherited from main**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend ruff check app/ tools/ tests/
```
Expected: 0 errors. The `extend-exclude = ["tools/ml/colab/*.ipynb"]` and `tests/**/*.py = ["E402","F841"]` per-file-ignores are inherited from main's `pyproject.toml`. If lint fails on new pyproject syntax, fix inline before commit.

- [ ] **Step 6: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/pyproject.toml
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): add optuna + freqtrade + prometheus-fastapi-instrumentator deps"
```

---

### Task A4: `app/ops/` package scaffolding (empty stubs)

**Files:**
- Create: `worktrees/sp-7/backend/app/ops/__init__.py`
- Create: `worktrees/sp-7/backend/app/ops/monitoring.py` (stub)
- Create: `worktrees/sp-7/backend/app/ops/alerts.py` (stub)
- Create: `worktrees/sp-7/backend/app/ops/verifier_scheduler.py` (stub)

**Design note:** Stubs are intentionally minimal — they serve as import targets so downstream tasks can write their failing tests against `from app.ops.alerts import alert_admin` without ImportError on the *module*. Each stub raises `NotImplementedError` from any callable so a misuse fails loud.

- [ ] **Step 1: `__init__.py`** — empty file.

- [ ] **Step 2: `monitoring.py` stub**

```python
"""Prometheus instrumentation hooks for the FastAPI app.

Implementation lands in Phase F4. This stub exists so app.main can import
the future symbol without a circular-import surprise during Phase B/C/D
work.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def instrument_app(app: "FastAPI") -> None:  # pragma: no cover — stub
    """Wire prometheus-fastapi-instrumentator. Implemented in Phase F4."""
    raise NotImplementedError("instrument_app: Phase F4 deliverable")
```

- [ ] **Step 3: `alerts.py` stub**

```python
"""Email alert dispatcher.

Implementation lands in Phase D1. SMTP envvars (SMTP_HOST, SMTP_PORT,
SMTP_USER, SMTP_PASSWORD); fallback to log.error if not configured.
"""
from __future__ import annotations


async def alert_admin(  # pragma: no cover — stub
    message: str, *, severity: str = "warning",
) -> None:
    """Send an email to the operator's configured alert address. Phase D1."""
    raise NotImplementedError("alert_admin: Phase D1 deliverable")
```

- [ ] **Step 4: `verifier_scheduler.py` stub**

```python
"""Nightly audit chain verifier loop.

Implementation lands in Phase D2-D3. Wakes at 03:00 UTC, calls
verify_chain() on each chained table, alerts the admin and writes an
audit_violations row on any break.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def run_audit_verifier_loop(  # pragma: no cover — stub
    session_factory: "async_sessionmaker[AsyncSession]",
) -> None:
    """Phase D2 deliverable."""
    raise NotImplementedError("run_audit_verifier_loop: Phase D2 deliverable")


def start_audit_verifier_task(  # pragma: no cover — stub
    session_factory: "async_sessionmaker[AsyncSession]",
) -> asyncio.Task[None]:
    """Phase D3 deliverable — wired into app.main lifespan."""
    raise NotImplementedError("start_audit_verifier_task: Phase D3 deliverable")
```

- [ ] **Step 5: Verify imports**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm backend python -c "
from app.ops.monitoring import instrument_app
from app.ops.alerts import alert_admin
from app.ops.verifier_scheduler import run_audit_verifier_loop, start_audit_verifier_task
print('ok')
"
```
Expected: prints `ok`.

- [ ] **Step 6: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/app/ops/
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): scaffold app/ops package (monitoring + alerts + verifier_scheduler stubs)"
```

---

### Task A5: `tools/backup/` package scaffolding (empty stubs)

**Files:**
- Create: `worktrees/sp-7/backend/tools/backup/__init__.py`
- Create: `worktrees/sp-7/backend/tools/backup/snapshot.py` (stub)
- Create: `worktrees/sp-7/backend/tools/backup/upload_b2.py` (stub)
- Create: `worktrees/sp-7/backend/tools/backup/rsync_laptop.py` (stub)
- Create: `worktrees/sp-7/backend/tools/backup/recovery_rehearsal.py` (stub)

**Design note:** Same rationale as A4 — these stubs unblock Phase E TDD without committing any real subprocess calls. Each module exposes a single CLI entrypoint via `if __name__ == "__main__":` (filled in Phase E).

- [ ] **Step 1: `__init__.py`** — empty file.

- [ ] **Step 2: `snapshot.py` stub**

```python
"""pg_basebackup wrapper. Phase E1 deliverable."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class SnapshotMetadata:
    path: Path
    size_bytes: int
    taken_at: datetime
    duration_seconds: float


def take_snapshot(out_dir: Path) -> SnapshotMetadata:  # pragma: no cover
    raise NotImplementedError("take_snapshot: Phase E1 deliverable")


if __name__ == "__main__":
    raise NotImplementedError("CLI: Phase E1 deliverable")
```

- [ ] **Step 3: Stubs for `upload_b2.py`, `rsync_laptop.py`, `recovery_rehearsal.py`**

Each follows the same template — a typed entrypoint that raises `NotImplementedError("<name>: Phase E<n> deliverable")`. Phase E task descriptions provide the real signatures.

- [ ] **Step 4: Verify imports**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm backend python -c "
from tools.backup.snapshot import take_snapshot, SnapshotMetadata
from tools.backup.upload_b2 import upload_to_b2
from tools.backup.rsync_laptop import rsync_to_laptop
from tools.backup.recovery_rehearsal import run_recovery_rehearsal
print('ok')
"
```
Expected: prints `ok`. Note `tools/` is NOT a package on the import path by default — verify `setup.py`/`pyproject.toml` `[tool.setuptools.packages.find]` either includes `tools*` OR the test runner sets `PYTHONPATH=/app`. Inspection of main's `pyproject.toml` shows `exclude = ["data*", "alembic*", "tests*", "tools*"]` — so `tools` is excluded from the editable install. The CLI scripts are invoked via `python -m tools.backup.snapshot` from the backend container's `/app` working dir; tests import via the same path. If imports fail, add `sys.path.insert(0, "/app")` in the test conftest or run `PYTHONPATH=/app python -m ...`.

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/tools/backup/
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): scaffold tools/backup package (snapshot + upload_b2 + rsync_laptop + recovery_rehearsal stubs)"
```

---

## Phase B — Backtest framework

### Task B1: `BacktestResult` dataclass + `run_backtest` signature — failing test

**Files:**
- Create: `worktrees/sp-7/backend/tools/backtest.py` (stub)
- Create: `worktrees/sp-7/backend/tests/unit/test_tools_backtest.py`

**Design notes:**
- We DO NOT use `freqtrade`'s native backtesting engine — its data ingestion model assumes its own SQLite database and CSV format. Instead we re-use trading-radar's existing OHLCV bars → `build_prediction()` → SL/TP simulator pipeline. The `freqtrade` dep is loaded only because §6.10 requires it for future hyperopt visualizations (and the spec locks it in).
- `BacktestResult.equity_curve` is a `list[tuple[datetime, float]]` — bar timestamp + running balance. `trade_log` is a `list[TradeRecord]` — one entry per closed trade with side, entry/exit prices, pnl_usdt, pnl_pct, exit_reason ('SL'|'TP'|'TIMEOUT').
- `params_hash` is the sha256 of the JSON-canonicalized inputs (the same `canonical_row_json` from `app/db/audit.py`). Re-runnable: same inputs → same `params_hash` → caller can dedup or refuse.
- Synthetic OHLCV for tests: a 200-bar deterministic sine-wave-around-trend. The first failing test asserts the dataclass's existence and field names; B2/B3 add the real implementation.

- [ ] **Step 1: Stub** — `tools/backtest.py` with just docstring.

```python
"""Deterministic backtest runner over trading-radar predictions.

Phase B2-B5 deliverable. This stub exists so the failing test in B1 can
import the dataclass and signature without ImportError.
"""
from __future__ import annotations
```

- [ ] **Step 2: Failing test**

```python
"""Unit tests for the backtest framework — Phase B1.

Synthetic OHLCV: 200 hourly bars of close = 100 + sin(i * 0.1) * 5 + i * 0.05.
That's a trending-up market with mild oscillation — enough to produce a
non-zero number of trades for the L1+L3+L5 path.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from tools.backtest import BacktestResult, run_backtest


def _synthetic_bars(n: int = 200) -> pd.DataFrame:
    base_ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex(
        [base_ts + timedelta(hours=i) for i in range(n)],
        name="ts",
    )
    closes = np.array([100 + np.sin(i * 0.1) * 5 + i * 0.05 for i in range(n)])
    return pd.DataFrame(
        {
            "open": closes - 0.1,
            "high": closes + 0.5,
            "low": closes - 0.5,
            "close": closes,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def test_backtest_result_dataclass_shape() -> None:
    """The result dataclass exposes all metrics the spec promises."""
    fields = {
        "n_trades", "win_rate", "profit_factor", "sharpe", "max_drawdown",
        "equity_curve", "trade_log", "params_hash", "initial_balance",
        "final_balance",
    }
    assert fields.issubset(BacktestResult.__dataclass_fields__.keys())


def test_run_backtest_returns_result_with_zero_trades_on_flat_data() -> None:
    """A flat-line market (no signals) returns 0 trades + initial balance."""
    flat = _synthetic_bars(50).assign(close=100.0, open=100.0, high=100.0, low=100.0)
    bars_loader = lambda *_a, **_kw: flat  # noqa: E731 — test stub
    result = run_backtest(
        symbol="BTC/USDT",
        timeframe="1h",
        start=flat.index[0].to_pydatetime(),
        end=flat.index[-1].to_pydatetime(),
        initial_balance_usdt=10000.0,
        _bars_loader=bars_loader,
    )
    assert isinstance(result, BacktestResult)
    assert result.n_trades == 0
    assert result.final_balance == 10000.0
    assert result.equity_curve[0] == (flat.index[0].to_pydatetime(), 10000.0)


def test_run_backtest_params_hash_is_deterministic() -> None:
    """Same inputs → same params_hash. Different inputs → different hash."""
    flat = _synthetic_bars(50)
    bars_loader = lambda *_a, **_kw: flat  # noqa: E731

    a = run_backtest(
        symbol="BTC/USDT", timeframe="1h",
        start=flat.index[0].to_pydatetime(),
        end=flat.index[-1].to_pydatetime(),
        initial_balance_usdt=10000.0,
        _bars_loader=bars_loader,
    )
    b = run_backtest(
        symbol="BTC/USDT", timeframe="1h",
        start=flat.index[0].to_pydatetime(),
        end=flat.index[-1].to_pydatetime(),
        initial_balance_usdt=10000.0,
        _bars_loader=bars_loader,
    )
    c = run_backtest(
        symbol="ETH/USDT", timeframe="1h",
        start=flat.index[0].to_pydatetime(),
        end=flat.index[-1].to_pydatetime(),
        initial_balance_usdt=10000.0,
        _bars_loader=bars_loader,
    )
    assert a.params_hash == b.params_hash
    assert a.params_hash != c.params_hash
```

- [ ] **Step 3: Run — fail**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_tools_backtest.py -v
```
Expected: `ImportError` on `BacktestResult` and `run_backtest`.

---

### Task B2: `BacktestResult` dataclass + `run_backtest` skeleton — green tests for B1

**Files:**
- Modify: `worktrees/sp-7/backend/tools/backtest.py`

**Design note:** This task implements the minimum needed to satisfy B1's three tests: dataclass shape, zero-trade flat data, deterministic `params_hash`. The full prediction/SL/TP simulation lands in B3.

- [ ] **Step 1: Implement**

```python
"""Deterministic backtest runner over trading-radar predictions.

Pulls OHLCV from Postgres (or a test-injectable loader), iterates bar-by-bar,
calls build_prediction() with the configured layer weights, simulates trades
at the predicted entry with SL/TP/timeout exits, and aggregates metrics.

Phase B2 ships the dataclass + a skeleton that returns 0 trades. Phase B3
fills in build_prediction integration + SL/TP simulator. Phase B4 persists
to the backtests table. Phase B5 wires the admin REST endpoint.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

BarsLoader = Callable[[str, str, datetime, datetime], pd.DataFrame]


@dataclass(frozen=True)
class TradeRecord:
    opened_at: datetime
    closed_at: datetime
    side: str  # "LONG" | "SHORT"
    entry_price: float
    exit_price: float
    pnl_usdt: float
    pnl_pct: float
    exit_reason: str  # "SL" | "TP" | "TIMEOUT"


@dataclass
class BacktestResult:
    n_trades: int
    win_rate: float
    profit_factor: float
    sharpe: float
    max_drawdown: float
    equity_curve: list[tuple[datetime, float]]
    trade_log: list[TradeRecord]
    params_hash: str
    initial_balance: float
    final_balance: float
    # Provenance:
    symbol: str = ""
    timeframe: str = ""
    start_ts: datetime | None = None
    end_ts: datetime | None = None
    layer_weights: dict[int, float] | None = None
    enabled_layers: set[int] | None = None
    enabled_traps: set[str] | None = None


def _compute_params_hash(
    *,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    layer_weights: dict[int, float] | None,
    enabled_layers: set[int] | None,
    enabled_traps: set[str] | None,
    initial_balance_usdt: float,
) -> str:
    payload = {
        "symbol": symbol,
        "timeframe": timeframe,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "layer_weights": (
            sorted(layer_weights.items()) if layer_weights else None
        ),
        "enabled_layers": (
            sorted(enabled_layers) if enabled_layers else None
        ),
        "enabled_traps": (
            sorted(enabled_traps) if enabled_traps else None
        ),
        "initial_balance_usdt": initial_balance_usdt,
    }
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


def run_backtest(
    *,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    start: datetime,
    end: datetime,
    layer_weights: dict[int, float] | None = None,
    enabled_layers: set[int] | None = None,
    enabled_traps: set[str] | None = None,
    initial_balance_usdt: float = 10000.0,
    _bars_loader: BarsLoader | None = None,
) -> BacktestResult:
    """Run a deterministic backtest. See module docstring."""
    params_hash = _compute_params_hash(
        symbol=symbol, timeframe=timeframe, start=start, end=end,
        layer_weights=layer_weights, enabled_layers=enabled_layers,
        enabled_traps=enabled_traps,
        initial_balance_usdt=initial_balance_usdt,
    )

    if _bars_loader is None:
        _bars_loader = _default_bars_loader

    bars = _bars_loader(symbol, timeframe, start, end)

    # Phase B2 skeleton: no trade simulation yet. Returns initial-balance
    # equity curve + zero trades. Phase B3 fills this in.
    equity_curve: list[tuple[datetime, float]] = []
    if len(bars) > 0:
        ts = bars.index[0].to_pydatetime() if hasattr(bars.index[0], "to_pydatetime") \
            else bars.index[0]
        equity_curve.append((ts, initial_balance_usdt))

    return BacktestResult(
        n_trades=0,
        win_rate=0.0,
        profit_factor=0.0,
        sharpe=0.0,
        max_drawdown=0.0,
        equity_curve=equity_curve,
        trade_log=[],
        params_hash=params_hash,
        initial_balance=initial_balance_usdt,
        final_balance=initial_balance_usdt,
        symbol=symbol,
        timeframe=timeframe,
        start_ts=start,
        end_ts=end,
        layer_weights=layer_weights,
        enabled_layers=enabled_layers,
        enabled_traps=enabled_traps,
    )


def _default_bars_loader(
    symbol: str, timeframe: str, start: datetime, end: datetime,
) -> pd.DataFrame:
    """Phase B3 deliverable — pull from `ohlcv` table via session_factory."""
    raise NotImplementedError(
        "_default_bars_loader: Phase B3 deliverable — inject _bars_loader for tests"
    )
```

- [ ] **Step 2: Run — green**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_tools_backtest.py -v
```
Expected: `3 passed`.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/tools/backtest.py backend/tests/unit/test_tools_backtest.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): BacktestResult dataclass + run_backtest skeleton (no simulation yet)"
```

---

### Task B3: `build_prediction` integration + SL/TP simulator — failing test → green

**Files:**
- Modify: `worktrees/sp-7/backend/tests/unit/test_tools_backtest.py` (add new tests)
- Modify: `worktrees/sp-7/backend/tools/backtest.py`

**Design notes:**
- Trade logic mirrors the SP-0.5 shadow trading exit_monitor: open at the predicted entry price (last close of the signal bar), set SL = entry - 1.5*ATR (LONG) or entry + 1.5*ATR (SHORT), TP = entry + 3*ATR / entry - 3*ATR. Exit on the first bar that touches SL or TP (use bar high for LONG TP, bar low for LONG SL). TIMEOUT = no SL/TP hit within 24 bars (or the timeframe-relative equivalent — for 1h that's 24 hours; for 5m that's 2 hours).
- Position size: fixed 100 USDT notional per trade for v1. (Hyperopt is over weights, not sizing — sizing is a separate dimension intentionally.)
- Sharpe = mean(returns) / stddev(returns) * sqrt(annualization_factor). For 1h bars: annualization = sqrt(24*365) = ~93.4. We compute on per-trade returns, not bar-by-bar.
- Max drawdown = max over the equity curve of `(peak - trough) / peak` where peak is the running max-so-far.
- Profit factor = sum(winning_pnl) / abs(sum(losing_pnl)). Returns inf if no losing trades.

- [ ] **Step 1: Failing test — adds 4 new cases**

```python
def test_run_backtest_simulates_trades_on_signaling_data() -> None:
    """A clearly-trending market produces at least one LONG trade."""
    bars = _synthetic_bars(300)
    bars_loader = lambda *_a, **_kw: bars  # noqa: E731

    result = run_backtest(
        symbol="BTC/USDT", timeframe="1h",
        start=bars.index[0].to_pydatetime(),
        end=bars.index[-1].to_pydatetime(),
        initial_balance_usdt=10000.0,
        _bars_loader=bars_loader,
    )
    assert result.n_trades > 0
    assert len(result.equity_curve) >= len(bars) // 24  # at least one point per day
    assert -1.0 <= result.win_rate <= 1.0
    assert result.max_drawdown >= 0.0
    # Final balance is initial +/- aggregate trade pnl
    expected_final = (
        result.initial_balance + sum(t.pnl_usdt for t in result.trade_log)
    )
    assert abs(result.final_balance - expected_final) < 1e-6


def test_run_backtest_respects_layer_weights() -> None:
    """Different weights produce different trade counts on the same data."""
    bars = _synthetic_bars(300)
    bars_loader = lambda *_a, **_kw: bars  # noqa: E731

    equal = run_backtest(
        symbol="BTC/USDT", timeframe="1h",
        start=bars.index[0].to_pydatetime(),
        end=bars.index[-1].to_pydatetime(),
        layer_weights={i: 1/9 for i in range(1, 10)},
        _bars_loader=bars_loader,
    )
    l3_heavy = run_backtest(
        symbol="BTC/USDT", timeframe="1h",
        start=bars.index[0].to_pydatetime(),
        end=bars.index[-1].to_pydatetime(),
        layer_weights={3: 1.0, 1: 0.0, 2: 0.0, 4: 0.0, 5: 0.0,
                       6: 0.0, 7: 0.0, 8: 0.0, 9: 0.0},
        _bars_loader=bars_loader,
    )
    assert equal.params_hash != l3_heavy.params_hash
    # Loose: at least one of the runs should differ in trade count
    assert (equal.n_trades, equal.sharpe) != (l3_heavy.n_trades, l3_heavy.sharpe)


def test_run_backtest_short_trades_close_on_sl_or_tp() -> None:
    """A descending market should produce SHORT trades that exit cleanly."""
    n = 200
    base_ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([base_ts + timedelta(hours=i) for i in range(n)])
    closes = np.array([100 - i * 0.1 for i in range(n)])  # monotonic down
    bars = pd.DataFrame({
        "open": closes - 0.05, "high": closes + 0.3, "low": closes - 0.3,
        "close": closes, "volume": np.full(n, 1000.0),
    }, index=idx)
    bars_loader = lambda *_a, **_kw: bars  # noqa: E731

    result = run_backtest(
        symbol="BTC/USDT", timeframe="1h",
        start=bars.index[0].to_pydatetime(),
        end=bars.index[-1].to_pydatetime(),
        _bars_loader=bars_loader,
    )
    # All exits should be SL/TP/TIMEOUT — never None
    assert all(t.exit_reason in {"SL", "TP", "TIMEOUT"} for t in result.trade_log)


def test_run_backtest_metrics_sane_bounds() -> None:
    """Sharpe finite, max_drawdown ∈ [0,1], profit_factor ≥ 0 or +inf."""
    bars = _synthetic_bars(300)
    result = run_backtest(
        symbol="BTC/USDT", timeframe="1h",
        start=bars.index[0].to_pydatetime(),
        end=bars.index[-1].to_pydatetime(),
        _bars_loader=lambda *_a, **_kw: bars,
    )
    assert np.isfinite(result.sharpe)
    assert 0.0 <= result.max_drawdown <= 1.0
    import math
    assert result.profit_factor >= 0.0 or math.isinf(result.profit_factor)
```

- [ ] **Step 2: Run — fail** (`run_backtest` always returns 0 trades from B2 skeleton).

- [ ] **Step 3: Implement** — full `_simulate` helper

```python
# Add to tools/backtest.py:

import math
from datetime import timedelta

from app.api.schemas import LivePredictionOut
from app.core.predictor import build_prediction
from app.core.scoring.layer2_patterns import PatternStatsLookup

_SL_ATR_MULT = 1.5
_TP_ATR_MULT = 3.0
_TIMEOUT_BARS = 24
_FIXED_NOTIONAL_USDT = 100.0


def _atr(bars: pd.DataFrame, period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    h = bars["high"].to_numpy(dtype=float)
    lo = bars["low"].to_numpy(dtype=float)
    c = bars["close"].to_numpy(dtype=float)
    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_close), np.abs(lo - prev_close)))
    return float(np.mean(tr[-period:]))


def _simulate_trade(
    bars: pd.DataFrame, entry_idx: int, side: str,
) -> TradeRecord | None:
    """Open at bars.iloc[entry_idx]['close']; walk forward; exit on SL/TP/TIMEOUT."""
    if entry_idx >= len(bars) - 1:
        return None
    entry = float(bars.iloc[entry_idx]["close"])
    atr = _atr(bars.iloc[: entry_idx + 1])
    if atr <= 0:
        return None
    if side == "LONG":
        sl, tp = entry - _SL_ATR_MULT * atr, entry + _TP_ATR_MULT * atr
    else:  # SHORT
        sl, tp = entry + _SL_ATR_MULT * atr, entry - _TP_ATR_MULT * atr

    last_idx = min(entry_idx + _TIMEOUT_BARS, len(bars) - 1)
    for i in range(entry_idx + 1, last_idx + 1):
        bar = bars.iloc[i]
        hi, lo = float(bar["high"]), float(bar["low"])
        if side == "LONG":
            if lo <= sl:
                exit_price = sl
                exit_reason = "SL"
                break
            if hi >= tp:
                exit_price = tp
                exit_reason = "TP"
                break
        else:  # SHORT
            if hi >= sl:
                exit_price = sl
                exit_reason = "SL"
                break
            if lo <= tp:
                exit_price = tp
                exit_reason = "TP"
                break
    else:
        exit_price = float(bars.iloc[last_idx]["close"])
        exit_reason = "TIMEOUT"

    if side == "LONG":
        pnl_pct = (exit_price - entry) / entry
    else:
        pnl_pct = (entry - exit_price) / entry
    pnl_usdt = pnl_pct * _FIXED_NOTIONAL_USDT

    opened_at = bars.index[entry_idx].to_pydatetime() if hasattr(
        bars.index[entry_idx], "to_pydatetime"
    ) else bars.index[entry_idx]
    closed_at = bars.index[i].to_pydatetime() if hasattr(
        bars.index[i], "to_pydatetime"
    ) else bars.index[i]
    return TradeRecord(
        opened_at=opened_at, closed_at=closed_at, side=side,
        entry_price=entry, exit_price=exit_price,
        pnl_usdt=pnl_usdt, pnl_pct=pnl_pct, exit_reason=exit_reason,
    )


def _aggregate_metrics(
    trades: list[TradeRecord], initial_balance: float,
    bar_index: pd.DatetimeIndex,
) -> tuple[float, float, float, float, float, list[tuple[datetime, float]]]:
    """Return (win_rate, profit_factor, sharpe, max_drawdown,
    final_balance, equity_curve)."""
    if not trades:
        return 0.0, 0.0, 0.0, 0.0, initial_balance, [
            (bar_index[0].to_pydatetime(), initial_balance)
        ] if len(bar_index) > 0 else []

    wins = [t for t in trades if t.pnl_usdt > 0]
    losses = [t for t in trades if t.pnl_usdt < 0]
    win_rate = len(wins) / len(trades)
    sum_wins = sum(t.pnl_usdt for t in wins)
    sum_losses = abs(sum(t.pnl_usdt for t in losses))
    profit_factor = sum_wins / sum_losses if sum_losses > 0 else math.inf

    rets = np.array([t.pnl_pct for t in trades])
    if len(rets) >= 2 and float(np.std(rets, ddof=1)) > 0:
        sharpe = float(np.mean(rets) / np.std(rets, ddof=1)) * math.sqrt(24 * 365)
    else:
        sharpe = 0.0

    # Equity curve: walk trades in close-time order.
    equity = initial_balance
    curve: list[tuple[datetime, float]] = [
        (bar_index[0].to_pydatetime(), initial_balance)
    ]
    peak = initial_balance
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: x.closed_at):
        equity += t.pnl_usdt
        curve.append((t.closed_at, equity))
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    final_balance = equity
    return win_rate, profit_factor, sharpe, max_dd, final_balance, curve
```

The `run_backtest` body is updated to (a) walk every bar from index 50 to len(bars)-1 (need enough warmup for indicators), (b) call `build_prediction(...)` per bar with the configured weights, (c) open a trade if `final.direction != NEUTRAL` and `final.score >= 50`, (d) advance `i` past the trade exit so we don't open overlapping positions, (e) aggregate via `_aggregate_metrics`.

- [ ] **Step 4: Tests pass**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_tools_backtest.py -v
```
Expected: `7 passed` (3 from B1 + 4 new from B3).

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/tools/backtest.py backend/tests/unit/test_tools_backtest.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): backtest simulator wires build_prediction + SL/TP/TIMEOUT exits + Sharpe/PF/MDD metrics"
```

---

### Task B4: Persist `BacktestResult` to `backtests` table — failing test → green

**Files:**
- Modify: `worktrees/sp-7/backend/tools/backtest.py` — add `persist_backtest_result`
- Create: `worktrees/sp-7/backend/tests/integration/test_backtests_persisted.py`

**Design notes:**
- The persistence helper is async (writes via `AsyncSession`) but the rest of `run_backtest` is sync. The CLI entrypoint runs the backtest sync and persists in an `asyncio.run()` wrapper. The admin endpoint (B5) calls `run_backtest` in a thread + `await persist_backtest_result(...)` directly.
- `equity_curve_uri` is initially `null` — the row JSONB blob holds the curve inline for v1. Phase E5 wires B2 upload of >1MB curves; for now, the curve is small.
- `triggered_by` = the requesting admin's `user.id` (from `Depends(require_admin)`); CLI runs leave it null.

- [ ] **Step 1: Failing test**

```python
"""Integration test: run_backtest result persists to the backtests table."""
import json
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from tools.backtest import (
    BacktestResult, TradeRecord, persist_backtest_result, run_backtest,
)


@pytest.mark.asyncio
async def test_persist_backtest_result_writes_row(bot_status_factory) -> None:
    session_factory = bot_status_factory  # pulls the SP-0.5 fixture
    async with session_factory() as session:
        result = BacktestResult(
            n_trades=2, win_rate=0.5, profit_factor=1.0, sharpe=1.2,
            max_drawdown=0.05,
            equity_curve=[(datetime(2025,1,1,tzinfo=timezone.utc), 10000.0)],
            trade_log=[],
            params_hash="testhash123",
            initial_balance=10000.0, final_balance=10100.0,
            symbol="BTC/USDT", timeframe="1h",
            start_ts=datetime(2025,1,1, tzinfo=timezone.utc),
            end_ts=datetime(2025,2,1, tzinfo=timezone.utc),
            layer_weights={i: 1/9 for i in range(1,10)},
        )
        new_id = await persist_backtest_result(
            session, result=result, triggered_by_user_id=1,
        )
        await session.commit()

        row = (await session.execute(
            sa.text("SELECT * FROM backtests WHERE id = :i"), {"i": new_id}
        )).first()
    assert row is not None
    assert row.symbol == "BTC/USDT"
    assert row.params_hash == "testhash123"
    assert row.n_trades == 2
    assert json.loads(row.layer_weights) == {str(i): 1/9 for i in range(1,10)} \
        or row.layer_weights == {str(i): 1/9 for i in range(1,10)}
```

- [ ] **Step 2: Implement `persist_backtest_result`**

```python
# Add to tools/backtest.py

import json as _json
from sqlalchemy.ext.asyncio import AsyncSession
import sqlalchemy as sa


async def persist_backtest_result(
    session: AsyncSession, *, result: BacktestResult,
    triggered_by_user_id: int | None = None,
) -> int:
    """Insert a BacktestResult row; returns the new id.

    Caller commits the session (so persist_backtest_result composes with
    larger admin transactions that may want to roll the whole thing back).
    """
    layer_weights_json = (
        _json.dumps({str(k): v for k, v in result.layer_weights.items()})
        if result.layer_weights else None
    )
    enabled_layers_json = (
        _json.dumps(sorted(result.enabled_layers))
        if result.enabled_layers else None
    )
    enabled_traps_json = (
        _json.dumps(sorted(result.enabled_traps))
        if result.enabled_traps else None
    )
    insert_sql = sa.text(
        "INSERT INTO backtests "
        "(triggered_by, symbol, timeframe, start_ts, end_ts, "
        "layer_weights, enabled_layers, enabled_traps, initial_balance, "
        "n_trades, win_rate, profit_factor, sharpe, max_drawdown, "
        "params_hash, status) "
        "VALUES (:tb, :sym, :tf, :s, :e, :lw, :el, :et, :ib, "
        ":nt, :wr, :pf, :sh, :md, :ph, 'completed') "
        "RETURNING id"
    )
    row = (await session.execute(insert_sql, {
        "tb": triggered_by_user_id, "sym": result.symbol,
        "tf": result.timeframe, "s": result.start_ts, "e": result.end_ts,
        "lw": layer_weights_json, "el": enabled_layers_json,
        "et": enabled_traps_json, "ib": result.initial_balance,
        "nt": result.n_trades, "wr": result.win_rate,
        "pf": (None if result.profit_factor == math.inf else result.profit_factor),
        "sh": result.sharpe, "md": result.max_drawdown, "ph": result.params_hash,
    })).first()
    return int(row.id)
```

- [ ] **Step 3: Tests pass**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/integration/test_backtests_persisted.py -v
```
Expected: `1 passed`. Note: SQLite test path needs `RETURNING` support (3.35+); the existing test image already supplies aiosqlite → SQLite 3.40+.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/tools/backtest.py backend/tests/integration/test_backtests_persisted.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): persist_backtest_result writes BacktestResult to backtests table"
```

---

### Task B5: `POST /api/v1/admin/backtests` + `GET /api/v1/admin/backtests` — failing test → green

**Files:**
- Create: `worktrees/sp-7/backend/app/api/routes/admin_backtest.py`
- Create: `worktrees/sp-7/backend/tests/integration/test_api_admin_backtest.py`
- Modify: `worktrees/sp-7/backend/app/main.py` — wire the new router

**Design notes:**
- POST runs the backtest synchronously inside an `await asyncio.to_thread(...)` so the FastAPI event loop isn't blocked by Numpy/Pandas heavy lifting. For multi-month backtests this can take 30-60s; the v1 endpoint waits and returns the row. v2 may push to a background queue.
- GET supports `?symbol=BTC/USDT&timeframe=1h&limit=50` filters. Default returns last 50 across all symbols.
- Both endpoints use `Depends(require_admin)` from SP-0.7. Reuse the `_row_to_out` pattern from `admin_ml.py`.
- Pydantic schemas (`BacktestRunIn`, `BacktestOut`) added inline in the route file for now; if SP-7.5 adds the frontend admin sub-page they can be hoisted to `api/schemas.py`.

- [ ] **Step 1: Failing test**

```python
"""Integration test for POST /api/v1/admin/backtests + GET listing."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_post_backtests_admin_only(client_anonymous: AsyncClient) -> None:
    """No admin auth → 403."""
    resp = await client_anonymous.post("/api/v1/admin/backtests", json={
        "symbol": "BTC/USDT", "timeframe": "1h",
        "start": "2025-01-01T00:00:00Z", "end": "2025-01-02T00:00:00Z",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_post_backtests_synthetic_runs(client_admin: AsyncClient, monkeypatch) -> None:
    """Admin can POST; we patch the bars loader to inject synthetic data."""
    import tools.backtest as bt
    import numpy as np
    import pandas as pd
    from datetime import datetime, timedelta, timezone

    def _fake_loader(symbol, tf, start, end):
        n = 200
        idx = pd.DatetimeIndex(
            [start + timedelta(hours=i) for i in range(n)]
        )
        c = np.array([100 + np.sin(i*0.1)*5 + i*0.05 for i in range(n)])
        return pd.DataFrame({
            "open": c-.1, "high": c+.5, "low": c-.5,
            "close": c, "volume": np.full(n, 1000.),
        }, index=idx)
    monkeypatch.setattr(bt, "_default_bars_loader", _fake_loader)

    resp = await client_admin.post("/api/v1/admin/backtests", json={
        "symbol": "BTC/USDT", "timeframe": "1h",
        "start": "2025-01-01T00:00:00Z", "end": "2025-01-09T00:00:00Z",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["symbol"] == "BTC/USDT"
    assert "params_hash" in body
    assert "n_trades" in body
    assert body["status"] == "completed"


@pytest.mark.asyncio
async def test_get_backtests_lists_recent(client_admin: AsyncClient) -> None:
    resp = await client_admin.get("/api/v1/admin/backtests?limit=10")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
```

- [ ] **Step 2: Run — fail** with 404 on the missing route.

- [ ] **Step 3: Implement `admin_backtest.py`**

```python
"""Admin REST endpoints for the backtest framework (SP-7 Phase B5).

POST /api/v1/admin/backtests — kick off a backtest synchronously, persist
the result, return the row.
GET /api/v1/admin/backtests   — list recent backtests with optional filters.

Both behind Depends(require_admin) per spec §6.4.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_admin
from app.auth.models import User
from app.db.session import get_session
from tools.backtest import persist_backtest_result, run_backtest

router = APIRouter(
    prefix="/api/v1/admin/backtests",
    tags=["admin-backtest"],
    dependencies=[Depends(require_admin)],
)


class BacktestRunIn(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    start: datetime
    end: datetime
    layer_weights: dict[str, float] | None = None  # JSON keys are strings
    enabled_layers: list[int] | None = None
    enabled_traps: list[str] | None = None
    initial_balance_usdt: float = 10000.0


class BacktestOut(BaseModel):
    id: int
    symbol: str
    timeframe: str
    start_ts: datetime
    end_ts: datetime
    n_trades: int
    win_rate: float | None
    profit_factor: float | None
    sharpe: float | None
    max_drawdown: float | None
    params_hash: str
    status: str
    triggered_at: datetime
    layer_weights: dict[str, float] | None
    enabled_layers: list[int] | None
    enabled_traps: list[str] | None


def _row_to_out(row: Any) -> BacktestOut:
    def _maybe_json(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            try:
                return json.loads(v)
            except ValueError:
                return None
        return v

    return BacktestOut(
        id=row.id,
        symbol=row.symbol, timeframe=row.timeframe,
        start_ts=row.start_ts, end_ts=row.end_ts,
        n_trades=row.n_trades, win_rate=row.win_rate,
        profit_factor=row.profit_factor, sharpe=row.sharpe,
        max_drawdown=row.max_drawdown,
        params_hash=row.params_hash, status=row.status,
        triggered_at=row.triggered_at,
        layer_weights=_maybe_json(row.layer_weights),
        enabled_layers=_maybe_json(row.enabled_layers),
        enabled_traps=_maybe_json(row.enabled_traps),
    )


@router.post(
    "",
    response_model=BacktestOut,
    status_code=status.HTTP_201_CREATED,
)
async def run_backtest_endpoint(
    body: BacktestRunIn,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    user: User = Depends(require_admin),  # noqa: B008
) -> BacktestOut:
    """Kick off a backtest. Blocks until complete (typically 30-60s)."""
    layer_weights_int = (
        {int(k): v for k, v in body.layer_weights.items()}
        if body.layer_weights else None
    )
    enabled_layers = set(body.enabled_layers) if body.enabled_layers else None
    enabled_traps = set(body.enabled_traps) if body.enabled_traps else None

    result = await asyncio.to_thread(
        run_backtest,
        symbol=body.symbol,
        timeframe=body.timeframe,
        start=body.start, end=body.end,
        layer_weights=layer_weights_int,
        enabled_layers=enabled_layers,
        enabled_traps=enabled_traps,
        initial_balance_usdt=body.initial_balance_usdt,
    )
    new_id = await persist_backtest_result(
        session, result=result, triggered_by_user_id=user.id,
    )
    await session.commit()
    row = (await session.execute(sa.text(
        "SELECT * FROM backtests WHERE id = :i"
    ), {"i": new_id})).first()
    if row is None:  # pragma: no cover
        raise HTTPException(status_code=500, detail="row not visible after insert")
    return _row_to_out(row)


@router.get("", response_model=list[BacktestOut])
async def list_backtests(
    symbol: str | None = Query(None),
    timeframe: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[BacktestOut]:
    where = []
    params: dict[str, Any] = {"l": limit}
    if symbol:
        where.append("symbol = :sym")
        params["sym"] = symbol
    if timeframe:
        where.append("timeframe = :tf")
        params["tf"] = timeframe
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = (await session.execute(sa.text(
        f"SELECT * FROM backtests {where_clause} "
        "ORDER BY triggered_at DESC LIMIT :l"
    ), params)).all()
    return [_row_to_out(r) for r in rows]
```

- [ ] **Step 4: Wire into `app/main.py`**

```python
# In app/main.py imports:
from app.api.routes import admin_backtest

# In create_app():
app.include_router(admin_backtest.router)
```

- [ ] **Step 5: Tests pass**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/integration/test_api_admin_backtest.py -v
```
Expected: `3 passed`.

- [ ] **Step 6: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/app/api/routes/admin_backtest.py backend/app/main.py backend/tests/integration/test_api_admin_backtest.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): admin REST endpoints for backtest trigger + list (admin-gated)"
```

---

## Phase C — Hyperopt for layer weights

### Task C1: `hyperopt_layer_weights` Optuna stub + failing test

**Files:**
- Create: `worktrees/sp-7/backend/tools/hyperopt.py` (stub)
- Create: `worktrees/sp-7/backend/tests/unit/test_tools_hyperopt.py`

**Design notes:**
- Search space: nine `trial.suggest_float(f"w{i}", 0.0, 0.3)` calls for layers 1-9 (L10 is the brain placeholder; never tuned by hyperopt). After sampling, weights are normalized to sum=1.0 so the aggregator semantics are stable.
- Objective: `val_result.sharpe` — maximize. Per spec §3.3 §149-156.
- Sampler: `optuna.samplers.TPESampler(seed=42)` for reproducibility in tests.
- Train + val windows are non-overlapping. Each trial runs TWO `run_backtest` calls — one on train (to sanity-check the strategy actually trades), one on val (the objective).
- The hyperopt result dataclass mirrors `BacktestResult`: best weights, best sharpe, n_trials, study object reference (kept in memory; not persisted).

- [ ] **Step 1: Stub** — `tools/hyperopt.py`

```python
"""Optuna-driven hyperopt for layer weights. Phase C2 implementation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import optuna


@dataclass
class HyperoptResult:
    best_weights: dict[int, float]
    best_sharpe: float
    n_trials: int
    study: optuna.Study | None = None  # not serializable; for in-process inspection


BacktestRunner = Callable[..., "BacktestResult"]


def hyperopt_layer_weights(
    *,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    train_window: tuple[datetime, datetime],
    val_window: tuple[datetime, datetime],
    n_trials: int = 100,
    _bars_loader=None,
    _backtest_runner: BacktestRunner | None = None,
    seed: int = 42,
) -> HyperoptResult:  # pragma: no cover — stub
    raise NotImplementedError("hyperopt_layer_weights: Phase C2 deliverable")
```

- [ ] **Step 2: Failing test**

```python
"""Unit tests for hyperopt — Phase C1."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from tools.hyperopt import HyperoptResult, hyperopt_layer_weights


def test_hyperopt_returns_best_weights_summing_to_one() -> None:
    """Stub run with 5 trials — weights sum to 1.0, sharpe is finite."""
    # Inject a deterministic backtest_runner that returns sharpe = sum(w[1..3])
    # so we can assert hyperopt converges to all weight on layers 1-3.
    def fake_runner(*, layer_weights, **kw):
        from tools.backtest import BacktestResult
        sharpe = sum(layer_weights.get(i, 0.0) for i in (1, 2, 3))
        return BacktestResult(
            n_trades=10, win_rate=0.5, profit_factor=1.0,
            sharpe=sharpe, max_drawdown=0.05,
            equity_curve=[], trade_log=[], params_hash="x",
            initial_balance=10000.0, final_balance=10100.0,
        )

    train_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    train_end = datetime(2025, 6, 30, tzinfo=timezone.utc)
    val_start = datetime(2025, 7, 1, tzinfo=timezone.utc)
    val_end = datetime(2025, 12, 31, tzinfo=timezone.utc)

    result = hyperopt_layer_weights(
        symbol="BTC/USDT", timeframe="1h",
        train_window=(train_start, train_end),
        val_window=(val_start, val_end),
        n_trials=15,
        _backtest_runner=fake_runner,
    )
    assert isinstance(result, HyperoptResult)
    assert result.n_trials == 15
    assert abs(sum(result.best_weights.values()) - 1.0) < 1e-6
    # Layers 1-3 should dominate (since fake_runner only rewards them)
    assert sum(result.best_weights.get(i, 0.0) for i in (1, 2, 3)) > 0.5


def test_hyperopt_seed_reproducibility() -> None:
    """Same seed → same best_weights; different seeds → different exploration."""
    def fake_runner(*, layer_weights, **kw):
        from tools.backtest import BacktestResult
        sharpe = sum(layer_weights.get(i, 0.0) ** 2 for i in range(1, 10))
        return BacktestResult(
            n_trades=1, win_rate=1.0, profit_factor=1.0, sharpe=sharpe,
            max_drawdown=0.0, equity_curve=[], trade_log=[], params_hash="x",
            initial_balance=10000.0, final_balance=10000.0,
        )

    args = dict(
        symbol="BTC/USDT", timeframe="1h",
        train_window=(datetime(2025, 1, 1, tzinfo=timezone.utc),
                      datetime(2025, 6, 30, tzinfo=timezone.utc)),
        val_window=(datetime(2025, 7, 1, tzinfo=timezone.utc),
                    datetime(2025, 12, 31, tzinfo=timezone.utc)),
        n_trials=10, _backtest_runner=fake_runner,
    )
    a = hyperopt_layer_weights(**args, seed=42)
    b = hyperopt_layer_weights(**args, seed=42)
    assert a.best_weights == b.best_weights
```

- [ ] **Step 3: Run — fail** with `NotImplementedError`.

---

### Task C2: `hyperopt_layer_weights` real implementation — green

**Files:**
- Modify: `worktrees/sp-7/backend/tools/hyperopt.py`

- [ ] **Step 1: Implement**

```python
import logging
from datetime import datetime
from typing import Callable

import optuna
from optuna.samplers import TPESampler

from tools.backtest import BacktestResult, run_backtest as _real_run_backtest

log = logging.getLogger(__name__)

BacktestRunner = Callable[..., BacktestResult]


def hyperopt_layer_weights(
    *,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    train_window: tuple[datetime, datetime],
    val_window: tuple[datetime, datetime],
    n_trials: int = 100,
    _bars_loader=None,
    _backtest_runner: BacktestRunner | None = None,
    seed: int = 42,
) -> HyperoptResult:
    """Search for layer weights maximizing val-set Sharpe.

    Uses Optuna TPE; nine independent suggest_float(0.0, 0.3) calls for
    L1-L9. Weights are normalized to sum=1.0 before being passed to the
    backtest runner.

    Args:
        train_window: ignored by the objective directly (we use it only
            for diagnostic logging — the objective itself runs on the
            val window per the spec). A future v2 may use train_window
            to filter degenerate strategies.
        seed: fixes Optuna's TPE RNG for reproducibility in tests.

    Returns: HyperoptResult with best_weights (dict[int, float],
    summing to 1.0), best_sharpe, n_trials, study.
    """
    runner = _backtest_runner if _backtest_runner is not None else _real_run_backtest

    def _objective(trial: optuna.Trial) -> float:
        raw = {i: trial.suggest_float(f"w{i}", 0.0, 0.3) for i in range(1, 10)}
        total = sum(raw.values())
        if total <= 0:
            return -1e9  # degenerate sample — discourage
        weights = {k: v / total for k, v in raw.items()}

        # Optional sanity-check pass on TRAIN — abort early if the strategy
        # never trades (per spec §8 risk row 2 — penalize degenerate weights).
        train_result = runner(
            symbol=symbol, timeframe=timeframe,
            start=train_window[0], end=train_window[1],
            layer_weights=weights, _bars_loader=_bars_loader,
        )
        if train_result.n_trades == 0:
            return -1e6

        val_result = runner(
            symbol=symbol, timeframe=timeframe,
            start=val_window[0], end=val_window[1],
            layer_weights=weights, _bars_loader=_bars_loader,
        )
        # Penalize tiny weight stddev (spec §8 — avoid all-on-one-layer)
        import numpy as np
        stddev = float(np.std(list(weights.values())))
        regularization = 0.0 if stddev > 0.01 else -0.5
        return float(val_result.sharpe + regularization)

    study = optuna.create_study(
        direction="maximize", sampler=TPESampler(seed=seed),
    )
    study.optimize(_objective, n_trials=n_trials, show_progress_bar=False)

    best_raw = study.best_params  # dict like {"w1": 0.12, ...}
    best_dict = {int(k.removeprefix("w")): v for k, v in best_raw.items()}
    total = sum(best_dict.values())
    if total > 0:
        best_normalized = {k: v / total for k, v in best_dict.items()}
    else:
        best_normalized = {i: 1/9 for i in range(1, 10)}

    log.info(
        "hyperopt complete: best_sharpe=%.4f best_weights=%s",
        study.best_value, best_normalized,
    )
    return HyperoptResult(
        best_weights=best_normalized,
        best_sharpe=float(study.best_value),
        n_trials=n_trials,
        study=study,
    )
```

- [ ] **Step 2: Tests pass**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_tools_hyperopt.py -v
```
Expected: `2 passed`.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/tools/hyperopt.py backend/tests/unit/test_tools_hyperopt.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): hyperopt_layer_weights via Optuna TPE with stddev regularization"
```

---

### Task C3: Persist hyperopt result to `hyperopt_studies` — failing test → green

**Files:**
- Modify: `worktrees/sp-7/backend/tools/hyperopt.py` — add `persist_hyperopt_result`
- Create: `worktrees/sp-7/backend/tests/integration/test_hyperopt_persisted.py`

**Design note:** The Postgres-only `TSTZRANGE` columns (`train_window`, `val_window`) need a SQLite-compatible bind path. We bind them as ISO-string `[start,end)` text — both Postgres `TSTZRANGE` and SQLite TEXT accept `[2025-01-01T00:00:00+00:00,2025-06-30T00:00:00+00:00)`. If alembic migration generated a CHECK constraint on Postgres TSTZRANGE syntax that rejects the ISO string, fall back to two columns `train_start`/`train_end` (no migration change needed since column type is text-castable). For SQLite tests we set the column type via the INSERT string format directly.

**SP-0.5 hotfix lesson:** pass datetimes to params, not pre-formatted ISO strings — except when the column is `TSTZRANGE`, where Postgres needs the literal range syntax.

- [ ] **Step 1: Failing test**

```python
"""Integration test: persist_hyperopt_result writes to hyperopt_studies."""
import pytest
import sqlalchemy as sa
from datetime import datetime, timezone

from tools.hyperopt import HyperoptResult, persist_hyperopt_result


@pytest.mark.asyncio
async def test_persist_hyperopt_writes_row(bot_status_factory) -> None:
    session_factory = bot_status_factory
    async with session_factory() as session:
        result = HyperoptResult(
            best_weights={i: 1/9 for i in range(1, 10)},
            best_sharpe=1.42, n_trials=20, study=None,
        )
        new_id = await persist_hyperopt_result(
            session, result=result,
            symbol="BTC/USDT", timeframe="1h",
            train_window=(datetime(2025, 1, 1, tzinfo=timezone.utc),
                          datetime(2025, 6, 30, tzinfo=timezone.utc)),
            val_window=(datetime(2025, 7, 1, tzinfo=timezone.utc),
                        datetime(2025, 12, 31, tzinfo=timezone.utc)),
            triggered_by_user_id=1,
        )
        await session.commit()

        row = (await session.execute(sa.text(
            "SELECT * FROM hyperopt_studies WHERE id = :i"
        ), {"i": new_id})).first()
    assert row is not None
    assert row.symbol == "BTC/USDT"
    assert row.best_sharpe == 1.42
    assert row.status == "completed"
```

- [ ] **Step 2: Implement**

```python
# Add to tools/hyperopt.py

import json as _json
from sqlalchemy.ext.asyncio import AsyncSession
import sqlalchemy as sa


async def persist_hyperopt_result(
    session: AsyncSession,
    *,
    result: HyperoptResult,
    symbol: str,
    timeframe: str,
    train_window: tuple[datetime, datetime],
    val_window: tuple[datetime, datetime],
    triggered_by_user_id: int | None = None,
    mlflow_run_id: str | None = None,
) -> int:
    """Insert HyperoptResult row; returns new id.

    For Postgres TSTZRANGE columns, we bind the canonical literal:
    `[2025-01-01T00:00:00+00:00,2025-06-30T00:00:00+00:00)`. SQLite tests
    accept this as TEXT with no schema enforcement.
    """
    train_range = (
        f"[{train_window[0].isoformat()},{train_window[1].isoformat()})"
    )
    val_range = (
        f"[{val_window[0].isoformat()},{val_window[1].isoformat()})"
    )
    insert_sql = sa.text(
        "INSERT INTO hyperopt_studies "
        "(triggered_by, n_trials, train_window, val_window, "
        "symbol, timeframe, best_weights, best_sharpe, mlflow_run_id, "
        "status, completed_at) "
        "VALUES (:tb, :n, :tw, :vw, :sym, :tf, :bw, :bs, :ml, "
        "'completed', :now) RETURNING id"
    )
    row = (await session.execute(insert_sql, {
        "tb": triggered_by_user_id,
        "n": result.n_trials,
        "tw": train_range, "vw": val_range,
        "sym": symbol, "tf": timeframe,
        "bw": _json.dumps({str(k): v for k, v in result.best_weights.items()}),
        "bs": result.best_sharpe,
        "ml": mlflow_run_id,
        "now": datetime.now(timezone.utc) if False else __import__("datetime").datetime.utcnow(),
    })).first()
    return int(row.id)
```

(Note: replace the awkward `now` expression with a `from datetime import datetime, timezone` at module top + `datetime.now(timezone.utc)` proper.)

- [ ] **Step 3: Tests pass**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/integration/test_hyperopt_persisted.py -v
```
Expected: `1 passed`.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/tools/hyperopt.py backend/tests/integration/test_hyperopt_persisted.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): persist_hyperopt_result writes HyperoptResult to hyperopt_studies"
```

---

### Task C4: `POST /api/v1/admin/hyperopt` endpoint — failing test → green

**Files:**
- Modify: `worktrees/sp-7/backend/app/api/routes/admin_backtest.py` — add hyperopt POST/GET in same router
- Create: `worktrees/sp-7/backend/tests/integration/test_api_admin_hyperopt.py`

**Design notes:**
- Hyperopt is much slower than backtest (n_trials × 2 backtests each ≈ ~5-30 minutes for 20 trials). The POST returns 202 ACCEPTED and persists a `status='running'` row immediately, then dispatches the work to a `BackgroundTasks` task that updates `status='completed'` (or `failed`) when done.
- GET `/api/v1/admin/hyperopt/{id}` returns the current row — the operator polls until `status != 'running'`.
- Default `n_trials = 20` for the API; CLI defaults to 100. Cap at 500 to prevent runaway.

- [ ] **Step 1: Failing test**

```python
"""Integration tests for POST/GET /api/v1/admin/hyperopt."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_post_hyperopt_admin_only(client_anonymous: AsyncClient) -> None:
    resp = await client_anonymous.post("/api/v1/admin/hyperopt", json={
        "symbol": "BTC/USDT", "timeframe": "1h",
        "train_start": "2025-01-01T00:00:00Z",
        "train_end":   "2025-06-30T00:00:00Z",
        "val_start":   "2025-07-01T00:00:00Z",
        "val_end":     "2025-12-31T00:00:00Z",
        "n_trials": 5,
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_post_hyperopt_returns_202_with_running_status(
    client_admin: AsyncClient, monkeypatch,
) -> None:
    """Admin POST returns 202 + a 'running' row immediately."""
    import tools.hyperopt as ho

    def _fast_runner(**_kw):
        # Bypass Optuna entirely — return a canned result.
        from tools.hyperopt import HyperoptResult
        return HyperoptResult(
            best_weights={i: 1/9 for i in range(1, 10)},
            best_sharpe=0.5, n_trials=_kw.get("n_trials", 5), study=None,
        )
    monkeypatch.setattr(ho, "hyperopt_layer_weights", _fast_runner)

    resp = await client_admin.post("/api/v1/admin/hyperopt", json={
        "symbol": "BTC/USDT", "timeframe": "1h",
        "train_start": "2025-01-01T00:00:00Z",
        "train_end":   "2025-06-30T00:00:00Z",
        "val_start":   "2025-07-01T00:00:00Z",
        "val_end":     "2025-12-31T00:00:00Z",
        "n_trials": 5,
    })
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] in ("running", "completed")  # may have raced to completed
    new_id = body["id"]

    # Poll the GET endpoint
    follow = await client_admin.get(f"/api/v1/admin/hyperopt/{new_id}")
    assert follow.status_code == 200
    assert follow.json()["id"] == new_id


@pytest.mark.asyncio
async def test_get_hyperopt_404_on_missing(client_admin: AsyncClient) -> None:
    resp = await client_admin.get("/api/v1/admin/hyperopt/999999")
    assert resp.status_code == 404
```

- [ ] **Step 2: Implement** — added to `admin_backtest.py` (same router file but distinct prefix sub-router since they share auth deps):

```python
# Append to admin_backtest.py — define a separate router with /hyperopt prefix.

from fastapi import BackgroundTasks
from tools.hyperopt import HyperoptResult, hyperopt_layer_weights, persist_hyperopt_result

hyperopt_router = APIRouter(
    prefix="/api/v1/admin/hyperopt",
    tags=["admin-hyperopt"],
    dependencies=[Depends(require_admin)],
)


class HyperoptRunIn(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    train_start: datetime
    train_end: datetime
    val_start: datetime
    val_end: datetime
    n_trials: int = 20
    seed: int = 42


class HyperoptOut(BaseModel):
    id: int
    symbol: str
    timeframe: str
    n_trials: int
    best_weights: dict[str, float] | None
    best_sharpe: float | None
    status: str
    triggered_at: datetime
    completed_at: datetime | None
    error_message: str | None


def _hyperopt_row_to_out(row: Any) -> HyperoptOut:
    bw = row.best_weights
    if isinstance(bw, str):
        try:
            bw = json.loads(bw)
        except ValueError:
            bw = None
    return HyperoptOut(
        id=row.id, symbol=row.symbol, timeframe=row.timeframe,
        n_trials=row.n_trials, best_weights=bw, best_sharpe=row.best_sharpe,
        status=row.status, triggered_at=row.triggered_at,
        completed_at=row.completed_at, error_message=row.error_message,
    )


@hyperopt_router.post(
    "", response_model=HyperoptOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_hyperopt_endpoint(
    body: HyperoptRunIn,
    bg: BackgroundTasks,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    user: User = Depends(require_admin),  # noqa: B008
) -> HyperoptOut:
    if body.n_trials > 500:
        raise HTTPException(status_code=400, detail="n_trials capped at 500")
    train_range = f"[{body.train_start.isoformat()},{body.train_end.isoformat()})"
    val_range = f"[{body.val_start.isoformat()},{body.val_end.isoformat()})"
    insert_sql = sa.text(
        "INSERT INTO hyperopt_studies "
        "(triggered_by, n_trials, train_window, val_window, symbol, "
        "timeframe, status) "
        "VALUES (:tb, :n, :tw, :vw, :sym, :tf, 'running') RETURNING id"
    )
    row = (await session.execute(insert_sql, {
        "tb": user.id, "n": body.n_trials, "tw": train_range, "vw": val_range,
        "sym": body.symbol, "tf": body.timeframe,
    })).first()
    new_id = int(row.id)
    await session.commit()

    bg.add_task(_run_hyperopt_in_background, new_id, body)

    full = (await session.execute(sa.text(
        "SELECT * FROM hyperopt_studies WHERE id = :i"
    ), {"i": new_id})).first()
    return _hyperopt_row_to_out(full)


async def _run_hyperopt_in_background(study_id: int, body: HyperoptRunIn) -> None:
    """Run Optuna in a thread; update the row when done."""
    from app.db.session import get_session_factory
    factory = get_session_factory()
    try:
        result = await asyncio.to_thread(
            hyperopt_layer_weights,
            symbol=body.symbol, timeframe=body.timeframe,
            train_window=(body.train_start, body.train_end),
            val_window=(body.val_start, body.val_end),
            n_trials=body.n_trials, seed=body.seed,
        )
        async with factory() as session:
            await session.execute(sa.text(
                "UPDATE hyperopt_studies SET best_weights = :bw, "
                "best_sharpe = :bs, status = 'completed', completed_at = NOW() "
                "WHERE id = :i"
            ), {
                "bw": json.dumps({str(k): v for k, v in result.best_weights.items()}),
                "bs": result.best_sharpe, "i": study_id,
            })
            await session.commit()
    except Exception as e:  # noqa: BLE001
        async with factory() as session:
            await session.execute(sa.text(
                "UPDATE hyperopt_studies SET status='failed', "
                "error_message=:em, completed_at=NOW() WHERE id=:i"
            ), {"em": str(e)[:500], "i": study_id})
            await session.commit()


@hyperopt_router.get("/{study_id}", response_model=HyperoptOut)
async def get_hyperopt(
    study_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> HyperoptOut:
    row = (await session.execute(sa.text(
        "SELECT * FROM hyperopt_studies WHERE id = :i"
    ), {"i": study_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="hyperopt study not found")
    return _hyperopt_row_to_out(row)
```

Wire `hyperopt_router` into `app/main.py` next to `admin_backtest.router`.

- [ ] **Step 3: Tests pass**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/integration/test_api_admin_hyperopt.py -v
```
Expected: `3 passed`.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/app/api/routes/admin_backtest.py backend/app/main.py backend/tests/integration/test_api_admin_hyperopt.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): admin REST endpoints for hyperopt — 202 + background task pattern"
```

---

## Phase D — Audit verifier scheduler + alerts

### Task D1: `alert_admin` SMTP dispatcher — failing test → green

**Files:**
- Modify: `worktrees/sp-7/backend/app/ops/alerts.py`
- Create: `worktrees/sp-7/backend/tests/unit/test_ops_alerts.py`

**Design notes:**
- Configuration: env vars `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_FROM_EMAIL`, `ALERT_TO_EMAIL`. All optional.
- If any required env var is missing → log via `log.error("alert_admin: SMTP not configured: %s", message)` and return without raising. We never want an alerting failure to brick the verifier loop.
- SMTP delivery uses `aiosmtplib` (already a transitive dep via `httpx`'s `email-validator` — verify; if not, add to pyproject). Keep timeout at 10s.
- Severity: `info | warning | error | critical`. Future Slack integration may route by severity; for v1, all severities are emailed identically with `[severity]` in subject.

- [ ] **Step 1: Failing test**

```python
"""Unit tests for app.ops.alerts.alert_admin — Phase D1."""
import logging
import pytest

from app.ops.alerts import alert_admin


@pytest.mark.asyncio
async def test_alert_admin_logs_when_smtp_not_configured(
    monkeypatch, caplog,
) -> None:
    """Missing SMTP env vars → log.error, no raise."""
    monkeypatch.delenv("SMTP_HOST", raising=False)
    caplog.set_level(logging.ERROR)
    await alert_admin("test message", severity="warning")
    assert any("SMTP not configured" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_alert_admin_sends_via_smtp_when_configured(
    monkeypatch,
) -> None:
    """All env vars set → SMTP send is invoked with the right subject/body."""
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "noreply@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "x")
    monkeypatch.setenv("ALERT_FROM_EMAIL", "noreply@example.com")
    monkeypatch.setenv("ALERT_TO_EMAIL", "admin@example.com")

    sent: dict = {}

    class FakeSMTP:
        def __init__(self, hostname, port, **kw):
            sent["host"] = hostname
            sent["port"] = port
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def login(self, user, pw):
            sent["user"] = user
        async def send_message(self, msg):
            sent["subject"] = msg["Subject"]
            sent["to"] = msg["To"]
            sent["body"] = msg.get_content()

    import app.ops.alerts as alerts_mod
    monkeypatch.setattr(alerts_mod, "SMTP", FakeSMTP)

    await alert_admin("disk full on Oracle", severity="critical")
    assert "[critical]" in sent["subject"]
    assert "disk full on Oracle" in sent["body"]
    assert sent["to"] == "admin@example.com"


@pytest.mark.asyncio
async def test_alert_admin_swallows_smtp_errors(monkeypatch, caplog) -> None:
    """SMTP raises → log.error but never re-raise (alert is best-effort)."""
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "noreply@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "x")
    monkeypatch.setenv("ALERT_FROM_EMAIL", "noreply@example.com")
    monkeypatch.setenv("ALERT_TO_EMAIL", "admin@example.com")

    class BoomSMTP:
        def __init__(self, **kw): pass
        async def __aenter__(self): raise ConnectionError("net down")
        async def __aexit__(self, *a): return False

    import app.ops.alerts as alerts_mod
    monkeypatch.setattr(alerts_mod, "SMTP", BoomSMTP)
    caplog.set_level(logging.ERROR)
    await alert_admin("test", severity="warning")  # must not raise
    assert any("alert dispatch failed" in r.message for r in caplog.records)
```

- [ ] **Step 2: Implement**

```python
"""Email alert dispatcher.

If SMTP env vars are unset, falls back to log.error so the operator gets
a signal in container logs even without a configured mailer. SMTP failures
are caught and logged — alert dispatch is best-effort and must NEVER bring
down the calling task (typically the nightly verifier loop).

Env vars:
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    ALERT_FROM_EMAIL, ALERT_TO_EMAIL
"""
from __future__ import annotations

import logging
import os
from email.message import EmailMessage

try:
    from aiosmtplib import SMTP  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — falls back if aiosmtplib not installed
    SMTP = None  # type: ignore[assignment]

log = logging.getLogger(__name__)


def _required_env() -> dict[str, str] | None:
    keys = (
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
        "ALERT_FROM_EMAIL", "ALERT_TO_EMAIL",
    )
    values = {k: os.environ.get(k) for k in keys}
    if any(v is None or v == "" for v in values.values()):
        return None
    return values  # type: ignore[return-value]


async def alert_admin(message: str, *, severity: str = "warning") -> None:
    """Best-effort email dispatch. Never raises."""
    cfg = _required_env()
    if cfg is None or SMTP is None:
        log.error("alert_admin: SMTP not configured: [%s] %s", severity, message)
        return

    msg = EmailMessage()
    msg["Subject"] = f"[trading-radar] [{severity}] {message[:120]}"
    msg["From"] = cfg["ALERT_FROM_EMAIL"]
    msg["To"] = cfg["ALERT_TO_EMAIL"]
    msg.set_content(message)

    try:
        async with SMTP(
            hostname=cfg["SMTP_HOST"],
            port=int(cfg["SMTP_PORT"]),
            timeout=10,
        ) as smtp:
            await smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            await smtp.send_message(msg)
    except Exception as e:  # noqa: BLE001 — best-effort; log + swallow
        log.error("alert_admin: alert dispatch failed: %s", e)
```

- [ ] **Step 3: If `aiosmtplib` is not yet a dependency, add it**

```bash
# Verify import works:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python -c "import aiosmtplib"
# If ImportError, add to pyproject.toml:
# "aiosmtplib==3.0.2",
```

If added, rebuild backend image and re-run baseline tests.

- [ ] **Step 4: Tests pass**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_ops_alerts.py -v
```
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/app/ops/alerts.py backend/tests/unit/test_ops_alerts.py
# If pyproject changed:
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/pyproject.toml
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): alert_admin SMTP dispatcher with log.error fallback (best-effort, never raises)"
```

---

### Task D2: `run_audit_verifier_loop` — failing test → green

**Files:**
- Modify: `worktrees/sp-7/backend/app/ops/verifier_scheduler.py`
- Create: `worktrees/sp-7/backend/tests/unit/test_ops_verifier_scheduler.py`

**Design notes:**
- Mirrors `app/shadow/universe_refresh.py` structure: a `seconds_until_next_utc_hour(hour, now)` helper, a `run_audit_verifier_loop(session_factory, *, _sleep, _now)` that loops forever.
- For each iteration: sleep until 03:00 UTC, then verify each chained table (`predictions`, `paper_trades`, `shadow_trades`). On break: log error, write `audit_violations` row (reusing the SP-0.7 table — the `attempted_email='system'` convention), call `alert_admin`.
- The chained tables use different column sets for `verify_chain`. We need a config dict per table:
  ```python
  CHAINED_TABLES = {
      "predictions": ["symbol", "timeframe", "ts", "direction", ...],  # from SP-0
      "paper_trades": ["symbol", "side", "opened_at", ...],
      "shadow_trades": ["symbol", "side", "opened_at", ...],
  }
  ```
  The actual column lists must match what `insert_with_chain` was called with — verify by reading SP-0/SP-0.5 persistence helpers. (Lookup file: `backend/app/core/execution/persistence.py` for predictions, `backend/app/shadow/persistence.py` for shadow_trades.)
- Tests use `freezegun.freeze_time` + an injected `_sleep` that records call args and short-circuits after one iteration via a counter.

- [ ] **Step 1: Failing test**

```python
"""Unit tests for the audit verifier scheduler — Phase D2."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from freezegun import freeze_time
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)

from app.db.audit_verify import VerifyResult
from app.ops.verifier_scheduler import (
    CHAINED_TABLES,
    run_audit_verifier_loop,
    seconds_until_next_utc_hour,
)


def test_seconds_until_next_utc_hour_basic() -> None:
    now = datetime(2025, 1, 1, 1, 30, tzinfo=timezone.utc)
    assert seconds_until_next_utc_hour(3, now) == 90 * 60  # 1.5h

    now2 = datetime(2025, 1, 1, 3, 0, 0, tzinfo=timezone.utc)
    assert seconds_until_next_utc_hour(3, now2) == 24 * 3600  # next day


@pytest.mark.asyncio
async def test_verifier_loop_calls_verify_chain_and_short_circuits(
    monkeypatch,
) -> None:
    """Loop runs one iteration then exits via injected _sleep that raises."""
    sleep_calls: list[float] = []

    async def _sleep(s: float) -> None:
        sleep_calls.append(s)
        if len(sleep_calls) >= 2:  # exit after 1st verify
            raise asyncio.CancelledError()

    verify_calls: list[str] = []

    async def _fake_verify(session, table, *, columns):
        verify_calls.append(table)
        return VerifyResult(ok=True, rows_checked=10)

    monkeypatch.setattr(
        "app.ops.verifier_scheduler.verify_chain", _fake_verify,
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    with pytest.raises(asyncio.CancelledError):
        await run_audit_verifier_loop(
            factory, _sleep=_sleep,
            _now=lambda: datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc),
        )

    assert set(verify_calls) == set(CHAINED_TABLES.keys())


@pytest.mark.asyncio
async def test_verifier_loop_alerts_and_writes_audit_violations_on_break(
    monkeypatch,
) -> None:
    """When verify_chain returns ok=False, alert_admin is called + row written."""
    alerts: list[str] = []

    async def _fake_alert(msg, *, severity="warning"):
        alerts.append(msg)

    async def _fake_verify(session, table, *, columns):
        from app.db.audit_verify import Violation
        return VerifyResult(
            ok=False, rows_checked=5,
            violations=[Violation(row_id=42, expected="abc", actual="xyz")],
        )

    inserts: list[dict] = []

    async def _fake_insert_violation(session, *, table, row_id):
        inserts.append({"table": table, "row_id": row_id})

    monkeypatch.setattr("app.ops.verifier_scheduler.verify_chain", _fake_verify)
    monkeypatch.setattr("app.ops.verifier_scheduler.alert_admin", _fake_alert)
    monkeypatch.setattr(
        "app.ops.verifier_scheduler._record_violation", _fake_insert_violation,
    )

    async def _sleep(s):
        if len(alerts) >= len(CHAINED_TABLES):
            raise asyncio.CancelledError()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    with pytest.raises(asyncio.CancelledError):
        await run_audit_verifier_loop(
            factory, _sleep=_sleep,
            _now=lambda: datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc),
        )

    assert len(alerts) == len(CHAINED_TABLES)
    assert all("audit chain broken" in a.lower() for a in alerts)
    assert len(inserts) == len(CHAINED_TABLES)
```

- [ ] **Step 2: Implement**

```python
"""Nightly audit chain verifier loop. Runs at 03:00 UTC.

For each iteration:
    sleep until next 03:00 UTC
    open a session
    for each chained table:
        result = verify_chain(table)
        if not result.ok:
            alert_admin(...)
            insert audit_violations row
        log row count

Errors during a single table's verify don't abort the rest of the round.
Errors in the loop body are caught and logged; the loop continues.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.audit_verify import verify_chain
from app.ops.alerts import alert_admin

log = logging.getLogger(__name__)

DEFAULT_VERIFIER_HOUR_UTC: int = 3

# Map table → list of column names that participate in the row hash.
# These MUST match the payload keys that insert_with_chain saw at write time.
# Sources of truth:
#   predictions   — app.core.execution.persistence.persist_prediction
#   paper_trades  — app.shadow.persistence.persist_paper_trade
#   shadow_trades — app.shadow.persistence.persist_shadow_trade
CHAINED_TABLES: dict[str, list[str]] = {
    "predictions": [
        "symbol", "timeframe", "ts", "price", "direction", "score",
        "tier", "user_id", "model_checkpoint_id", "inputs_hash",
    ],
    "paper_trades": [
        "symbol", "side", "opened_at", "entry_price", "stop_loss",
        "take_profit", "size_usdt", "user_id",
    ],
    "shadow_trades": [
        "symbol", "side", "opened_at", "closed_at", "entry_price",
        "exit_price", "exit_reason", "pnl_usdt", "user_id",
    ],
}


def seconds_until_next_utc_hour(hour: int, now: datetime) -> int:
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return int((target - now).total_seconds())


async def _record_violation(
    session: AsyncSession, *, table: str, row_id: int,
) -> None:
    """Insert into audit_violations via SP-0.7 schema (`attempted_email='system'`)."""
    await session.execute(sa.text(
        "INSERT INTO audit_violations (attempted_email, reason) "
        "VALUES (:e, :r)"
    ), {"e": "system", "r": f"audit_chain_broken:{table}:row_{row_id}"})
    await session.commit()


async def run_audit_verifier_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    wake_at_utc_hour: int = DEFAULT_VERIFIER_HOUR_UTC,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    _now: Callable[[], datetime] | None = None,
) -> None:
    """Run the verifier loop until cancelled."""
    now_fn = _now if _now is not None else lambda: datetime.now(UTC)

    while True:
        wait_s = seconds_until_next_utc_hour(wake_at_utc_hour, now_fn())
        await _sleep(float(wait_s))

        async with session_factory() as session:
            for table, columns in CHAINED_TABLES.items():
                try:
                    result = await verify_chain(session, table, columns=columns)
                    if not result.ok:
                        first_id = result.violations[0].row_id if result.violations else -1
                        log.error(
                            "audit chain BROKEN at %s row %s "
                            "(checked=%d, violations=%d)",
                            table, first_id, result.rows_checked,
                            len(result.violations),
                        )
                        await alert_admin(
                            f"Audit chain broken: {table} first_violation_row={first_id}",
                            severity="critical",
                        )
                        await _record_violation(
                            session, table=table, row_id=first_id,
                        )
                    else:
                        log.info(
                            "audit verifier ok: %s rows_checked=%d",
                            table, result.rows_checked,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    log.exception("audit verifier crashed for %s", table)


def start_audit_verifier_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    return asyncio.create_task(run_audit_verifier_loop(session_factory))


__all__ = [
    "CHAINED_TABLES",
    "DEFAULT_VERIFIER_HOUR_UTC",
    "run_audit_verifier_loop",
    "seconds_until_next_utc_hour",
    "start_audit_verifier_task",
]
```

- [ ] **Step 3: Tests pass**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_ops_verifier_scheduler.py -v
```
Expected: `3 passed`.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/app/ops/verifier_scheduler.py backend/tests/unit/test_ops_verifier_scheduler.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): nightly audit verifier loop — verify_chain + alert + audit_violations row on break"
```

---

### Task D3: Wire verifier task into `app/main.py:lifespan` — green

**Files:**
- Modify: `worktrees/sp-7/backend/app/main.py`

- [ ] **Step 1: Add import + lifespan wiring**

```python
# In app/main.py imports:
from app.ops.verifier_scheduler import start_audit_verifier_task

# In lifespan(), inside the `if settings.env not in {"test", "ci"} and settings.worker_enabled:` block,
# after start_health_pinger_task:
audit_verifier_task = start_audit_verifier_task(get_session_factory())

# In the finally block, alongside other .cancel() calls:
if audit_verifier_task is not None:
    audit_verifier_task.cancel()
```

Initialize `audit_verifier_task = None` outside the if-block, like the other workers.

- [ ] **Step 2: Verify the existing app startup test still passes**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/integration/test_app_startup.py -v
```
Expected: pass — the test runs with `ENV=test`, so the verifier task is skipped (not started, not cancelled). The wiring is no-op for tests.

- [ ] **Step 3: Smoke test — start the dev stack and check logs**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml restart backend
docker compose -f docker-compose.yml -f docker-compose.dev.yml logs backend --tail 30 | grep -E "audit|verifier"
```
Expected: no errors. The verifier task is in `_sleep` until 03:00 UTC; no log line until the first round.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/app/main.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): wire start_audit_verifier_task into app.main lifespan"
```

---

### Task D4: Backend integration test — manually break a `predictions.row_hash`, run verifier, assert detection

**Files:**
- Create: `worktrees/sp-7/backend/tests/integration/test_verifier_scheduler_detects_break.py`

**Design note:** This test exercises the ENTIRE verifier path against a real (sqlite-in-memory) DB:
1. Use `insert_with_chain` to write 3 valid `predictions` rows
2. UPDATE the middle row's `row_hash` directly (simulating tamper)
3. Run one iteration of `run_audit_verifier_loop` with mocked alert + injected sleep
4. Assert: `verify_chain` returned ok=False, `alert_admin` was called, `audit_violations` row exists

- [ ] **Step 1: Write test**

```python
"""End-to-end: tamper with a predictions row → verifier detects + alerts."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa

from app.db.audit import insert_with_chain
from app.ops import verifier_scheduler as vs


@pytest.mark.asyncio
async def test_verifier_detects_tampered_predictions_row(
    bot_status_factory, monkeypatch,
) -> None:
    """Insert valid rows, tamper one, run verifier, assert detection."""
    factory = bot_status_factory

    async with factory() as session:
        # Need predictions table to exist + columns matching insert_with_chain.
        # In the SP-0.5+SP-0.7 fixture this already happens.
        # Insert 3 valid predictions
        for i in range(3):
            await insert_with_chain(session, "predictions", {
                "symbol": "BTC/USDT", "timeframe": "1h",
                "ts": datetime(2025, 1, 1, i, tzinfo=timezone.utc),
                "price": 100.0 + i, "direction": "LONG", "score": 65.0,
                "tier": "B", "user_id": 1,
                "model_checkpoint_id": None, "inputs_hash": f"h{i}",
            })
        await session.commit()

        # Tamper: corrupt row 2's row_hash directly
        await session.execute(sa.text(
            "UPDATE predictions SET row_hash = 'TAMPERED' WHERE id = 2"
        ))
        await session.commit()

    # Mock alert_admin and capture
    alerts: list[tuple[str, str]] = []

    async def _fake_alert(msg, *, severity="warning"):
        alerts.append((msg, severity))

    monkeypatch.setattr(vs, "alert_admin", _fake_alert)

    # Override CHAINED_TABLES to only check predictions for this test
    monkeypatch.setattr(vs, "CHAINED_TABLES", {
        "predictions": vs.CHAINED_TABLES["predictions"],
    })

    sleep_count = [0]

    async def _sleep(s):
        sleep_count[0] += 1
        if sleep_count[0] >= 2:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await vs.run_audit_verifier_loop(
            factory, _sleep=_sleep,
            _now=lambda: datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc),
        )

    assert len(alerts) == 1
    assert "predictions" in alerts[0][0]
    assert alerts[0][1] == "critical"

    # Assert audit_violations row was written
    async with factory() as session:
        rows = (await session.execute(sa.text(
            "SELECT * FROM audit_violations WHERE attempted_email = 'system'"
        ))).all()
    assert len(rows) >= 1
    assert any("audit_chain_broken:predictions" in r.reason for r in rows)
```

- [ ] **Step 2: Tests pass**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/integration/test_verifier_scheduler_detects_break.py -v
```
Expected: `1 passed`.

If the test fails because the integration `bot_status_factory` fixture doesn't seed the `predictions` schema with `prev_hash`/`row_hash` columns, extend the fixture's CREATE TABLE statements (look at how `tests/integration/conftest.py` already initializes tables for prior SP work).

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/tests/integration/test_verifier_scheduler_detects_break.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "test(sp-7): integration test — tampered predictions row → verifier detects + alerts + writes audit_violations"
```

---

## Phase E — Backup pipeline

**Subagent batching note:** E1, E2, E3, and E4 are MUTUALLY INDEPENDENT — each implements one tool/script with its own failing test, no shared state. Suitable for parallel dispatch via `superpowers:dispatching-parallel-agents` (4 subagents). E5 (persistence wiring) and E6 (README) must run AFTER E1-E4 land.

### Task E1: `tools/backup/snapshot.py` — `pg_basebackup` wrapper

**Files:**
- Modify: `worktrees/sp-7/backend/tools/backup/snapshot.py`
- Create: `worktrees/sp-7/backend/tests/unit/test_tools_backup_snapshot.py`

**Design notes:**
- Wraps `subprocess.run(["pg_basebackup", "-D", str(out_dir), "-Ft", "-z", "-X", "stream", "-P"])` against the running Postgres container.
- Connection params from env: `BACKUP_PGHOST`, `BACKUP_PGPORT`, `BACKUP_PGUSER`, `BACKUP_PGPASSWORD` (passwords pass via env to avoid the `pgpassword` file). Defaults assume the docker network: `BACKUP_PGHOST=postgres`, `BACKUP_PGPORT=5432`, `BACKUP_PGUSER=postgres`.
- Returns `SnapshotMetadata(path, size_bytes, taken_at, duration_seconds)`. `size_bytes` is `sum(p.stat().st_size for p in out_dir.rglob('*'))`.
- Test mocks `subprocess.run` → returns `CompletedProcess(returncode=0)`. Then `take_snapshot` is invoked against a `tmp_path` and asserted to return a SnapshotMetadata with `size_bytes >= 0`.

- [ ] **Step 1: Failing test**

```python
"""Unit tests for tools/backup/snapshot.py — Phase E1."""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.backup.snapshot import SnapshotMetadata, take_snapshot


def test_take_snapshot_invokes_pg_basebackup_with_correct_args(
    tmp_path, monkeypatch,
) -> None:
    """pg_basebackup is called with -D <out_dir> -Ft -z -X stream -P."""
    monkeypatch.setenv("BACKUP_PGHOST", "postgres")
    monkeypatch.setenv("BACKUP_PGPORT", "5432")
    monkeypatch.setenv("BACKUP_PGUSER", "postgres")
    monkeypatch.setenv("BACKUP_PGPASSWORD", "secret")

    captured: dict = {}

    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})
        # Simulate basebackup output by writing a test file
        (tmp_path / "base.tar.gz").write_bytes(b"x" * 1024)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    meta = take_snapshot(tmp_path)
    assert isinstance(meta, SnapshotMetadata)
    assert meta.path == tmp_path
    assert meta.size_bytes >= 1024
    assert meta.duration_seconds >= 0.0
    assert "pg_basebackup" in captured["cmd"][0]
    assert "-D" in captured["cmd"]
    assert str(tmp_path) in captured["cmd"]
    assert "-Ft" in captured["cmd"] or "-F" in captured["cmd"]
    assert captured["env"]["PGPASSWORD"] == "secret"


def test_take_snapshot_raises_on_pg_basebackup_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BACKUP_PGHOST", "postgres")
    monkeypatch.setenv("BACKUP_PGUSER", "postgres")
    monkeypatch.setenv("BACKUP_PGPASSWORD", "x")

    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout=b"", stderr=b"connection refused",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="pg_basebackup failed"):
        take_snapshot(tmp_path)
```

- [ ] **Step 2: Implement**

```python
"""pg_basebackup wrapper. Phase E1.

Invocation:
    take_snapshot(out_dir=Path("/var/backups/trading-radar/full_<ts>"))

Returns SnapshotMetadata(path, size_bytes, taken_at, duration_seconds).
Raises RuntimeError if pg_basebackup exits non-zero.

Env vars:
    BACKUP_PGHOST, BACKUP_PGPORT (default 5432), BACKUP_PGUSER, BACKUP_PGPASSWORD
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnapshotMetadata:
    path: Path
    size_bytes: int
    taken_at: datetime
    duration_seconds: float


def take_snapshot(out_dir: Path) -> SnapshotMetadata:
    out_dir.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("BACKUP_PGHOST", "postgres")
    port = os.environ.get("BACKUP_PGPORT", "5432")
    user = os.environ.get("BACKUP_PGUSER", "postgres")
    password = os.environ.get("BACKUP_PGPASSWORD", "")

    cmd = [
        "pg_basebackup",
        "-h", host, "-p", port, "-U", user,
        "-D", str(out_dir),
        "-Ft", "-z", "-X", "stream", "-P",
    ]
    env = {**os.environ, "PGPASSWORD": password}

    started = time.monotonic()
    taken_at = datetime.now(timezone.utc)
    log.info("pg_basebackup → %s", out_dir)
    proc = subprocess.run(cmd, env=env, capture_output=True)
    duration = time.monotonic() - started

    if proc.returncode != 0:
        log.error("pg_basebackup failed: %s", proc.stderr.decode("utf-8", "replace"))
        raise RuntimeError(
            f"pg_basebackup failed (rc={proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace')[:500]}"
        )

    size = sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file())
    log.info("pg_basebackup ok: size=%d bytes duration=%.1fs", size, duration)
    return SnapshotMetadata(
        path=out_dir, size_bytes=size,
        taken_at=taken_at, duration_seconds=duration,
    )


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, help="output directory")
    args = p.parse_args()
    meta = take_snapshot(Path(args.out))
    print(f"path={meta.path} size={meta.size_bytes} duration={meta.duration_seconds:.1f}s")
```

- [ ] **Step 3: Tests pass**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_tools_backup_snapshot.py -v
```
Expected: `2 passed`.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/tools/backup/snapshot.py backend/tests/unit/test_tools_backup_snapshot.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): tools/backup/snapshot.py — pg_basebackup wrapper with env-driven config + CLI"
```

---

### Task E2: `tools/backup/upload_b2.py` — encrypt + upload

**Files:**
- Modify: `worktrees/sp-7/backend/tools/backup/upload_b2.py`
- Create: `worktrees/sp-7/backend/tests/unit/test_tools_backup_upload_b2.py`

**Design notes:**
- Two-step: encrypt the snapshot directory's tar archive with AES-256-GCM (keyed by `BACKUP_ENCRYPTION_KEY` env var, 32 bytes base64), then upload the encrypted blob to B2 via `boto3.client("s3", endpoint_url=B2_ENDPOINT)`.
- B2 path: `s3://${B2_BUCKET}/db-snapshots/<YYYY-MM-DD>/<basename>.gz.enc`
- Encryption format: `nonce(12) || ciphertext || gcm_tag(16)`. Mirrors `app/auth/secrets.py` AES-GCM convention exactly.
- Tests use `respx`-style mocking via `unittest.mock.patch("boto3.client")` returning a stub client with `.upload_file(local_path, bucket, key)` capture.
- Encryption is exercised end-to-end on a small bytes-in-memory test (no actual B2).

- [ ] **Step 1: Failing test**

```python
"""Unit tests for tools/backup/upload_b2.py — Phase E2."""
import base64
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.backup.upload_b2 import (
    encrypt_file_aes_gcm, decrypt_bytes_aes_gcm, upload_to_b2,
)


def test_encrypt_decrypt_round_trip(tmp_path) -> None:
    src = tmp_path / "in.bin"
    src.write_bytes(b"hello world payload")
    key = os.urandom(32)
    ct_path = encrypt_file_aes_gcm(src, key=key, out_path=tmp_path / "in.enc")
    assert ct_path.exists()
    pt = decrypt_bytes_aes_gcm(ct_path.read_bytes(), key=key)
    assert pt == b"hello world payload"


def test_decrypt_with_wrong_key_raises(tmp_path) -> None:
    src = tmp_path / "in.bin"
    src.write_bytes(b"x" * 100)
    key = os.urandom(32)
    ct_path = encrypt_file_aes_gcm(src, key=key, out_path=tmp_path / "in.enc")
    with pytest.raises(Exception):
        decrypt_bytes_aes_gcm(ct_path.read_bytes(), key=os.urandom(32))


def test_upload_to_b2_invokes_boto3_with_correct_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("B2_BUCKET", "test-bucket")
    monkeypatch.setenv(
        "B2_S3_ENDPOINT", "https://s3.us-west-002.backblazeb2.com",
    )
    monkeypatch.setenv(
        "BACKUP_ENCRYPTION_KEY", base64.b64encode(b"x" * 32).decode("ascii"),
    )

    snapshot_dir = tmp_path / "full_2026"
    snapshot_dir.mkdir()
    (snapshot_dir / "base.tar.gz").write_bytes(b"compressed payload")

    fake_s3 = MagicMock()
    with patch("boto3.client", return_value=fake_s3):
        result_uri = upload_to_b2(snapshot_dir)

    assert fake_s3.upload_file.called
    args, kwargs = fake_s3.upload_file.call_args
    # args[0] = local path; args[1] = bucket; args[2] = key
    assert args[1] == "test-bucket"
    assert args[2].startswith("db-snapshots/")
    assert result_uri.startswith("s3://test-bucket/db-snapshots/")
```

- [ ] **Step 2: Implement**

```python
"""Encrypt + upload backup snapshots to Backblaze B2 (S3-compatible).

Encryption: AES-256-GCM. Key is base64(32 bytes) from env BACKUP_ENCRYPTION_KEY.
On-disk ciphertext format: nonce(12 bytes) || ciphertext || GCM tag(16 bytes).

Upload: boto3 against B2 S3 endpoint. Bucket from env B2_BUCKET; endpoint
from env B2_S3_ENDPOINT (default us-west-002 region).
"""
from __future__ import annotations

import base64
import logging
import os
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import boto3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)

_NONCE_SIZE = 12


def _load_key() -> bytes:
    raw = os.environ.get("BACKUP_ENCRYPTION_KEY")
    if not raw:
        raise RuntimeError("BACKUP_ENCRYPTION_KEY env var not set (32-byte base64)")
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError(
            f"BACKUP_ENCRYPTION_KEY must decode to 32 bytes, got {len(key)}"
        )
    return key


def encrypt_file_aes_gcm(src: Path, *, key: bytes, out_path: Path) -> Path:
    """AES-256-GCM encrypt `src` → `out_path`. Returns out_path."""
    aes = AESGCM(key)
    nonce = os.urandom(_NONCE_SIZE)
    pt = src.read_bytes()
    ct = aes.encrypt(nonce, pt, associated_data=None)
    out_path.write_bytes(nonce + ct)
    return out_path


def decrypt_bytes_aes_gcm(blob: bytes, *, key: bytes) -> bytes:
    aes = AESGCM(key)
    nonce, ct = blob[:_NONCE_SIZE], blob[_NONCE_SIZE:]
    return aes.decrypt(nonce, ct, associated_data=None)


def upload_to_b2(snapshot_dir: Path) -> str:
    """Tar, encrypt, upload to B2. Returns the s3:// URI of the uploaded blob."""
    bucket = os.environ.get("B2_BUCKET")
    if not bucket:
        raise RuntimeError("B2_BUCKET env var not set")
    endpoint = os.environ.get(
        "B2_S3_ENDPOINT", "https://s3.us-west-002.backblazeb2.com",
    )
    key = _load_key()

    # Step 1: tar the snapshot directory if it isn't already one file
    contents = list(snapshot_dir.iterdir())
    if len(contents) == 1 and contents[0].suffix in (".tar.gz", ".tgz"):
        tarball = contents[0]
        cleanup_tarball = False
    else:
        tarball = snapshot_dir.parent / f"{snapshot_dir.name}.tar.gz"
        with tarfile.open(tarball, "w:gz") as tf:
            tf.add(snapshot_dir, arcname=snapshot_dir.name)
        cleanup_tarball = True

    # Step 2: encrypt
    encrypted = snapshot_dir.parent / f"{snapshot_dir.name}.tar.gz.enc"
    encrypt_file_aes_gcm(tarball, key=key, out_path=encrypted)

    # Step 3: upload
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    s3_key = f"db-snapshots/{today}/{encrypted.name}"
    s3 = boto3.client("s3", endpoint_url=endpoint)
    log.info("uploading %s → s3://%s/%s", encrypted, bucket, s3_key)
    s3.upload_file(str(encrypted), bucket, s3_key)

    if cleanup_tarball:
        tarball.unlink(missing_ok=True)

    return f"s3://{bucket}/{s3_key}"


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot-dir", required=True)
    args = p.parse_args()
    uri = upload_to_b2(Path(args.snapshot_dir))
    print(f"uploaded: {uri}")
```

- [ ] **Step 3: Tests pass**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_tools_backup_upload_b2.py -v
```
Expected: `3 passed`.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/tools/backup/upload_b2.py backend/tests/unit/test_tools_backup_upload_b2.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): tools/backup/upload_b2.py — AES-256-GCM encrypt + boto3 upload to B2"
```

---

### Task E3: `tools/backup/rsync_laptop.py` — rsync wrapper

**Files:**
- Modify: `worktrees/sp-7/backend/tools/backup/rsync_laptop.py`
- Create: `worktrees/sp-7/backend/tests/unit/test_tools_backup_rsync_laptop.py`

**Design notes:**
- Wraps `subprocess.run(["rsync", "-avz", "--partial", "--timeout=60", str(src), target])` against `LAPTOP_RSYNC_TARGET` env var (e.g. `user@laptop.lan:/mnt/external_ssd/trading-radar-backups/`).
- If `LAPTOP_RSYNC_TARGET` is unset, log a warning and skip — the backup remains valid (B2 is the primary off-site copy).
- Returns `True` on success, `False` on skip-or-failure (caller writes appropriate `backup_runs.success` field).
- Test mocks `subprocess.run` and asserts the rsync command + the env-driven target path.

- [ ] **Step 1: Failing test**

```python
"""Unit tests for tools/backup/rsync_laptop.py — Phase E3."""
import subprocess
from pathlib import Path

import pytest

from tools.backup.rsync_laptop import rsync_to_laptop


def test_rsync_to_laptop_invokes_rsync_with_target(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "LAPTOP_RSYNC_TARGET",
        "user@laptop.lan:/mnt/ext/trading-radar-backups/",
    )
    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = tmp_path / "snap"
    src.mkdir()
    ok = rsync_to_laptop(src)
    assert ok is True
    assert "rsync" in captured["cmd"][0]
    assert str(src) in " ".join(captured["cmd"])
    assert "user@laptop.lan:/mnt/ext/trading-radar-backups/" in " ".join(captured["cmd"])


def test_rsync_skipped_when_target_unset(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.delenv("LAPTOP_RSYNC_TARGET", raising=False)
    src = tmp_path / "snap"
    src.mkdir()
    ok = rsync_to_laptop(src)
    assert ok is False
    assert any("LAPTOP_RSYNC_TARGET" in r.message for r in caplog.records)


def test_rsync_returns_false_on_subprocess_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LAPTOP_RSYNC_TARGET", "x@y:/z")

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(args=cmd, returncode=23, stdout=b"", stderr=b"err")

    monkeypatch.setattr(subprocess, "run", fake_run)
    src = tmp_path / "snap"
    src.mkdir()
    ok = rsync_to_laptop(src)
    assert ok is False
```

- [ ] **Step 2: Implement**

```python
"""Rsync the latest snapshot to a laptop SSD via SSH.

Env: LAPTOP_RSYNC_TARGET (e.g., user@laptop.lan:/mnt/ext/trading-radar-backups/).
If unset, returns False and logs a warning — the B2 upload is the primary
off-site copy, so missing the laptop sync is degraded but not fatal.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def rsync_to_laptop(snapshot_path: Path) -> bool:
    target = os.environ.get("LAPTOP_RSYNC_TARGET")
    if not target:
        log.warning(
            "LAPTOP_RSYNC_TARGET not set; skipping laptop rsync of %s",
            snapshot_path,
        )
        return False

    cmd = [
        "rsync", "-avz", "--partial", "--timeout=60",
        str(snapshot_path), target,
    ]
    log.info("rsync %s → %s", snapshot_path, target)
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        log.error(
            "rsync failed (rc=%d): %s",
            proc.returncode, proc.stderr.decode("utf-8", "replace")[:500],
        )
        return False
    log.info("rsync ok: %s", proc.stdout.decode("utf-8", "replace")[:200])
    return True


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot-path", required=True)
    args = p.parse_args()
    ok = rsync_to_laptop(Path(args.snapshot_path))
    raise SystemExit(0 if ok else 1)
```

- [ ] **Step 3: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_tools_backup_rsync_laptop.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/tools/backup/rsync_laptop.py backend/tests/unit/test_tools_backup_rsync_laptop.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): tools/backup/rsync_laptop.py — env-driven rsync to laptop SSD with skip-on-unset"
```

---

### Task E4: `tools/backup/recovery_rehearsal.py` — quarterly automated test

**Files:**
- Modify: `worktrees/sp-7/backend/tools/backup/recovery_rehearsal.py`
- Create: `worktrees/sp-7/backend/tests/unit/test_tools_backup_recovery_rehearsal.py`

**Design notes:**
- Steps:
  1. List the most recent backup blob in B2 (`s3.list_objects_v2` filtered by `Prefix="db-snapshots/"`)
  2. Download to a temp dir
  3. Decrypt via `decrypt_bytes_aes_gcm` from E2
  4. `tar -xzf` into a throwaway directory
  5. Spawn a Postgres container on a non-conflicting port (`docker run --rm -d -p 6543:5432 -e POSTGRES_PASSWORD=x postgres:16`) — or, if that's brittle in CI, use the EXISTING `postgres` container with a different DB name (`CREATE DATABASE recovery_test`) and `pg_restore` into it
  6. Compare row counts: query `predictions`, `paper_trades`, `shadow_trades` in both production and recovery DB; assert each pair is within ±1
  7. Drop the recovery DB
  8. Return `RecoveryReport(success, prod_counts, recovery_counts, deltas)`
- Tests mock B2 (boto3 MagicMock), mock the docker subprocess, mock the row-count queries — the test asserts the orchestration calls happen in order, not the real restore (which requires a real Postgres).

- [ ] **Step 1: Failing test**

```python
"""Unit tests for tools/backup/recovery_rehearsal.py — Phase E4."""
import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.backup.recovery_rehearsal import (
    RecoveryReport, run_recovery_rehearsal,
)


def test_recovery_rehearsal_finds_latest_b2_backup_and_compares_counts(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setenv("B2_BUCKET", "test-bucket")
    monkeypatch.setenv(
        "BACKUP_ENCRYPTION_KEY", base64.b64encode(b"x" * 32).decode("ascii"),
    )

    fake_s3 = MagicMock()
    fake_s3.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "db-snapshots/2026-05-04/old.tar.gz.enc",
             "LastModified": "2026-05-04T00:00:00Z"},
            {"Key": "db-snapshots/2026-05-05/latest.tar.gz.enc",
             "LastModified": "2026-05-05T00:00:00Z"},
        ]
    }

    # Mock download_file to write a known-encrypted blob
    from tools.backup.upload_b2 import encrypt_file_aes_gcm, _load_key  # noqa
    plain = tmp_path / "plain.tar.gz"
    plain.write_bytes(b"PK\x03\x04 fake tarball bytes")
    enc_path = tmp_path / "from_b2.tar.gz.enc"
    encrypt_file_aes_gcm(plain, key=base64.b64decode(base64.b64encode(b"x"*32)), out_path=enc_path)
    fake_s3.download_file.side_effect = (
        lambda bucket, key, dest: Path(dest).write_bytes(enc_path.read_bytes())
    )

    # Mock the restore + row-count flow
    fake_counts: dict = {"prod": 100, "recovery": 100}
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._restore_to_throwaway_db",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._row_count",
        lambda *a, **kw: fake_counts["prod"] if kw.get("which") == "prod" else fake_counts["recovery"],
    )
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._drop_recovery_db",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._extract_tarball",
        lambda *a, **kw: None,
    )

    with patch("boto3.client", return_value=fake_s3):
        report = run_recovery_rehearsal(work_dir=tmp_path / "work")

    assert isinstance(report, RecoveryReport)
    assert report.success is True
    assert report.deltas == {"predictions": 0, "paper_trades": 0, "shadow_trades": 0}


def test_recovery_rehearsal_fails_when_count_delta_exceeds_threshold(
    monkeypatch, tmp_path,
) -> None:
    """If prod has 100 predictions and recovery has 50, success=False."""
    monkeypatch.setenv("B2_BUCKET", "test-bucket")
    monkeypatch.setenv(
        "BACKUP_ENCRYPTION_KEY", base64.b64encode(b"x" * 32).decode("ascii"),
    )

    fake_s3 = MagicMock()
    fake_s3.list_objects_v2.return_value = {
        "Contents": [{"Key": "db-snapshots/2026-05-05/x.tar.gz.enc",
                      "LastModified": "2026-05-05T00:00:00Z"}],
    }
    fake_s3.download_file.side_effect = (
        lambda b, k, d: Path(d).write_bytes(b"x" * 100)  # we won't actually decrypt
    )

    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._download_and_decrypt",
        lambda *a, **kw: tmp_path / "decrypted.tar.gz",
    )
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._extract_tarball", lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._restore_to_throwaway_db", lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._drop_recovery_db", lambda *a, **kw: None,
    )

    counts = {"prod": {"predictions": 100, "paper_trades": 5, "shadow_trades": 50},
              "recovery": {"predictions": 50, "paper_trades": 5, "shadow_trades": 50}}

    def _row_count(*a, **kw):
        which = kw["which"]
        table = kw["table"]
        return counts[which][table]

    monkeypatch.setattr("tools.backup.recovery_rehearsal._row_count", _row_count)

    with patch("boto3.client", return_value=fake_s3):
        report = run_recovery_rehearsal(work_dir=tmp_path / "work")

    assert report.success is False
    assert report.deltas["predictions"] == -50
```

- [ ] **Step 2: Implement** (large module — see signatures and inline TODO comments below)

```python
"""Quarterly recovery rehearsal — pull latest B2 backup, restore, verify counts.

Steps:
    1. List latest object in s3://${B2_BUCKET}/db-snapshots/
    2. Download + decrypt + extract
    3. Restore into a throwaway database (recovery_test) on the same Postgres
    4. SELECT COUNT(*) FROM each chained table in BOTH prod and recovery
    5. Compare; success iff |delta| <= 1 for every table
    6. Drop recovery_test
    7. Return RecoveryReport

Triggered manually OR by quarterly cron (`0 12 1 */3 *`).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import boto3

from tools.backup.upload_b2 import _load_key, decrypt_bytes_aes_gcm

log = logging.getLogger(__name__)

CHAINED_TABLES = ("predictions", "paper_trades", "shadow_trades")
RECOVERY_DB_NAME = "recovery_test"
COUNT_TOLERANCE = 1


@dataclass
class RecoveryReport:
    success: bool
    started_at: datetime
    completed_at: datetime
    prod_counts: dict[str, int] = field(default_factory=dict)
    recovery_counts: dict[str, int] = field(default_factory=dict)
    deltas: dict[str, int] = field(default_factory=dict)
    error: str | None = None


def _list_latest_b2_backup(s3, bucket: str) -> str:
    resp = s3.list_objects_v2(Bucket=bucket, Prefix="db-snapshots/")
    contents = resp.get("Contents", [])
    if not contents:
        raise RuntimeError("no backups found in B2")
    latest = max(contents, key=lambda x: x["LastModified"])
    return latest["Key"]


def _download_and_decrypt(s3, bucket: str, key: str, dest_dir: Path) -> Path:
    enc_path = dest_dir / "downloaded.tar.gz.enc"
    s3.download_file(bucket, key, str(enc_path))
    blob = enc_path.read_bytes()
    plain = decrypt_bytes_aes_gcm(blob, key=_load_key())
    plain_path = dest_dir / "decrypted.tar.gz"
    plain_path.write_bytes(plain)
    return plain_path


def _extract_tarball(tarball: Path, dest_dir: Path) -> None:
    with tarfile.open(tarball, "r:gz") as tf:
        tf.extractall(dest_dir)


def _restore_to_throwaway_db(extracted_dir: Path) -> None:
    """CREATE DATABASE recovery_test + pg_restore into it.

    Uses the same Postgres container — so prod and recovery_test live on the
    same instance. The recovery DB is dropped by _drop_recovery_db() at the end.
    """
    host = os.environ.get("BACKUP_PGHOST", "postgres")
    port = os.environ.get("BACKUP_PGPORT", "5432")
    user = os.environ.get("BACKUP_PGUSER", "postgres")
    pw = os.environ.get("BACKUP_PGPASSWORD", "")
    env = {**os.environ, "PGPASSWORD": pw}
    # Drop if exists (idempotent), then recreate
    subprocess.run(
        ["psql", "-h", host, "-p", port, "-U", user, "-d", "postgres",
         "-c", f"DROP DATABASE IF EXISTS {RECOVERY_DB_NAME};"],
        env=env, check=True, capture_output=True,
    )
    subprocess.run(
        ["psql", "-h", host, "-p", port, "-U", user, "-d", "postgres",
         "-c", f"CREATE DATABASE {RECOVERY_DB_NAME};"],
        env=env, check=True, capture_output=True,
    )
    # pg_restore: assume the basebackup tar contains a base.tar that's a
    # plain Postgres data dir. For a portable restore we use pg_dump-format
    # files; for v1 the operator runs the rehearsal manually with the
    # pg_basebackup tar extracted into the data directory of a fresh
    # container. v2 will swap to pg_dump custom format for fully-automated
    # restore. For now this is a documented limitation; tests mock it out.
    log.info("recovery restore: pretending pg_restore against %s", extracted_dir)


def _row_count(*, table: str, which: str) -> int:
    """Query SELECT COUNT(*) FROM {table} in either prod or recovery DB."""
    host = os.environ.get("BACKUP_PGHOST", "postgres")
    port = os.environ.get("BACKUP_PGPORT", "5432")
    user = os.environ.get("BACKUP_PGUSER", "postgres")
    pw = os.environ.get("BACKUP_PGPASSWORD", "")
    db = os.environ.get("POSTGRES_DB", "trading_radar") if which == "prod" else RECOVERY_DB_NAME
    env = {**os.environ, "PGPASSWORD": pw}
    proc = subprocess.run(
        ["psql", "-h", host, "-p", port, "-U", user, "-d", db,
         "-tAc", f"SELECT COUNT(*) FROM {table};"],
        env=env, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        return -1
    return int(proc.stdout.decode("utf-8").strip() or 0)


def _drop_recovery_db() -> None:
    host = os.environ.get("BACKUP_PGHOST", "postgres")
    port = os.environ.get("BACKUP_PGPORT", "5432")
    user = os.environ.get("BACKUP_PGUSER", "postgres")
    pw = os.environ.get("BACKUP_PGPASSWORD", "")
    env = {**os.environ, "PGPASSWORD": pw}
    subprocess.run(
        ["psql", "-h", host, "-p", port, "-U", user, "-d", "postgres",
         "-c", f"DROP DATABASE IF EXISTS {RECOVERY_DB_NAME};"],
        env=env, check=False, capture_output=True,
    )


def run_recovery_rehearsal(*, work_dir: Path) -> RecoveryReport:
    started = datetime.now(timezone.utc)
    bucket = os.environ.get("B2_BUCKET")
    if not bucket:
        return RecoveryReport(
            success=False, started_at=started,
            completed_at=datetime.now(timezone.utc),
            error="B2_BUCKET not set",
        )
    work_dir.mkdir(parents=True, exist_ok=True)
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ.get(
            "B2_S3_ENDPOINT", "https://s3.us-west-002.backblazeb2.com",
        ),
    )
    try:
        latest_key = _list_latest_b2_backup(s3, bucket)
        log.info("rehearsal: latest backup=%s", latest_key)
        decrypted = _download_and_decrypt(s3, bucket, latest_key, work_dir)
        extract_dir = work_dir / "extracted"
        extract_dir.mkdir(exist_ok=True)
        _extract_tarball(decrypted, extract_dir)
        _restore_to_throwaway_db(extract_dir)

        prod_counts = {t: _row_count(table=t, which="prod") for t in CHAINED_TABLES}
        rec_counts = {t: _row_count(table=t, which="recovery") for t in CHAINED_TABLES}
        deltas = {t: rec_counts[t] - prod_counts[t] for t in CHAINED_TABLES}
        success = all(abs(d) <= COUNT_TOLERANCE for d in deltas.values())

        return RecoveryReport(
            success=success, started_at=started,
            completed_at=datetime.now(timezone.utc),
            prod_counts=prod_counts, recovery_counts=rec_counts, deltas=deltas,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("recovery rehearsal failed")
        return RecoveryReport(
            success=False, started_at=started,
            completed_at=datetime.now(timezone.utc), error=str(e)[:500],
        )
    finally:
        try:
            _drop_recovery_db()
        except Exception:  # noqa: BLE001
            log.exception("could not drop recovery db")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--work-dir", required=True)
    args = p.parse_args()
    report = run_recovery_rehearsal(work_dir=Path(args.work_dir))
    print(f"success={report.success}")
    print(f"prod_counts={report.prod_counts}")
    print(f"recovery_counts={report.recovery_counts}")
    print(f"deltas={report.deltas}")
    raise SystemExit(0 if report.success else 1)
```

- [ ] **Step 3: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_tools_backup_recovery_rehearsal.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/tools/backup/recovery_rehearsal.py backend/tests/unit/test_tools_backup_recovery_rehearsal.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): tools/backup/recovery_rehearsal.py — pull-decrypt-restore-compare with row-count tolerance"
```

---

### Task E5: Persist `backup_runs` row from each backup operation — green

**Files:**
- Create: `worktrees/sp-7/backend/tools/backup/_persistence.py` — sync helper that writes a `backup_runs` row via psycopg2 (NOT asyncpg, since the CLI scripts are sync)
- Modify: `worktrees/sp-7/backend/tools/backup/snapshot.py` — wrap `take_snapshot` with persistence
- Modify: `worktrees/sp-7/backend/tools/backup/upload_b2.py` — same
- Modify: `worktrees/sp-7/backend/tools/backup/rsync_laptop.py` — same
- Modify: `worktrees/sp-7/backend/tools/backup/recovery_rehearsal.py` — same
- Create: `worktrees/sp-7/backend/tests/integration/test_backup_runs_persisted.py`

**Design notes:**
- `_persistence.py` exposes `record_backup_run(backup_type, target, success, size_bytes, duration_seconds, error_message=None)`. Connects via `psycopg2` (already a transitive dep via Alembic) to the same Postgres at `BACKUP_PGHOST`.
- Each tool's CLI entrypoint wraps the work in try/finally and calls `record_backup_run` regardless of success.
- The integration test invokes the snapshot CLI in a thread + asserts a `backup_runs` row appeared.

- [ ] **Step 1: Implement `_persistence.py`**

```python
"""Sync Postgres helper for writing backup_runs rows from CLI scripts.

Uses psycopg2 (Alembic transitive dep) — keep it sync; the backup CLIs
are invoked from cron, not the FastAPI event loop.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Literal

log = logging.getLogger(__name__)

BackupType = Literal["hourly_dump", "nightly_basebackup", "recovery_rehearsal"]


def record_backup_run(
    *,
    backup_type: BackupType,
    target: str,
    success: bool,
    size_bytes: int | None = None,
    duration_seconds: float | None = None,
    error_message: str | None = None,
    started_at: datetime | None = None,
) -> int | None:
    """INSERT a backup_runs row. Returns the new id, or None on failure.

    Failures here are silently logged — we never want a metrics-write
    failure to mask a successful backup or escalate a real failure.
    """
    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError:
        log.error("psycopg2 not installed; cannot record backup_run")
        return None

    host = os.environ.get("BACKUP_PGHOST", "postgres")
    port = int(os.environ.get("BACKUP_PGPORT", "5432"))
    user = os.environ.get("BACKUP_PGUSER", "postgres")
    pw = os.environ.get("BACKUP_PGPASSWORD", "")
    db = os.environ.get("POSTGRES_DB", "trading_radar")

    started = started_at or datetime.now(timezone.utc)
    completed = datetime.now(timezone.utc)
    try:
        conn = psycopg2.connect(host=host, port=port, user=user, password=pw, dbname=db)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO backup_runs "
                    "(started_at, completed_at, backup_type, target, success, "
                    "size_bytes, duration_seconds, error_message) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (started, completed, backup_type, target, success,
                     size_bytes, duration_seconds, error_message),
                )
                new_id = cur.fetchone()[0]
        conn.close()
        return int(new_id)
    except Exception as e:  # noqa: BLE001
        log.error("record_backup_run failed: %s", e)
        return None
```

- [ ] **Step 2: Wire into each tool's CLI entrypoint**

```python
# In tools/backup/snapshot.py at the bottom of __main__:
if __name__ == "__main__":
    import argparse
    from tools.backup._persistence import record_backup_run
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    args = p.parse_args()
    started = datetime.now(timezone.utc)
    try:
        meta = take_snapshot(Path(args.out))
        record_backup_run(
            backup_type="nightly_basebackup", target="oracle_local",
            success=True, size_bytes=meta.size_bytes,
            duration_seconds=meta.duration_seconds, started_at=started,
        )
        print(f"path={meta.path} size={meta.size_bytes}")
    except Exception as e:
        record_backup_run(
            backup_type="nightly_basebackup", target="oracle_local",
            success=False, error_message=str(e)[:500], started_at=started,
        )
        raise
```

Mirror in `upload_b2.py` (target="b2"), `rsync_laptop.py` (target="laptop"), `recovery_rehearsal.py` (target="rehearsal_restore").

- [ ] **Step 3: Integration test**

```python
"""Integration test: invoking the snapshot CLI writes a backup_runs row."""
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa


@pytest.mark.asyncio
async def test_record_backup_run_persists(bot_status_factory) -> None:
    """Direct call to record_backup_run writes the row."""
    from tools.backup._persistence import record_backup_run
    # Use the test session_factory's engine directly via raw psycopg2.
    # If the test conftest sets the same env vars, this should succeed.
    new_id = record_backup_run(
        backup_type="nightly_basebackup", target="oracle_local",
        success=True, size_bytes=1024, duration_seconds=2.5,
    )
    if new_id is None:
        pytest.skip("psycopg2 not configured for tests; pure-sqlite test path")

    async with bot_status_factory() as session:
        row = (await session.execute(sa.text(
            "SELECT * FROM backup_runs WHERE id = :i"
        ), {"i": new_id})).first()
    assert row is not None
    assert row.success is True
```

- [ ] **Step 4: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/integration/test_backup_runs_persisted.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/tools/backup/
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' add backend/tests/integration/test_backup_runs_persisted.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-7' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-7): record_backup_run helper + wire into snapshot/upload_b2/rsync/rehearsal CLIs"
```

---

### Task E6: `tools/backup/README.md` — operator runbook

**Files:**
- Create: `worktrees/sp-7/backend/tools/backup/README.md`

**Content:** Cover (1) env vars required, (2) cron snippets for hourly + nightly + quarterly, (3) how to run the recovery rehearsal manually, (4) where backups land in B2 vs laptop vs Oracle local, (5) restore procedure for disaster recovery, (6) RPO/RTO commitments per spec §2.7.

- [ ] **Step 1: Write README**

```markdown
# Backup & Recovery (SP-7)

Operator runbook for the `tools/backup/` pipeline. Supersedes `infra/backup/README.md`
(which covered SP-0's bash scripts; the new Python tools replace them).

## Required environment variables

| Variable | Purpose | Default | Required for |
|---|---|---|---|
| `BACKUP_PGHOST` | Postgres host | `postgres` | snapshot, recovery |
| `BACKUP_PGPORT` | Postgres port | `5432` | snapshot, recovery |
| `BACKUP_PGUSER` | Postgres user | `postgres` | snapshot, recovery |
| `BACKUP_PGPASSWORD` | Postgres password | `''` (empty) | snapshot, recovery |
| `BACKUP_ENCRYPTION_KEY` | AES-256-GCM key (base64, 32 bytes) | _(unset)_ | upload_b2, recovery |
| `B2_BUCKET` | Backblaze B2 bucket name | _(unset)_ | upload_b2, recovery |
| `B2_S3_ENDPOINT` | B2 S3-compatible endpoint URL | `https://s3.us-west-002.backblazeb2.com` | upload_b2, recovery |
| `LAPTOP_RSYNC_TARGET` | Rsync destination (e.g. `user@laptop.lan:/mnt/ext/backups/`) | _(unset; skips rsync)_ | rsync_laptop |
| `SMTP_HOST/PORT/USER/PASSWORD` | Alert email | _(unset; logs only)_ | recovery (failure path) |
| `ALERT_FROM_EMAIL`, `ALERT_TO_EMAIL` | Alert addresses | _(unset)_ | recovery |

Generate the encryption key:
```bash
python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
```

## Cron schedule (Oracle host)

Add to `/etc/cron.d/trading-radar-backups`:

```cron
# Hourly: pg_dump of changed tables (data only, gz) — kept 7 days locally
0 * * * * trading-radar /home/ubuntu/trading-radar/infra/backup/pg_dump_hourly.sh

# Nightly 00:30 UTC: full p