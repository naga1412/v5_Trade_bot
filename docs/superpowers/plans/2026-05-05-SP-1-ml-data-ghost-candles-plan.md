# SP-1 — ML Data Pipeline + Ghost Candles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add machine-learning-driven 1-step OHLC prediction. Train a small Conv-LSTM on Binance OHLCV, evaluate on five fixed historical regime windows, deploy as inference on the existing FastAPI backend, render a dimmed "ghost" candle on Tab 1's chart with a Monte-Carlo-dropout-derived uncertainty band. Wire a nightly `pattern_stats` job and admin tooling for checkpoint registration / activation.

**Architecture:** New `app.ml.*` package: `model.py` (Conv-LSTM nn.Module), `normalize.py` (window normalization + denormalization), `inference.py` (`predict_ghost_candle`), `eval.py` (regime-window MAE harness), `regimes.py` (5 fixed date constants), `baseline.py` (random-walk MAE baseline), `export.py` (Parquet export to B2), `patterns.py` (nightly pattern-stats job), `checkpoints.py` (load active checkpoint at startup). New `app.api.routes.admin_ml` for `/api/v1/admin/ml-checkpoints` REST. Migration 0007 creates `feature_registry` + `ml_checkpoints` + `pattern_stats` tables and `ALTER`s `predictions` with eight ghost-related columns. Live prediction worker (existing `app/ws/live_prediction.py`) is extended to call `predict_ghost_candle()` after `build_prediction()` and persist ghost columns on the same row, with the row hash recomputed over a payload that includes the new fields. Frontend extends `LivePrediction` interface with a `ghost` field, `TVChart` renders a 50%-opacity candle to the right of the latest bar with thin uncertainty wicks at p5_low / p95_high, and `MasterBiasScore` panel gets a sub-row showing predicted close + uncertainty band width.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2 / asyncpg / TimescaleDB / PyTorch 2.x (CPU at runtime, T4 at training) / pandas / pyarrow (Parquet) / boto3 (B2 S3-compat) / cryptography (existing) · React 18 / Vite / TypeScript strict / Tailwind / lightweight-charts · pytest / pytest-asyncio / Vitest / Playwright

**Spec reference:** [`docs/superpowers/specs/2026-05-05-SP-1-ml-data-ghost-candles-design.md`](../specs/2026-05-05-SP-1-ml-data-ghost-candles-design.md). When this plan and the spec disagree, the spec wins.

**Cross-cutting policy compliance map (which §5 meta-plan policy each phase touches):**
- Phase A — §5.13 (B2 backups already wired; ML exports go into the same bucket under `ml-exports/`)
- Phase B — §5.14 partially (eval harness is pure functions; deterministic seed for repro)
- Phase C — §5.13 + §5.14 (checkpoints stored in B2 with sha256; registry table is append-mostly; `is_active` flip is auditable)
- Phase D — §5.14 (audit hash chain extended — `predictions.row_hash` payload now includes `ghost_*` columns and `model_checkpoint_id`)
- Phase E — model iteration; no new policy surface (Colab-driven)
- Phase F — §2.6 (admin endpoints inherit `Depends(require_admin)` from SP-0.7) + §5.14 (`pattern_stats` is derivable / not chained, but its update job uses already-chained source rows)

---

## File Structure

This is what SP-1 creates inside the new worktree. All paths are under `worktrees/sp-1/`.

```
worktrees/sp-1/
├── backend/
│   ├── alembic/versions/
│   │   └── 2026_05_05_0007_ml_tables_and_predictions_ghost.py   # NEW
│   ├── app/
│   │   ├── ml/                                # NEW package
│   │   │   ├── __init__.py
│   │   │   ├── model.py                       # ConvLSTMPredictor nn.Module
│   │   │   ├── normalize.py                   # normalize_window / denormalize_prediction
│   │   │   ├── inference.py                   # predict_ghost_candle (MC dropout)
│   │   │   ├── regimes.py                     # 5 regime window constants
│   │   │   ├── baseline.py                    # random-walk baseline
│   │   │   ├── eval.py                        # evaluate_on_regime
│   │   │   ├── export.py                      # parquet export (predictions/ohlcv/shadow_trades)
│   │   │   ├── patterns.py                    # nightly pattern_stats updater
│   │   │   ├── checkpoints.py                 # load active checkpoint at startup
│   │   │   └── seeds/
│   │   │       └── feature_registry.json      # initial feature registry seed
│   │   ├── api/
│   │   │   ├── routes/
│   │   │   │   └── admin_ml.py                # NEW — /api/v1/admin/ml-checkpoints
│   │   │   └── schemas.py                     # MODIFIED — GhostOut, MlCheckpointOut, LivePredictionOut.ghost
│   │   ├── ws/
│   │   │   └── live_prediction.py             # MODIFIED — call predict_ghost_candle, extend payload
│   │   ├── core/execution/
│   │   │   └── persistence.py                 # MODIFIED — accept ghost_* keys (audit chain payload)
│   │   └── main.py                            # MODIFIED — load active checkpoint in lifespan
│   ├── tools/
│   │   └── ml/
│   │       ├── bulk_export.py                 # NEW — one-time historical bulk export
│   │       └── colab/
│   │           ├── train_conv_lstm.ipynb      # NEW (committed)
│   │           └── sweep.ipynb                # NEW (committed)
│   └── tests/
│       ├── unit/
│       │   ├── test_ml_model.py               # ConvLSTMPredictor shape + dropout
│       │   ├── test_ml_normalize.py           # round-trip property
│       │   ├── test_ml_inference.py           # predict_ghost_candle against tiny stub
│       │   ├── test_ml_eval.py                # determinism + acceptance gate
│       │   ├── test_ml_baseline.py            # random walk MAE bounds
│       │   ├── test_ml_export.py              # Parquet write + readback
│       │   ├── test_ml_patterns.py            # join logic + ≥50 sample gate
│       │   ├── test_ml_checkpoints.py         # active checkpoint loader
│       │   └── test_ws_live_prediction_ghost.py  # ghost fields persisted
│       └── integration/
│           ├── test_api_admin_ml_checkpoints.py
│           └── test_ml_pipeline_e2e.py        # stub model → end-to-end
├── frontend/
│   ├── src/
│   │   ├── components/chart/
│   │   │   └── TVChart.tsx                    # MODIFIED — accept `ghost` prop, render ghost candle
│   │   ├── tabs/Admin/
│   │   │   ├── index.tsx                      # MODIFIED — add ML Checkpoints sub-tab
│   │   │   └── MlCheckpoints.tsx              # NEW
│   │   ├── tabs/Tab1LivePrediction/
│   │   │   ├── index.tsx                      # MODIFIED — pass data.ghost to TVChart + MasterBiasScore
│   │   │   └── panels/MasterBiasScore.tsx     # MODIFIED — render ghost candle preview row
│   │   └── lib/api.ts                         # MODIFIED — GhostCandle, MlCheckpoint types + endpoints
│   └── tests/
│       ├── unit/
│       │   ├── TVChart.ghost.test.tsx
│       │   ├── MasterBiasScore.ghost.test.tsx
│       │   └── Admin.MlCheckpoints.test.tsx
│       └── e2e/
│           └── ml-checkpoints.spec.ts
├── docs/
│   └── superpowers/
│       └── log.md                             # MODIFIED — SP-1 ship entry
└── docker-compose.yml + dev override + .env.example  (inherited from main, plus ML deps)
```

---

## Phase A — Worktree, schema, data export pipeline

### Task A1: Create SP-1 worktree + verify baseline

**Files:** none (git operation only)

- [ ] **Step 1: Verify clean main**

```bash
cd a:/v5_Trade_bot
git -c safe.directory='A:/v5_Trade_bot' status
git -c safe.directory='A:/v5_Trade_bot' log -1 --oneline
```
Expected: `On branch main`, `nothing to commit, working tree clean`, last commit hash starts with `72c24d6` (SP-0.7 hotfix).

- [ ] **Step 2: Create worktree**

```bash
git -c safe.directory='A:/v5_Trade_bot' worktree add worktrees/sp-1 -b sp-1/main
```
Expected: `Preparing worktree (new branch 'sp-1/main')`.

- [ ] **Step 3: Verify**

```bash
git -c safe.directory='A:/v5_Trade_bot' worktree list
```
Expected output includes `worktrees/sp-1  <hash> [sp-1/main]`.

- [ ] **Step 4: Bring stack up + run baseline tests**

```bash
cd worktrees/sp-1
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T frontend npm test -- --run
```
Expected: `309 passed` backend, `167 passed` Vitest. If either fails, stop — main is not green.

- [ ] **Step 5: Add ML deps to backend pyproject**

Edit `worktrees/sp-1/backend/pyproject.toml` `[project.dependencies]` to append:
```
"torch==2.4.1",
"pyarrow==17.0.0",
"boto3==1.35.0",
"duckdb==1.1.3",
```
(Torch is CPU-only at runtime; install via wheel. Specify `torch==2.4.1+cpu` in the Dockerfile pin if image size is a concern; for plan purposes the standard wheel is fine.)

- [ ] **Step 6: Rebuild + verify imports**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml build backend
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d backend
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python -c "import torch, pyarrow, boto3, duckdb; print('ok', torch.__version__)"
```
Expected: `ok 2.4.1`. If wheel install fails, fall back to `torch==2.4.1+cpu` from `https://download.pytorch.org/whl/cpu`.

- [ ] **Step 7: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/pyproject.toml
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "chore(sp-1): add torch, pyarrow, boto3, duckdb backend deps for ML pipeline"
```

---

### Task A2: Migration 0007 — `feature_registry` + `ml_checkpoints` + `pattern_stats` + ALTER `predictions`

**Files:**
- Create: `worktrees/sp-1/backend/alembic/versions/2026_05_05_0007_ml_tables_and_predictions_ghost.py`

**Design notes (apply throughout the migration):**
- All new ghost columns on `predictions` are NULLABLE — predictions written before SP-1 ships have NULL ghost data; predictions written after the active checkpoint is loaded include the ghost candle. Existing `row_hash`/`prev_hash` for SP-0.5/SP-0.7 rows still verify because the canonical hash payload at insertion time did not include these fields; Phase D extends the canonical payload going forward (see Task D1).
- `model_checkpoint_id` is a FK to `ml_checkpoints(id)`. Predictions written without a checkpoint loaded keep `model_checkpoint_id = NULL`.
- `pattern_stats.accuracy` is a Postgres `GENERATED ALWAYS AS ... STORED` column — `n_correct::float / n_samples` with a fallback to `0.5` when `n_samples = 0` so cold-start patterns get a neutral prior. Must use `DOUBLE PRECISION`.
- `ml_checkpoints` has a partial unique index on `(model_name)` `WHERE is_active = TRUE` so at most one checkpoint per model can be active at a time; this is enforced at the DB layer rather than in app code.

- [ ] **Step 1: Write migration**

```python
"""ML tables (feature_registry, ml_checkpoints, pattern_stats) + ghost columns on predictions

Revision ID: 0007_ml_tables_and_predictions_ghost
Revises: 0006_user_id_not_null
Create Date: 2026-05-05
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0007_ml_tables_and_predictions_ghost"
down_revision: str | None = "0006_user_id_not_null"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) ml_checkpoints — registry + active flag
    op.execute(
        """
        CREATE TABLE ml_checkpoints (
            id BIGSERIAL PRIMARY KEY,
            model_name TEXT NOT NULL,
            version TEXT NOT NULL,
            checkpoint_uri TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            trained_at TIMESTAMPTZ NOT NULL,
            train_data_window TEXT NOT NULL,
            eval_results JSONB NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT FALSE,
            activated_at TIMESTAMPTZ,
            deactivated_at TIMESTAMPTZ,
            notes TEXT,
            UNIQUE (model_name, version)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ml_checkpoints_active_idx "
        "ON ml_checkpoints (model_name) WHERE is_active = TRUE;"
    )

    # 2) feature_registry
    op.execute(
        """
        CREATE TABLE feature_registry (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            version INTEGER NOT NULL DEFAULT 1,
            description TEXT NOT NULL,
            dtype TEXT NOT NULL CHECK (dtype IN ('float', 'int', 'bool', 'category')),
            layer INTEGER,
            computation TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )

    # 3) pattern_stats — generated accuracy column
    op.execute(
        """
        CREATE TABLE pattern_stats (
            id BIGSERIAL PRIMARY KEY,
            pattern_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            n_samples INTEGER NOT NULL DEFAULT 0,
            n_correct INTEGER NOT NULL DEFAULT 0,
            accuracy DOUBLE PRECISION GENERATED ALWAYS AS
                (CASE WHEN n_samples = 0 THEN 0.5
                      ELSE n_correct::double precision / n_samples END) STORED,
            last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (pattern_id, symbol, timeframe)
        );
        """
    )
    op.execute(
        "CREATE INDEX pattern_stats_symbol_tf_idx "
        "ON pattern_stats (symbol, timeframe);"
    )

    # 4) ghost columns on predictions
    op.execute("ALTER TABLE predictions ADD COLUMN ghost_open DOUBLE PRECISION;")
    op.execute("ALTER TABLE predictions ADD COLUMN ghost_high DOUBLE PRECISION;")
    op.execute("ALTER TABLE predictions ADD COLUMN ghost_low DOUBLE PRECISION;")
    op.execute("ALTER TABLE predictions ADD COLUMN ghost_close DOUBLE PRECISION;")
    op.execute("ALTER TABLE predictions ADD COLUMN ghost_p5_low DOUBLE PRECISION;")
    op.execute("ALTER TABLE predictions ADD COLUMN ghost_p95_high DOUBLE PRECISION;")
    op.execute("ALTER TABLE predictions ADD COLUMN ghost_uncertainty DOUBLE PRECISION;")
    op.execute(
        "ALTER TABLE predictions ADD COLUMN model_checkpoint_id BIGINT "
        "REFERENCES ml_checkpoints(id);"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS model_checkpoint_id;")
    for col in (
        "ghost_uncertainty",
        "ghost_p95_high",
        "ghost_p5_low",
        "ghost_close",
        "ghost_low",
        "ghost_high",
        "ghost_open",
    ):
        op.execute(f"ALTER TABLE predictions DROP COLUMN IF EXISTS {col};")
    op.execute("DROP TABLE IF EXISTS pattern_stats;")
    op.execute("DROP TABLE IF EXISTS feature_registry;")
    op.execute("DROP INDEX IF EXISTS ml_checkpoints_active_idx;")
    op.execute("DROP TABLE IF EXISTS ml_checkpoints;")
```

- [ ] **Step 2: Run migration**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend bash -c "cd /app && alembic upgrade head"
```
Expected: `Running upgrade 0006_user_id_not_null -> 0007_ml_tables_and_predictions_ghost`.

- [ ] **Step 3: Verify tables + columns**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U postgres trading_radar -c "\dt"
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U postgres trading_radar -c "\d predictions"
```
Expected: lists `feature_registry`, `ml_checkpoints`, `pattern_stats`; `\d predictions` shows the eight new columns including the `model_checkpoint_id` FK.

- [ ] **Step 4: Verify audit chain still intact for pre-existing rows**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_audit_verify.py -v
```
Expected: passes — the migration is purely additive (NULLable columns); existing `row_hash` values still verify against their original payloads.

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/alembic/versions/2026_05_05_0007_ml_tables_and_predictions_ghost.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): migration 0007 — ml_checkpoints + feature_registry + pattern_stats + predictions.ghost_*"
```

---

### Task A3: Seed `feature_registry` with initial indicator set — TDD

**Files:**
- Create: `worktrees/sp-1/backend/app/ml/__init__.py` (empty)
- Create: `worktrees/sp-1/backend/app/ml/seeds/feature_registry.json`
- Create: `worktrees/sp-1/backend/alembic/versions/2026_05_05_0008_seed_feature_registry.py`
- Create: `worktrees/sp-1/backend/tests/unit/test_feature_registry_seed.py`

**Design note:** seed via a separate alembic data migration (0008) rather than appending to 0007. This keeps schema-vs-data migrations cleanly separated and makes the seed re-runnable conceptually (the SQL uses `ON CONFLICT (name) DO NOTHING`). The seed JSON ships in the repo so the next sub-project can extend it.

- [ ] **Step 1: Write seed JSON**

`worktrees/sp-1/backend/app/ml/seeds/feature_registry.json`:
```json
[
  {"name": "open",          "version": 1, "dtype": "float", "layer": null, "computation": "raw_ohlcv.open",  "description": "Raw bar open price"},
  {"name": "high",          "version": 1, "dtype": "float", "layer": null, "computation": "raw_ohlcv.high",  "description": "Raw bar high price"},
  {"name": "low",           "version": 1, "dtype": "float", "layer": null, "computation": "raw_ohlcv.low",   "description": "Raw bar low price"},
  {"name": "close",         "version": 1, "dtype": "float", "layer": null, "computation": "raw_ohlcv.close", "description": "Raw bar close price"},
  {"name": "volume",        "version": 1, "dtype": "float", "layer": null, "computation": "raw_ohlcv.volume","description": "Raw bar volume"},
  {"name": "rsi_14",        "version": 1, "dtype": "float", "layer": 1,    "computation": "indicators.rsi(close, 14)",     "description": "Wilder RSI, period 14"},
  {"name": "macd_line",     "version": 1, "dtype": "float", "layer": 1,    "computation": "indicators.macd(close).line",   "description": "MACD line (ema12 - ema26)"},
  {"name": "macd_signal",   "version": 1, "dtype": "float", "layer": 1,    "computation": "indicators.macd(close).signal", "description": "MACD signal line (ema9 of macd_line)"},
  {"name": "macd_hist",     "version": 1, "dtype": "float", "layer": 1,    "computation": "indicators.macd(close).hist",   "description": "MACD histogram"},
  {"name": "atr_14",        "version": 1, "dtype": "float", "layer": 3,    "computation": "indicators.atr(high, low, close, 14)", "description": "Average True Range, period 14"},
  {"name": "ema_20",        "version": 1, "dtype": "float", "layer": 5,    "computation": "indicators.ema(close, 20)",     "description": "EMA period 20"},
  {"name": "ema_50",        "version": 1, "dtype": "float", "layer": 5,    "computation": "indicators.ema(close, 50)",     "description": "EMA period 50"}
]
```

- [ ] **Step 2: Failing test**

`worktrees/sp-1/backend/tests/unit/test_feature_registry_seed.py`:
```python
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

SEED_PATH = Path("app/ml/seeds/feature_registry.json")


def test_seed_json_loadable_and_has_min_set() -> None:
    data = json.loads(SEED_PATH.read_text())
    names = {e["name"] for e in data}
    assert {"open", "high", "low", "close", "volume",
            "rsi_14", "macd_line", "macd_signal", "macd_hist",
            "atr_14", "ema_20", "ema_50"} <= names
    for entry in data:
        assert entry["dtype"] in {"float", "int", "bool", "category"}
        assert isinstance(entry["computation"], str) and entry["computation"]


@pytest.mark.asyncio
async def test_seed_inserts_idempotently_on_clean_db() -> None:
    """Seed function used by alembic 0008 must be importable + idempotent."""
    from app.ml.seeds_loader import load_feature_registry_seed

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE feature_registry ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL UNIQUE, version INTEGER NOT NULL DEFAULT 1, "
            "description TEXT NOT NULL, dtype TEXT NOT NULL, "
            "layer INTEGER, computation TEXT NOT NULL, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
        ))

    async with AsyncSession(engine) as session:
        n1 = await load_feature_registry_seed(session)
        await session.commit()
        n2 = await load_feature_registry_seed(session)
        await session.commit()

    assert n1 >= 12
    assert n2 == 0  # idempotent re-run inserts zero new rows
```

- [ ] **Step 3: Run — fail** (`pytest tests/unit/test_feature_registry_seed.py -v`). Expected: ImportError on `app.ml.seeds_loader`.

- [ ] **Step 4: Implement**

`worktrees/sp-1/backend/app/ml/seeds_loader.py`:
```python
"""Idempotent seed loader for feature_registry.

Used by alembic 0008 and by tests. Reads the JSON seed file and inserts rows
with ON CONFLICT(name) DO NOTHING semantics. SQLite doesn't support that exact
clause; we emulate by reading existing names first then inserting only the new
ones. Postgres production path uses a true ON CONFLICT.
"""
import json
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

_SEED_PATH = Path(__file__).parent / "seeds" / "feature_registry.json"


async def load_feature_registry_seed(session: AsyncSession) -> int:
    """Insert rows from feature_registry.json that aren't already present.

    Returns the number of newly-inserted rows.
    """
    entries = json.loads(_SEED_PATH.read_text())
    existing = {
        r.name for r in (
            await session.execute(sa.text("SELECT name FROM feature_registry"))
        ).all()
    }
    inserted = 0
    for e in entries:
        if e["name"] in existing:
            continue
        await session.execute(
            sa.text(
                "INSERT INTO feature_registry "
                "(name, version, description, dtype, layer, computation) "
                "VALUES (:n, :v, :d, :dt, :l, :c)"
            ),
            {
                "n": e["name"], "v": e.get("version", 1), "d": e["description"],
                "dt": e["dtype"], "l": e.get("layer"), "c": e["computation"],
            },
        )
        inserted += 1
    return inserted
```

`worktrees/sp-1/backend/alembic/versions/2026_05_05_0008_seed_feature_registry.py`:
```python
"""Seed feature_registry with initial indicator set

Revision ID: 0008_seed_feature_registry
Revises: 0007_ml_tables_and_predictions_ghost
Create Date: 2026-05-05
"""
import asyncio
from collections.abc import Sequence

from alembic import op


revision: str = "0008_seed_feature_registry"
down_revision: str | None = "0007_ml_tables_and_predictions_ghost"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Run async seed loader synchronously inside alembic."""
    from app.db.session import get_session_factory
    from app.ml.seeds_loader import load_feature_registry_seed

    async def _run() -> None:
        async with get_session_factory()() as session:
            await load_feature_registry_seed(session)
            await session.commit()

    asyncio.run(_run())


def downgrade() -> None:
    op.execute("DELETE FROM feature_registry;")
```

- [ ] **Step 5: Run migration + tests**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend bash -c "cd /app && alembic upgrade head"
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_feature_registry_seed.py -v
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U postgres trading_radar -c "SELECT COUNT(*) FROM feature_registry;"
```
Expected: `Running upgrade 0007 -> 0008`, both unit tests pass, count `>= 12`.

- [ ] **Step 6: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/app/ml/__init__.py backend/app/ml/seeds_loader.py backend/app/ml/seeds/feature_registry.json backend/alembic/versions/2026_05_05_0008_seed_feature_registry.py backend/tests/unit/test_feature_registry_seed.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): seed feature_registry with initial 12-feature indicator set (alembic 0008)"
```

---

### Task A4: `app/ml/export.py` — Parquet export of last 30d data — TDD

**Files:**
- Create: `worktrees/sp-1/backend/app/ml/export.py`
- Create: `worktrees/sp-1/backend/tests/unit/test_ml_export.py`

**Design note:** export targets local disk first (parameterized output dir); B2 upload is a separate concern delegated to the existing backup pipeline (it copies whatever lands in `/app/data/ml-exports/<date>/` to the B2 bucket). This keeps `export.py` testable without mocking S3. The script writes 3 Parquet files + a `manifest.json` with sha256 of each file. Each parquet uses snappy compression and Arrow table schema explicitly specified for column-type stability.

- [ ] **Step 1: Failing test**

```python
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.ml.export import export_recent_to_parquet, ExportManifest


@pytest.fixture
async def engine_with_minimal_tables(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, ts TEXT NOT NULL, "
            "final_score REAL NOT NULL, direction TEXT NOT NULL, "
            "confidence REAL NOT NULL, inputs_hash TEXT NOT NULL, "
            "model_version TEXT NOT NULL, cold_start INTEGER NOT NULL, "
            "layer_scores TEXT NOT NULL, "
            "ghost_open REAL, ghost_high REAL, ghost_low REAL, ghost_close REAL, "
            "ghost_p5_low REAL, ghost_p95_high REAL, ghost_uncertainty REAL, "
            "model_checkpoint_id INTEGER, "
            "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL, exit_price REAL NOT NULL, "
            "stop_loss REAL NOT NULL, take_profit REAL NOT NULL, "
            "pnl_pct REAL NOT NULL, pnl_usdt REAL NOT NULL, "
            "exit_reason TEXT NOT NULL, bars_held INTEGER NOT NULL, "
            "opened_at TEXT NOT NULL, closed_at TEXT NOT NULL, "
            "signal_id TEXT NOT NULL, "
            "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL)"
        ))
    yield engine, tmp_path
    await engine.dispose()


@pytest.mark.asyncio
async def test_export_writes_three_parquets_plus_manifest(engine_with_minimal_tables) -> None:
    engine, tmp_path = engine_with_minimal_tables
    now = datetime.now(timezone.utc)

    async with AsyncSession(engine) as session:
        for i in range(5):
            await session.execute(sa.text(
                "INSERT INTO predictions "
                "(user_id, symbol, timeframe, ts, final_score, direction, confidence, "
                "inputs_hash, model_version, cold_start, layer_scores, "
                "prev_hash, row_hash) "
                "VALUES (1, 'BTC/USDT', '1h', :ts, 0.5, 'LONG', 0.7, "
                "'h', 'sp-0', 0, '{}', '0', 'r')"
            ), {"ts": (now - timedelta(hours=i)).isoformat()})
        await session.commit()

    manifest = await export_recent_to_parquet(
        session_factory=lambda: AsyncSession(engine),
        out_dir=tmp_path,
        days=30,
        now=now,
    )

    assert (tmp_path / "predictions_30d.parquet").exists()
    assert (tmp_path / "ohlcv_1h_30d.parquet").exists()  # may be 0 rows if no ohlcv table; export still creates empty file
    assert (tmp_path / "shadow_trades_30d.parquet").exists()
    assert (tmp_path / "manifest.json").exists()

    table = pq.read_table(tmp_path / "predictions_30d.parquet")
    assert table.num_rows == 5
    assert "ghost_close" in table.schema.names

    manifest_json = json.loads((tmp_path / "manifest.json").read_text())
    assert "predictions_30d.parquet" in manifest_json["files"]
    assert len(manifest_json["files"]["predictions_30d.parquet"]["sha256"]) == 64
    assert manifest_json["window_days"] == 30


@pytest.mark.asyncio
async def test_export_returns_manifest_dataclass(engine_with_minimal_tables) -> None:
    engine, tmp_path = engine_with_minimal_tables
    manifest = await export_recent_to_parquet(
        session_factory=lambda: AsyncSession(engine),
        out_dir=tmp_path,
        days=30,
    )
    assert isinstance(manifest, ExportManifest)
    assert manifest.window_days == 30
    assert manifest.created_at is not None
```

- [ ] **Step 2: Run — fail** (ImportError on `app.ml.export`).

- [ ] **Step 3: Implement**

```python
"""Parquet export of recent OHLCV/predictions/shadow_trades for ML training.

Spec §5.1 — runs nightly via cron; writes to a date-stamped local directory
under /app/data/ml-exports/<YYYY-MM-DD>/. The existing B2 backup pipeline picks
the directory up and ships it to b2://trading-radar-backups/ml-exports/.

Tables exported:
  - predictions (incl. ghost_* columns + model_checkpoint_id)
  - shadow_trades (used to label which predictions resulted in wins)
  - ohlcv_1h (raw bars, for retraining input — pulled from binance_klines if
    we cache it, otherwise from a market-data adapter)

Each parquet file uses snappy compression. Manifest carries sha256 of each
file so downstream consumers can verify integrity after S3 transit.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import pyarrow as pa
import pyarrow.parquet as pq
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ExportFileInfo:
    sha256: str
    rows: int
    bytes: int


@dataclass(frozen=True)
class ExportManifest:
    window_days: int
    created_at: str
    files: dict[str, dict[str, Any]] = field(default_factory=dict)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


async def _select_to_table(
    session: AsyncSession, sql: str, params: dict[str, Any],
) -> pa.Table:
    result = await session.execute(sa.text(sql), params)
    rows = result.mappings().all()
    if not rows:
        return pa.table({})
    cols: dict[str, list[Any]] = {k: [] for k in rows[0].keys()}
    for r in rows:
        for k, v in r.items():
            cols[k].append(v)
    return pa.table(cols)


async def export_recent_to_parquet(
    *,
    session_factory: Callable[[], AsyncSession],
    out_dir: Path,
    days: int = 30,
    now: datetime | None = None,
) -> ExportManifest:
    """Export the last `days` days of training-relevant data to `out_dir`."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    queries: dict[str, tuple[str, dict[str, Any]]] = {
        "predictions_30d.parquet": (
            "SELECT * FROM predictions WHERE ts >= :since ORDER BY ts ASC",
            {"since": since.isoformat()},
        ),
        "shadow_trades_30d.parquet": (
            "SELECT * FROM shadow_trades WHERE closed_at >= :since ORDER BY closed_at ASC",
            {"since": since.isoformat()},
        ),
        # ohlcv may not exist as a table in dev; fall back to empty Parquet
        "ohlcv_1h_30d.parquet": (
            "SELECT 1 AS placeholder WHERE 0=1",  # empty schema; replaced if table exists
            {},
        ),
    }

    files: dict[str, dict[str, Any]] = {}
    async with session_factory() as session:
        for filename, (sql, params) in queries.items():
            try:
                table = await _select_to_table(session, sql, params)
            except Exception:  # noqa: BLE001
                table = pa.table({})
            target = out_dir / filename
            if table.num_rows == 0:
                # Write an empty parquet with at least a marker schema
                pq.write_table(pa.table({"_empty": []}), target, compression="snappy")
            else:
                pq.write_table(table, target, compression="snappy")
            files[filename] = {
                "sha256": _sha256_of(target),
                "rows": table.num_rows,
                "bytes": target.stat().st_size,
            }

    manifest = ExportManifest(
        window_days=days,
        created_at=now.isoformat(),
        files=files,
    )
    (out_dir / "manifest.json").write_text(json.dumps(asdict(manifest), indent=2))
    return manifest
```

- [ ] **Step 4: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_ml_export.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/app/ml/export.py backend/tests/unit/test_ml_export.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): app/ml/export.py — parquet export of last 30d predictions/shadow_trades + manifest"
```
Expected: `2 passed`.

---

### Task A5: `tools/ml/bulk_export.py` — one-time historical bulk export script

**Files:**
- Create: `worktrees/sp-1/backend/tools/ml/__init__.py` (empty, for package import)
- Create: `worktrees/sp-1/backend/tools/ml/bulk_export.py`

**Design note:** this is a manual operations script, *not* a scheduled job. Run by the developer once to seed the B2 bucket with 5 GB of historical OHLCV data for the first Conv-LSTM training run. Subsequent training runs use the nightly incremental Parquet from Task A4. Because it's manual, no pytest gates — just an importability smoke check.

- [ ] **Step 1: Implement**

```python
"""One-time historical bulk export for the first Conv-LSTM training run.

Pulls all-time 1h OHLCV for the 30 USDT-quoted assets via the existing
BinanceClient REST adapter (no live websocket), writes ~5 GB of Parquet to
the local out dir, then upload to B2 manually with rclone or `aws s3 cp`.

Usage:
    docker compose exec backend python -m tools.ml.bulk_export \
        --out /app/data/ml-bulk-export \
        --start 2017-08-01 --end 2026-05-04

Not committed to a cron — operator runs once. Subsequent training runs use
the nightly incremental from app/ml/export.py (last 30 days).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.data.adapters.binance import BinanceClient
from app.shadow.universe import TOP_USDT_ASSETS  # 30 symbols list

log = logging.getLogger(__name__)


async def fetch_history_for_symbol(
    client: BinanceClient, symbol: str, start: datetime, end: datetime,
) -> pd.DataFrame:
    """Page through Binance 1h klines from start to end (1000 per request)."""
    all_rows: list[dict] = []
    cursor = start
    while cursor < end:
        batch = await client.fetch_klines(
            symbol.replace("/", ""), "1h", limit=1000, start_ms=int(cursor.timestamp() * 1000),
        )
        if not batch:
            break
        all_rows.extend([c.__dict__ for c in batch])
        cursor = max(c.ts for c in batch)
    df = pd.DataFrame(all_rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.drop_duplicates(subset=["ts"]).sort_values("ts")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    manifest: dict = {"start": start.isoformat(), "end": end.isoformat(), "files": {}}
    async with httpx.AsyncClient(timeout=60.0) as http:
        client = BinanceClient(http=http)
        for sym in TOP_USDT_ASSETS:
            log.info("exporting %s", sym)
            df = await fetch_history_for_symbol(client, sym, start, end)
            target = out / f"ohlcv_1h_{sym.replace('/', '_')}.parquet"
            pq.write_table(pa.Table.from_pandas(df), target, compression="snappy")
            manifest["files"][target.name] = {"rows": len(df)}
            log.info("  wrote %d rows -> %s", len(df), target.name)

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"bulk export complete: {out}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
```

- [ ] **Step 2: Smoke check (no pytest — manual)**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python -c "from tools.ml.bulk_export import main; print('importable')"
```
Expected: `importable`. (We do not actually run the script in CI — it would download ~5GB.)

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/tools/ml/__init__.py backend/tools/ml/bulk_export.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): tools/ml/bulk_export.py — one-time historical OHLCV bulk export script"
```

---

## Phase B — Eval harness + 5 regime windows

### Task B1: `app/ml/regimes.py` — 5 regime window date constants

**Files:**
- Create: `worktrees/sp-1/backend/app/ml/regimes.py`
- Create: `worktrees/sp-1/backend/tests/unit/test_ml_regimes.py`

- [ ] **Step 1: Failing test**

```python
from datetime import datetime, timezone

import pytest

from app.ml.regimes import REGIME_WINDOWS, RegimeWindow


def test_five_named_regime_windows_present() -> None:
    names = {w.name for w in REGIME_WINDOWS}
    assert names == {
        "bull_breakout", "bear_crash", "sideways_grind",
        "high_volatility", "low_volatility",
    }


def test_each_window_has_valid_date_range() -> None:
    for w in REGIME_WINDOWS:
        assert isinstance(w, RegimeWindow)
        assert isinstance(w.start, datetime)
        assert isinstance(w.end, datetime)
        assert w.start.tzinfo is timezone.utc
        assert w.end.tzinfo is timezone.utc
        assert w.start < w.end
        assert (w.end - w.start).days >= 14  # all windows ≥ 2 weeks


def test_acceptance_threshold_constant() -> None:
    from app.ml.regimes import ACCEPTANCE_MAE_THRESHOLD
    assert ACCEPTANCE_MAE_THRESHOLD == 0.015  # spec §2 row 13: 1.5%
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement**

```python
"""Five fixed historical regime windows for the eval harness.

Spec §5.3 — the model must achieve MAE ≤ 1.5% on ALL five windows. A single
window failing rejects the checkpoint for activation.
"""
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class RegimeWindow:
    name: str
    start: datetime
    end: datetime
    description: str


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


# Spec §5.3
REGIME_WINDOWS: tuple[RegimeWindow, ...] = (
    RegimeWindow("bull_breakout",   _utc(2020, 10, 1),  _utc(2021, 4, 30),
                 "Post-COVID rally — sustained uptrend, expanding volatility"),
    RegimeWindow("bear_crash",      _utc(2022, 4, 1),   _utc(2022, 12, 31),
                 "LUNA + FTX collapses — sustained downtrend, large gaps"),
    RegimeWindow("sideways_grind",  _utc(2023, 4, 1),   _utc(2023, 9, 30),
                 "Range-bound — no directional bias, mean-reverting"),
    RegimeWindow("high_volatility", _utc(2020, 3, 1),   _utc(2020, 4, 15),
                 "COVID crash — extreme volatility, both directions"),
    RegimeWindow("low_volatility",  _utc(2024, 4, 1),   _utc(2024, 7, 31),
                 "Post-halving compression — tight range, low ATR"),
)


# Spec §2 row 13: ≤ 1.5% MAE on ALL 5 windows.
ACCEPTANCE_MAE_THRESHOLD: float = 0.015
```

- [ ] **Step 4: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_ml_regimes.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/app/ml/regimes.py backend/tests/unit/test_ml_regimes.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): app/ml/regimes.py — 5 fixed regime windows + 1.5% acceptance threshold"
```
Expected: `3 passed`.

---

### Task B2: `app/ml/eval.py` — `evaluate_on_regime()` — TDD

**Files:**
- Create: `worktrees/sp-1/backend/app/ml/eval.py`
- Create: `worktrees/sp-1/backend/tests/unit/test_ml_eval.py`

**Design note:** the eval harness takes (model, ohlcv_dataframe, regime_window) and returns `RegimeEvalResult{mae, samples, passes_acceptance}`. It does NOT load OHLCV from B2 itself — caller passes a pandas DataFrame so tests can use synthetic data. Determinism requirement: given the same model + bars + window + seed, must return identical MAE. That requires the model's MC dropout to be seeded; eval flips dropout off (`model.eval()`) for the per-bar prediction (we want point estimate, not distribution, for MAE reporting — uncertainty is reported separately if needed).

- [ ] **Step 1: Failing test**

```python
import numpy as np
import pandas as pd
import pytest
import torch
from torch import nn

from app.ml.eval import evaluate_on_regime, RegimeEvalResult
from app.ml.regimes import REGIME_WINDOWS, ACCEPTANCE_MAE_THRESHOLD


class _ConstantModel(nn.Module):
    """Always predicts 0% change for all 4 outputs. MAE = avg|actual_pct|."""
    def forward(self, x: torch.Tensor) -> torch.Tensor:  # noqa: D401
        return torch.zeros(x.shape[0], 4, dtype=torch.float32)


def _synthetic_bars(n: int, start_ts: pd.Timestamp, drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.005, size=n)))
    df = pd.DataFrame({
        "open": closes * (1 + rng.normal(0, 0.001, size=n)),
        "high": closes * (1 + np.abs(rng.normal(0, 0.002, size=n))),
        "low":  closes * (1 - np.abs(rng.normal(0, 0.002, size=n))),
        "close": closes,
        "volume": rng.uniform(1e3, 1e4, size=n),
    })
    df.index = pd.date_range(start=start_ts, periods=n, freq="1h", tz="UTC")
    return df


def test_evaluate_on_regime_returns_result_dataclass() -> None:
    bars = _synthetic_bars(512, pd.Timestamp("2024-04-01", tz="UTC"))
    model = _ConstantModel()
    window = next(w for w in REGIME_WINDOWS if w.name == "low_volatility")

    result = evaluate_on_regime(model=model, bars=bars, window=window, seed=42)
    assert isinstance(result, RegimeEvalResult)
    assert result.regime_name == "low_volatility"
    assert result.samples > 0
    assert result.mae > 0  # constant predictor never gets exact 0% bars
    assert isinstance(result.passes_acceptance, bool)


def test_evaluate_is_deterministic_for_fixed_seed() -> None:
    bars = _synthetic_bars(512, pd.Timestamp("2024-04-01", tz="UTC"))
    model = _ConstantModel()
    window = next(w for w in REGIME_WINDOWS if w.name == "low_volatility")

    a = evaluate_on_regime(model=model, bars=bars, window=window, seed=42)
    b = evaluate_on_regime(model=model, bars=bars, window=window, seed=42)
    assert a.mae == b.mae
    assert a.samples == b.samples


def test_passes_acceptance_uses_threshold() -> None:
    bars = _synthetic_bars(512, pd.Timestamp("2024-04-01", tz="UTC"))
    window = next(w for w in REGIME_WINDOWS if w.name == "low_volatility")

    class _PerfectModel(nn.Module):
        """Memorizes the true % change for each window-end's actual next bar.

        Trivially achieves MAE ~0; passes_acceptance must be True.
        """
        def __init__(self, bars: pd.DataFrame) -> None:
            super().__init__()
            self.bars = bars
            self._counter = 256

        def forward(self, x: torch.Tensor) -> torch.Tensor:  # noqa: D401
            i = self._counter
            self._counter += 1
            actual = self.bars.iloc[i + 1]
            last_close = self.bars.iloc[i]["close"]
            pct = torch.tensor([
                actual["open"] / last_close - 1,
                actual["high"] / last_close - 1,
                actual["low"]  / last_close - 1,
                actual["close"]/ last_close - 1,
            ], dtype=torch.float32).unsqueeze(0)
            return pct

    model = _PerfectModel(bars)
    result = evaluate_on_regime(model=model, bars=bars, window=window, seed=42)
    assert result.mae < ACCEPTANCE_MAE_THRESHOLD
    assert result.passes_acceptance is True
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement**

```python
"""Per-regime MAE evaluation harness.

Spec §5.3 — for each window:
  1. Slice `bars` to [window.start, window.end]
  2. Build a sliding window prediction loop: at each i (>= 256), feed last 256
     bars into the model, compare predicted next-bar O/H/L/C against actual.
  3. Aggregate to MAE (mean over all 4 outputs across all samples).
  4. `passes_acceptance = mae <= ACCEPTANCE_MAE_THRESHOLD`.

Determinism: callers pass a `seed`. Torch RNG + NumPy RNG are seeded inside
this function. `model.eval()` is called (we want point estimates here, not
MC dropout — uncertainty is reported separately by `predict_ghost_candle`).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch
from torch import nn

from app.ml.normalize import normalize_window
from app.ml.regimes import ACCEPTANCE_MAE_THRESHOLD, RegimeWindow


WINDOW_BARS: int = 256


@dataclass(frozen=True)
class RegimeEvalResult:
    regime_name: str
    mae: float
    samples: int
    passes_acceptance: bool
    per_output_mae: tuple[float, float, float, float]  # open/high/low/close


def evaluate_on_regime(
    *,
    model: nn.Module,
    bars: pd.DataFrame,
    window: RegimeWindow,
    seed: int = 42,
    threshold: float = ACCEPTANCE_MAE_THRESHOLD,
) -> RegimeEvalResult:
    """Evaluate `model` on `window` slice of `bars`. Returns MAE."""
    torch.manual_seed(seed)
    np.random.seed(seed)
    model.eval()

    sl = bars.loc[window.start:window.end]
    if len(sl) < WINDOW_BARS + 2:
        return RegimeEvalResult(
            regime_name=window.name, mae=float("inf"), samples=0,
            passes_acceptance=False, per_output_mae=(0.0, 0.0, 0.0, 0.0),
        )

    abs_errs: list[np.ndarray] = []  # list of (4,) arrays
    with torch.no_grad():
        for i in range(WINDOW_BARS, len(sl) - 1):
            window_bars = sl.iloc[i - WINDOW_BARS:i]
            x = normalize_window(window_bars).unsqueeze(0)  # (1, 256, 5)
            pred_pct = model(x).squeeze(0).numpy()  # (4,) — predicted % change
            last_close = float(sl.iloc[i]["close"])
            actual_next = sl.iloc[i + 1]
            actual_pct = np.array([
                actual_next["open"]  / last_close - 1.0,
                actual_next["high"]  / last_close - 1.0,
                actual_next["low"]   / last_close - 1.0,
                actual_next["close"] / last_close - 1.0,
            ])
            abs_errs.append(np.abs(pred_pct - actual_pct))

    arr = np.array(abs_errs)  # (samples, 4)
    per_output_mae = tuple(float(arr[:, j].mean()) for j in range(4))
    mae = float(arr.mean())
    return RegimeEvalResult(
        regime_name=window.name,
        mae=mae,
        samples=len(abs_errs),
        passes_acceptance=mae <= threshold,
        per_output_mae=per_output_mae,
    )
```

- [ ] **Step 4: Tests pass + commit** — note the test imports `app.ml.normalize` which doesn't exist yet; this task **requires Task C2 (normalize) to be implemented first**, OR we add a temporary stub. Plan resolves by reordering: do **B2 → C2 → B3 → C1 → ...** with normalize built first. Implementer should do **C2 here** before completing B2.

Reordering note: complete **C2 first**, return to finish B2's tests.

```bash
# After C2 lands:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_ml_eval.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/app/ml/eval.py backend/tests/unit/test_ml_eval.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): app/ml/eval.py — evaluate_on_regime + deterministic seed + acceptance gate"
```
Expected: `3 passed`.

---

### Task B3: `app/ml/baseline.py` — random walk MAE baseline — TDD

**Files:**
- Create: `worktrees/sp-1/backend/app/ml/baseline.py`
- Create: `worktrees/sp-1/backend/tests/unit/test_ml_baseline.py`

**Design note:** the baseline is a `nn.Module` that returns `torch.zeros(batch, 4)` — predicting "next bar = current bar (zero % change)". This is actually a strong baseline for crypto 1h: most bars don't move much. The model must beat it by a meaningful margin.

- [ ] **Step 1: Failing test**

```python
import numpy as np
import pandas as pd
import pytest
import torch

from app.ml.baseline import RandomWalkBaseline
from app.ml.eval import evaluate_on_regime
from app.ml.regimes import REGIME_WINDOWS


def _synthetic_bars(n: int, start_ts: pd.Timestamp, sigma: float = 0.005) -> pd.DataFrame:
    rng = np.random.default_rng(123)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, sigma, size=n)))
    df = pd.DataFrame({
        "open": closes, "high": closes * 1.001, "low": closes * 0.999,
        "close": closes, "volume": np.full(n, 1000.0),
    })
    df.index = pd.date_range(start=start_ts, periods=n, freq="1h", tz="UTC")
    return df


def test_random_walk_baseline_predicts_zero_pct_change() -> None:
    model = RandomWalkBaseline()
    x = torch.zeros(8, 256, 5)
    out = model(x)
    assert out.shape == (8, 4)
    assert torch.all(out == 0)


def test_baseline_mae_in_low_vol_under_one_percent() -> None:
    """On a low-vol synthetic series, the baseline's MAE should be < 1%."""
    model = RandomWalkBaseline()
    bars = _synthetic_bars(512, pd.Timestamp("2024-04-01", tz="UTC"), sigma=0.003)
    window = next(w for w in REGIME_WINDOWS if w.name == "low_volatility")
    result = evaluate_on_regime(model=model, bars=bars, window=window, seed=42)
    assert result.mae < 0.01


def test_baseline_mae_in_high_vol_higher() -> None:
    """In high vol, baseline MAE should be > low vol."""
    bars_low = _synthetic_bars(512, pd.Timestamp("2024-04-01", tz="UTC"), sigma=0.003)
    bars_high = _synthetic_bars(512, pd.Timestamp("2020-03-01", tz="UTC"), sigma=0.02)
    window_low = next(w for w in REGIME_WINDOWS if w.name == "low_volatility")
    window_high = next(w for w in REGIME_WINDOWS if w.name == "high_volatility")

    model = RandomWalkBaseline()
    r_low = evaluate_on_regime(model=model, bars=bars_low, window=window_low, seed=42)
    r_high = evaluate_on_regime(model=model, bars=bars_high, window=window_high, seed=42)
    assert r_high.mae > r_low.mae
```

- [ ] **Step 2: Implement**

```python
"""Random-walk baseline: predicts next-bar OHLC = current close (0% change).

Used as a sanity floor — the trained Conv-LSTM must beat this MAE on every
regime window or the model is not adding value over a "do nothing" predictor.
"""
import torch
from torch import nn


class RandomWalkBaseline(nn.Module):
    """Predicts zero % change on all 4 outputs."""

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # noqa: D401
        # x shape: (batch, 256, 5); output (batch, 4)
        batch = x.shape[0]
        return torch.zeros(batch, 4, dtype=x.dtype, device=x.device)
```

- [ ] **Step 3: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_ml_baseline.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/app/ml/baseline.py backend/tests/unit/test_ml_baseline.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): app/ml/baseline.py — random-walk MAE floor for sanity check"
```
Expected: `3 passed`.

---

### Task B4: Run baseline against 5 regime windows; record `eval_baseline.json`

**Files:**
- Create: `worktrees/sp-1/backend/tools/ml/run_baseline_eval.py`
- Create: `worktrees/sp-1/backend/data/eval_baseline.json` (committed artifact)

**Design note:** this is a **manual ops step**, not pytest. Operator runs the script once after the bulk export (Task A5) lands historical OHLCV. The output JSON is committed so future Conv-LSTM eval runs can show the side-by-side "baseline MAE vs model MAE" comparison.

- [ ] **Step 1: Implement**

```python
"""Run the RandomWalkBaseline against all 5 regime windows and emit JSON.

Manual run — not part of pytest. Output is committed to backend/data/eval_baseline.json
so the Conv-LSTM eval (Task E2) can produce a direct MAE comparison.

Usage:
    docker compose exec backend python -m tools.ml.run_baseline_eval \
        --bulk-export /app/data/ml-bulk-export \
        --out /app/data/eval_baseline.json
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from app.ml.baseline import RandomWalkBaseline
from app.ml.eval import evaluate_on_regime
from app.ml.regimes import REGIME_WINDOWS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bulk-export", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--symbol", default="BTC_USDT")
    args = parser.parse_args()

    parquet = args.bulk_export / f"ohlcv_1h_{args.symbol}.parquet"
    df = pq.read_table(parquet).to_pandas().set_index("ts").sort_index()

    model = RandomWalkBaseline()
    results = []
    for window in REGIME_WINDOWS:
        r = evaluate_on_regime(model=model, bars=df, window=window, seed=42)
        results.append(asdict(r))
        print(f"{window.name:20s}  mae={r.mae:.5f}  n={r.samples}  passes={r.passes_acceptance}")

    args.out.write_text(json.dumps({
        "model": "random_walk_baseline_v1",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke check + run (after operator has bulk export available)**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python -c "from tools.ml.run_baseline_eval import main; print('importable')"
# Operator runs (manual, requires bulk export):
# docker compose exec backend python -m tools.ml.run_baseline_eval --bulk-export /app/data/ml-bulk-export --out /app/data/eval_baseline.json
```
Expected: `importable`. Real run produces 5 baseline-MAE rows (typical: 0.003-0.015 depending on regime).

- [ ] **Step 3: Commit (script + placeholder result)**

If operator hasn't run yet, commit the script alone with an empty placeholder JSON:

```bash
echo '{"model": "random_walk_baseline_v1", "results": [], "note": "regenerate via tools/ml/run_baseline_eval.py after bulk export"}' > backend/data/eval_baseline.json
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/tools/ml/run_baseline_eval.py backend/data/eval_baseline.json
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): tools/ml/run_baseline_eval.py + eval_baseline.json placeholder"
```

If operator has run, commit the actual JSON instead.

---

### Task B5: Eval harness determinism integration test

**Files:**
- Create: `worktrees/sp-1/backend/tests/integration/test_ml_eval_determinism.py`

This was partly covered by B2's unit tests, but the integration variant runs against the *whole* `RandomWalkBaseline + REGIME_WINDOWS` matrix to guarantee no randomness leaks across all 5 windows simultaneously.

- [ ] **Step 1: Test**

```python
import numpy as np
import pandas as pd
import pytest

from app.ml.baseline import RandomWalkBaseline
from app.ml.eval import evaluate_on_regime
from app.ml.regimes import REGIME_WINDOWS


def _synthetic_bars_for(window) -> pd.DataFrame:
    n_hours = int((window.end - window.start).total_seconds() / 3600) + 1
    rng = np.random.default_rng(seed=hash(window.name) & 0xFFFF)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.005, size=n_hours)))
    df = pd.DataFrame({
        "open": closes, "high": closes * 1.001, "low": closes * 0.999,
        "close": closes, "volume": np.full(n_hours, 1000.0),
    })
    df.index = pd.date_range(start=window.start, periods=n_hours, freq="1h", tz="UTC")
    return df


def test_all_five_windows_evaluate_deterministically() -> None:
    model = RandomWalkBaseline()
    first_run: dict[str, float] = {}
    for window in REGIME_WINDOWS:
        bars = _synthetic_bars_for(window)
        r = evaluate_on_regime(model=model, bars=bars, window=window, seed=42)
        first_run[window.name] = r.mae

    second_run: dict[str, float] = {}
    for window in REGIME_WINDOWS:
        bars = _synthetic_bars_for(window)
        r = evaluate_on_regime(model=model, bars=bars, window=window, seed=42)
        second_run[window.name] = r.mae

    assert first_run == second_run
    assert len(first_run) == 5
```

- [ ] **Step 2: Run + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/integration/test_ml_eval_determinism.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/tests/integration/test_ml_eval_determinism.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "test(sp-1): integration — eval harness deterministic across all 5 regimes"
```
Expected: `1 passed`.

---

## Phase C — Conv-LSTM v0

### Task C1: `app/ml/model.py` — `ConvLSTMPredictor` class — TDD

**Files:**
- Create: `worktrees/sp-1/backend/app/ml/model.py`
- Create: `worktrees/sp-1/backend/tests/unit/test_ml_model.py`

- [ ] **Step 1: Failing test**

```python
import torch

from app.ml.model import ConvLSTMPredictor


def test_forward_pass_shape_single() -> None:
    model = ConvLSTMPredictor()
    x = torch.randn(1, 256, 5)
    out = model(x)
    assert out.shape == (1, 4)


def test_forward_pass_shape_batch() -> None:
    model = ConvLSTMPredictor()
    x = torch.randn(8, 256, 5)
    out = model(x)
    assert out.shape == (8, 4)


def test_param_count_around_500k() -> None:
    """Spec §3.1 says ~480K params; allow +/- 20%."""
    model = ConvLSTMPredictor()
    n = sum(p.numel() for p in model.parameters())
    assert 380_000 < n < 600_000, f"got {n} params"


def test_dropout_changes_output_in_train_mode() -> None:
    """In train() mode, dropout should produce different outputs across passes."""
    torch.manual_seed(0)
    model = ConvLSTMPredictor()
    model.train()
    x = torch.randn(1, 256, 5)
    a = model(x)
    b = model(x)
    # Outputs should differ (almost surely) when dropout is active
    assert not torch.allclose(a, b)


def test_dropout_off_in_eval_mode() -> None:
    """In eval() mode, repeated calls should be identical."""
    model = ConvLSTMPredictor()
    model.eval()
    x = torch.randn(1, 256, 5)
    with torch.no_grad():
        a = model(x)
        b = model(x)
    assert torch.allclose(a, b)
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement**

```python
"""Conv-LSTM 1-step predictor.

Spec §3.1 — 1D conv stack to extract local OHLCV patterns, then LSTM over the
conv-feature sequence, dense head produces 4 outputs (% change for next bar's
OHLC). Total ~480K params. Trains on Colab T4; runs CPU on Oracle.
"""
import torch
import torch.nn.functional as F
from torch import nn


class ConvLSTMPredictor(nn.Module):
    """Input: (batch, 256, 5) tensor of normalized OHLCV.
    Output: (batch, 4) tensor of predicted next-bar O/H/L/C as % change.
    """

    def __init__(
        self,
        *,
        n_features: int = 5,
        conv1_channels: int = 32,
        conv2_channels: int = 64,
        lstm_hidden: int = 128,
        lstm_layers: int = 2,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self.conv1 = nn.Conv1d(
            in_channels=n_features, out_channels=conv1_channels,
            kernel_size=3, padding=1,
        )
        self.conv2 = nn.Conv1d(
            in_channels=conv1_channels, out_channels=conv2_channels,
            kernel_size=3, padding=1,
        )
        self.conv_drop = nn.Dropout(dropout)
        self.lstm = nn.LSTM(
            input_size=conv2_channels,
            hidden_size=lstm_hidden,
            num_layers=lstm_layers,
            dropout=dropout if lstm_layers > 1 else 0.0,
            batch_first=True,
        )
        self.fc = nn.Linear(lstm_hidden, 4)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, 256, 5)
        x = x.transpose(1, 2)            # (batch, 5, 256) for Conv1d
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = self.conv_drop(x)
        x = x.transpose(1, 2)            # (batch, 256, 64) for LSTM
        h, _ = self.lstm(x)
        return self.fc(h[:, -1, :])      # (batch, 4)
```

- [ ] **Step 4: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_ml_model.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/app/ml/model.py backend/tests/unit/test_ml_model.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): app/ml/model.py — ConvLSTMPredictor (~480K params, MC dropout)"
```
Expected: `5 passed`.

---

### Task C2: `app/ml/normalize.py` — `normalize_window()` + `denormalize_prediction()` — TDD

**Files:**
- Create: `worktrees/sp-1/backend/app/ml/normalize.py`
- Create: `worktrees/sp-1/backend/tests/unit/test_ml_normalize.py`

**Design note:** `normalize_window` returns a `torch.Tensor` of shape (256, 5). OHLC are normalized as % change from `bars["close"].iloc[-1]`; volume is z-scored over the window. `denormalize_prediction` takes the (4,) tensor of % changes back to absolute prices given last_close. Round-trip property: for any last_close > 0 and any (4,) pct vector, `denormalize(pct, last_close)["close"] == last_close * (1 + pct[3])` exactly.

This task should be done **before B2** (B2's tests import this module).

- [ ] **Step 1: Failing test**

```python
import numpy as np
import pandas as pd
import pytest
import torch

from app.ml.normalize import normalize_window, denormalize_prediction


def _bars(n: int = 256, base: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    closes = base * np.exp(np.cumsum(rng.normal(0, 0.005, size=n)))
    return pd.DataFrame({
        "open":  closes * 0.999,
        "high":  closes * 1.002,
        "low":   closes * 0.997,
        "close": closes,
        "volume": rng.uniform(1e3, 1e4, size=n),
    })


def test_normalize_window_shape_and_dtype() -> None:
    bars = _bars()
    out = normalize_window(bars)
    assert out.shape == (256, 5)
    assert out.dtype == torch.float32


def test_normalize_window_last_close_is_zero_pct() -> None:
    bars = _bars()
    out = normalize_window(bars)
    # Last row's close column (idx 3) should be ~0 (last close vs last close)
    assert abs(out[-1, 3].item()) < 1e-6


def test_normalize_window_volume_z_scored() -> None:
    bars = _bars()
    out = normalize_window(bars)
    vol_col = out[:, 4].numpy()
    assert abs(vol_col.mean()) < 1e-3
    assert abs(vol_col.std() - 1.0) < 1e-2


def test_denormalize_round_trip_close() -> None:
    last_close = 80000.0
    pred_pct = torch.tensor([-0.001, 0.005, -0.003, 0.002], dtype=torch.float32)
    out = denormalize_prediction(pred_pct, last_close=last_close)
    assert abs(out["close"] - last_close * (1.0 + 0.002)) < 1e-3
    assert abs(out["open"]  - last_close * (1.0 - 0.001)) < 1e-3
    assert abs(out["high"]  - last_close * (1.0 + 0.005)) < 1e-3
    assert abs(out["low"]   - last_close * (1.0 - 0.003)) < 1e-3


def test_denormalize_returns_dict_with_four_keys() -> None:
    out = denormalize_prediction(torch.zeros(4), last_close=100.0)
    assert set(out.keys()) == {"open", "high", "low", "close"}
    for k in out:
        assert out[k] == 100.0


def test_normalize_volume_zero_std_handled() -> None:
    """If volume is constant, std=0 — must not produce NaN/inf."""
    bars = _bars()
    bars["volume"] = 1000.0
    out = normalize_window(bars)
    assert torch.isfinite(out).all()
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement**

```python
"""Window normalization for Conv-LSTM input.

Spec §3.2 — OHLC are converted to % change from the window's LAST close (not
mean), so the last row's close column is exactly 0. Volume is z-scored over
the window. This makes the model price-scale-invariant — the same model
generalizes from $30k BTC to $80k BTC with no retraining needed.

Edge: a window where volume std is 0 (constant volume) would produce inf/nan
under naive z-scoring. We fall back to zero in that case.
"""
import numpy as np
import pandas as pd
import torch


def normalize_window(bars: pd.DataFrame) -> torch.Tensor:
    """bars: DataFrame with columns [open, high, low, close, volume], 256 rows.

    Returns: tensor shape (256, 5) — OHLC as % change from bars['close'].iloc[-1],
    volume z-scored over the window.
    """
    if len(bars) < 2:
        raise ValueError(f"normalize_window needs >= 2 rows, got {len(bars)}")

    last_close = float(bars["close"].iloc[-1])
    if last_close <= 0:
        raise ValueError(f"last close must be positive, got {last_close}")

    pct = bars[["open", "high", "low", "close"]].astype(float).div(last_close).sub(1.0).values

    vol = bars["volume"].astype(float).values
    vol_mean = vol.mean()
    vol_std = vol.std()
    if vol_std < 1e-12:
        vol_z = np.zeros_like(vol)
    else:
        vol_z = (vol - vol_mean) / vol_std

    arr = np.column_stack([pct, vol_z]).astype(np.float32)
    return torch.from_numpy(arr)


def denormalize_prediction(pred_pct: torch.Tensor, *, last_close: float) -> dict[str, float]:
    """pred_pct: (4,) tensor of % changes for next bar's OHLC.

    Returns: {open, high, low, close} in absolute price units.
    """
    if pred_pct.shape != (4,):
        raise ValueError(f"expected shape (4,), got {pred_pct.shape}")
    return {
        "open":  last_close * (1.0 + float(pred_pct[0].item())),
        "high":  last_close * (1.0 + float(pred_pct[1].item())),
        "low":   last_close * (1.0 + float(pred_pct[2].item())),
        "close": last_close * (1.0 + float(pred_pct[3].item())),
    }
```

- [ ] **Step 4: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_ml_normalize.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/app/ml/normalize.py backend/tests/unit/test_ml_normalize.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): app/ml/normalize.py — normalize_window + denormalize_prediction (round-trip)"
```
Expected: `6 passed`.

---

### Task C3: `app/ml/inference.py` — `predict_ghost_candle()` with MC dropout — TDD

**Files:**
- Create: `worktrees/sp-1/backend/app/ml/inference.py`
- Create: `worktrees/sp-1/backend/tests/unit/test_ml_inference.py`

**Design note:** the function takes (model, bars_df, last_close) and returns a typed `GhostCandle` dataclass with eight float fields (the seven prices + uncertainty). Internally it calls `model.train()` to activate dropout, runs 32 forward passes inside `torch.no_grad()` (MC sampling — gradients off, dropout on), computes mean/p5/p95/std on samples. Returns the prices via `denormalize_prediction(mean, last_close)` plus the uncertainty extracted from `samples.std().mean()`. p5_low and p95_high specifically use the **low column's p5** and **high column's p95** (not OHLC's collective p5/p95) — gives the tightest visually-honest uncertainty wicks.

- [ ] **Step 1: Failing test**

```python
import numpy as np
import pandas as pd
import pytest
import torch
from torch import nn

from app.ml.inference import predict_ghost_candle, GhostCandle


def _bars(n: int = 256, last_close: float = 80000.0) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    closes = last_close * np.exp(np.cumsum(rng.normal(0, 0.005, size=n)))
    closes[-1] = last_close
    return pd.DataFrame({
        "open":  closes * 0.999,
        "high":  closes * 1.002,
        "low":   closes * 0.997,
        "close": closes,
        "volume": rng.uniform(1e3, 1e4, size=n),
    })


class _StubModel(nn.Module):
    """Always predicts (0.001, 0.003, -0.002, 0.0015) plus dropout noise."""
    def __init__(self) -> None:
        super().__init__()
        self.drop = nn.Dropout(0.5)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Apply dropout to a constant tensor so MC sampling produces variance.
        base = torch.tensor([0.001, 0.003, -0.002, 0.0015]).expand(x.shape[0], 4)
        # Multiply by dropout-affected ones to get stochastic outputs in train() mode.
        noise = self.drop(torch.ones(x.shape[0], 4))
        return base * noise


def test_predict_ghost_candle_returns_dataclass() -> None:
    model = _StubModel()
    bars = _bars()
    ghost = predict_ghost_candle(model=model, bars=bars, last_close=80000.0, n_samples=32)
    assert isinstance(ghost, GhostCandle)
    for field in ("open", "high", "low", "close", "p5_low", "p95_high", "uncertainty"):
        assert hasattr(ghost, field)
        assert isinstance(getattr(ghost, field), float)


def test_ghost_candle_prices_around_last_close() -> None:
    """Stub predicts +0.1% open, +0.3% high, -0.2% low, +0.15% close → all near 80k."""
    model = _StubModel()
    bars = _bars()
    ghost = predict_ghost_candle(model=model, bars=bars, last_close=80000.0, n_samples=64)
    assert 79000 < ghost.open < 81000
    assert 79000 < ghost.high < 81000
    assert 79000 < ghost.low < 81000
    assert 79000 < ghost.close < 81000
    # p5_low <= low, p95_high >= high (uncertainty band brackets the central candle)
    assert ghost.p5_low <= ghost.low + 1e-3
    assert ghost.p95_high >= ghost.high - 1e-3


def test_uncertainty_is_nonnegative() -> None:
    model = _StubModel()
    bars = _bars()
    ghost = predict_ghost_candle(model=model, bars=bars, last_close=80000.0, n_samples=32)
    assert ghost.uncertainty >= 0.0


def test_zero_dropout_model_has_zero_uncertainty() -> None:
    """A model with no dropout should yield uncertainty ≈ 0 (deterministic)."""
    class _Det(nn.Module):
        def forward(self, x: torch.Tensor) -> torch.Tensor:
            return torch.tensor([[0.001, 0.003, -0.002, 0.0015]]).expand(x.shape[0], 4)

    bars = _bars()
    ghost = predict_ghost_candle(model=_Det(), bars=bars, last_close=80000.0, n_samples=16)
    assert ghost.uncertainty < 1e-5


def test_predict_uses_last_256_bars_only() -> None:
    """Caller may pass more than 256 bars; function must slice to last 256."""
    model = _StubModel()
    bars = _bars(n=500)
    ghost = predict_ghost_candle(model=model, bars=bars, last_close=80000.0, n_samples=8)
    assert isinstance(ghost, GhostCandle)
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement**

```python
"""MC-dropout ghost candle prediction.

Spec §3.3 — model.train() at inference time so dropout fires; run 32 forward
passes per closed candle, compute mean (point estimate), p5/p95 (uncertainty
wicks), and std (overall confidence indicator).

Returned dataclass `GhostCandle` matches the persistence schema (the eight
columns added in migration 0007) and the WS payload `ghost` field.

p5_low: 5th percentile of the LOW column → conservative downside bound.
p95_high: 95th percentile of the HIGH column → conservative upside bound.
uncertainty: scalar = mean of stds across all 4 outputs.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import torch
from torch import nn

from app.ml.normalize import denormalize_prediction, normalize_window


WINDOW_BARS: int = 256


@dataclass(frozen=True)
class GhostCandle:
    open: float
    high: float
    low: float
    close: float
    p5_low: float
    p95_high: float
    uncertainty: float


def predict_ghost_candle(
    *,
    model: nn.Module,
    bars: pd.DataFrame,
    last_close: float,
    n_samples: int = 32,
) -> GhostCandle:
    """Predict the next-bar ghost candle with MC-dropout uncertainty band."""
    if len(bars) < WINDOW_BARS:
        raise ValueError(f"need >= {WINDOW_BARS} bars, got {len(bars)}")
    if last_close <= 0:
        raise ValueError(f"last_close must be positive, got {last_close}")

    window = bars.iloc[-WINDOW_BARS:]
    x = normalize_window(window).unsqueeze(0)  # (1, 256, 5)

    # Spec §3.3: model.train() activates dropout; we still use no_grad to skip autograd.
    model.train()
    with torch.no_grad():
        samples = torch.stack([model(x) for _ in range(n_samples)])  # (n, 1, 4)
    samples = samples.squeeze(1)  # (n, 4)

    mean = samples.mean(dim=0)              # (4,)
    p5_per_col = samples.quantile(0.05, dim=0)   # (4,)
    p95_per_col = samples.quantile(0.95, dim=0)  # (4,)
    std_scalar = float(samples.std(dim=0).mean().item())

    central = denormalize_prediction(mean, last_close=last_close)
    p5_low = last_close * (1.0 + float(p5_per_col[2].item()))   # low column p5
    p95_high = last_close * (1.0 + float(p95_per_col[1].item())) # high column p95

    return GhostCandle(
        open=central["open"],
        high=central["high"],
        low=central["low"],
        close=central["close"],
        p5_low=p5_low,
        p95_high=p95_high,
        uncertainty=std_scalar,
    )
```

- [ ] **Step 4: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_ml_inference.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/app/ml/inference.py backend/tests/unit/test_ml_inference.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): app/ml/inference.py — predict_ghost_candle (MC dropout, p5/p95 wicks)"
```
Expected: `5 passed`.

---

### Task C4: `tools/ml/colab/train_conv_lstm.ipynb` — Colab training notebook (committed, not auto-tested)

**Files:**
- Create: `worktrees/sp-1/backend/tools/ml/colab/train_conv_lstm.ipynb`

**Design note:** Notebook is committed to repo and opened directly in Colab via the GitHub integration (`https://colab.research.google.com/github/<user>/v5_Trade_bot/blob/sp-1/main/backend/tools/ml/colab/train_conv_lstm.ipynb`). Not pytest-tested — operator runs interactively. The implementer's job here is to construct a notebook with the cells listed below; the actual training run happens in C5.

**Cell-by-cell structure** (each is a Jupyter cell, mostly Python with one markdown header at top):

- [ ] **Cell 1 (markdown):** Title + instructions: "SP-1 Conv-LSTM training. Runs on Colab T4 free tier in ~3 hours. Output: checkpoint v0.x.x uploaded to B2."

- [ ] **Cell 2 (Python):** Install deps not in default Colab image:
  ```python
  !pip install -q duckdb==1.1.3 boto3==1.35.0 pyarrow==17.0.0
  ```

- [ ] **Cell 3 (Python):** Mount Drive + clone repo (so the notebook can `import` from `app.ml.*`):
  ```python
  from google.colab import drive
  drive.mount("/content/drive")
  %cd /content
  !git clone -b sp-1/main https://github.com/<user>/v5_Trade_bot.git
  %cd v5_Trade_bot/backend
  !pip install -e .
  ```

- [ ] **Cell 4 (Python):** Pull bulk Parquet from B2:
  ```python
  import boto3, os
  os.environ["AWS_ACCESS_KEY_ID"] = "<set in Colab secrets>"
  os.environ["AWS_SECRET_ACCESS_KEY"] = "<set in Colab secrets>"
  s3 = boto3.client("s3", endpoint_url="https://s3.us-west-002.backblazeb2.com")
  s3.download_file("trading-radar-backups", "ml-bulk-export/manifest.json", "/content/data/manifest.json")
  # Loop through all OHLCV parquets in the manifest and download.
  ```

- [ ] **Cell 5 (Python):** Build training pairs with duckdb sliding window:
  ```python
  import duckdb
  con = duckdb.connect()
  df = con.execute("""
      SELECT * FROM parquet_scan('/content/data/ohlcv_1h_*.parquet')
      WHERE ts >= '2017-08-01' AND ts < '2024-01-01'
      ORDER BY symbol, ts
  """).df()
  # Build (X[256x5], y[4]) tuples per symbol via pandas rolling window.
  ```

- [ ] **Cell 6 (Python):** PyTorch DataLoader with batch_size=64, shuffle within train split.

- [ ] **Cell 7 (Python):** Instantiate `ConvLSTMPredictor`, optimizer (Adam), cosine LR scheduler, MAE loss.

- [ ] **Cell 8 (Python):** Training loop — 30 epochs, early stop patience=5, log per-epoch val MAE, save best to `/content/ckpt.pt`.

- [ ] **Cell 9 (Python):** Eval against the 5 regime windows using `app.ml.eval.evaluate_on_regime`. Print results table.

- [ ] **Cell 10 (Python):** Compute sha256 of `ckpt.pt`. Upload to B2:
  ```python
  s3.upload_file("/content/ckpt.pt", "trading-radar-models", "conv_lstm_v0.1.0.pt")
  ```

- [ ] **Cell 11 (Python):** Register checkpoint via admin API (Phase F endpoint):
  ```python
  import requests
  resp = requests.post(
      "https://trading-radar.example.com/api/v1/admin/ml-checkpoints",
      json={
          "model_name": "conv_lstm_predictor",
          "version": "0.1.0",
          "checkpoint_uri": "b2://trading-radar-models/conv_lstm_v0.1.0.pt",
          "sha256": "<computed>",
          "trained_at": "2026-05-15T12:00:00Z",
          "train_data_window": "2017-08 to 2023-12",
          "eval_results": { "bull_breakout": 0.013, ... },
          "notes": "v0.1.0 first training run"
      },
      headers={"cf-access-jwt-assertion": "<dev token>"},
  )
  print(resp.json())
  ```

- [ ] **Step 1: Save notebook + smoke check**

```bash
ls -la backend/tools/ml/colab/train_conv_lstm.ipynb
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python -c "import json; json.loads(open('tools/ml/colab/train_conv_lstm.ipynb').read()); print('notebook is valid JSON')"
```
Expected: notebook file present + valid JSON.

- [ ] **Step 2: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/tools/ml/colab/train_conv_lstm.ipynb
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): tools/ml/colab/train_conv_lstm.ipynb — initial training notebook"
```

---

### Task C5: First training run on Colab → checkpoint v0.1.0 (manual)

**Files:** none (operator runs the committed notebook on Colab)

**Workflow:**
1. Operator opens the notebook in Colab, sets B2 credentials in Colab secrets.
2. Runs cells 1–9. Expected wall-clock: ~3 hours on free T4.
3. **Expected outcome (per spec §12):** at least 1 of 5 regime windows passes, but likely NOT all 5 — that's the iteration target for Phase E.
4. Operator captures the eval JSON output (the table from cell 9) and pastes it into a comment on the SP-1 PR.
5. Skip cell 10 + 11 if no window passes — the checkpoint is not eligible for activation. If at least one passes (likely just for sanity), proceed to upload but DO NOT activate.

**Verification:**
- ckpt.pt file exists on Colab (`!ls -la /content/ckpt.pt` shows ~2 MB)
- eval table prints 5 rows with MAE values
- If uploaded: `aws s3 ls s3://trading-radar-models/` shows `conv_lstm_v0.1.0.pt`

**No commit** — this is purely a Colab operation. The eval results get committed in the next checkpoint iteration (Phase E).

---

### Task C6: Upload v0.1.0 checkpoint to B2; register via admin API (manual)

**Files:** none (uses cells 10 + 11 of train_conv_lstm.ipynb)

**Workflow:**
1. After C5 yields a ckpt.pt, run cell 10 (B2 upload).
2. Cell 11 calls `POST /api/v1/admin/ml-checkpoints` with `is_active=False` (default). The endpoint is added in Task F2 — until F2 ships, registration must be done by `psql` directly:
   ```sql
   INSERT INTO ml_checkpoints (model_name, version, checkpoint_uri, sha256,
       trained_at, train_data_window, eval_results, notes)
   VALUES ('conv_lstm_predictor', '0.1.0',
       'b2://trading-radar-models/conv_lstm_v0.1.0.pt',
       '<sha>', NOW(), '2017-08 to 2023-12',
       '{"bull_breakout": 0.013, "bear_crash": 0.025, ...}'::jsonb,
       'v0.1.0 first run');
   ```
3. **Do NOT set `is_active=true` yet** — the spec acceptance bar (≤1.5% MAE on ALL 5 windows) is unlikely to be met by v0.1.0. Activation happens in Phase E after iteration.

**Verification:**
- `SELECT version, eval_results FROM ml_checkpoints;` shows the new row.

---

## Phase D — Ghost candle UI

### Task D1: Backend — extend `app/ws/live_prediction.py` to call `predict_ghost_candle` — TDD

**Files:**
- Modify: `worktrees/sp-1/backend/app/ws/live_prediction.py`
- Modify: `worktrees/sp-1/backend/app/core/execution/persistence.py`
- Create: `worktrees/sp-1/backend/tests/unit/test_ws_live_prediction_ghost.py`

**Design note:** the worker reads `_active_model` from `app.ml.checkpoints` (loaded at startup, see Task D6). If it's None, ghost columns stay NULL. The persist payload gets seven new keys (`ghost_open` through `ghost_uncertainty`) plus `model_checkpoint_id`. Audit chain: the canonical hash payload ALREADY includes everything in the dict passed to `insert_with_chain`, so adding new keys automatically extends the hash. SP-1 commitment: pre-existing rows still verify (tested in A2).

- [ ] **Step 1: Failing test**

```python
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.execution.persistence import persist_prediction
from app.ml.inference import GhostCandle


@pytest.mark.asyncio
async def test_persist_prediction_accepts_ghost_columns() -> None:
    """persist_prediction must accept the eight new ghost-related keys."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, ts TEXT NOT NULL, "
            "final_score REAL NOT NULL, direction TEXT NOT NULL, "
            "confidence REAL NOT NULL, inputs_hash TEXT NOT NULL, "
            "model_version TEXT NOT NULL, cold_start INTEGER NOT NULL, "
            "layer_scores TEXT NOT NULL, "
            "ghost_open REAL, ghost_high REAL, ghost_low REAL, ghost_close REAL, "
            "ghost_p5_low REAL, ghost_p95_high REAL, ghost_uncertainty REAL, "
            "model_checkpoint_id INTEGER, "
            "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL)"
        ))

    payload = {
        "user_id": 1, "symbol": "BTC/USDT", "timeframe": "1h",
        "ts": datetime.now(timezone.utc).isoformat(),
        "final_score": 0.5, "direction": "LONG", "confidence": 0.7,
        "inputs_hash": "h", "model_version": "sp-0", "cold_start": 0,
        "layer_scores": json.dumps({}),
        "ghost_open": 80100.0, "ghost_high": 80300.0, "ghost_low": 79900.0,
        "ghost_close": 80200.0, "ghost_p5_low": 79500.0, "ghost_p95_high": 80800.0,
        "ghost_uncertainty": 0.005, "model_checkpoint_id": 42,
    }
    async with AsyncSession(engine) as session:
        row_hash = await persist_prediction(session, payload)
        await session.commit()
        row = (await session.execute(sa.text("SELECT ghost_close, model_checkpoint_id FROM predictions"))).one()
    assert row.ghost_close == 80200.0
    assert row.model_checkpoint_id == 42
    assert isinstance(row_hash, str) and len(row_hash) == 64


@pytest.mark.asyncio
async def test_persist_prediction_omitting_ghost_keys_still_works() -> None:
    """Ghost keys are optional — when no model loaded, persistence works without them."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, ts TEXT NOT NULL, "
            "final_score REAL NOT NULL, direction TEXT NOT NULL, "
            "confidence REAL NOT NULL, inputs_hash TEXT NOT NULL, "
            "model_version TEXT NOT NULL, cold_start INTEGER NOT NULL, "
            "layer_scores TEXT NOT NULL, "
            "ghost_open REAL, ghost_high REAL, ghost_low REAL, ghost_close REAL, "
            "ghost_p5_low REAL, ghost_p95_high REAL, ghost_uncertainty REAL, "
            "model_checkpoint_id INTEGER, "
            "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL)"
        ))

    payload = {
        "user_id": 1, "symbol": "BTC/USDT", "timeframe": "1h",
        "ts": datetime.now(timezone.utc).isoformat(),
        "final_score": 0.5, "direction": "LONG", "confidence": 0.7,
        "inputs_hash": "h", "model_version": "sp-0", "cold_start": 0,
        "layer_scores": json.dumps({}),
    }
    async with AsyncSession(engine) as session:
        await persist_prediction(session, payload)
        await session.commit()
        row = (await session.execute(sa.text("SELECT ghost_close FROM predictions"))).one()
    assert row.ghost_close is None
```

- [ ] **Step 2: Run — fail.** Expected: persist_prediction works fine for the no-ghost case (it already passes the dict through), but the test_with_ghost case may surface SQLite mismatches if column listing is wrong. Fix as needed.

- [ ] **Step 3: persist_prediction is unchanged in signature**

It already accepts arbitrary payload dicts and forwards to `insert_with_chain`, which builds the INSERT column list from the dict's keys. Therefore adding ghost keys "just works" at the persistence layer — the test verifies this. The only change required is in `live_prediction.py` (next step).

- [ ] **Step 4: Modify `live_prediction.py`**

```python
# Add at top:
from app.ml.checkpoints import get_active_model_and_checkpoint
from app.ml.inference import predict_ghost_candle

# Then inside run_live_prediction, after build_prediction(...) succeeds:
        try:
            pred = build_prediction(symbol=symbol_pair, timeframe=timeframe, bars=bars)
        except Exception as e:  # noqa: BLE001
            log.warning("build_prediction failed: %s", e)
            continue

        # SP-1: ghost candle prediction (additive, never blocks)
        ghost_payload: dict = {}
        active = get_active_model_and_checkpoint()
        if active is not None and len(bars) >= 256:
            model, checkpoint = active
            try:
                ghost = predict_ghost_candle(
                    model=model, bars=bars, last_close=float(bars["close"].iloc[-1]),
                )
                ghost_payload = {
                    "ghost_open": ghost.open,
                    "ghost_high": ghost.high,
                    "ghost_low": ghost.low,
                    "ghost_close": ghost.close,
                    "ghost_p5_low": ghost.p5_low,
                    "ghost_p95_high": ghost.p95_high,
                    "ghost_uncertainty": ghost.uncertainty,
                    "model_checkpoint_id": checkpoint.id,
                }
            except Exception as e:  # noqa: BLE001
                log.warning("predict_ghost_candle failed: %s; persisting without ghost", e)

        try:
            async with session_factory() as session:
                await persist_prediction(session, {
                    "user_id": BOOTSTRAP_ADMIN_USER_ID,
                    "symbol": pred.symbol,
                    "timeframe": pred.timeframe,
                    "ts": pred.ts.isoformat(),
                    "layer_scores": json.dumps({
                        k: (v.model_dump() if v else None)
                        for k, v in pred.layer_scores.items()
                    }),
                    "final_score": pred.final.score,
                    "direction": pred.final.direction,
                    "confidence": pred.final.confidence,
                    "inputs_hash": pred.inputs_hash,
                    "model_version": "sp-0",
                    "cold_start": pred.cold_start,
                    **ghost_payload,
                })
                await session.commit()
        except Exception as e:  # noqa: BLE001
            log.error("persist_prediction failed; suppressing publish: %s", e)
            continue

        # Extend WS payload with ghost.
        payload = pred.model_dump(mode="json")
        if ghost_payload:
            payload["ghost"] = {
                "open": ghost_payload["ghost_open"],
                "high": ghost_payload["ghost_high"],
                "low": ghost_payload["ghost_low"],
                "close": ghost_payload["ghost_close"],
                "p5_low": ghost_payload["ghost_p5_low"],
                "p95_high": ghost_payload["ghost_p95_high"],
                "uncertainty": ghost_payload["ghost_uncertainty"],
            }
        else:
            payload["ghost"] = None
        await manager.publish(
            channel="live_prediction",
            key={"symbol": symbol_pair, "timeframe": timeframe},
            payload=payload,
        )
```

(`get_active_model_and_checkpoint` is implemented in Task D6 below.)

- [ ] **Step 5: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_ws_live_prediction_ghost.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/app/ws/live_prediction.py backend/tests/unit/test_ws_live_prediction_ghost.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): live_prediction worker calls predict_ghost_candle, persists ghost columns + extends WS payload"
```
Expected: `2 passed`.

---

### Task D2: Extend `LivePredictionOut` schema with `ghost: GhostOut | None`

**Files:**
- Modify: `worktrees/sp-1/backend/app/api/schemas.py`
- Create: `worktrees/sp-1/backend/tests/unit/test_schemas_ghost.py`

- [ ] **Step 1: Failing test**

```python
import pytest

from app.api.schemas import GhostOut, LivePredictionOut


def test_ghost_out_required_fields() -> None:
    g = GhostOut(open=1, high=2, low=0.5, close=1.5,
                 p5_low=0.1, p95_high=2.5, uncertainty=0.05)
    assert g.uncertainty == 0.05


def test_live_prediction_out_ghost_optional() -> None:
    """LivePredictionOut.ghost must be Optional (None when no model loaded)."""
    fields = LivePredictionOut.model_fields
    assert "ghost" in fields
    # Default must be None so existing payloads without ghost still parse.
    assert fields["ghost"].default is None
```

- [ ] **Step 2: Implement** — append to `app/api/schemas.py`:

```python
# --- SP-1: Ghost candle ---


class GhostOut(BaseModel):
    """Predicted next-bar ghost candle + uncertainty band."""
    open: float
    high: float
    low: float
    close: float
    p5_low: float
    p95_high: float
    uncertainty: float = Field(ge=0.0)
```

And modify `LivePredictionOut` (line 49–60) to add the field:

```python
class LivePredictionOut(BaseModel):
    # ... existing fields ...
    signal_markers: SignalMarkersOut | None = None
    ghost: GhostOut | None = None  # SP-1: ghost candle prediction (None when no checkpoint loaded)
```

- [ ] **Step 3: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_schemas_ghost.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/app/api/schemas.py backend/tests/unit/test_schemas_ghost.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): GhostOut schema + LivePredictionOut.ghost optional field"
```
Expected: `2 passed`.

---

### Task D3: Frontend — extend `LivePrediction` interface with `ghost` field

**Files:**
- Modify: `worktrees/sp-1/frontend/src/lib/api.ts`
- Create: `worktrees/sp-1/frontend/tests/unit/api-ghost.test.ts`

- [ ] **Step 1: Failing test**

```typescript
import { describe, it, expect } from "vitest";
import type { LivePrediction, GhostCandle } from "@/lib/api";

describe("GhostCandle / LivePrediction.ghost typing", () => {
  it("LivePrediction has optional ghost field", () => {
    const p: LivePrediction = {
      symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-05T12:00:00Z",
      price: 80000,
      final: { score: 0.5, direction: "LONG", confidence: 0.7, contributing_layers: [1] },
      layer_scores: {},
      trade_setup: { direction: "LONG", entry: null, stop_loss: null, take_profit: null, risk_reward: null },
      momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
      cold_start: false,
      inputs_hash: "h",
      ghost: null,  // explicit null
    };
    expect(p.ghost).toBeNull();
  });

  it("ghost can be a GhostCandle object", () => {
    const ghost: GhostCandle = {
      open: 80100, high: 80300, low: 79900, close: 80200,
      p5_low: 79500, p95_high: 80800, uncertainty: 0.005,
    };
    expect(ghost.uncertainty).toBeGreaterThanOrEqual(0);
  });
});
```

- [ ] **Step 2: Implement** — append to `frontend/src/lib/api.ts` near the `LivePrediction` interface:

```typescript
export interface GhostCandle {
  open: number;
  high: number;
  low: number;
  close: number;
  p5_low: number;
  p95_high: number;
  uncertainty: number;       // [0, ∞), lower = more confident
}

// Extend existing interface (modify in-place):
export interface LivePrediction {
  // ... existing fields ...
  signal_markers?: SignalMarkers | null;
  ghost?: GhostCandle | null;   // SP-1
}
```

- [ ] **Step 3: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T frontend npm test -- --run api-ghost
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add frontend/src/lib/api.ts frontend/tests/unit/api-ghost.test.ts
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): frontend api.ts adds GhostCandle interface + LivePrediction.ghost"
```
Expected: 2 passed.

---

### Task D4: Frontend — extend `TVChart` to render ghost candle with uncertainty band — TDD

**Files:**
- Modify: `worktrees/sp-1/frontend/src/components/chart/TVChart.tsx`
- Create: `worktrees/sp-1/frontend/tests/unit/TVChart.ghost.test.tsx`

**What file:** `TVChart.tsx`. **What behavior:**
- Add a new optional `ghost: GhostCandle | null` prop.
- If `ghost` is non-null and `liveTs` is set, append a candle to the right of the latest bar at `liveTs + 1 * timeframe-in-seconds`.
- Use a SECOND candlestick series with `priceLineVisible: false`, color tinted with 50% opacity (TR_GREEN / TR_RED with `aa` alpha suffix in hex, e.g., `#00d68faa`).
- Use a third price line at `ghost.p5_low` and `ghost.p95_high` rendered as thin dashed lines (uncertainty wicks).
- When `ghost` becomes null, remove the second series + price lines.

**What test fixture:**
- Mock `lightweight-charts` (already mocked in existing TVChart tests if any; otherwise inline-mock `createChart` to a stub that records `addCandlestickSeries` and `setData` calls).
- Render `<TVChart symbol="BTC/USDT" timeframe="1h" liveTs="2026-05-05T12:00:00Z" ghost={...}/>`.
- Assert that `addCandlestickSeries` was called twice (real + ghost) when ghost is present.
- Assert that ghost-series `setData` was called with a single bar at `12:00 + 1h = 13:00`.
- Assert that two `createPriceLine` calls match `p5_low` and `p95_high`.
- Re-render with `ghost={null}` and assert the ghost series was removed.

The agent applies the SP-0.7 frontend TDD pattern (vitest + @testing-library/react with mocked lightweight-charts).

- [ ] **Step 1: Failing test (skeleton)**

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import type { GhostCandle } from "@/lib/api";

const mockSeries = {
  setData: vi.fn(),
  update: vi.fn(),
  setMarkers: vi.fn(),
  createPriceLine: vi.fn(() => ({})),
  removePriceLine: vi.fn(),
  applyOptions: vi.fn(),
};
const mockSeries2 = { ...mockSeries, setData: vi.fn(), createPriceLine: vi.fn(() => ({})) };
const mockChart = {
  addCandlestickSeries: vi.fn()
    .mockReturnValueOnce(mockSeries)    // real series (first call)
    .mockReturnValueOnce(mockSeries2),  // ghost series (second call)
  remove: vi.fn(),
  removeSeries: vi.fn(),
};

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => mockChart),
  LineStyle: { Dashed: 1, Solid: 0 },
}));

vi.mock("@/hooks/useChartHistory", () => ({
  useChartHistory: () => [],
}));

import { TVChart } from "@/components/chart/TVChart";

describe("TVChart ghost rendering", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not render ghost series when ghost is null", () => {
    render(<TVChart symbol="BTC/USDT" timeframe="1h" liveTs="2026-05-05T12:00:00Z" livePrice={80000} ghost={null} />);
    // Real candle series + nothing else
    expect(mockChart.addCandlestickSeries).toHaveBeenCalledTimes(1);
  });

  it("renders ghost candle one timeframe ahead of liveTs", () => {
    const ghost: GhostCandle = {
      open: 80100, high: 80300, low: 79900, close: 80200,
      p5_low: 79500, p95_high: 80800, uncertainty: 0.005,
    };
    render(<TVChart symbol="BTC/USDT" timeframe="1h" liveTs="2026-05-05T12:00:00Z" livePrice={80000} ghost={ghost} />);
    expect(mockChart.addCandlestickSeries).toHaveBeenCalledTimes(2);
    // Ghost series setData called with a single bar at 13:00 UTC
    expect(mockSeries2.setData).toHaveBeenCalled();
    const args = mockSeries2.setData.mock.calls[0][0];
    expect(args).toHaveLength(1);
    expect(args[0].open).toBe(80100);
    expect(args[0].close).toBe(80200);
    // Two price lines for p5_low / p95_high
    expect(mockSeries2.createPriceLine).toHaveBeenCalledTimes(2);
  });
});
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement** — modify `TVChart.tsx`:

```typescript
// Add to imports
import type { GhostCandle, SignalMarkers } from "@/lib/api";

interface Props {
  symbol: string;
  timeframe: string;
  livePrice?: number;
  liveTs?: string;
  signalMarkers?: SignalMarkers | null;
  ghost?: GhostCandle | null;            // SP-1
}

// Color constants (add):
const TR_GREEN_GHOST = "#00d68f80";  // 50% alpha
const TR_RED_GHOST   = "#ff3d7180";

// Helper to compute timeframe-seconds:
function tfToSeconds(tf: string): number {
  const m = tf.match(/^(\d+)([mhd])$/);
  if (!m) return 3600;
  const [, n, unit] = m;
  const mult = unit === "m" ? 60 : unit === "h" ? 3600 : 86400;
  return parseInt(n, 10) * mult;
}

// Inside component, add state for ghost series + price lines:
const ghostSeriesRef = useRef<ISeriesApi<"Candlestick", Time> | null>(null);
const ghostPriceLinesRef = useRef<IPriceLine[]>([]);

// Add a useEffect for ghost rendering (after the signalMarkers effect):
useEffect(() => {
  const chart = chartRef.current;
  if (!chart) return;

  const clearGhost = () => {
    if (ghostSeriesRef.current) {
      for (const line of ghostPriceLinesRef.current) {
        ghostSeriesRef.current.removePriceLine(line);
      }
      ghostPriceLinesRef.current = [];
      chart.removeSeries(ghostSeriesRef.current);
      ghostSeriesRef.current = null;
    }
  };

  if (!ghost || !liveTs) {
    clearGhost();
    return;
  }

  clearGhost();

  const isUp = ghost.close >= ghost.open;
  const series = chart.addCandlestickSeries({
    upColor: TR_GREEN_GHOST,
    downColor: TR_RED_GHOST,
    borderUpColor: TR_GREEN_GHOST,
    borderDownColor: TR_RED_GHOST,
    wickUpColor: TR_GREEN_GHOST,
    wickDownColor: TR_RED_GHOST,
    priceLineVisible: false,
  });
  ghostSeriesRef.current = series;

  const ghostTs = (Math.floor(new Date(liveTs).getTime() / 1000) + tfToSeconds(timeframe)) as Time;
  series.setData([{
    time: ghostTs,
    open: ghost.open, high: ghost.high, low: ghost.low, close: ghost.close,
  }]);

  ghostPriceLinesRef.current.push(series.createPriceLine({
    price: ghost.p5_low,
    color: TR_RED_GHOST,
    lineWidth: 1,
    lineStyle: LineStyle.Dashed,
    axisLabelVisible: false,
    title: "P5",
  }));
  ghostPriceLinesRef.current.push(series.createPriceLine({
    price: ghost.p95_high,
    color: TR_GREEN_GHOST,
    lineWidth: 1,
    lineStyle: LineStyle.Dashed,
    axisLabelVisible: false,
    title: "P95",
  }));

  return clearGhost;
}, [ghost, liveTs, timeframe]);

// Update destructured props at top:
export function TVChart({ symbol, timeframe, livePrice, liveTs, signalMarkers, ghost }: Props) {
```

- [ ] **Step 4: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T frontend npm test -- --run TVChart.ghost
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add frontend/src/components/chart/TVChart.tsx frontend/tests/unit/TVChart.ghost.test.tsx
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): TVChart renders ghost candle one timeframe ahead with p5/p95 wicks"
```
Expected: 2 passed.

---

### Task D5: Frontend — `MasterBiasScore` panel ghost preview row + Tab1 wiring

**Files:**
- Modify: `worktrees/sp-1/frontend/src/tabs/Tab1LivePrediction/panels/MasterBiasScore.tsx`
- Modify: `worktrees/sp-1/frontend/src/tabs/Tab1LivePrediction/index.tsx`
- Create: `worktrees/sp-1/frontend/tests/unit/MasterBiasScore.ghost.test.tsx`

**What file:** `MasterBiasScore.tsx`. **What behavior:**
- When `data.ghost` is non-null AND `data.price` is set, render a second sub-block under the bias bar:
  ```
  GHOST CANDLE                ±$<uncertainty_usd> / <uncertainty_pct>%
  Open  $<open> → Close  $<close>  (<+/-pct>%)
  ```
- `uncertainty_usd = ghost.uncertainty * data.price` (uncertainty is in fractional units; multiply by price for dollar width)
- `uncertainty_pct = ghost.uncertainty * 100`
- `delta_pct = ((ghost.close - ghost.open) / ghost.open) * 100`
- Color the change: green if positive, red if negative.
- When `data.ghost` is null, render nothing extra (just the existing bias bar).

**What test fixture:**
- Mock `LivePrediction` data with and without ghost.
- Assert ghost row text appears with formatted price + uncertainty.

**Tab1 wiring:** `Tab1LivePrediction/index.tsx` already passes `data?.signal_markers` to TVChart. Add `ghost={data?.ghost ?? null}` to the same prop list.

- [ ] **Step 1: Failing test**

```typescript
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MasterBiasScore } from "@/tabs/Tab1LivePrediction/panels/MasterBiasScore";
import type { LivePrediction } from "@/lib/api";

const baseData: LivePrediction = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-05T12:00:00Z",
  price: 80000,
  final: { score: 0.5, direction: "LONG", confidence: 0.7, contributing_layers: [1] },
  layer_scores: {},
  trade_setup: { direction: "LONG", entry: null, stop_loss: null, take_profit: null, risk_reward: null },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false,
  inputs_hash: "h",
  ghost: null,
};

describe("MasterBiasScore ghost row", () => {
  it("does not render ghost block when ghost is null", () => {
    render(<MasterBiasScore data={baseData} />);
    expect(screen.queryByText(/ghost candle/i)).toBeNull();
  });

  it("renders ghost block when ghost present", () => {
    const data: LivePrediction = {
      ...baseData,
      ghost: { open: 80100, high: 80300, low: 79900, close: 80200,
               p5_low: 79500, p95_high: 80800, uncertainty: 0.005 },
    };
    render(<MasterBiasScore data={data} />);
    expect(screen.getByText(/ghost candle/i)).toBeInTheDocument();
    expect(screen.getByText(/80100/)).toBeInTheDocument();
    expect(screen.getByText(/80200/)).toBeInTheDocument();
    // 0.005 * 80000 = $400 uncertainty (allow rounding)
    expect(screen.getByText(/\$4\d\d/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Implement**

```tsx
// MasterBiasScore.tsx — append to existing component

export function MasterBiasScore({ data }: Props) {
  if (!data) return <Panel title="Master Bias Score">—</Panel>;
  const score = data.final.score;
  const pct = ((score + 1) / 2) * 100;
  const label = labelFor(score);
  const trackColor =
    label === "BULL" ? "bg-green" : label === "BEAR" ? "bg-red" : "bg-purple";

  const ghost = data.ghost;
  const ghostUsd = ghost ? Math.round(ghost.uncertainty * data.price) : 0;
  const ghostPct = ghost ? (ghost.uncertainty * 100).toFixed(2) : "0";
  const deltaPct = ghost ? (((ghost.close - ghost.open) / ghost.open) * 100).toFixed(2) : "0";
  const deltaUp = ghost ? ghost.close >= ghost.open : false;

  return (
    <Panel title="Master Bias Score">
      <div className="flex justify-between mb-1">
        <span>{(score * 100).toFixed(1)}</span>
        <span className="text-text-secondary">{label}</span>
      </div>
      <div className="h-1 bg-bg-elevated rounded">
        <div
          className={`h-1 rounded ${trackColor}`}
          style={{ width: `${pct}%` }}
          aria-label={`bias ${score.toFixed(2)}`}
        />
      </div>
      {ghost && (
        <div className="mt-3 pt-3 border-t border-border">
          <div className="flex justify-between text-xs uppercase text-text-secondary tracking-wide">
            <span>Ghost Candle</span>
            <span>±${ghostUsd} / {ghostPct}%</span>
          </div>
          <div className="text-xs mt-1 font-mono">
            Open ${ghost.open.toFixed(2)} → Close ${ghost.close.toFixed(2)}{" "}
            <span className={deltaUp ? "text-green" : "text-red"}>
              ({deltaUp ? "+" : ""}{deltaPct}%)
            </span>
          </div>
        </div>
      )}
    </Panel>
  );
}
```

`Tab1LivePrediction/index.tsx` — modify line 39 to pass ghost:
```tsx
<TVChart
  symbol={symbol}
  timeframe={timeframe}
  {...(data?.price != null ? { livePrice: data.price } : {})}
  {...(data?.ts != null ? { liveTs: data.ts } : {})}
  signalMarkers={data?.signal_markers ?? null}
  ghost={data?.ghost ?? null}
/>
```

- [ ] **Step 3: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T frontend npm test -- --run MasterBiasScore.ghost
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add frontend/src/tabs/Tab1LivePrediction/panels/MasterBiasScore.tsx frontend/src/tabs/Tab1LivePrediction/index.tsx frontend/tests/unit/MasterBiasScore.ghost.test.tsx
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): MasterBiasScore renders ghost candle preview row + Tab1 passes ghost to TVChart"
```
Expected: 2 passed.

---

### Task D6: Backend — `app/ml/checkpoints.py` — load active checkpoint at startup — TDD

**Files:**
- Create: `worktrees/sp-1/backend/app/ml/checkpoints.py`
- Modify: `worktrees/sp-1/backend/app/main.py`
- Create: `worktrees/sp-1/backend/tests/unit/test_ml_checkpoints.py`

**Design note:** loaded once at lifespan; held in module-scope `_active_model: ConvLSTMPredictor | None` and `_active_checkpoint: dict | None` (lightweight dict with id/version, NOT the SQLAlchemy ORM row). `get_active_model_and_checkpoint()` returns the tuple or None. Download from B2 happens via boto3 with sha256 verification; in dev with no B2 credentials, this gracefully returns None and logs a warning. The model and checkpoint dict are mutated atomically (set both or neither) to avoid race conditions in the worker.

This task is technically a prerequisite to D1, but pragmatically the worker check (`if active is not None`) means D1's tests pass even without D6 (they just exercise the no-ghost path). Implementer should land D6 immediately after D5 to enable the actual ghost path.

- [ ] **Step 1: Failing test**

```python
import hashlib
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
import torch
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.ml.checkpoints import (
    ActiveCheckpoint,
    get_active_model_and_checkpoint,
    load_active_checkpoint,
    set_active,
    clear_active,
)
from app.ml.model import ConvLSTMPredictor


def test_module_state_starts_empty() -> None:
    clear_active()
    assert get_active_model_and_checkpoint() is None


def test_set_and_get_active() -> None:
    model = ConvLSTMPredictor()
    ck = ActiveCheckpoint(id=42, model_name="conv_lstm_predictor", version="0.1.0",
                          sha256="abc", checkpoint_uri="b2://x")
    set_active(model, ck)
    got = get_active_model_and_checkpoint()
    assert got is not None
    m, c = got
    assert c.id == 42
    assert c.version == "0.1.0"
    clear_active()


@pytest.mark.asyncio
async def test_load_active_returns_none_when_no_active_row() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE ml_checkpoints ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, model_name TEXT, version TEXT, "
            "checkpoint_uri TEXT, sha256 TEXT, trained_at TEXT, "
            "train_data_window TEXT, eval_results TEXT, "
            "is_active INTEGER NOT NULL DEFAULT 0, activated_at TEXT, "
            "deactivated_at TEXT, notes TEXT)"
        ))
    async with AsyncSession(engine) as session:
        result = await load_active_checkpoint(session, model_name="conv_lstm_predictor")
    assert result is None


@pytest.mark.asyncio
async def test_load_active_downloads_and_loads_state_dict(tmp_path) -> None:
    """When an active row exists, download the checkpoint, sha-verify, load it."""
    # Build a real ConvLSTMPredictor checkpoint to use as the "downloaded" file.
    ck_path = tmp_path / "ckpt.pt"
    m = ConvLSTMPredictor()
    torch.save(m.state_dict(), ck_path)
    sha = hashlib.sha256(ck_path.read_bytes()).hexdigest()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE ml_checkpoints ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, model_name TEXT, version TEXT, "
            "checkpoint_uri TEXT, sha256 TEXT, trained_at TEXT, "
            "train_data_window TEXT, eval_results TEXT, "
            "is_active INTEGER NOT NULL DEFAULT 0, activated_at TEXT, "
            "deactivated_at TEXT, notes TEXT)"
        ))
        await conn.execute(sa.text(
            "INSERT INTO ml_checkpoints (model_name, version, checkpoint_uri, sha256, "
            "trained_at, train_data_window, eval_results, is_active) "
            "VALUES ('conv_lstm_predictor', '0.1.0', :uri, :sha, "
            "'2026-05-05T00:00:00', '2017-2023', '{}', 1)"
        ), {"uri": f"file://{ck_path}", "sha": sha})

    async with AsyncSession(engine) as session:
        loaded = await load_active_checkpoint(session, model_name="conv_lstm_predictor")
    assert loaded is not None
    model, ck = loaded
    assert isinstance(model, ConvLSTMPredictor)
    assert ck.version == "0.1.0"
    assert ck.sha256 == sha
```

- [ ] **Step 2: Implement**

```python
"""Active ML checkpoint loader + module-scope state.

Spec §6.1 — at backend startup, look up the row in `ml_checkpoints` where
`is_active=true AND model_name='conv_lstm_predictor'`. Download the file
from `checkpoint_uri` (B2 bucket URI or local file:// for testing), verify
sha256 matches, then load the state_dict into a fresh ConvLSTMPredictor.

If no active row exists, log a warning and leave `_active_model=None` —
ghost candle prediction is gracefully skipped by the worker.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import sqlalchemy as sa
import torch
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.model import ConvLSTMPredictor

log = logging.getLogger(__name__)

_active_model: ConvLSTMPredictor | None = None
_active_checkpoint: "ActiveCheckpoint | None" = None


@dataclass(frozen=True)
class ActiveCheckpoint:
    id: int
    model_name: str
    version: str
    sha256: str
    checkpoint_uri: str


def set_active(model: ConvLSTMPredictor, checkpoint: ActiveCheckpoint) -> None:
    global _active_model, _active_checkpoint
    _active_model = model
    _active_checkpoint = checkpoint


def clear_active() -> None:
    global _active_model, _active_checkpoint
    _active_model = None
    _active_checkpoint = None


def get_active_model_and_checkpoint() -> tuple[ConvLSTMPredictor, ActiveCheckpoint] | None:
    if _active_model is None or _active_checkpoint is None:
        return None
    return _active_model, _active_checkpoint


async def _download_to_local(uri: str, *, dest: Path) -> Path:
    """Download `uri` to `dest`. Supports b2://, s3://, file://."""
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return Path(parsed.path)
    if parsed.scheme in ("b2", "s3"):
        import boto3  # local import — heavy dep
        endpoint = os.environ.get(
            "B2_S3_ENDPOINT", "https://s3.us-west-002.backblazeb2.com",
        )
        s3 = boto3.client("s3", endpoint_url=endpoint)
        bucket, key = parsed.netloc, parsed.path.lstrip("/")
        s3.download_file(bucket, key, str(dest))
        return dest
    raise ValueError(f"unsupported checkpoint URI scheme: {parsed.scheme}")


async def load_active_checkpoint(
    session: AsyncSession, *, model_name: str = "conv_lstm_predictor",
) -> tuple[ConvLSTMPredictor, ActiveCheckpoint] | None:
    """Look up active checkpoint, download, verify, load. Returns None if absent."""
    row = (await session.execute(
        sa.text(
            "SELECT id, model_name, version, checkpoint_uri, sha256 "
            "FROM ml_checkpoints WHERE model_name = :n AND is_active = 1 "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"n": model_name},
    )).first()
    if row is None:
        log.warning("no active ML checkpoint for %s; ghost candles disabled", model_name)
        return None

    ck = ActiveCheckpoint(
        id=row.id, model_name=row.model_name, version=row.version,
        sha256=row.sha256, checkpoint_uri=row.checkpoint_uri,
    )

    cache_dir = Path(os.environ.get("ML_CACHE_DIR", "/app/data/ml-cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    local_path = cache_dir / f"{ck.model_name}_{ck.version}.pt"

    if not local_path.exists():
        try:
            await _download_to_local(ck.checkpoint_uri, dest=local_path)
        except Exception as e:  # noqa: BLE001
            log.error("checkpoint download failed: %s; ghost candles disabled", e)
            return None

    actual_sha = hashlib.sha256(local_path.read_bytes()).hexdigest()
    if actual_sha != ck.sha256:
        log.error(
            "sha256 mismatch for %s: expected %s, got %s; ghost candles disabled",
            ck.checkpoint_uri, ck.sha256, actual_sha,
        )
        return None

    model = ConvLSTMPredictor()
    state = torch.load(local_path, map_location="cpu")
    model.load_state_dict(state)
    model.eval()
    set_active(model, ck)
    log.info("loaded active checkpoint %s v%s", ck.model_name, ck.version)
    return model, ck
```

- [ ] **Step 3: Wire into `app/main.py` lifespan**

```python
# Inside the existing lifespan() context manager, after engine setup:
from app.ml.checkpoints import load_active_checkpoint

async with session_factory() as session:
    await load_active_checkpoint(session)
```

- [ ] **Step 4: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_ml_checkpoints.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/app/ml/checkpoints.py backend/app/main.py backend/tests/unit/test_ml_checkpoints.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): app/ml/checkpoints.py — active checkpoint loader + lifespan wiring"
```
Expected: `4 passed`.

---

## Phase E — Iterate to acceptance

### Task E1: `tools/ml/colab/sweep.ipynb` — hyperparameter sweep notebook (committed)

**Files:**
- Create: `worktrees/sp-1/backend/tools/ml/colab/sweep.ipynb`

**Cell structure** (manual / Colab — not pytest-tested):

- [ ] **Cell 1 (markdown):** Title: "SP-1 Conv-LSTM hyperparameter sweep — try 6-8 architecture variants, pick the one that maximizes worst-regime MAE."

- [ ] **Cell 2 (Python):** Define a grid:
  ```python
  GRID = [
      {"conv1_channels": 32, "conv2_channels": 64,  "lstm_hidden": 128, "lstm_layers": 2, "dropout": 0.2},  # baseline
      {"conv1_channels": 32, "conv2_channels": 64,  "lstm_hidden": 128, "lstm_layers": 2, "dropout": 0.3},  # more dropout
      {"conv1_channels": 64, "conv2_channels": 128, "lstm_hidden": 128, "lstm_layers": 2, "dropout": 0.2},  # wider conv
      {"conv1_channels": 32, "conv2_channels": 64,  "lstm_hidden": 256, "lstm_layers": 2, "dropout": 0.2},  # wider LSTM
      {"conv1_channels": 32, "conv2_channels": 64,  "lstm_hidden": 128, "lstm_layers": 3, "dropout": 0.2},  # deeper LSTM
      {"conv1_channels": 16, "conv2_channels": 32,  "lstm_hidden": 64,  "lstm_layers": 1, "dropout": 0.1},  # tiny
      {"conv1_channels": 64, "conv2_channels": 128, "lstm_hidden": 256, "lstm_layers": 2, "dropout": 0.3},  # large
  ]
  ```
- [ ] **Cell 3 (Python):** Loop: for each config, train 15 epochs (shorter than full run), eval on all 5 regimes, record `(config, worst_regime_mae)`.
- [ ] **Cell 4 (Python):** Print sorted table by worst-regime MAE ascending. Pick top 1-2 for full re-train (Task E2).
- [ ] **Cell 5 (Python):** Save sweep results to `/content/sweep_results.json` and download.

- [ ] **Step 1: Save notebook + smoke check**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python -c "import json; json.loads(open('tools/ml/colab/sweep.ipynb').read()); print('valid')"
```

- [ ] **Step 2: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/tools/ml/colab/sweep.ipynb
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): tools/ml/colab/sweep.ipynb — 7-variant hyperparameter sweep notebook"
```

---

### Task E2: Best variant retrain + full eval (manual)

**Workflow:**
1. Operator opens `train_conv_lstm.ipynb` again, modifies the model instantiation cell to use the best config from the sweep.
2. Re-run all training cells (~3 hours).
3. Run eval cells; compare against `eval_baseline.json` from B4. The new model should beat baseline on at least 3 regimes.
4. If all 5 regimes pass `≤ 1.5%` MAE: proceed to E3.
5. If 3-4 of 5 pass: iterate the architecture again (return to E1 with different grid). The spec is firm: ALL 5 must pass for activation.
6. If 0-2 of 5 pass: the model fundamentally doesn't work — flag for spec discussion (per spec §10 risk row "Conv-LSTM fundamentally doesn't work for crypto" → SP-1.5 pivot).

**No commit** — Colab work. Operator pastes eval table into PR comments.

---

### Task E3: Register checkpoint v0.2.0+, mark active (manual)

**Workflow:**
1. After E2 produces a passing checkpoint, follow C6 to upload + register, but this time set `is_active=true` via the admin endpoint (Task F2 must have shipped):
   ```
   PATCH /api/v1/admin/ml-checkpoints/<new_id>  {"is_active": true}
   ```
2. The unique partial index (migration 0007) ensures the previous active checkpoint is automatically conflict-deactivated; the admin endpoint must atomic-deactivate the old one inside a transaction (see F2).
3. Restart backend so `lifespan` picks up the new active row:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.dev.yml restart backend
   ```
4. Verify in logs:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.dev.yml logs backend | grep "loaded active checkpoint"
   ```
   Expected: `loaded active checkpoint conv_lstm_predictor v0.2.0`

**No commit** — DB state change.

---

### Task E4: Deploy verification — ghost candles producing on every closed candle

**Workflow:**
1. After E3, watch logs for ghost candle persistence:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f backend | grep -E "(persist_prediction|ghost)"
   ```
2. After one BTC/USDT 1h candle closes (~1 hour wait), verify persistence:
   ```sql
   SELECT id, ts, ghost_close, ghost_uncertainty, model_checkpoint_id
   FROM predictions
   WHERE user_id = 1
   ORDER BY id DESC
   LIMIT 3;
   ```
   Expected: latest row has non-NULL `ghost_*` columns and `model_checkpoint_id = <new_id>`.
3. Open Tab 1 in browser. Expected: ghost candle visible to right of latest BTC bar with dimmed colors and p5/p95 wicks.
4. Verify WS payload from browser DevTools network tab: filter to WebSocket frames, confirm `ghost: {open, high, low, close, ...}` present in `live_prediction` channel messages.

**No commit** — verification only. Document outcome in PR description.

---

## Phase F — Pattern stats + admin endpoints + ship

### Task F1: `app/ml/patterns.py` — nightly pattern_stats updater — TDD

**Files:**
- Create: `worktrees/sp-1/backend/app/ml/patterns.py`
- Create: `worktrees/sp-1/backend/tests/unit/test_ml_patterns.py`

**Design note:** join `predictions` × `shadow_trades` on `signal_id` (existing column on shadow_trades). For each prediction's `layer_scores` JSON, extract any `pattern_id` markers (placeholder: no patterns are emitted yet by SP-0 layers, so for SP-1 ship the join is empty — but the function must work end-to-end). When a real pattern detector lands in SP-2, this function picks up the patterns automatically.

A "win" = shadow_trade with `exit_reason = 'TAKE_PROFIT'`. Pattern accuracy = wins / total_with_pattern. Cold-start (`n_samples < 50`) returns prior `0.5` (handled by the GENERATED column).

- [ ] **Step 1: Failing test**

```python
import json
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.ml.patterns import update_pattern_stats


@pytest.mark.asyncio
async def test_update_pattern_stats_creates_rows_from_join() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # Schema enough for the join — predictions + shadow_trades + pattern_stats
        await conn.execute(sa.text(
            "CREATE TABLE predictions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "symbol TEXT, timeframe TEXT, ts TEXT, layer_scores TEXT, "
            "inputs_hash TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "symbol TEXT, timeframe TEXT, signal_id TEXT, exit_reason TEXT, "
            "closed_at TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE pattern_stats ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "pattern_id TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
            "n_samples INTEGER NOT NULL DEFAULT 0, "
            "n_correct INTEGER NOT NULL DEFAULT 0, "
            "last_updated TEXT NOT NULL DEFAULT (datetime('now')), "
            "UNIQUE (pattern_id, symbol, timeframe))"
        ))

    # Seed: 3 predictions with pattern 'hammer' on BTC, 2 of them with TP shadow_trades.
    async with AsyncSession(engine) as session:
        for i, won in enumerate([True, True, False]):
            await session.execute(sa.text(
                "INSERT INTO predictions (user_id, symbol, timeframe, ts, layer_scores, inputs_hash) "
                "VALUES (1, 'BTC/USDT', '1h', :ts, :ls, :h)"
            ), {
                "ts": f"2026-05-{i+1:02d}T12:00:00",
                "ls": json.dumps({"L2": {"patterns": ["hammer"]}}),
                "h": f"hash{i}",
            })
            await session.execute(sa.text(
                "INSERT INTO shadow_trades (user_id, symbol, timeframe, signal_id, exit_reason, closed_at) "
                "VALUES (1, 'BTC/USDT', '1h', :sig, :reason, :ca)"
            ), {
                "sig": f"hash{i}",
                "reason": "TAKE_PROFIT" if won else "STOP_LOSS",
                "ca": f"2026-05-{i+1:02d}T13:00:00",
            })
        await session.commit()

        n_updated = await update_pattern_stats(session)
        await session.commit()

        rows = (await session.execute(sa.text(
            "SELECT pattern_id, n_samples, n_correct FROM pattern_stats"
        ))).all()

    assert n_updated >= 1
    by_pat = {r.pattern_id: r for r in rows}
    assert "hammer" in by_pat
    assert by_pat["hammer"].n_samples == 3
    assert by_pat["hammer"].n_correct == 2


@pytest.mark.asyncio
async def test_update_with_no_predictions_is_noop() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions (id INTEGER PRIMARY KEY, layer_scores TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades (id INTEGER PRIMARY KEY, signal_id TEXT, exit_reason TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE pattern_stats ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, pattern_id TEXT, symbol TEXT, "
            "timeframe TEXT, n_samples INTEGER DEFAULT 0, n_correct INTEGER DEFAULT 0, "
            "last_updated TEXT, UNIQUE(pattern_id, symbol, timeframe))"
        ))

    async with AsyncSession(engine) as session:
        n = await update_pattern_stats(session)
        await session.commit()
    assert n == 0
```

- [ ] **Step 2: Implement**

```python
"""Nightly pattern_stats updater.

Spec §4.3 — joins predictions × shadow_trades on signal_id (== inputs_hash),
extracts pattern_ids from each prediction's layer_scores.L2.patterns array,
counts wins (exit_reason='TAKE_PROFIT') vs losses, upserts pattern_stats rows.

Cold-start gating (n_samples < 50) is handled by the GENERATED accuracy
column in the table definition (returns 0.5 prior).
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


def _extract_patterns(layer_scores_json: str) -> list[str]:
    """Pull pattern IDs from layer_scores. SP-0 has no patterns yet → returns []."""
    if not layer_scores_json:
        return []
    try:
        data = json.loads(layer_scores_json)
    except json.JSONDecodeError:
        return []
    l2 = data.get("L2") or {}
    if isinstance(l2, dict):
        pats = l2.get("patterns") or []
        return [p for p in pats if isinstance(p, str)]
    return []


async def update_pattern_stats(session: AsyncSession) -> int:
    """Recompute pattern_stats from current predictions × shadow_trades join.

    Returns the number of pattern_stats rows upserted.
    """
    sql = sa.text(
        "SELECT p.symbol, p.timeframe, p.layer_scores, t.exit_reason "
        "FROM predictions p "
        "INNER JOIN shadow_trades t ON t.signal_id = p.inputs_hash "
        "WHERE t.exit_reason IS NOT NULL"
    )
    result = await session.execute(sql)
    rows = result.all()

    # (pattern_id, symbol, timeframe) -> [n_samples, n_correct]
    counts: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        for pat in _extract_patterns(r.layer_scores):
            key = (pat, r.symbol or "GLOBAL", r.timeframe or "1h")
            counts[key][0] += 1
            if r.exit_reason == "TAKE_PROFIT":
                counts[key][1] += 1

    n_upserted = 0
    now = datetime.now(timezone.utc).isoformat()
    for (pat, sym, tf), (n_total, n_win) in counts.items():
        # SQLite-friendly upsert (Postgres accepts the same syntax).
        await session.execute(sa.text(
            "INSERT INTO pattern_stats (pattern_id, symbol, timeframe, n_samples, n_correct, last_updated) "
            "VALUES (:p, :s, :tf, :n, :w, :u) "
            "ON CONFLICT (pattern_id, symbol, timeframe) DO UPDATE SET "
            "n_samples = excluded.n_samples, "
            "n_correct = excluded.n_correct, "
            "last_updated = excluded.last_updated"
        ), {"p": pat, "s": sym, "tf": tf, "n": n_total, "w": n_win, "u": now})
        n_upserted += 1

    if n_upserted:
        log.info("pattern_stats: upserted %d rows", n_upserted)
    return n_upserted
```

- [ ] **Step 3: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_ml_patterns.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/app/ml/patterns.py backend/tests/unit/test_ml_patterns.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): app/ml/patterns.py — nightly pattern_stats updater (predictions × shadow_trades join)"
```
Expected: `2 passed`.

---

### Task F2: `app/api/routes/admin_ml.py` — REST endpoints — TDD

**Files:**
- Create: `worktrees/sp-1/backend/app/api/routes/admin_ml.py`
- Create: `worktrees/sp-1/backend/tests/integration/test_api_admin_ml_checkpoints.py`
- Modify: `worktrees/sp-1/backend/app/api/schemas.py` (add `MlCheckpointOut`, `MlCheckpointCreateIn`, `MlCheckpointPatchIn`)
- Modify: `worktrees/sp-1/backend/app/main.py` (register router)

**Endpoints:**
- `POST /api/v1/admin/ml-checkpoints` — register (caller passes model_name, version, checkpoint_uri, sha256, trained_at, train_data_window, eval_results, notes; never auto-activates)
- `GET /api/v1/admin/ml-checkpoints` — list all, newest-first
- `PATCH /api/v1/admin/ml-checkpoints/{id}` — body `{"is_active": true|false}`. Activating one row deactivates all others for the same model_name in a single transaction.
- `DELETE /api/v1/admin/ml-checkpoints/{id}` — soft delete: sets `is_active=false`, `deactivated_at=now`. Does not actually remove the row (audit trail stays intact).

All admin-gated via `dependencies=[Depends(require_admin)]` from SP-0.7.

- [ ] **Step 1: Failing test (single-file integration test)**

```python
import json
from datetime import datetime, timezone

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import MlCheckpointOut


@pytest.mark.asyncio
async def test_post_creates_inactive_checkpoint(admin_client) -> None:
    body = {
        "model_name": "conv_lstm_predictor",
        "version": "0.1.0",
        "checkpoint_uri": "b2://trading-radar-models/conv_lstm_v0.1.0.pt",
        "sha256": "a" * 64,
        "trained_at": "2026-05-15T12:00:00Z",
        "train_data_window": "2017-08 to 2023-12",
        "eval_results": {"bull_breakout": 0.013, "bear_crash": 0.025},
        "notes": "first run",
    }
    r = await admin_client.post("/api/v1/admin/ml-checkpoints", json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["is_active"] is False
    assert out["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_list_returns_all_checkpoints(admin_client) -> None:
    # Create 2
    for v in ("0.1.0", "0.2.0"):
        await admin_client.post("/api/v1/admin/ml-checkpoints", json={
            "model_name": "conv_lstm_predictor", "version": v,
            "checkpoint_uri": f"b2://x/v{v}.pt", "sha256": "a"*64,
            "trained_at": "2026-05-15T12:00:00Z",
            "train_data_window": "x", "eval_results": {},
        })
    r = await admin_client.get("/api/v1/admin/ml-checkpoints")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 2
    versions = {c["version"] for c in items}
    assert {"0.1.0", "0.2.0"} <= versions


@pytest.mark.asyncio
async def test_patch_activate_deactivates_previous(admin_client) -> None:
    # Create v0.1.0, activate it.
    r1 = await admin_client.post("/api/v1/admin/ml-checkpoints", json={
        "model_name": "conv_lstm_predictor", "version": "0.1.0",
        "checkpoint_uri": "b2://x/v1.pt", "sha256": "a"*64,
        "trained_at": "2026-05-15T12:00:00Z",
        "train_data_window": "x", "eval_results": {},
    })
    id1 = r1.json()["id"]
    await admin_client.patch(f"/api/v1/admin/ml-checkpoints/{id1}", json={"is_active": True})

    # Create v0.2.0, activate it — v0.1.0 must be deactivated.
    r2 = await admin_client.post("/api/v1/admin/ml-checkpoints", json={
        "model_name": "conv_lstm_predictor", "version": "0.2.0",
        "checkpoint_uri": "b2://x/v2.pt", "sha256": "b"*64,
        "trained_at": "2026-05-16T12:00:00Z",
        "train_data_window": "x", "eval_results": {},
    })
    id2 = r2.json()["id"]
    await admin_client.patch(f"/api/v1/admin/ml-checkpoints/{id2}", json={"is_active": True})

    # Verify v0.1.0 is now inactive
    r_list = await admin_client.get("/api/v1/admin/ml-checkpoints")
    items = {c["id"]: c for c in r_list.json()}
    assert items[id1]["is_active"] is False
    assert items[id2]["is_active"] is True


@pytest.mark.asyncio
async def test_delete_soft_marks_deactivated(admin_client) -> None:
    r1 = await admin_client.post("/api/v1/admin/ml-checkpoints", json={
        "model_name": "conv_lstm_predictor", "version": "0.3.0",
        "checkpoint_uri": "b2://x/v3.pt", "sha256": "c"*64,
        "trained_at": "2026-05-17T12:00:00Z",
        "train_data_window": "x", "eval_results": {},
    })
    cid = r1.json()["id"]
    r_del = await admin_client.delete(f"/api/v1/admin/ml-checkpoints/{cid}")
    assert r_del.status_code == 204
    r_list = await admin_client.get("/api/v1/admin/ml-checkpoints")
    item = next(c for c in r_list.json() if c["id"] == cid)
    assert item["is_active"] is False
    assert item["deactivated_at"] is not None
```

The `admin_client` fixture mirrors the SP-0.7 `bot_status_client` fixture — overrides `require_admin` to a stub returning a User(id=1, is_admin=True). Implementer adds it to `tests/integration/conftest.py` similar to that pattern.

- [ ] **Step 2: Implement schemas**

In `app/api/schemas.py`, append:
```python
# --- SP-1: ML checkpoint admin schemas ---


class MlCheckpointOut(BaseModel):
    id: int
    model_name: str
    version: str
    checkpoint_uri: str
    sha256: str
    trained_at: datetime
    train_data_window: str
    eval_results: dict
    is_active: bool
    activated_at: datetime | None = None
    deactivated_at: datetime | None = None
    notes: str | None = None


class MlCheckpointCreateIn(BaseModel):
    model_name: str
    version: str
    checkpoint_uri: str
    sha256: str = Field(min_length=64, max_length=64)
    trained_at: datetime
    train_data_window: str
    eval_results: dict
    notes: str | None = None


class MlCheckpointPatchIn(BaseModel):
    is_active: bool | None = None
    notes: str | None = None
```

- [ ] **Step 3: Implement router**

```python
"""Admin REST endpoints for ML checkpoint registry (SP-1 Phase F)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    MlCheckpointCreateIn,
    MlCheckpointOut,
    MlCheckpointPatchIn,
)
from app.auth.deps import require_admin
from app.db.session import get_session

router = APIRouter(
    prefix="/api/v1/admin/ml-checkpoints",
    tags=["admin-ml"],
    dependencies=[Depends(require_admin)],
)


def _row_to_out(row) -> MlCheckpointOut:
    eval_results = row.eval_results
    if isinstance(eval_results, str):
        eval_results = json.loads(eval_results)
    return MlCheckpointOut(
        id=row.id,
        model_name=row.model_name,
        version=row.version,
        checkpoint_uri=row.checkpoint_uri,
        sha256=row.sha256,
        trained_at=row.trained_at if isinstance(row.trained_at, datetime) else datetime.fromisoformat(row.trained_at),
        train_data_window=row.train_data_window,
        eval_results=eval_results or {},
        is_active=bool(row.is_active),
        activated_at=row.activated_at,
        deactivated_at=row.deactivated_at,
        notes=row.notes,
    )


@router.post("", response_model=MlCheckpointOut, status_code=status.HTTP_201_CREATED)
async def register_checkpoint(
    body: MlCheckpointCreateIn,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> MlCheckpointOut:
    """Register a freshly-trained checkpoint. Always inactive on creation."""
    result = await session.execute(
        sa.text(
            "INSERT INTO ml_checkpoints "
            "(model_name, version, checkpoint_uri, sha256, trained_at, "
            "train_data_window, eval_results, notes, is_active) "
            "VALUES (:n, :v, :u, :s, :t, :w, :e, :no, FALSE) "
            "RETURNING id, model_name, version, checkpoint_uri, sha256, "
            "trained_at, train_data_window, eval_results, is_active, "
            "activated_at, deactivated_at, notes"
        ),
        {
            "n": body.model_name, "v": body.version, "u": body.checkpoint_uri,
            "s": body.sha256, "t": body.trained_at,
            "w": body.train_data_window, "e": json.dumps(body.eval_results),
            "no": body.notes,
        },
    )
    row = result.first()
    await session.commit()
    return _row_to_out(row)


@router.get("", response_model=list[MlCheckpointOut])
async def list_checkpoints(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[MlCheckpointOut]:
    rows = (await session.execute(sa.text(
        "SELECT id, model_name, version, checkpoint_uri, sha256, trained_at, "
        "train_data_window, eval_results, is_active, activated_at, "
        "deactivated_at, notes FROM ml_checkpoints ORDER BY id DESC"
    ))).all()
    return [_row_to_out(r) for r in rows]


@router.patch("/{checkpoint_id}", response_model=MlCheckpointOut)
async def patch_checkpoint(
    checkpoint_id: int, body: MlCheckpointPatchIn,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> MlCheckpointOut:
    existing = (await session.execute(
        sa.text("SELECT id, model_name FROM ml_checkpoints WHERE id = :i"),
        {"i": checkpoint_id},
    )).first()
    if existing is None:
        raise HTTPException(status_code=404, detail="checkpoint not found")

    now = datetime.now(timezone.utc)
    if body.is_active is True:
        # Atomically deactivate previous active for same model_name, then activate this one.
        await session.execute(
            sa.text(
                "UPDATE ml_checkpoints SET is_active = FALSE, deactivated_at = :n "
                "WHERE model_name = :m AND is_active = TRUE AND id != :i"
            ),
            {"n": now, "m": existing.model_name, "i": checkpoint_id},
        )
        await session.execute(
            sa.text(
                "UPDATE ml_checkpoints SET is_active = TRUE, activated_at = :n, "
                "deactivated_at = NULL WHERE id = :i"
            ),
            {"n": now, "i": checkpoint_id},
        )
    elif body.is_active is False:
        await session.execute(
            sa.text(
                "UPDATE ml_checkpoints SET is_active = FALSE, deactivated_at = :n "
                "WHERE id = :i"
            ),
            {"n": now, "i": checkpoint_id},
        )

    if body.notes is not None:
        await session.execute(
            sa.text("UPDATE ml_checkpoints SET notes = :n WHERE id = :i"),
            {"n": body.notes, "i": checkpoint_id},
        )

    await session.commit()
    row = (await session.execute(sa.text(
        "SELECT id, model_name, version, checkpoint_uri, sha256, trained_at, "
        "train_data_window, eval_results, is_active, activated_at, "
        "deactivated_at, notes FROM ml_checkpoints WHERE id = :i"
    ), {"i": checkpoint_id})).first()
    return _row_to_out(row)


@router.delete("/{checkpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_checkpoint(
    checkpoint_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    existing = (await session.execute(
        sa.text("SELECT id FROM ml_checkpoints WHERE id = :i"),
        {"i": checkpoint_id},
    )).first()
    if existing is None:
        raise HTTPException(status_code=404, detail="checkpoint not found")

    await session.execute(
        sa.text(
            "UPDATE ml_checkpoints SET is_active = FALSE, deactivated_at = :n "
            "WHERE id = :i"
        ),
        {"n": datetime.now(timezone.utc), "i": checkpoint_id},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 4: Register router in main.py**

```python
# In app/main.py:
from app.api.routes import admin_ml
app.include_router(admin_ml.router)
```

- [ ] **Step 5: Tests pass + commit**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/integration/test_api_admin_ml_checkpoints.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' add backend/app/api/routes/admin_ml.py backend/app/api/schemas.py backend/app/main.py backend/tests/integration/test_api_admin_ml_checkpoints.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-1' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-1): admin REST endpoints for ml-checkpoints (POST/GET/PATCH/DELETE) + atomic activate"
```
Expected: `4 passed`.

---

### Task F3: Frontend — Admin/MlCheckpoints sub-page — TDD

**Files:**
- Create: `worktrees/sp-1/frontend/src/tabs/Admin/MlCheckpoints.t