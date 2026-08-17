# Phase 4 — Futures-Only Signal Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the live prediction/dispatch path so futures-only symbols (no spot equivalent on Binance — 169 of 527 USDT-M perpetuals today) reach Telegram and a new app view, with a liquidity floor keeping thin coins out of the operator's hands, without touching the existing spot-WS path's behavior.

**Architecture:** Extract `run_live_prediction`'s candle source into an injectable parameter (Step 0, ships and soaks alone). Build a second, fully independent supervisor (`futures_poll_task`) that REST-polls Binance Futures klines every ~60s and calls the same `run_live_prediction` — identical scoring/gating/dispatch/persistence, only the candle source differs. A pure `check_liquidity` function gates futures-only symbols at both daily universe-refresh and dispatch time. A new `symbol_source` column threads cohort identity through `predictions`/`telegram_signals`/`live_trades`. A new read-only app tab sources directly from `telegram_signals`.

**Tech Stack:** Python 3.11+ / FastAPI / SQLAlchemy async / Alembic / pytest + pytest-asyncio / React + TypeScript + Tailwind (frontend).

> **SUPERSEDED (2026-08-15), RE-DRAFTED (2026-08-17).** The universe selector inverts from top-N-by-volume to liquidity-floor pass/fail across the full market (not just the futures-only cohort); the old "Start at N=8, widen to 20-25" Stage A/B sizing is gone. See the addendum at the top of `docs/superpowers/specs/2026-08-14-phase4-futures-signal-coverage-design.md` and the decision record at `docs/superpowers/decisions/2026-08-15-liquidity-floor-selector-supersedes-top20.md` for the full ruling, the hysteresis rule, the cost-check findings, and three-way cohort tagging. **Task 4 (`check_liquidity`) was and remains unaffected.** **Tasks 2, 3, 5, 8, 9, and 18 below are now fully re-drafted against the addendum** (migration revision numbers also fixed — they'd drifted stale against what actually landed on `dev` in the interim, see Task 2's own note). **Task 5b is new** (not in the original 18-task list) — wiring `ws_keepalive_task` onto the new selector, a consequence of the addendum superseding the base design's own non-goal of leaving that fleet untouched.

**Spec:** `docs/superpowers/specs/2026-08-14-phase4-futures-signal-coverage-design.md`

## Global Constraints

- Step 0 (Task 1) must ship, soak, and merge to dev **before** any task that depends on it (Tasks 6-10). It is its own PR.
- Every existing test must pass **unchanged** after Task 1. A required test edit is a failure signal — stop and reconsider, don't just fix the test.
- No DEBUG-level logging anywhere in the poller's failure paths — WARNING minimum per attempt, ERROR on systematic (consecutive-streak) failure.
- The idempotency replay test (Task 7) is a required proof obligation, not optional coverage.
- `symbol_source` defaults to `'established_top20'` everywhere (corrected 2026-08-17 — was `'spot_ws'`, a transport-named default from the two-value scheme; the three-way liquidity-floor-selector addendum uses cohort-named values instead, and `'established_top20'` is the correct default for any call site that doesn't explicitly pass a cohort) — every existing caller/row is unaffected without passing anything new.
- Liquidity thresholds are exact: `qvol_24h >= $20_000_000 AND spread_bps <= 5.0 AND depth_0_5pct_usdt >= $50_000`.
- Poll cadence is exactly ~60s; closure detection is by open-time advancing, never wall-clock.
- **The futures-poller (and, since Task 5b, the spot-WS fleet too) has a hard open-position retention override — corrected 2026-08-17.** This constraint originally read "mirror the existing cancellation behavior, no new retention logic"; the liquidity-floor-selector addendum makes retention a hard requirement on both fleets. Left here as a corrected record, not deleted, so a reader who only skims Global Constraints doesn't pick up the stale version.
- Follow this codebase's dual-dialect migration pattern (`is_pg = dialect.startswith("postgres")`) for every new table/column.
- **Downstream tasks not yet redrafted as of 2026-08-17: Task 10 (dispatch-time re-check), Tasks 12-16 (app-view backend + frontend) still contain the stale two-value `Literal["spot_ws", "futures_poll"]` scheme and literal `"spot_ws"` defaults/filters.** These are far enough downstream (behind Tasks 1-9's sequential execution) that redrafting them now risks going stale again before they're reached — apply the SAME discipline used for Tasks 5/8/9/18 here: re-verify against the addendum and fix the value scheme immediately before each of these specific tasks executes, not before. Do not execute Task 10 or 12-16 as currently drafted without that check first.

---

## Task 1: Extract `run_live_prediction`'s candle source (Step 0 — its own PR)

**Files:**
- Modify: `backend/app/ws/live_prediction.py:109-336` (`run_live_prediction`)
- Test: `backend/tests/unit/test_live_prediction_candle_source.py` (new)
- Reference (do not modify): `backend/tests/integration/test_live_prediction_validator_isolation.py`, `backend/tests/unit/test_live_prediction_dispatch_hook.py`, `backend/tests/unit/test_live_prediction_history_seed.py`, `backend/tests/ops/test_ws_keepalive.py`, `backend/tests/unit/test_ws_live_prediction_ghost.py`

**Interfaces:**
- Produces: `run_live_prediction(symbol_pair: str = "BTC/USDT", timeframe: str = "1h", *, candle_source: AsyncIterator[MultiStreamCandle] | None = None, symbol_source: str = "established_top20") -> None` — the shared entrypoint every later task calls. (Default corrected 2026-08-17 from `"spot_ws"` — see Global Constraints.)

- [ ] **Step 1: Run the full existing test suite and record the baseline**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/integration/test_live_prediction_validator_isolation.py tests/unit/test_live_prediction_dispatch_hook.py tests/unit/test_live_prediction_history_seed.py tests/ops/test_ws_keepalive.py tests/unit/test_ws_live_prediction_ghost.py -v`
Expected: all pass. Note the exact pass count — this is the number that must be unchanged after Step 4.

- [ ] **Step 2: Write the new guard test**

```python
# backend/tests/unit/test_live_prediction_candle_source.py
"""Step 0 guard: the WS path must still construct and consume its own
BinanceKlineStream when no candle_source is injected — a regression here
means a future change silently repointed the default source."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.ws.live_prediction import run_live_prediction


@pytest.mark.asyncio
async def test_ws_path_constructs_own_stream_when_no_candle_source_given():
    with patch("app.ws.live_prediction.BinanceKlineStream") as mock_stream_cls:
        mock_stream = mock_stream_cls.return_value

        async def empty_stream():
            return
            yield  # pragma: no cover — makes this an async generator

        mock_stream.stream = lambda: empty_stream()
        with patch("app.ws.live_prediction.httpx.AsyncClient"):
            await run_live_prediction(symbol_pair="ETH/USDT", timeframe="1h")

    mock_stream_cls.assert_called_once_with(symbol="ETHUSDT", timeframe="1h")


@pytest.mark.asyncio
async def test_injected_candle_source_is_consumed_instead_of_ws():
    """When candle_source is supplied, BinanceKlineStream must never be
    constructed — proves the two paths are mutually exclusive, not that
    one silently falls back to the other."""
    from app.shadow.multi_stream import MultiStreamCandle
    from datetime import datetime, timezone

    candle = MultiStreamCandle(
        symbol="SOLUSDT", timeframe="1h",
        ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=100.0, high=101.0, low=99.0, close=100.5, volume=1000.0,
    )

    async def one_candle():
        yield candle

    with patch("app.ws.live_prediction.BinanceKlineStream") as mock_stream_cls, \
         patch("app.ws.live_prediction.build_prediction", new_callable=AsyncMock) as mock_build, \
         patch("app.ws.live_prediction._persist_prediction_and_schedule_validation", new_callable=AsyncMock), \
         patch("app.ws.live_prediction.record_heartbeat", new_callable=AsyncMock), \
         patch("app.ws.live_prediction._maybe_dispatch", new_callable=AsyncMock), \
         patch("app.ws.live_prediction.manager"), \
         patch("app.ws.live_prediction.httpx.AsyncClient"):
        mock_pred = AsyncMock()
        mock_pred.layer_scores = {}
        mock_pred.prediction_extras = None
        mock_pred.symbol = "SOL/USDT"
        mock_pred.timeframe = "1h"
        mock_pred.final.direction = "NEUTRAL"
        mock_pred.final.score = 0.0
        mock_pred.final.confidence = 0.0
        mock_pred.ts = candle.ts
        mock_pred.mtf_agreement = None
        mock_pred.mtf_dominant_tf = None
        mock_pred.mtf_directions_json = None
        mock_pred.p_win = None
        mock_pred.effective_score = None
        mock_pred.realized_vol_20d = None
        mock_pred.funding_directional_adj = None
        mock_pred.funding_rate_daily = None
        mock_pred.model_dump = lambda mode=None: {}
        mock_build.return_value = mock_pred

        await run_live_prediction(
            symbol_pair="SOL/USDT", timeframe="1h", candle_source=one_candle(),
        )

    mock_stream_cls.assert_not_called()
    mock_build.assert_awaited_once()
```

- [ ] **Step 3: Run the new test to verify it fails**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_live_prediction_candle_source.py -v`
Expected: FAIL — `run_live_prediction() got an unexpected keyword argument 'candle_source'`.

- [ ] **Step 4: Refactor `run_live_prediction` to accept an injectable candle source**

In `backend/app/ws/live_prediction.py`, change only the signature and the stream-construction line — nothing else in the function body changes:

```python
async def run_live_prediction(
    symbol_pair: str = "BTC/USDT",
    timeframe: str = "1h",
    *,
    candle_source: AsyncIterator[MultiStreamCandle] | None = None,
    symbol_source: str = "established_top20",
) -> None:
    """Seed REST history, subscribe to Binance WS (or consume an injected
    candle_source), on each closed candle:
    1. Append candle to in-memory DataFrame (last 1000 bars)
    2. Build prediction (compose layers + aggregate)
    3. Persist prediction row to predictions table via audit hash chain
    4. Publish payload over WebSocket so UI updates
    Persist comes BEFORE publish — if persist fails (DB down), do not publish.

    ``candle_source``, when supplied, replaces the default Binance SPOT
    WS subscription — used by the futures-only REST poller (Phase 4) so
    this function's scoring/gating/dispatch/persistence logic is shared,
    not duplicated, between the two candle-delivery mechanisms.
    ``symbol_source`` is cohort metadata threaded into the persisted and
    dispatched payloads; defaults to ``"established_top20"`` so every
    existing caller is unaffected.
    """
    binance_symbol = symbol_pair.replace("/", "")

    async with httpx.AsyncClient() as http:
        client = BinanceClient(http=http)
        history = await client.fetch_klines(
            binance_symbol, timeframe, limit=HISTORY_SEED_BARS,
        )
    bars = pd.DataFrame([c.__dict__ for c in history])
    bars["ts"] = pd.to_datetime(bars["ts"], utc=True)
    bars = bars.set_index("ts")[["open", "high", "low", "close", "volume"]]

    session_factory = get_session_factory()
    source = candle_source
    if source is None:
        stream = BinanceKlineStream(symbol=binance_symbol, timeframe=timeframe)
        source = stream.stream()

    async for candle in source:
        # <everything below this line is UNCHANGED from the current body:
        #  pattern-stats lookup, ghost prediction, build_prediction,
        #  _layer_payload/_predictions_payload construction,
        #  _persist_prediction_and_schedule_validation,
        #  manager.publish, _maybe_dispatch, record_heartbeat>
        ...
```

Add the import: `from collections.abc import AsyncIterator` and `from app.shadow.multi_stream import MultiStreamCandle` at the top of the file (check they aren't already imported first).

Do **not** touch anything else in the function body in this task — no cleanup, no renaming, no reordering. The `symbol_source` parameter is threaded into the payload construction in Task 9, not here; this task only adds the parameter with its default.

- [ ] **Step 5: Run the new guard tests to verify they pass**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_live_prediction_candle_source.py -v`
Expected: PASS, both tests.

- [ ] **Step 6: Run the full existing test suite to verify the exact same pass count as Step 1**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/integration/test_live_prediction_validator_isolation.py tests/unit/test_live_prediction_dispatch_hook.py tests/unit/test_live_prediction_history_seed.py tests/ops/test_ws_keepalive.py tests/unit/test_ws_live_prediction_ghost.py -v`
Expected: identical pass count to Step 1. If any test needed editing to pass, stop — that means the extraction changed behavior, and the refactor needs to be redone, not the test.

- [ ] **Step 7: Run the full backend suite for a final regression check**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider -q`
Expected: the same 6 known pre-existing failures as any clean baseline run on this repo (test_api_intermarket_route, test_ml_checkpoints ×2, test_pwin_calibrator ×3 — sklearn/network/environment-caused, unrelated to this change), nothing new.

- [ ] **Step 8: Commit**

```bash
git add backend/app/ws/live_prediction.py backend/tests/unit/test_live_prediction_candle_source.py
git commit -m "refactor(live-prediction): extract candle source as injectable parameter (Phase 4 Step 0)"
```

**This task ships as its own PR to dev, soaks per the standing behavior-changing class (4-6h), and merges before any task below begins.**

---

## Task 2: Migration — `live_prediction_watermarks` table

> **Revision renumbered 2026-08-17.** Originally drafted as `0036_live_prediction_watermarks` — that ID was already at the exact 32-char `alembic_version` hard limit (zero margin, violates this project's own ≤30-char convention) AND `0036` has since been claimed by PR #456 (`0036_deactivate_rl_ckpt_62`, merged to dev). Renumbered to `0037` and shortened.

**Files:**
- Create: `backend/alembic/versions/2026_08_17_0037_live_pred_watermarks.py`
- Test: `backend/tests/db/test_live_prediction_watermarks_migration.py` (new)

**Interfaces:**
- Produces: table `live_prediction_watermarks(symbol TEXT, timeframe TEXT, last_open_time BIGINT, updated_at TIMESTAMPTZ, PRIMARY KEY (symbol, timeframe))`.

- [ ] **Step 1: Write the failing migration test**

```python
# backend/tests/db/test_live_prediction_watermarks_migration.py
"""Postgres-only: verifies the live_prediction_watermarks table shape."""
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="Postgres DATABASE_URL not set — migration tests are CI-only. "
           "Set DATABASE_URL=postgresql+asyncpg://... to run locally.",
)


@pytest.mark.asyncio
async def test_watermark_table_upsert_and_pk() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO live_prediction_watermarks (symbol, timeframe, last_open_time) "
            "VALUES ('SOL/USDT', '1h', 1000) "
            "ON CONFLICT (symbol, timeframe) DO UPDATE SET last_open_time = EXCLUDED.last_open_time"
        ))
        await conn.execute(sa.text(
            "INSERT INTO live_prediction_watermarks (symbol, timeframe, last_open_time) "
            "VALUES ('SOL/USDT', '1h', 2000) "
            "ON CONFLICT (symbol, timeframe) DO UPDATE SET last_open_time = EXCLUDED.last_open_time"
        ))
        row = (await conn.execute(sa.text(
            "SELECT last_open_time FROM live_prediction_watermarks "
            "WHERE symbol = 'SOL/USDT' AND timeframe = '1h'"
        ))).one()
        assert row.last_open_time == 2000
        count = (await conn.execute(sa.text(
            "SELECT COUNT(*) AS n FROM live_prediction_watermarks "
            "WHERE symbol = 'SOL/USDT' AND timeframe = '1h'"
        ))).one()
        assert count.n == 1  # PK enforced upsert, not a second row
    await engine.dispose()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && DATABASE_URL=postgresql+asyncpg://... python -m pytest --no-cov -p no:cacheprovider tests/db/test_live_prediction_watermarks_migration.py -v`
Expected: FAIL — relation "live_prediction_watermarks" does not exist. (If no local Postgres is available, this step is verified in CI instead — proceed to Step 3.)

- [ ] **Step 3: Write the migration**

```python
# backend/alembic/versions/2026_08_17_0037_live_pred_watermarks.py
"""live_prediction_watermarks -- Phase 4 idempotency for the futures REST poller

The REST poller can re-observe the same closed candle after a restart, a
clock-skew tick, or an overlapping poll. This table is the persisted
watermark that guarantees a given (symbol, timeframe, candle open-time)
is processed at most once, independent of the hash-chained predictions
table's schema.

Revision ID: 0037_live_pred_watermarks
Revises: 0036_deactivate_rl_ckpt_62
Create Date: 2026-08-17
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0037_live_pred_watermarks"
down_revision: str | None = "0036_deactivate_rl_ckpt_62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    is_pg = dialect.startswith("postgres")
    ts_type = "TIMESTAMPTZ" if is_pg else "TEXT"

    op.execute(f"""
        CREATE TABLE live_prediction_watermarks (
            symbol VARCHAR(20) NOT NULL,
            timeframe VARCHAR(8) NOT NULL,
            last_open_time BIGINT NOT NULL,
            updated_at {ts_type} NOT NULL {"DEFAULT now()" if is_pg else "DEFAULT CURRENT_TIMESTAMP"},
            PRIMARY KEY (symbol, timeframe)
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS live_prediction_watermarks;")
```

- [ ] **Step 4: Run the test to verify it passes (CI or local Postgres)**

Run: `cd backend && alembic upgrade head && DATABASE_URL=postgresql+asyncpg://... python -m pytest --no-cov -p no:cacheprovider tests/db/test_live_prediction_watermarks_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/2026_08_17_0037_live_pred_watermarks.py backend/tests/db/test_live_prediction_watermarks_migration.py
git commit -m "feat(db): add live_prediction_watermarks table (Phase 4 poller idempotency)"
```

---

## Task 3: Migration — `symbol_source` column, four tables, three-way cohort values

> **Redrafted 2026-08-17 against the liquidity-floor-selector addendum** (`docs/superpowers/specs/2026-08-14-phase4-futures-signal-coverage-design.md`, ADDENDUM section (c)). Two corrections from the original draft: (1) renumbered `0037`→`0038` and shortened — `0037` is now Task 2's ID (itself renumbered), and the old `0037_symbol_source_cohort_tag` was already at the 30-char recommended ceiling with zero margin; (2) **`shadow_trades` added as a fourth tagged table** — missing from the original draft (and from the addendum's first cut, caught in operator review of PR #458). This isn't optional: the addendum's reversal criterion #2 (re-measuring the new cohort's EV) is methodologically the same `shadow_trades`-based split the 07-28 fleet-cap ratification itself used — without a tag on `shadow_trades`, that measurement can never be run. Value set also changes from two transport-named values (`spot_ws`/`futures_poll`) to three cohort-named values (`established_top20`/`liquidity_added_spot`/`futures_poll`) — see the addendum for why these aren't the same axis (transport vs. rationale) even though `futures_poll` happens to be spelled the same in both schemes.

**Files:**
- Create: `backend/alembic/versions/2026_08_17_0038_symbol_source_tag.py`
- Test: `backend/tests/db/test_symbol_source_migration.py` (new)

**Interfaces:**
- Produces: `symbol_source TEXT NOT NULL DEFAULT 'established_top20'` on `predictions`, `shadow_trades`, `telegram_signals`, `live_trades`.

- [ ] **Step 1: Write the failing migration test**

```python
# backend/tests/db/test_symbol_source_migration.py
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="Postgres DATABASE_URL not set — migration tests are CI-only.",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["predictions", "shadow_trades", "telegram_signals", "live_trades"])
async def test_symbol_source_column_defaults_established_top20(table: str) -> None:
    """Existing rows backfill to 'established_top20' -- they were all
    covered under the pre-2026-08-15 top-N-by-volume selector, which is
    what that tag denotes (a lineage marker, not a live-recomputed rank)."""
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        col = (await conn.execute(sa.text(
            "SELECT column_name, column_default, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = 'symbol_source'"
        ), {"t": table})).one()
        assert col.is_nullable == "NO"
        assert "established_top20" in col.column_default
    await engine.dispose()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/db/test_symbol_source_migration.py -v` (CI, or local Postgres)
Expected: FAIL — no such column.

- [ ] **Step 3: Write the migration**

```python
# backend/alembic/versions/2026_08_17_0038_symbol_source_tag.py
"""symbol_source cohort tag -- Phase 4, three-way (2026-08-17 redraft)

Additive metadata column on predictions, shadow_trades, telegram_signals,
and live_trades so every downstream consumer (Telegram card, app view,
future reporting, AND the reversal-criterion-#2 safety re-measurement --
see the liquidity-floor-selector decision record) can split cohorts from
one source of truth. NOT part of any hash-chained payload's hashed
content on predictions/shadow_trades/live_trades -- existing rows keep
their existing row_hash values, matching the PR1 record-only column
precedent (telegram_signals is not hash-chained at all).

Values: 'established_top20' (default -- pre-cutover coverage, a lineage
tag assigned once and not recomputed), 'liquidity_added_spot' (spot-
backed, newly covered, the larger and unproven of the two new cohorts),
'futures_poll' (futures-only, unchanged name from the original two-value
scheme but now one of three, not one of two).

Revision ID: 0038_symbol_source_tag
Revises: 0037_live_pred_watermarks
Create Date: 2026-08-17
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0038_symbol_source_tag"
down_revision: str | None = "0037_live_pred_watermarks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: tuple[str, ...] = ("predictions", "shadow_trades", "telegram_signals", "live_trades")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN symbol_source TEXT NOT NULL DEFAULT 'established_top20';"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS symbol_source;")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && alembic upgrade head && python -m pytest --no-cov -p no:cacheprovider tests/db/test_symbol_source_migration.py -v`
Expected: PASS, all 4 parametrized cases.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/2026_08_17_0038_symbol_source_tag.py backend/tests/db/test_symbol_source_migration.py
git commit -m "feat(db): add symbol_source cohort tag to predictions/shadow_trades/telegram_signals/live_trades"
```

---

## Task 4: `check_liquidity` — pure liquidity-floor function

**Files:**
- Create: `backend/app/data/futures_liquidity.py`
- Test: `backend/tests/unit/test_futures_liquidity.py` (new)

**Interfaces:**
- Consumes: `app.data.ratelimit.RateLimitedClient` (existing, from `app/data/ratelimit.py`).
- Produces: `LiquidityCheck(passed: bool, qvol_24h: float, spread_bps: float, depth_0_5pct_usdt: float)` dataclass; `async def check_liquidity(symbol: str, rate_client: RateLimitedClient) -> LiquidityCheck`. Consumed by Task 5 (universe selection) and Task 10 (dispatch-time re-check).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_futures_liquidity.py
from __future__ import annotations

import httpx
import pytest

from app.data.futures_liquidity import LiquidityCheck, check_liquidity
from app.data.ratelimit import RateLimitedClient, TokenBucket


def _rate_client(transport: httpx.MockTransport) -> RateLimitedClient:
    http = httpx.AsyncClient(transport=transport)
    return RateLimitedClient(
        exchange="binance_futures", http=http,
        buckets={"default": TokenBucket(capacity=2400.0, refill_per_sec=40.0)},
    )


def _mock_transport(*, qvol: str, bids: list[tuple[str, str]], asks: list[tuple[str, str]]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/fapi/v1/ticker/24hr":
            return httpx.Response(200, json={"quoteVolume": qvol})
        if req.url.path == "/fapi/v1/depth":
            return httpx.Response(200, json={"bids": bids, "asks": asks})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_liquid_symbol_passes_all_three_thresholds() -> None:
    # mid=100, spread=(100.01-99.99)/100*10000=2bps, deep book both sides
    transport = _mock_transport(
        qvol="25000000",
        bids=[("99.99", "1000"), ("99.98", "1000")],
        asks=[("100.01", "1000"), ("100.02", "1000")],
    )
    result = await check_liquidity("HYPEUSDT", _rate_client(transport))
    assert result.passed is True
    assert result.qvol_24h == pytest.approx(25_000_000.0)
    assert result.spread_bps == pytest.approx(2.0, abs=0.1)
    assert result.depth_0_5pct_usdt > 50_000


@pytest.mark.asyncio
async def test_fails_on_low_volume_despite_deep_book() -> None:
    transport = _mock_transport(
        qvol="5000000",  # under $20M floor
        bids=[("99.99", "1000")], asks=[("100.01", "1000")],
    )
    result = await check_liquidity("THINUSDT", _rate_client(transport))
    assert result.passed is False
    assert result.qvol_24h == pytest.approx(5_000_000.0)


@pytest.mark.asyncio
async def test_fails_on_wide_spread_despite_high_volume() -> None:
    # mid=100, spread=(101-99)/100*10000=200bps -- way over 5bps floor
    transport = _mock_transport(
        qvol="1000000000",
        bids=[("99.00", "10000")], asks=[("101.00", "10000")],
    )
    result = await check_liquidity("WIDEUSDT", _rate_client(transport))
    assert result.passed is False
    assert result.spread_bps > 5.0


@pytest.mark.asyncio
async def test_fails_on_thin_depth_despite_high_volume_and_tight_spread() -> None:
    """The AKE/APR/CYS/VELVET/BTW case from FU-43: real high-volume,
    tight-spread symbols with under $50k resting depth."""
    transport = _mock_transport(
        qvol="1000000000", bids=[("99.99", "0.1")], asks=[("100.01", "0.1")],
    )
    result = await check_liquidity("AKEUSDT", _rate_client(transport))
    assert result.passed is False
    assert result.depth_0_5pct_usdt < 50_000


@pytest.mark.asyncio
async def test_depth_sums_both_sides_within_half_percent_band() -> None:
    transport = _mock_transport(
        qvol="25000000",
        bids=[("99.99", "300"), ("99.50", "300")],  # 99.50 is outside 0.5% of mid=100
        asks=[("100.01", "300")],
    )
    result = await check_liquidity("BANDUSDT", _rate_client(transport))
    # Only 99.99 bid level (within [99.5, 100.5]... actually 99.50 IS exactly
    # at the edge) and 100.01 ask level count toward the 0.5% band.
    assert result.depth_0_5pct_usdt == pytest.approx(300 * 99.99 + 300 * 100.01, rel=0.01) \
        or result.depth_0_5pct_usdt == pytest.approx(300 * 99.99 + 300 * 99.50 + 300 * 100.01, rel=0.01)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_futures_liquidity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.data.futures_liquidity'`.

- [ ] **Step 3: Write the implementation**

```python
# backend/app/data/futures_liquidity.py
"""Phase 4 liquidity floor -- FU-43: 24h volume alone does not imply
resting order-book depth (AKE/APR/CYS/VELVET/BTW all carry $100M-$1.1B
24h volume against under $50k depth within 0.5% of mid). All three
metrics -- volume, spread, depth -- must pass together.

Runs twice: once at daily futures-only universe selection (coarse
inclusion), and again at dispatch time for futures-only-cohort signals
specifically, since books move intraday and a symbol qualifying at
00:00 UTC can be thin hours later when a real signal fires.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.data.ratelimit import RateLimitedClient

_BASE_URL = "https://fapi.binance.com"

QVOL_FLOOR_USDT: float = 20_000_000.0
SPREAD_MAX_BPS: float = 5.0
DEPTH_FLOOR_USDT: float = 50_000.0
_DEPTH_BAND: float = 0.005  # 0.5% of mid, both sides


@dataclass(frozen=True)
class LiquidityCheck:
    passed: bool
    qvol_24h: float
    spread_bps: float
    depth_0_5pct_usdt: float


async def check_liquidity(symbol: str, rate_client: RateLimitedClient) -> LiquidityCheck:
    """Fetch 24h ticker + order-book depth for *symbol* and evaluate the floor.

    Raises on network/parse failure -- callers are responsible for
    catching and logging (see Task 5's daily-selection caller and
    Task 10's dispatch-time caller for the two different failure-
    handling contracts).
    """
    ticker_resp = await rate_client.request(
        "GET", f"{_BASE_URL}/fapi/v1/ticker/24hr",
        endpoint_key="ticker24hr", params={"symbol": symbol}, timeout=10.0,
    )
    ticker_resp.raise_for_status()
    qvol_24h = float(ticker_resp.json()["quoteVolume"])

    depth_resp = await rate_client.request(
        "GET", f"{_BASE_URL}/fapi/v1/depth",
        endpoint_key="depth", params={"symbol": symbol, "limit": "100"}, timeout=10.0,
    )
    depth_resp.raise_for_status()
    book = depth_resp.json()
    bids = [(float(p), float(q)) for p, q in book["bids"]]
    asks = [(float(p), float(q)) for p, q in book["asks"]]

    best_bid, best_ask = bids[0][0], asks[0][0]
    mid = (best_bid + best_ask) / 2
    spread_bps = (best_ask - best_bid) / mid * 10_000

    lo, hi = mid * (1 - _DEPTH_BAND), mid * (1 + _DEPTH_BAND)
    bid_depth = sum(p * q for p, q in bids if p >= lo)
    ask_depth = sum(p * q for p, q in asks if p <= hi)
    depth_0_5pct_usdt = bid_depth + ask_depth

    passed = (
        qvol_24h >= QVOL_FLOOR_USDT
        and spread_bps <= SPREAD_MAX_BPS
        and depth_0_5pct_usdt >= DEPTH_FLOOR_USDT
    )
    return LiquidityCheck(
        passed=passed, qvol_24h=qvol_24h, spread_bps=spread_bps,
        depth_0_5pct_usdt=depth_0_5pct_usdt,
    )


__all__ = ["DEPTH_FLOOR_USDT", "QVOL_FLOOR_USDT", "SPREAD_MAX_BPS", "LiquidityCheck", "check_liquidity"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_futures_liquidity.py -v`
Expected: PASS, all 5 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/data/futures_liquidity.py backend/tests/unit/test_futures_liquidity.py
git commit -m "feat(futures): add check_liquidity pure function (Phase 4 liquidity floor)"
```

---

## Task 5: Live-fleet universe selector — liquidity floor, full market, hysteresis

> **REDRAFTED 2026-08-17.** The original Task 5 built `fetch_top_n_usdt_futures_only`: rank futures-only symbols by 24h volume, walk down taking the first N that also pass `check_liquidity`. That is exactly the shape the liquidity-floor-selector addendum supersedes — volume-ranked with liquidity as a secondary filter, scoped to futures-only only. This redraft builds the addendum's actual selector: **every symbol across the full market (spot-backed and futures-only) that passes the liquidity floor, full stop — no rank, no N cutoff** — with the hysteresis rule (3-of-5 enter / unanimous 5-of-5 exit / 24h dwell / open-position override) from addendum section (a). This single function is the shared source both `ws_keepalive_task` (Task 5b, spot-backed rows) and `futures_poll_task` (Task 8, futures-only rows) read from — "one mechanism closes both gaps," per the addendum.
>
> `check_liquidity` (Task 4) itself is unchanged and reused verbatim — it's evaluated against **futures** market data for every candidate regardless of cohort, which is correct: the operator trades everything on Binance futures, so futures-side liquidity is what matters even for a spot-backed symbol's tradeability, not its spot order book.

**Files:**
- Create: `backend/app/shadow/live_fleet_universe.py`
- Create: `backend/alembic/versions/2026_08_17_0039_live_fleet_universe.py`
- Test: `backend/tests/unit/test_live_fleet_universe.py` (new)
- Test: `backend/tests/db/test_live_fleet_universe_migration.py` (new)

**Interfaces:**
- Consumes: `check_liquidity` (Task 4), `httpx.AsyncClient`, `RateLimitedClient`, `load_current_universe` (existing, `app.shadow.universe` — cold-start seed only), `KEEPALIVE_TOP_N` (existing, `app.ws.keepalive` — cold-start seed only).
- Produces:
  - `@dataclass(frozen=True) class LiveFleetEntry: symbol: str; cohort: Literal["established_top20", "liquidity_added_spot", "futures_poll"]; qvol_24h: float; spread_bps: float; depth_0_5pct_usdt: float`
  - `async def refresh_live_fleet_universe(session_factory, http: httpx.AsyncClient, rate_client: RateLimitedClient) -> list[LiveFleetEntry]` — the daily job. Fetches the full market, samples `check_liquidity` 5x per $20M-qvol-prefilter candidate, applies hysteresis against the previous snapshot, persists the new snapshot, returns it.
  - `async def load_live_fleet_universe(session, *, cohort: str | None = None) -> list[LiveFleetEntry]` — read the latest snapshot, optionally filtered to one cohort. Consumed by Task 5b (`cohort in {"established_top20", "liquidity_added_spot"}`) and Task 8 (`cohort == "futures_poll"`).
  - `async def has_open_position(session, symbol_pair: str) -> bool` — checks `live_trades WHERE status='open'` OR `shadow_open_positions`. Shared by Task 5b's and Task 8's reconciliation (open-position override).

- [ ] **Step 1: Write the failing migration test**

```python
# backend/tests/db/test_live_fleet_universe_migration.py
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="Postgres DATABASE_URL not set — migration tests are CI-only.",
)


@pytest.mark.asyncio
async def test_live_fleet_universe_table_shape() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('FOOUSDT', 'liquidity_added_spot', 25000000, 2.5, 60000, now())"
        ))
        row = (await conn.execute(sa.text(
            "SELECT cohort FROM live_fleet_universe WHERE symbol = 'FOOUSDT'"
        ))).one()
        assert row.cohort == "liquidity_added_spot"
    await engine.dispose()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/db/test_live_fleet_universe_migration.py -v`
Expected: FAIL — relation "live_fleet_universe" does not exist.

- [ ] **Step 3: Write the migration**

```python
# backend/alembic/versions/2026_08_17_0039_live_fleet_universe.py
"""live_fleet_universe -- Phase 4 liquidity-floor selector snapshots

One row per (symbol, snapshot_at) -- mirrors asset_universe's own
snapshot-keyed shape (same "latest snapshot_at wins" read pattern), but
this is a SEPARATE table, not a repurposing of asset_universe: this one
is liquidity-floor-selected across the full market for the live-fleet
(WS + REST-poll) supervisors specifically; asset_universe stays exactly
as-is (spot-top-30 by volume, still used by shadow_worker's own
universe). Do not merge these two tables -- see the liquidity-floor-
selector decision record's "open dependency question" for why they can
legitimately diverge (a symbol can be liquidity-floor-qualified via deep
FUTURES liquidity while ranking outside the spot-volume top-30).

Revision ID: 0039_live_fleet_universe
Revises: 0038_symbol_source_tag
Create Date: 2026-08-17
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0039_live_fleet_universe"
down_revision: str | None = "0038_symbol_source_tag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    is_pg = dialect.startswith("postgres")
    ts_type = "TIMESTAMPTZ" if is_pg else "TEXT"
    ts_default = "DEFAULT now()" if is_pg else "DEFAULT CURRENT_TIMESTAMP"

    op.execute(f"""
        CREATE TABLE live_fleet_universe (
            symbol VARCHAR(20) NOT NULL,
            cohort TEXT NOT NULL,
            qvol_24h DOUBLE PRECISION NOT NULL,
            spread_bps DOUBLE PRECISION NOT NULL,
            depth_0_5pct_usdt DOUBLE PRECISION NOT NULL,
            snapshot_at {ts_type} NOT NULL {ts_default},
            PRIMARY KEY (symbol, snapshot_at)
        );
    """)
    op.execute(
        "CREATE INDEX live_fleet_universe_snapshot_idx "
        "ON live_fleet_universe (snapshot_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS live_fleet_universe_snapshot_idx;")
    op.execute("DROP TABLE IF EXISTS live_fleet_universe;")
```

- [ ] **Step 4: Run the migration test to verify it passes**

Run: `cd backend && alembic upgrade head && python -m pytest --no-cov -p no:cacheprovider tests/db/test_live_fleet_universe_migration.py -v`

- [ ] **Step 5: Write the failing unit tests**

```python
# backend/tests/unit/test_live_fleet_universe.py
from __future__ import annotations

import httpx
import pytest

from app.data.ratelimit import RateLimitedClient, TokenBucket
from app.shadow.live_fleet_universe import LiveFleetEntry, refresh_live_fleet_universe


def _rate_client(transport: httpx.MockTransport) -> RateLimitedClient:
    http = httpx.AsyncClient(transport=transport)
    return RateLimitedClient(
        exchange="binance_futures", http=http,
        buckets={"default": TokenBucket(capacity=2400.0, refill_per_sec=40.0)},
    )


def _handler_factory(futures_symbols, spot_symbols, tickers, deep_symbols):
    """deep_symbols pass depth+spread on EVERY sample; everything else
    fails every sample -- covers the two unambiguous cases (stable pass,
    stable fail). Flicker/borderline behavior is exercised by the
    hysteresis-specific tests below via a call-count-based handler."""
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/fapi/v1/exchangeInfo":
            return httpx.Response(200, json={"symbols": [
                {"symbol": s, "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"}
                for s in futures_symbols
            ]})
        if path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json={"symbols": [
                {"symbol": s, "quoteAsset": "USDT", "status": "TRADING"} for s in spot_symbols
            ]})
        if path == "/fapi/v1/ticker/24hr":
            return httpx.Response(200, json=tickers)
        if path == "/fapi/v1/depth":
            sym = req.url.params["symbol"]
            if sym in deep_symbols:
                return httpx.Response(200, json={
                    "bids": [("99.99", "10000")], "asks": [("100.01", "10000")],
                })
            return httpx.Response(200, json={"bids": [("99.99", "0.01")], "asks": [("100.01", "0.01")]})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_cold_start_seeds_established_top20_from_legacy_fleet(session_factory) -> None:
    """No prior live_fleet_universe snapshot exists. A symbol that (a)
    passes the new floor AND (b) is in today's legacy top-20-by-volume
    fleet gets tagged established_top20, not liquidity_added_spot."""
    tickers = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000000000"},
        {"symbol": "NEWUSDT", "quoteVolume": "25000000"},
    ]
    transport = _handler_factory(
        futures_symbols=["BTCUSDT", "NEWUSDT"], spot_symbols=["BTCUSDT", "NEWUSDT"],
        tickers=tickers, deep_symbols={"BTCUSDT", "NEWUSDT"},
    )
    # BTCUSDT is seeded as legacy-top-20 via a monkeypatched load_current_universe
    # stub in the real test (fixture omitted here for brevity) -- NEWUSDT is not.
    entries = await refresh_live_fleet_universe(
        session_factory, httpx.AsyncClient(transport=transport), _rate_client(transport),
    )
    by_symbol = {e.symbol: e for e in entries}
    assert by_symbol["BTCUSDT"].cohort == "established_top20"
    assert by_symbol["NEWUSDT"].cohort == "liquidity_added_spot"


@pytest.mark.asyncio
async def test_entry_requires_three_of_five_passes(session_factory) -> None:
    """A symbol passing exactly 3/5 samples enters; 2/5 does not."""
    # Implementation samples check_liquidity 5x per candidate -- test injects
    # a depth handler that alternates pass/fail by call count to hit exactly
    # 3 passes of 5, asserts the symbol IS included; then 2 of 5, asserts
    # it is NOT. (Full call-counting handler omitted here for brevity --
    # follow the pattern in test_futures_liquidity.py's MockTransport use.)


@pytest.mark.asyncio
async def test_exit_requires_unanimous_five_of_five_fails(session_factory) -> None:
    """A symbol already IN the universe (seeded via a prior snapshot row)
    that fails 4/5 samples is RETAINED (not unanimous); failing 5/5 exits."""


@pytest.mark.asyncio
async def test_open_position_overrides_unanimous_exit(session_factory) -> None:
    """A symbol failing 5/5 samples but with an open live_trades row is
    retained anyway -- has_open_position short-circuits the exit."""


@pytest.mark.asyncio
async def test_new_entrant_never_tagged_established_top20(session_factory) -> None:
    """Even on a WARM refresh (prior snapshot exists), a symbol newly
    entering (not present in the prior snapshot) gets liquidity_added_spot
    or futures_poll -- established_top20 is a cold-start-only tag, never
    assigned retroactively post-cutover."""
```

*(The five test stubs above without bodies are proof obligations, not decoration — each needs its full arrange/act/assert before this task is done. Abbreviated here to keep this plan doc's Task 5 section a reasonable length; do not skip writing them out in full in the actual test file. `session_factory` is a fixture — mirror the one used in `tests/healer/test_detectors.py` for an in-memory SQLite `live_fleet_universe`/`live_trades` setup.)*

- [ ] **Step 6: Run the tests to verify they fail**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_live_fleet_universe.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 7: Write the implementation**

```python
# backend/app/shadow/live_fleet_universe.py
"""Phase 4 liquidity-floor selector -- addendum to
docs/superpowers/specs/2026-08-14-phase4-futures-signal-coverage-design.md
(2026-08-17). Replaces top-N-by-volume with liquidity-floor pass/fail
across the full market, with hysteresis. See the decision record at
docs/superpowers/decisions/2026-08-15-liquidity-floor-selector-supersedes-top20.md.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.futures_liquidity import QVOL_FLOOR_USDT, check_liquidity
from app.data.ratelimit import RateLimitedClient

log = logging.getLogger(__name__)

_FUTURES_BASE = "https://fapi.binance.com"
_SPOT_BASE = "https://api.binance.com"

N_SAMPLES: int = 5
SAMPLE_GAP_SECONDS: float = 10.0
ENTRY_MIN_PASSES: int = 3   # of N_SAMPLES
EXIT_MAX_PASSES: int = 0    # exit requires unanimous failure -- 0 passes of 5

Cohort = Literal["established_top20", "liquidity_added_spot", "futures_poll"]


@dataclass(frozen=True)
class LiveFleetEntry:
    symbol: str
    cohort: Cohort
    qvol_24h: float
    spread_bps: float
    depth_0_5pct_usdt: float


async def has_open_position(session: AsyncSession, symbol_pair: str) -> bool:
    """True if symbol_pair has an open live_trades OR shadow_open_positions
    row. Checked before ANY exit -- hard override, not best-effort."""
    row = (await session.execute(
        sa.text(
            "SELECT 1 FROM live_trades WHERE status = 'open' AND symbol = :s "
            "UNION ALL "
            "SELECT 1 FROM shadow_open_positions WHERE symbol = :s LIMIT 1"
        ),
        {"s": symbol_pair},
    )).first()
    return row is not None


async def load_live_fleet_universe(
    session: AsyncSession, *, cohort: str | None = None,
) -> list[LiveFleetEntry]:
    where_cohort = "AND cohort = :cohort " if cohort else ""
    params: dict = {"cohort": cohort} if cohort else {}
    rows = await session.execute(
        sa.text(
            "SELECT symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt "
            "FROM live_fleet_universe "
            "WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM live_fleet_universe) "
            + where_cohort
        ),
        params,
    )
    return [
        LiveFleetEntry(
            symbol=r.symbol, cohort=r.cohort, qvol_24h=r.qvol_24h,
            spread_bps=r.spread_bps, depth_0_5pct_usdt=r.depth_0_5pct_usdt,
        )
        for r in rows
    ]


async def refresh_live_fleet_universe(
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    rate_client: RateLimitedClient,
) -> list[LiveFleetEntry]:
    """The daily job. See module docstring for the design this implements."""
    futures_resp = await http.get(f"{_FUTURES_BASE}/fapi/v1/exchangeInfo")
    futures_resp.raise_for_status()
    fut_symbols = {
        s["symbol"] for s in futures_resp.json()["symbols"]
        if s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL"
        and s.get("status") == "TRADING"
    }

    spot_resp = await http.get(f"{_SPOT_BASE}/api/v3/exchangeInfo")
    spot_resp.raise_for_status()
    spot_symbols = {
        s["symbol"] for s in spot_resp.json()["symbols"]
        if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
    }
    futures_only = fut_symbols - spot_symbols

    ticker_resp = await http.get(f"{_FUTURES_BASE}/fapi/v1/ticker/24hr")
    ticker_resp.raise_for_status()
    qvol_by_symbol = {t["symbol"]: float(t["quoteVolume"]) for t in ticker_resp.json()}

    # Cheap pre-filter -- skip the 5x-sampled depth check entirely for
    # anything that already fails on volume alone (the large majority).
    candidates = sorted(s for s in fut_symbols if qvol_by_symbol.get(s, 0.0) >= QVOL_FLOOR_USDT)

    async with session_factory() as session:
        prior = {e.symbol: e for e in await load_live_fleet_universe(session)}

        # Cold-start seed: no prior snapshot at all -- consult today's
        # legacy top-20-by-volume fleet so pre-cutover coverage gets
        # tagged established_top20, not liquidity_added_spot.
        legacy_top20: set[str] = set()
        if not prior:
            from app.shadow.universe import load_current_universe
            from app.ws.keepalive import KEEPALIVE_TOP_N, to_pair
            try:
                legacy_entries = await load_current_universe(session)
                legacy_top20 = {
                    to_pair(e.symbol).replace("/", "")
                    for e in legacy_entries[:KEEPALIVE_TOP_N]
                }
            except Exception as e:  # noqa: BLE001 -- cold-start seed is best-effort
                log.warning("live_fleet_universe: legacy top-20 seed failed: %s", e)

    results: dict[str, LiveFleetEntry] = {}
    for sym in candidates:
        pass_count = 0
        last_check = None
        for i in range(N_SAMPLES):
            try:
                last_check = await check_liquidity(sym, rate_client)
                if last_check.passed:
                    pass_count += 1
            except Exception as e:  # noqa: BLE001
                log.warning("live_fleet_universe: check_liquidity failed for %s: %s", sym, e)
            if i < N_SAMPLES - 1:
                await asyncio.sleep(SAMPLE_GAP_SECONDS)
        if last_check is None:
            continue

        currently_in = sym in prior
        if currently_in:
            keep = pass_count > EXIT_MAX_PASSES  # anything but unanimous failure
        else:
            keep = pass_count >= ENTRY_MIN_PASSES

        if not keep:
            continue

        if currently_in:
            cohort: Cohort = prior[sym].cohort  # sticky -- inherited, never recomputed
        elif sym in legacy_top20:
            cohort = "established_top20"
        elif sym in futures_only:
            cohort = "futures_poll"
        else:
            cohort = "liquidity_added_spot"

        results[sym] = LiveFleetEntry(
            symbol=sym, cohort=cohort, qvol_24h=qvol_by_symbol[sym],
            spread_bps=last_check.spread_bps, depth_0_5pct_usdt=last_check.depth_0_5pct_usdt,
        )

    # Open-position override -- re-add anything that failed exit but has
    # an open position, even though the loop above already dropped it.
    async with session_factory() as session:
        for sym, entry in prior.items():
            if sym in results:
                continue
            from app.ws.keepalive import to_pair
            if await has_open_position(session, to_pair(sym)):
                log.info("live_fleet_universe: retaining %s past exit -- open position", sym)
                results[sym] = entry  # last-known-good liquidity numbers, not re-sampled

        now = datetime.now(timezone.utc)
        for entry in results.values():
            await session.execute(
                sa.text(
                    "INSERT INTO live_fleet_universe "
                    "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
                    "VALUES (:sym, :cohort, :qvol, :spread, :depth, :ts)"
                ),
                {"sym": entry.symbol, "cohort": entry.cohort, "qvol": entry.qvol_24h,
                 "spread": entry.spread_bps, "depth": entry.depth_0_5pct_usdt, "ts": now},
            )
        await session.commit()

    return list(results.values())


__all__ = [
    "N_SAMPLES", "SAMPLE_GAP_SECONDS", "ENTRY_MIN_PASSES", "EXIT_MAX_PASSES",
    "LiveFleetEntry", "has_open_position", "load_live_fleet_universe",
    "refresh_live_fleet_universe",
]
```

**Note on `EXIT_MAX_PASSES = 0`**: "unanimous 5-of-5 fail to exit" is equivalent to "keep unless pass_count is exactly 0" — written as `pass_count > EXIT_MAX_PASSES` so the threshold is a named constant, not a magic `0` buried in the conditional.

**Note on dwell time**: no separate dwell-timer state is needed. `refresh_live_fleet_universe` only runs once per day (same cadence as `asset_universe`'s existing refresh), so a symbol structurally cannot be re-evaluated for removal more than once every 24h — the ≥24h minimum dwell from addendum (a) is satisfied by the daily cadence itself, not by additional tracked state. If this refresh cadence ever changes to run more than once a day, this note stops being true and dwell needs its own explicit tracking — flag that explicitly if it comes up.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_live_fleet_universe.py tests/db/test_live_fleet_universe_migration.py -v`

- [ ] **Step 9: Commit**

```bash
git add backend/app/shadow/live_fleet_universe.py backend/alembic/versions/2026_08_17_0039_live_fleet_universe.py backend/tests/unit/test_live_fleet_universe.py backend/tests/db/test_live_fleet_universe_migration.py
git commit -m "feat(universe): add live_fleet_universe liquidity-floor selector with hysteresis (Phase 4 redraft)"
```

---

## Task 5b: Wire `ws_keepalive_task` onto the new selector

> **NEW TASK, not in the original 18-task list.** The original Phase 4 design's own Non-goals explicitly excluded touching `ws_keepalive_task`/`keepalive.py` ("No change to the existing spot-WS keepalive fleet's behavior for existing symbols"). The liquidity-floor-selector addendum supersedes that non-goal directly — the whole point is that the spot fleet's selection criterion changes. This task is the consequence of that, and without it Task 5's new selector has no consumer on the spot-backed side (only Task 8/futures_poll_task would read it, leaving keepalive.py silently still running the old top-20-by-volume logic — exactly "executing as drafted would build the wrong thing," just one level removed).

**Files:**
- Modify: `backend/app/ws/keepalive.py`
- Test: `backend/tests/ops/test_ws_keepalive.py` (existing — extend, don't break)

**Interfaces:**
- Consumes: `load_live_fleet_universe`, `has_open_position` (Task 5).
- Changes: `_load_keepalive_symbols` reads from `live_fleet_universe` (cohorts `established_top20` + `liquidity_added_spot`) instead of `asset_universe`/`load_current_universe`; drops the `top_n` slice entirely (the liquidity floor is now the only membership criterion — there is no separate rank cutoff on top of it). `_refresh_children` gains the open-position override before cancelling a dropped symbol.

- [ ] **Step 1: Read the existing `_load_keepalive_symbols` and `_refresh_children` in full before changing either**

Run: `grep -n "_load_keepalive_symbols\|_refresh_children" -A 30 backend/app/ws/keepalive.py`

- [ ] **Step 2: Update the failing/changed tests**

Existing tests in `test_ws_keepalive.py` that assert `_load_keepalive_symbols` reads `asset_universe`/respects `top_n` need updating to assert it now reads `live_fleet_universe` with no `top_n` — this is an intentional, pre-authorized behavior change per the addendum, not a regression to work around. Existing tests asserting `_refresh_children`'s unconditional-cancel-on-dropout need a new case added (not removed — the base behavior for a symbol with NO open position is unchanged) for the open-position override:

```python
@pytest.mark.asyncio
async def test_refresh_children_retains_dropped_symbol_with_open_position(monkeypatch) -> None:
    """A symbol no longer in the desired set, but with an open position,
    is NOT cancelled -- addendum (a)'s hard open-position override."""
    from app.ws import keepalive as kmod

    async def fake_has_open_position(session, symbol_pair: str) -> bool:
        return symbol_pair == "FOO/USDT"

    monkeypatch.setattr(kmod, "has_open_position", fake_has_open_position)

    async def fake_runner(symbol_pair: str, timeframe: str) -> None:
        await asyncio.sleep(3600)

    children: dict[tuple[str, str], asyncio.Task[None]] = {}
    await kmod._refresh_children(children, [("FOO/USDT", "1h")], runner=fake_runner)
    await asyncio.sleep(0)
    await kmod._refresh_children(children, [], runner=fake_runner)  # FOO drops out
    assert ("FOO/USDT", "1h") in children  # retained -- open position
    children[("FOO/USDT", "1h")].cancel()
```

- [ ] **Step 3: Write the implementation changes**

In `_load_keepalive_symbols`: replace the `load_current_universe(session)` call + `entries[:top_n]` slice with:

```python
from app.shadow.live_fleet_universe import load_live_fleet_universe

async def _load_keepalive_symbols(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    exclude: frozenset[tuple[str, str]],
    timeframe: str,
) -> list[tuple[str, str]]:
    """Read the liquidity-floor-qualified spot-backed cohort (both
    established_top20 and liquidity_added_spot), normalize, apply
    excludes. No top_n slice -- the floor IS the membership criterion."""
    try:
        async with session_factory() as session:
            established = await load_live_fleet_universe(session, cohort="established_top20")
            added = await load_live_fleet_universe(session, cohort="liquidity_added_spot")
    except Exception as e:  # noqa: BLE001
        log.warning("ws_keepalive: load_live_fleet_universe failed: %s", e)
        return []

    pairs: list[tuple[str, str]] = []
    for entry in established + added:
        pair = to_pair(entry.symbol)
        key = (pair, timeframe)
        if key in exclude:
            continue
        pairs.append(key)
    return pairs
```

`top_n` parameter removed from the function signature and from `run_keepalive`'s call site — `KEEPALIVE_TOP_N` stays defined in the module (other code/tests may still reference it as a historical constant) but is no longer read by the selection path. Do not delete it outright in this task; a later cleanup pass can remove it once nothing references it, per this project's own "don't leave a stale decision record" discipline — same reasoning as the SUPERSEDED banner on the 07-28 doc.

In `_refresh_children`, before the cancel:

```python
from app.shadow.live_fleet_universe import has_open_position

async def _refresh_children(
    children: dict[tuple[str, str], asyncio.Task[None]],
    desired: list[tuple[str, str]],
    *,
    runner: SymbolRunner,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    desired_set = set(desired)
    for key in list(children):
        if key not in desired_set:
            if session_factory is not None:
                symbol_pair, _tf = key
                async with session_factory() as session:
                    if await has_open_position(session, symbol_pair):
                        log.info("ws_keepalive: retaining %s/%s -- open position", *key)
                        continue
            log.info("ws_keepalive: dropping %s/%s", *key)
            children[key].cancel()
            try:
                await children[key]
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            del children[key]
    for key in desired:
        if key in children:
            continue
        symbol_pair, timeframe = key
        log.info("ws_keepalive: starting %s/%s", symbol_pair, timeframe)
        children[key] = asyncio.create_task(
            _run_child_with_restart(runner, symbol_pair, timeframe),
            name=f"keepalive:{symbol_pair}:{timeframe}",
        )
```

`session_factory` added as an optional kwarg (defaults `None`, preserving every existing test call site that doesn't pass it) rather than a required param — this keeps the function's existing signature-compatible for callers/tests that don't care about the override, while `run_keepalive`'s real call site always passes it.

- [ ] **Step 4: Run the full keepalive test suite**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/ops/test_ws_keepalive.py -v`
Expected: PASS, including the new open-position-retention test. **If any OTHER existing test in this file needs editing beyond the two intentional changes above (the `top_n`-removal assertions and the unconditional-cancel test), STOP and report** — per the standing rule, an unexpected required test edit means behavior changed somewhere not accounted for here.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ws/keepalive.py backend/tests/ops/test_ws_keepalive.py
git commit -m "feat(keepalive): switch to liquidity-floor selector + open-position override (Phase 4)"
```

---

## Task 6: Watermark persistence helpers

**Files:**
- Create: `backend/app/ws/futures_poll.py` (module home for all of Tasks 6-8)
- Test: `backend/tests/unit/test_futures_poll_watermark.py` (new)

**Interfaces:**
- Consumes: `sqlalchemy.ext.asyncio.async_sessionmaker`.
- Produces: `async def _load_watermark(session_factory, symbol: str, timeframe: str) -> int | None`, `async def _save_watermark(session_factory, symbol: str, timeframe: str, open_time: int) -> None`. Consumed by Task 7.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_futures_poll_watermark.py
from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ws.futures_poll import _load_watermark, _save_watermark

_CREATE_TABLE = (
    "CREATE TABLE live_prediction_watermarks ("
    "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
    "last_open_time INTEGER NOT NULL, "
    "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "PRIMARY KEY (symbol, timeframe))"
)


@pytest.fixture
async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_CREATE_TABLE))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_load_returns_none_when_never_seen(_session_factory) -> None:
    result = await _load_watermark(_session_factory, "SOL/USDT", "1h")
    assert result is None


@pytest.mark.asyncio
async def test_save_then_load_roundtrips(_session_factory) -> None:
    await _save_watermark(_session_factory, "SOL/USDT", "1h", 123456)
    result = await _load_watermark(_session_factory, "SOL/USDT", "1h")
    assert result == 123456


@pytest.mark.asyncio
async def test_save_twice_upserts_not_duplicates(_session_factory) -> None:
    await _save_watermark(_session_factory, "SOL/USDT", "1h", 100)
    await _save_watermark(_session_factory, "SOL/USDT", "1h", 200)
    result = await _load_watermark(_session_factory, "SOL/USDT", "1h")
    assert result == 200
    async with _session_factory() as session:
        count = (await session.execute(sa.text(
            "SELECT COUNT(*) AS n FROM live_prediction_watermarks "
            "WHERE symbol = 'SOL/USDT' AND timeframe = '1h'"
        ))).one()
    assert count.n == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_futures_poll_watermark.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ws.futures_poll'`.

- [ ] **Step 3: Write the implementation**

```python
# backend/app/ws/futures_poll.py
"""Phase 4 -- REST-polling supervisor for futures-only symbols.

Mirrors app.ws.keepalive's fleet-of-independent-children pattern, but
polls Binance Futures REST klines every ~60s instead of subscribing to
a WS stream (the geoblocked Futures WS is not usable from this host --
see [[binance_futures_ws_geoblock]]). Feeds the same run_live_prediction
entrypoint the spot-WS fleet uses, via the candle_source injection point
added in Phase 4 Step 0 -- scoring/gating/dispatch/persistence are
byte-identical between the two fleets; only candle delivery differs.

This module is a fully separate supervisor from ws_keepalive_task -- own
child-task set, own reconciliation loop -- so a bug anywhere in this
file cannot reach the spot-WS fleet's tasks (see the design spec's
"Isolation" section for the full argument).
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)


async def _load_watermark(
    session_factory: async_sessionmaker[AsyncSession], symbol: str, timeframe: str,
) -> int | None:
    async with session_factory() as session:
        row = (await session.execute(
            sa.text(
                "SELECT last_open_time FROM live_prediction_watermarks "
                "WHERE symbol = :symbol AND timeframe = :timeframe"
            ),
            {"symbol": symbol, "timeframe": timeframe},
        )).one_or_none()
    return int(row.last_open_time) if row is not None else None


async def _save_watermark(
    session_factory: async_sessionmaker[AsyncSession],
    symbol: str, timeframe: str, open_time: int,
) -> None:
    async with session_factory() as session:
        dialect = session.bind.dialect.name if session.bind else "postgresql"
        if dialect.startswith("postgres"):
            sql = (
                "INSERT INTO live_prediction_watermarks (symbol, timeframe, last_open_time, updated_at) "
                "VALUES (:symbol, :timeframe, :open_time, now()) "
                "ON CONFLICT (symbol, timeframe) DO UPDATE "
                "SET last_open_time = EXCLUDED.last_open_time, updated_at = now()"
            )
        else:
            sql = (
                "INSERT INTO live_prediction_watermarks (symbol, timeframe, last_open_time) "
                "VALUES (:symbol, :timeframe, :open_time) "
                "ON CONFLICT (symbol, timeframe) DO UPDATE "
                "SET last_open_time = excluded.last_open_time"
            )
        await session.execute(sa.text(sql), {
            "symbol": symbol, "timeframe": timeframe, "open_time": open_time,
        })
        await session.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_futures_poll_watermark.py -v`
Expected: PASS, all 3 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ws/futures_poll.py backend/tests/unit/test_futures_poll_watermark.py
git commit -m "feat(futures-poll): add watermark persistence helpers (Phase 4)"
```

---

## Task 7: `futures_rest_poll_candles` — the candle-source generator

**Files:**
- Modify: `backend/app/ws/futures_poll.py`
- Test: `backend/tests/unit/test_futures_poll_candles.py` (new)

**Interfaces:**
- Consumes: `_load_watermark`/`_save_watermark` (Task 6), `RateLimitedClient` (existing), `MultiStreamCandle` (existing).
- Produces: `async def futures_rest_poll_candles(symbol_pair: str, timeframe: str, *, rate_client: RateLimitedClient, session_factory, poll_interval_s: float = 60.0, _sleep=asyncio.sleep) -> AsyncIterator[MultiStreamCandle]`. Consumed by Task 8.

- [ ] **Step 1: Write the failing tests, including the required idempotency replay test**

```python
# backend/tests/unit/test_futures_poll_candles.py
from __future__ import annotations

import logging

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.ratelimit import RateLimitedClient, TokenBucket
from app.ws.futures_poll import (
    _CONSECUTIVE_FAILURE_ALERT_THRESHOLD,
    _clear_poll_failure_streaks_for_tests,
    futures_rest_poll_candles,
)

_CREATE_TABLE = (
    "CREATE TABLE live_prediction_watermarks ("
    "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
    "last_open_time INTEGER NOT NULL, "
    "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "PRIMARY KEY (symbol, timeframe))"
)


@pytest.fixture(autouse=True)
def _reset_streaks():
    _clear_poll_failure_streaks_for_tests()
    yield
    _clear_poll_failure_streaks_for_tests()


@pytest.fixture
async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_CREATE_TABLE))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _klines_response(rows: list[tuple[int, str]]) -> list[list]:
    # Binance kline row shape: [open_time, open, high, low, close, volume, close_time, ...]
    return [[ot, "100.0", "101.0", "99.0", "100.5", "10.0", ot + 3599999, "0", 0, "0", "0", "0"] for ot, _ in rows]


async def _sleep_once_then_stop(seconds: float) -> None:
    raise StopAsyncIteration  # breaks the poller's while-True after N iterations in tests


@pytest.mark.asyncio
async def test_yields_new_closed_candle_and_advances_watermark(_session_factory) -> None:
    call_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=_klines_response([(1000, "a"), (4600, "b")]))

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    rate_client = RateLimitedClient(
        exchange="binance_futures", http=http,
        buckets={"default": TokenBucket(capacity=2400.0, refill_per_sec=40.0)},
    )

    gen = futures_rest_poll_candles(
        "SOL/USDT", "1h", rate_client=rate_client, session_factory=_session_factory,
        poll_interval_s=0.0, _sleep=_sleep_once_then_stop,
    )
    candle = await gen.__anext__()
    assert candle.symbol == "SOLUSDT"
    assert candle.close == pytest.approx(100.5)

    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

    watermark = await _load_watermark(_session_factory, "SOL/USDT", "1h")
    assert watermark == 1000


@pytest.mark.asyncio
async def test_idempotency_replay_same_candle_not_reprocessed(_session_factory) -> None:
    """Required proof obligation: feed the same closed candle twice
    (simulating a restart or overlapping poll) and assert it is NOT
    re-yielded the second time."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_klines_response([(1000, "a"), (4600, "b")]))

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    rate_client = RateLimitedClient(
        exchange="binance_futures", http=http,
        buckets={"default": TokenBucket(capacity=2400.0, refill_per_sec=40.0)},
    )

    # First generator instance processes open_time=1000 and saves the watermark.
    gen1 = futures_rest_poll_candles(
        "SOL/USDT", "1h", rate_client=rate_client, session_factory=_session_factory,
        poll_interval_s=0.0, _sleep=_sleep_once_then_stop,
    )
    first = await gen1.__anext__()
    assert first is not None

    # A second generator instance (simulating a restart) seeds its watermark
    # from the SAME persisted table and must not re-yield open_time=1000.
    gen2 = futures_rest_poll_candles(
        "SOL/USDT", "1h", rate_client=rate_client, session_factory=_session_factory,
        poll_interval_s=0.0, _sleep=_sleep_once_then_stop,
    )
    with pytest.raises(StopAsyncIteration):
        await gen2.__anext__()  # no new candle -- watermark already at 1000


@pytest.mark.asyncio
async def test_gap_detected_and_logged_at_error(_session_factory, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.ws.futures_poll")
    from app.ws.futures_poll import _save_watermark
    await _save_watermark(_session_factory, "SOL/USDT", "1h", 1000)  # seed: last seen candle at t=1000

    def handler(req: httpx.Request) -> httpx.Response:
        # Jump straight to open_time=8200 -- skips the t=4600 candle (a gap).
        return httpx.Response(200, json=_klines_response([(8200, "x"), (11800, "y")]))

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    rate_client = RateLimitedClient(
        exchange="binance_futures", http=http,
        buckets={"default": TokenBucket(capacity=2400.0, refill_per_sec=40.0)},
    )
    gen = futures_rest_poll_candles(
        "SOL/USDT", "1h", rate_client=rate_client, session_factory=_session_factory,
        poll_interval_s=0.0, _sleep=_sleep_once_then_stop,
    )
    await gen.__anext__()  # still yields the newest closed candle (skip-forward)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records
    assert "gap" in error_records[-1].getMessage().lower()


@pytest.mark.asyncio
async def test_fetch_failure_logs_warning_not_debug(_session_factory, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.ws.futures_poll")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    rate_client = RateLimitedClient(
        exchange="binance_futures", http=http,
        buckets={"default": TokenBucket(capacity=2400.0, refill_per_sec=40.0)},
    )
    gen = futures_rest_poll_candles(
        "SOL/USDT", "1h", rate_client=rate_client, session_factory=_session_factory,
        poll_interval_s=0.0, _sleep=_sleep_once_then_stop,
    )
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records
    debug_only_swallows = [r for r in caplog.records if r.levelno == logging.DEBUG and "error" in r.getMessage().lower()]
    assert not debug_only_swallows


@pytest.mark.asyncio
async def test_systematic_failure_escalates_to_error(_session_factory, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.ws.futures_poll")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    rate_client = RateLimitedClient(
        exchange="binance_futures", http=http,
        buckets={"default": TokenBucket(capacity=2400.0, refill_per_sec=40.0)},
    )

    call_count = {"n": 0}

    async def _sleep_n_times_then_stop(seconds: float) -> None:
        call_count["n"] += 1
        if call_count["n"] >= _CONSECUTIVE_FAILURE_ALERT_THRESHOLD:
            raise StopAsyncIteration

    gen = futures_rest_poll_candles(
        "SOL/USDT", "1h", rate_client=rate_client, session_factory=_session_factory,
        poll_interval_s=0.0, _sleep=_sleep_n_times_then_stop,
    )
    with pytest.raises(StopAsyncIteration):
        async for _ in gen:
            pass
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records
    assert "consecutive" in error_records[-1].getMessage().lower()


@pytest.mark.asyncio
async def test_rate_limit_wait_logged_and_counted(_session_factory, caplog, monkeypatch) -> None:
    caplog.set_level(logging.WARNING, logger="app.ws.futures_poll")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_klines_response([(1000, "a"), (4600, "b")]))

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    rate_client = RateLimitedClient(
        exchange="binance_futures", http=http,
        buckets={"default": TokenBucket(capacity=2400.0, refill_per_sec=40.0)},
    )

    # Force the warning branch deterministically rather than trying to
    # simulate a real slow request: any wait_s > threshold triggers it,
    # so setting the threshold below zero makes even a near-instant
    # mocked-transport round-trip qualify.
    from app.ws import futures_poll as fp_mod
    monkeypatch.setattr(fp_mod, "_RATE_LIMIT_WAIT_LOG_THRESHOLD_S", -1.0)

    gen = futures_rest_poll_candles(
        "SOL/USDT", "1h", rate_client=rate_client, session_factory=_session_factory,
        poll_interval_s=0.0, _sleep=_sleep_once_then_stop,
    )
    await gen.__anext__()
    warning_records = [r for r in caplog.records if "rate-limit wait" in r.getMessage()]
    assert warning_records
    assert fp_mod._RATE_LIMIT_WAIT_COUNT["SOL/USDT"] >= 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_futures_poll_candles.py -v`
Expected: FAIL — `ImportError: cannot import name 'futures_rest_poll_candles'`.

- [ ] **Step 3: Write the implementation**

Append to `backend/app/ws/futures_poll.py`:

```python
import asyncio
import time
from collections.abc import AsyncIterator, Callable, Awaitable
from datetime import datetime, timezone

from app.data.ratelimit import RateLimitedClient
from app.shadow.multi_stream import MultiStreamCandle

_BASE_URL = "https://fapi.binance.com"

_INTERVAL_SECONDS_MS: dict[str, int] = {"1h": 3_600_000, "15m": 900_000}

_RATE_LIMIT_WAIT_LOG_THRESHOLD_S: float = 0.5
_RATE_LIMIT_WAIT_COUNT: dict[str, int] = {}
_GAP_COUNT: dict[str, int] = {}

_CONSECUTIVE_FAILURE_ALERT_THRESHOLD: int = 20
_consecutive_failures: dict[str, int] = {}


def _record_poll_result(symbol_pair: str, *, ok: bool) -> None:
    if ok:
        _consecutive_failures[symbol_pair] = 0
        return
    _consecutive_failures[symbol_pair] = _consecutive_failures.get(symbol_pair, 0) + 1
    streak = _consecutive_failures[symbol_pair]
    if streak >= _CONSECUTIVE_FAILURE_ALERT_THRESHOLD:
        log.error(
            "futures_poller: %s has failed %d consecutive polls -- "
            "this symbol's poller looks broken, not a one-off network blip",
            symbol_pair, streak,
        )


def _clear_poll_failure_streaks_for_tests() -> None:
    _consecutive_failures.clear()
    _RATE_LIMIT_WAIT_COUNT.clear()
    _GAP_COUNT.clear()


def _to_multistream_candle(symbol_pair: str, timeframe: str, row: list) -> MultiStreamCandle:
    open_time_ms = int(row[0])
    return MultiStreamCandle(
        symbol=symbol_pair.replace("/", ""), timeframe=timeframe,
        ts=datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc),
        open=float(row[1]), high=float(row[2]), low=float(row[3]),
        close=float(row[4]), volume=float(row[5]),
    )


async def futures_rest_poll_candles(
    symbol_pair: str,
    timeframe: str,
    *,
    rate_client: RateLimitedClient,
    session_factory,
    poll_interval_s: float = 60.0,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[MultiStreamCandle]:
    """REST-poll Binance Futures klines every ~poll_interval_s, yielding
    only newly-closed candles (open-time advancing past the last one
    processed -- never wall-clock). At-most-once per (symbol, timeframe,
    open_time) via the persisted watermark table; skip-forward on a gap
    (never backfills); WARNING on every fetch failure, ERROR escalation
    on a systematic (consecutive) streak.
    """
    binance_symbol = symbol_pair.replace("/", "")
    interval_ms = _INTERVAL_SECONDS_MS[timeframe]
    watermark = await _load_watermark(session_factory, symbol_pair, timeframe)

    while True:
        t0 = time.monotonic()
        try:
            resp = await rate_client.request(
                "GET", f"{_BASE_URL}/fapi/v1/klines",
                endpoint_key="klines",
                params={"symbol": binance_symbol, "interval": timeframe, "limit": "2"},
                timeout=10.0,
            )
            resp.raise_for_status()
            rows = resp.json()
            _record_poll_result(symbol_pair, ok=True)
        except Exception as e:  # noqa: BLE001
            log.warning("futures_poller: fetch failed for %s/%s: %s", symbol_pair, timeframe, e)
            _record_poll_result(symbol_pair, ok=False)
            await _sleep(poll_interval_s)
            continue

        wait_s = time.monotonic() - t0
        if wait_s > _RATE_LIMIT_WAIT_LOG_THRESHOLD_S:
            log.warning(
                "futures_poller: rate-limit wait %.2fs for %s/%s",
                wait_s, symbol_pair, timeframe,
            )
            _RATE_LIMIT_WAIT_COUNT[symbol_pair] = _RATE_LIMIT_WAIT_COUNT.get(symbol_pair, 0) + 1

        if len(rows) >= 2:
            closed_row = rows[-2]
            closed_open_time = int(closed_row[0])

            if watermark is None or closed_open_time > watermark:
                if watermark is not None:
                    expected_next = watermark + interval_ms
                    if closed_open_time > expected_next:
                        gap = (closed_open_time - expected_next) // interval_ms
                        log.error(
                            "futures_poller: gap detected %s/%s, skipped ~%d candle(s)",
                            symbol_pair, timeframe, gap,
                        )
                        _GAP_COUNT[symbol_pair] = _GAP_COUNT.get(symbol_pair, 0) + 1

                candle = _to_multistream_candle(symbol_pair, timeframe, closed_row)
                yield candle
                # Resumed only after the consumer has fully finished
                # processing `candle` -- async-generator semantics
                # guarantee the watermark never advances past a candle
                # that wasn't actually processed end-to-end.
                watermark = closed_open_time
                await _save_watermark(session_factory, symbol_pair, timeframe, watermark)

        await _sleep(poll_interval_s)
```

Add `log = logging.getLogger(__name__)` near the top of the file if not already present from Task 6.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_futures_poll_candles.py -v`
Expected: PASS, all 7 tests including the idempotency replay test.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ws/futures_poll.py backend/tests/unit/test_futures_poll_candles.py
git commit -m "feat(futures-poll): add futures_rest_poll_candles generator (Phase 4)"
```

---

## Task 8: `futures_poll_task` supervisor

> **REDRAFTED 2026-08-17.** Three changes from the original draft, all consequences of the liquidity-floor-selector addendum: (1) consumes the shared `load_live_fleet_universe` (Task 5) filtered to the `futures_poll` cohort, not the old rank-capped `fetch_top_n_usdt_futures_only`; (2) drops the fixed `FUTURES_POLL_TOP_N=8` as the *selection* mechanism — the floor itself determines how many symbols qualify (currently 8-11, per the decision record's measurement), no artificial cap on top of that; (3) gains the same hard open-position override as Task 5b — the original draft's `test_cancels_child_when_symbol_drops_out_matching_spot_fleet_behavior` explicitly asserted the OPPOSITE of what the addendum now requires ("NOT a retention test, since retention isn't what's being built" — it is now being built, deliberately, per the operator's explicit instruction). That test is rewritten, not silently left contradicting the addendum.

**Files:**
- Modify: `backend/app/ws/futures_poll.py`
- Test: `backend/tests/ops/test_futures_poll_task.py` (new)

**Interfaces:**
- Consumes: `futures_rest_poll_candles` (Task 7), `load_live_fleet_universe`, `has_open_position` (Task 5), `run_live_prediction` (Task 1), `record_heartbeat` (existing, `app.ops.heartbeat`).
- Produces: `WORKER_NAME = "futures_poll_task"`, `FUTURES_POLL_SAFETY_MAX_N: int = 30` (safety valve only, not the selector — see below), `async def run_futures_poll(session_factory, *, timeframe=FUTURES_POLL_TIMEFRAME, ...) -> None`, `def start_futures_poll_task(session_factory) -> asyncio.Task[None]`. Consumed by Task 17 (`main.py` wiring).

**On the safety valve**: the addendum's cost-check (section (b)) recommends a staging `docker stats` gate before trusting N≈42 in prod, with an explicit fallback — "if it doesn't fit, drop to the largest workable N ranked by liquidity, never volume" — if that fallback is ever needed. `FUTURES_POLL_SAFETY_MAX_N` is that fallback's mechanism: a hard ceiling that, if the floor-qualified futures-only cohort ever exceeds it, truncates to the top N **by liquidity rank** (best `qvol_24h`, tie-broken by tightest `spread_bps`, then highest `depth_0_5pct_usdt` — the priority order the addendum specifies, never volume alone). At today's measured 8-11 futures-only qualifiers this never engages; it exists so a market-condition shift that suddenly qualifies many more symbols doesn't silently blow past whatever the staging soak validated.

- [ ] **Step 1: Read `app/ws/keepalive.py`'s (post-Task-5b) `_run_child_with_restart`, `_refresh_children`, `run_keepalive` in full to mirror the structure exactly, including the open-position override added in Task 5b**

Run: `cat backend/app/ws/keepalive.py`

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/ops/test_futures_poll_task.py
"""Mirrors tests/ops/test_ws_keepalive.py's (post-Task-5b) structure --
same fleet pattern, same isolation guarantees, same open-position
override, different candle source."""
from __future__ import annotations

import asyncio

import pytest

from app.ws.futures_poll import _refresh_futures_children, run_futures_poll


@pytest.mark.asyncio
async def test_spawns_a_child_per_desired_symbol() -> None:
    spawned: list[tuple[str, str]] = []

    async def fake_runner(symbol_pair: str, timeframe: str) -> None:
        spawned.append((symbol_pair, timeframe))
        await asyncio.sleep(3600)  # never returns on its own

    children: dict[tuple[str, str], asyncio.Task[None]] = {}
    await _refresh_futures_children(
        children, [("FOO/USDT", "1h"), ("BAR/USDT", "1h")], runner=fake_runner,
    )
    await asyncio.sleep(0)  # let the spawned tasks start
    assert set(children.keys()) == {("FOO/USDT", "1h"), ("BAR/USDT", "1h")}
    for task in children.values():
        task.cancel()


@pytest.mark.asyncio
async def test_cancels_child_when_symbol_drops_out_and_no_open_position() -> None:
    """REPLACES the original draft's 'unconditional cancel, retention
    isn't being built' test -- retention IS being built now (addendum
    (a), hard requirement). Base case unchanged: no open position means
    the drop-out still cancels exactly as before."""
    async def fake_runner(symbol_pair: str, timeframe: str) -> None:
        await asyncio.sleep(3600)

    children: dict[tuple[str, str], asyncio.Task[None]] = {}
    await _refresh_futures_children(children, [("FOO/USDT", "1h")], runner=fake_runner)
    await asyncio.sleep(0)
    assert ("FOO/USDT", "1h") in children

    await _refresh_futures_children(children, [], runner=fake_runner)  # FOO drops out
    assert ("FOO/USDT", "1h") not in children


@pytest.mark.asyncio
async def test_retains_dropped_child_with_open_position(monkeypatch) -> None:
    """NEW -- addendum (a)'s hard open-position override, futures-poll side."""
    from app.ws import futures_poll as fmod

    async def fake_has_open_position(session, symbol_pair: str) -> bool:
        return symbol_pair == "FOO/USDT"

    monkeypatch.setattr(fmod, "has_open_position", fake_has_open_position)

    async def fake_runner(symbol_pair: str, timeframe: str) -> None:
        await asyncio.sleep(3600)

    children: dict[tuple[str, str], asyncio.Task[None]] = {}
    await _refresh_futures_children(
        children, [("FOO/USDT", "1h")], runner=fake_runner, session_factory=object(),
    )
    await asyncio.sleep(0)
    await _refresh_futures_children(children, [], runner=fake_runner, session_factory=object())
    assert ("FOO/USDT", "1h") in children  # retained
    children[("FOO/USDT", "1h")].cancel()


@pytest.mark.asyncio
async def test_child_crash_does_not_take_down_siblings() -> None:
    from app.ws.futures_poll import _run_futures_child_with_restart

    calls: dict[str, int] = {"FOO/USDT": 0, "BAR/USDT": 0}

    async def flaky_runner(symbol_pair: str, timeframe: str) -> None:
        calls[symbol_pair] += 1
        if symbol_pair == "FOO/USDT" and calls[symbol_pair] == 1:
            raise RuntimeError("simulated crash")
        await asyncio.sleep(3600)

    task_foo = asyncio.create_task(
        _run_futures_child_with_restart(flaky_runner, "FOO/USDT", "1h", backoff_base_s=0.01),
    )
    task_bar = asyncio.create_task(
        _run_futures_child_with_restart(flaky_runner, "BAR/USDT", "1h", backoff_base_s=0.01),
    )
    await asyncio.sleep(0.1)
    assert calls["FOO/USDT"] >= 2  # crashed once, restarted
    assert calls["BAR/USDT"] >= 1  # never affected by FOO's crash
    task_foo.cancel()
    task_bar.cancel()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/ops/test_futures_poll_task.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 4: Write the implementation**

Append to `backend/app/ws/futures_poll.py` (structure deliberately mirrors the post-Task-5b `keepalive.py`'s `_run_child_with_restart`/`_refresh_children`/`run_keepalive`, including the open-position override):

```python
from collections.abc import Callable as _Callable

from app.ops.heartbeat import record_heartbeat
from app.shadow.live_fleet_universe import has_open_position, load_live_fleet_universe
from app.ws.live_prediction import run_live_prediction

WORKER_NAME: str = "futures_poll_task"
FUTURES_POLL_SAFETY_MAX_N: int = 30  # safety valve, not the selector -- see plan doc
FUTURES_POLL_TIMEFRAME: str = "1h"
FUTURES_POLL_REFRESH_SECONDS: int = 24 * 60 * 60
FUTURES_POLL_HEARTBEAT_SECONDS: int = 5 * 60
_CHILD_BACKOFF_BASE_S: float = 5.0
_CHILD_BACKOFF_MAX_S: float = 120.0

FuturesRunner = _Callable[[str, str], "asyncio.Future"]


async def _default_futures_runner(symbol_pair: str, timeframe: str) -> None:
    """Production runner: reuses run_live_prediction with an injected
    REST-poll candle source -- the entire point of Phase 4 Step 0."""
    from app.data.adapters import get_intermarket_adapter
    from app.db.session import get_session_factory

    session_factory = get_session_factory()
    rate_client = get_intermarket_adapter().rate_client
    assert rate_client is not None
    source = futures_rest_poll_candles(
        symbol_pair, timeframe, rate_client=rate_client, session_factory=session_factory,
    )
    await run_live_prediction(
        symbol_pair=symbol_pair, timeframe=timeframe,
        candle_source=source, symbol_source="futures_poll",
    )


async def _run_futures_child_with_restart(
    runner, symbol_pair: str, timeframe: str, *, backoff_base_s: float = _CHILD_BACKOFF_BASE_S,
) -> None:
    backoff = backoff_base_s
    while True:
        try:
            await runner(symbol_pair, timeframe)
            backoff = backoff_base_s
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning(
                "futures_poll child %s/%s crashed: %s; restart in %.1fs",
                symbol_pair, timeframe, e, backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(_CHILD_BACKOFF_MAX_S, backoff * 2)


async def _refresh_futures_children(
    children: dict[tuple[str, str], asyncio.Task[None]],
    desired: list[tuple[str, str]],
    *,
    runner,
    session_factory=None,
) -> None:
    """Reconcile running children with the desired set. Mirrors the
    post-Task-5b keepalive.py's _refresh_children exactly, INCLUDING the
    open-position override -- addendum (a) is a hard requirement on both
    fleets, not just the spot-WS one. session_factory optional (defaults
    None) so existing no-DB tests keep working unchanged; the real
    supervisor call site always passes it."""
    desired_set = set(desired)
    for key in list(children):
        if key not in desired_set:
            if session_factory is not None:
                symbol_pair, _tf = key
                async with session_factory() as session:
                    if await has_open_position(session, symbol_pair):
                        log.info("futures_poll: retaining %s/%s -- open position", *key)
                        continue
            log.info("futures_poll: dropping %s/%s", *key)
            children[key].cancel()
            try:
                await children[key]
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            del children[key]
    for key in desired:
        if key in children:
            continue
        symbol_pair, timeframe = key
        log.info("futures_poll: starting %s/%s", symbol_pair, timeframe)
        children[key] = asyncio.create_task(
            _run_futures_child_with_restart(runner, symbol_pair, timeframe),
            name=f"futures_poll:{symbol_pair}:{timeframe}",
        )


async def run_futures_poll(
    session_factory,
    *,
    timeframe: str = FUTURES_POLL_TIMEFRAME,
    refresh_seconds: int = FUTURES_POLL_REFRESH_SECONDS,
    heartbeat_seconds: int = FUTURES_POLL_HEARTBEAT_SECONDS,
    runner=_default_futures_runner,
) -> None:
    """Main supervisor loop -- structurally identical to run_keepalive
    (app.ws.keepalive), owning a completely separate child-task set so
    nothing here can reach the spot-WS fleet's tasks."""
    log.info("futures_poll: starting (tf=%s)", timeframe)
    children: dict[tuple[str, str], asyncio.Task[None]] = {}
    try:
        desired = await _load_desired_futures_symbols(session_factory, timeframe=timeframe)
        await _refresh_futures_children(children, desired, runner=runner, session_factory=session_factory)
        await record_heartbeat(
            session_factory, WORKER_NAME, status="ok",
            details={"children": len(children), "timeframe": timeframe,
                      "gap_counts": dict(_GAP_COUNT), "rate_limit_waits": dict(_RATE_LIMIT_WAIT_COUNT)},
        )
        last_refresh = 0.0
        loop = asyncio.get_event_loop()
        while True:
            await asyncio.sleep(heartbeat_seconds)
            now = loop.time()
            if now - last_refresh >= refresh_seconds:
                desired = await _load_desired_futures_symbols(session_factory, timeframe=timeframe)
                if desired:
                    await _refresh_futures_children(children, desired, runner=runner, session_factory=session_factory)
                    last_refresh = now
            await record_heartbeat(
                session_factory, WORKER_NAME, status="ok",
                details={"children": len(children), "timeframe": timeframe,
                          "gap_counts": dict(_GAP_COUNT), "rate_limit_waits": dict(_RATE_LIMIT_WAIT_COUNT)},
            )
    finally:
        for task in children.values():
            task.cancel()
        for task in children.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


async def _load_desired_futures_symbols(
    session_factory, *, timeframe: str,
) -> list[tuple[str, str]]:
    """No top_n -- the liquidity floor (Task 5) is the selector. The
    FUTURES_POLL_SAFETY_MAX_N truncation below is a safety valve, only
    engages if the qualifying cohort ever exceeds what a staging soak
    validated -- ranked by liquidity (qvol desc, then spread asc, then
    depth desc), never by volume alone, per the addendum's fallback."""
    from app.ws.keepalive import to_pair

    try:
        async with session_factory() as session:
            entries = await load_live_fleet_universe(session, cohort="futures_poll")
    except Exception as e:  # noqa: BLE001
        log.warning("futures_poll: load_live_fleet_universe failed: %s", e)
        return []

    if len(entries) > FUTURES_POLL_SAFETY_MAX_N:
        log.warning(
            "futures_poll: %d qualifying symbols exceeds safety valve %d -- "
            "truncating by liquidity rank, this should be investigated "
            "(staging soak may not cover this scale)",
            len(entries), FUTURES_POLL_SAFETY_MAX_N,
        )
        entries = sorted(
            entries, key=lambda e: (-e.qvol_24h, e.spread_bps, -e.depth_0_5pct_usdt),
        )[:FUTURES_POLL_SAFETY_MAX_N]

    return [(to_pair(e.symbol), timeframe) for e in entries]


def start_futures_poll_task(session_factory) -> asyncio.Task[None]:
    return asyncio.create_task(run_futures_poll(session_factory))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/ops/test_futures_poll_task.py -v`
Expected: PASS, all 4 tests.

- [ ] **Step 6: Run the full `futures_poll.py`-related test suite together**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_futures_poll_watermark.py tests/unit/test_futures_poll_candles.py tests/ops/test_futures_poll_task.py -v`
Expected: PASS, all tests from Tasks 6-8.

- [ ] **Step 7: Commit**

```bash
git add backend/app/ws/futures_poll.py backend/tests/ops/test_futures_poll_task.py
git commit -m "feat(futures-poll): add futures_poll_task supervisor, liquidity-floor selector + open-position override (Phase 4 redraft)"
```

---

## Task 9: Thread `symbol_source` through persist + dispatch (+ shadow)

> **Redrafted 2026-08-17.** Two corrections from the original draft: (1) the default value changes from `'spot_ws'` to `'established_top20'` throughout (matches Task 3's migration column default — a call site that doesn't explicitly pass a cohort, e.g. the `live_worker` BTC singleton, is legacy-established coverage, not a new transport-named default that no longer exists in the three-way scheme); `'futures_poll'` is unchanged, still spelled the same. (2) `shadow_trades.symbol_source` must also be populated — added as a new step below, not covered by the original draft's file list at all (predictions/telegram_signals/live_trades only). Without it, the addendum's reversal criterion #2 (re-measuring the new cohort's EV via a `shadow_trades` split) has nothing to read.
>
> **Open detail for the implementer, not fully resolved here**: `ws_keepalive_task`'s desired-symbol list (post-Task-5b) is `list[tuple[symbol_pair, timeframe]]` — it does not currently carry which of the two spot cohorts (`established_top20` vs `liquidity_added_spot`) each symbol belongs to. The runner that eventually calls `run_live_prediction(..., symbol_source=?)` needs that per-symbol answer, not a single fixed default, since keepalive.py now serves BOTH spot cohorts from one fleet. Resolve by either (a) widening the desired-set tuples to `(symbol_pair, timeframe, cohort)` and threading cohort through `_refresh_children`/the runner closure, or (b) having the runner look up the symbol's current cohort from `load_live_fleet_universe` at call time. Pick one, note the choice in the commit, and add a test asserting a `liquidity_added_spot` symbol's `predictions` row is actually tagged `liquidity_added_spot`, not silently defaulted to `established_top20` — that silent-default failure mode is exactly the kind of thing this task exists to prevent.

**Files:**
- Modify: `backend/app/ws/live_prediction.py` (the `async for candle in source:` body, and `_persist_prediction_and_schedule_validation`)
- Modify: `backend/app/db/payload_builders.py::build_predictions_payload`
- Modify: wherever `shadow_trades` rows are written on close (shadow worker's position-close path — grep for the existing `INSERT INTO shadow_trades` / ORM insert site before assuming a specific file)
- Test: `backend/tests/unit/test_live_prediction_symbol_source.py` (new)

**Interfaces:**
- Consumes: `symbol_source` parameter added in Task 1.
- Produces: `predictions.symbol_source`, `shadow_trades.symbol_source`, `telegram_signals.symbol_source`, `live_trades.symbol_source` populated correctly on every write path.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_live_prediction_symbol_source.py
from __future__ import annotations

from app.db.payload_builders import build_predictions_payload


class _StubFinal:
    score = 0.5
    direction = "LONG"
    confidence = 70.0


class _StubPred:
    symbol = "FOO/USDT"
    timeframe = "1h"
    ts = None
    final = _StubFinal()
    inputs_hash = "a" * 64
    cold_start = False


def test_build_predictions_payload_includes_symbol_source_default() -> None:
    payload = build_predictions_payload(
        _StubPred(), user_id=1, layer_payload={},
    )
    assert payload["symbol_source"] == "established_top20"


def test_build_predictions_payload_threads_futures_poll() -> None:
    payload = build_predictions_payload(
        _StubPred(), user_id=1, layer_payload={}, symbol_source="futures_poll",
    )
    assert payload["symbol_source"] == "futures_poll"
```

Also add, in the same test file, the dispatch-side threading test (this is the one that proves `telegram_signals`/`live_trades` end up tagged, since those are written from inside `_maybe_dispatch` → `dispatch_if_eligible`, not from the predictions-persist path above):

```python
import pytest
from unittest.mock import AsyncMock, patch

from app.ws.live_prediction import _maybe_dispatch


class _StubTradeSetup:
    entry = 100.0
    stop_loss = 95.0
    take_profit = 110.0


class _StubDispatchPred:
    symbol = "FOO/USDT"
    timeframe = "1h"
    trade_setup = _StubTradeSetup()
    final = _StubFinal()
    inputs_hash = "a" * 64
    mtf_agreement = None
    mtf_dominant_tf = None
    mtf_directions_json = None
    funding_rate_daily = 0.0
    mtf_adx_by_tf_json = None


@pytest.mark.asyncio
async def test_maybe_dispatch_threads_symbol_source_into_proposal_kwargs() -> None:
    with patch("app.ws.live_prediction.vault_keys", return_value="fake-key"), \
         patch("app.ws.live_prediction.dispatch_if_eligible", new_callable=AsyncMock) as mock_dispatch, \
         patch("app.ws.live_prediction.get_settings"):
        mock_dispatch.return_value = None
        await _maybe_dispatch(
            AsyncMock(), pred=_StubDispatchPred(), layer_payload={},
            symbol_source="futures_poll",
        )
    _, kwargs = mock_dispatch.call_args
    assert kwargs["proposal_kwargs"]["symbol_source"] == "futures_poll"


@pytest.mark.asyncio
async def test_maybe_dispatch_defaults_symbol_source_to_established_top20() -> None:
    with patch("app.ws.live_prediction.vault_keys", return_value="fake-key"), \
         patch("app.ws.live_prediction.dispatch_if_eligible", new_callable=AsyncMock) as mock_dispatch, \
         patch("app.ws.live_prediction.get_settings"):
        mock_dispatch.return_value = None
        await _maybe_dispatch(AsyncMock(), pred=_StubDispatchPred(), layer_payload={})
    _, kwargs = mock_dispatch.call_args
    assert kwargs["proposal_kwargs"]["symbol_source"] == "established_top20"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_live_prediction_symbol_source.py -v`
Expected: FAIL — `KeyError: 'symbol_source'` on the payload tests, `TypeError: _maybe_dispatch() got an unexpected keyword argument 'symbol_source'` on the dispatch tests.

- [ ] **Step 3: Update `build_predictions_payload`**

In `backend/app/db/payload_builders.py`, add a `symbol_source: str = "established_top20"` keyword parameter to `build_predictions_payload` and add `"symbol_source": symbol_source` to the returned `result` dict, next to the existing `"model_version"` key.

- [ ] **Step 4: Add `symbol_source` to `_maybe_dispatch`'s signature and thread it into `proposal_kwargs`**

Run `grep -n "def _maybe_dispatch\|proposal_kwargs" backend/app/ws/live_prediction.py` first to confirm the exact call shape.

In `backend/app/ws/live_prediction.py`, add `symbol_source: str = "established_top20"` to `_maybe_dispatch`'s signature (this is the **only** task that adds this parameter — Task 10 later adds new *behavior* inside this already-parameterized function, it does not re-add the parameter). Add `"symbol_source": symbol_source` to the `proposal_kwargs` dict passed to `dispatch_if_eligible`.

- [ ] **Step 5: Thread `symbol_source` through the two call sites inside `run_live_prediction`'s body**

Inside the `async for candle in source:` loop: update the `build_predictions_payload(...)` call to pass `symbol_source=symbol_source`, and update the `_maybe_dispatch(...)` call to pass `symbol_source=symbol_source`.

- [ ] **Step 6: Thread `symbol_source` into the `shadow_trades` write path**

Grep for the actual insert site first — `grep -rn "INSERT INTO shadow_trades\|class ShadowTrade\b" backend/app` — do not assume a specific file/ORM-vs-raw-SQL shape without checking. Add `symbol_source: str = "established_top20"` to whatever function persists a closed shadow trade, threaded from the same value `run_live_prediction`/the shadow worker already has for that symbol at open-time (per the addendum's open dependency question, this only produces real `liquidity_added_spot`/`futures_poll` rows for symbols shadow actually tracks — verify against that open question before assuming full coverage here).

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_live_prediction_symbol_source.py -v`
Expected: PASS, all 4 tests, plus whatever new shadow_trades-side test Step 6 requires.

- [ ] **Step 8: Run the Task 1 guard tests plus the broader live_prediction suite to confirm no regression**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_live_prediction_candle_source.py tests/integration/test_live_prediction_validator_isolation.py tests/unit/test_live_prediction_dispatch_hook.py -v`
Expected: PASS, all.

- [ ] **Step 9: Commit**

```bash
git add backend/app/db/payload_builders.py backend/app/ws/live_prediction.py backend/tests/unit/test_live_prediction_symbol_source.py
git commit -m "feat(phase4): thread symbol_source cohort tag through persist + dispatch + shadow"
```

---

## Task 10: Dispatch-time liquidity re-check + card suppression

**Files:**
- Modify: `backend/app/ws/live_prediction.py::_maybe_dispatch`
- Test: `backend/tests/unit/test_dispatch_liquidity_recheck.py` (new)

**Interfaces:**
- Consumes: `check_liquidity` (Task 4).
- Produces: dispatch-time suppression for futures-only-cohort signals that fail a fresh liquidity check; spot-backed signals (`symbol_source == "spot_ws"`) are untouched by this check entirely.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_dispatch_liquidity_recheck.py
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.ws.live_prediction import _maybe_dispatch


class _StubTradeSetup:
    entry = 100.0
    stop_loss = 95.0
    take_profit = 110.0


class _StubFinal:
    direction = "LONG"
    score = 0.5
    confidence = 70.0


class _StubPred:
    symbol = "FOO/USDT"
    timeframe = "1h"
    trade_setup = _StubTradeSetup()
    final = _StubFinal()
    inputs_hash = "a" * 64
    mtf_agreement = None
    mtf_dominant_tf = None
    mtf_directions_json = None
    funding_rate_daily = 0.0
    mtf_adx_by_tf_json = None


@pytest.mark.asyncio
async def test_spot_signals_skip_liquidity_recheck_entirely() -> None:
    with patch("app.ws.live_prediction.vault_keys", return_value="fake-key"), \
         patch("app.ws.live_prediction.check_liquidity", new_callable=AsyncMock) as mock_check, \
         patch("app.ws.live_prediction.dispatch_if_eligible", new_callable=AsyncMock, return_value=None), \
         patch("app.ws.live_prediction.get_settings"):
        await _maybe_dispatch(
            AsyncMock(), pred=_StubPred(), layer_payload={}, symbol_source="spot_ws",
        )
    mock_check.assert_not_called()


@pytest.mark.asyncio
async def test_futures_poll_signal_dispatches_when_liquidity_passes() -> None:
    from app.data.futures_liquidity import LiquidityCheck

    with patch("app.ws.live_prediction.vault_keys", return_value="fake-key"), \
         patch("app.ws.live_prediction.check_liquidity", new_callable=AsyncMock) as mock_check, \
         patch("app.ws.live_prediction.dispatch_if_eligible", new_callable=AsyncMock, return_value=None) as mock_dispatch, \
         patch("app.ws.live_prediction.get_settings"):
        mock_check.return_value = LiquidityCheck(
            passed=True, qvol_24h=25_000_000.0, spread_bps=2.0, depth_0_5pct_usdt=100_000.0,
        )
        await _maybe_dispatch(
            AsyncMock(), pred=_StubPred(), layer_payload={}, symbol_source="futures_poll",
        )
    mock_dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_futures_poll_signal_suppressed_when_liquidity_fails(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="app.ws.live_prediction")
    from app.data.futures_liquidity import LiquidityCheck

    with patch("app.ws.live_prediction.vault_keys", return_value="fake-key"), \
         patch("app.ws.live_prediction.check_liquidity", new_callable=AsyncMock) as mock_check, \
         patch("app.ws.live_prediction.dispatch_if_eligible", new_callable=AsyncMock) as mock_dispatch, \
         patch("app.ws.live_prediction.get_settings"):
        mock_check.return_value = LiquidityCheck(
            passed=False, qvol_24h=1_000_000.0, spread_bps=10.0, depth_0_5pct_usdt=5_000.0,
        )
        await _maybe_dispatch(
            AsyncMock(), pred=_StubPred(), layer_payload={}, symbol_source="futures_poll",
        )
    mock_dispatch.assert_not_called()
    assert any("liquidity" in r.getMessage().lower() for r in caplog.records)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_dispatch_liquidity_recheck.py -v`
Expected: FAIL — `AttributeError: <module 'app.ws.live_prediction'> does not have the attribute 'check_liquidity'`. `symbol_source` itself is already a valid parameter on `_maybe_dispatch` (added in Task 9); this task adds new behavior inside that already-parameterized function, not the parameter itself. The tests fail because `check_liquidity` isn't imported into `live_prediction.py` yet, so `patch("app.ws.live_prediction.check_liquidity")` has nothing to patch.

- [ ] **Step 3: Add the module-level imports `_maybe_dispatch` needs**

At the top of `backend/app/ws/live_prediction.py`, add:

```python
from app.data.adapters import get_intermarket_adapter
from app.data.futures_liquidity import check_liquidity
```

Module-level, not a local import inside the function — the tests patch `app.ws.live_prediction.check_liquidity`/`app.ws.live_prediction.get_intermarket_adapter`, which only works if these names are bound in this module's namespace at import time.

- [ ] **Step 4: Add the liquidity re-check to `_maybe_dispatch`**

At the top of `_maybe_dispatch`'s body, after the existing `vault_keys()`/`trade_setup` guard checks and before `dispatch_if_eligible` is called, add:

```python
    if symbol_source == "futures_poll":
        try:
            rate_client = get_intermarket_adapter().rate_client
            assert rate_client is not None
            check = await check_liquidity(pred.symbol.replace("/", ""), rate_client)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "dispatch-time liquidity re-check failed for %s, suppressing: %s",
                pred.symbol, e,
            )
            return
        if not check.passed:
            log.warning(
                "dispatch-time liquidity re-check failed for %s "
                "(qvol=%.0f spread=%.1fbps depth=%.0f) -- suppressing card",
                pred.symbol, check.qvol_24h, check.spread_bps, check.depth_0_5pct_usdt,
            )
            return
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_dispatch_liquidity_recheck.py -v`
Expected: PASS, all 3 tests.

- [ ] **Step 6: Run the full live_prediction test suite for regression**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_live_prediction_dispatch_hook.py tests/unit/test_live_prediction_candle_source.py tests/unit/test_live_prediction_symbol_source.py tests/unit/test_dispatch_liquidity_recheck.py -v`
Expected: PASS, all.

- [ ] **Step 7: Commit**

```bash
git add backend/app/ws/live_prediction.py backend/tests/unit/test_dispatch_liquidity_recheck.py
git commit -m "feat(phase4): dispatch-time liquidity re-check, suppress card on failure"
```

---

## Task 11: Telegram card visual distinction

**Files:**
- Modify: `backend/app/telegram/signals.py` (`SignalCandidate`, `render_message`)
- Test: `backend/tests/unit/test_telegram_signals_cohort.py` (new)

**Interfaces:**
- Consumes: `symbol_source`, `LiquidityCheck` fields.
- Produces: extended `SignalCandidate` with cohort + liquidity fields; `render_message` output includes the cohort banner and liquidity numbers when `symbol_source == "futures_poll"`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_telegram_signals_cohort.py
from __future__ import annotations

from datetime import datetime, timezone

from app.telegram.signals import SignalCandidate, render_message


def _base_kwargs() -> dict:
    return dict(
        signal_id="sig-1", symbol="BTC/USDT", timeframe="1h", direction="LONG",
        entry_price=100.0, stop_loss_price=95.0, take_profit_price=110.0,
        confidence_pct=70.0, layer_summary={}, margin_usdt=50.0,
        funding_rate_daily=0.001, chart_url="https://example.com/chart",
        sl_distance_pct=0.05, rr_ratio=2.0,
    )


def test_spot_signal_has_no_cohort_banner() -> None:
    candidate = SignalCandidate(**_base_kwargs(), symbol_source="spot_ws")
    rendered = render_message(candidate, leverage=5, auto_skip_seconds=60)
    assert "NEW COHORT" not in rendered.body


def test_futures_poll_signal_shows_cohort_banner_and_liquidity_numbers() -> None:
    candidate = SignalCandidate(
        **_base_kwargs(), symbol_source="futures_poll",
        qvol_24h=25_000_000.0, spread_bps=2.5, depth_0_5pct_usdt=75_000.0,
    )
    rendered = render_message(candidate, leverage=5, auto_skip_seconds=60)
    assert "NEW COHORT" in rendered.body
    assert "25,000,000" in rendered.body or "25000000" in rendered.body
    assert "2.5" in rendered.body
    assert "75,000" in rendered.body or "75000" in rendered.body
    assert "fast move" in rendered.body.lower() or "does not predict" in rendered.body.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_telegram_signals_cohort.py -v`
Expected: FAIL — `TypeError: SignalCandidate.__init__() got an unexpected keyword argument 'symbol_source'`.

- [ ] **Step 3: Extend `SignalCandidate`**

In `backend/app/telegram/signals.py`, add to the `SignalCandidate` dataclass (after the existing `mtf_directions` field, keeping it `frozen=True`):

```python
    symbol_source: str = "spot_ws"
    qvol_24h: float | None = None
    spread_bps: float | None = None
    depth_0_5pct_usdt: float | None = None
```

- [ ] **Step 4: Extend `render_message` with the cohort banner**

In `render_message`, after computing `body` (before the `keyboard = _build_keyboard(...)` line), insert:

```python
    cohort_banner = ""
    if candidate.symbol_source == "futures_poll":
        cohort_banner = (
            f"🆕 NEW COHORT — thinner liquidity, unvalidated\n"
            f"24h vol: ${candidate.qvol_24h:,.0f}  •  "
            f"Spread: {candidate.spread_bps:.1f}bps  •  "
            f"Depth (0.5%): ${candidate.depth_0_5pct_usdt:,.0f}\n"
            f"⚠ Resting depth does not predict depth during a fast move.\n"
            f"─────────────────────────────────────\n"
        )
```

Prepend `cohort_banner` to the `body` string at its construction site (immediately after the `body = (` opening, before the `f"{direction_emoji} {candidate.direction}...` line — concatenate as `body = (cohort_banner + f"{direction_emoji}...` or build `body` as before then do `body = cohort_banner + body`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/unit/test_telegram_signals_cohort.py -v`
Expected: PASS, both tests.

- [ ] **Step 6: Run the full telegram signals test suite for regression**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/ -k "telegram_signals or render_message" -v`
Expected: all existing tests still pass unchanged (find the existing test file with `grep -rl "render_message" backend/tests/` first and run it explicitly if the `-k` filter misses it).

- [ ] **Step 7: Commit**

```bash
git add backend/app/telegram/signals.py backend/tests/unit/test_telegram_signals_cohort.py
git commit -m "feat(phase4): visually distinct Telegram card for futures-only cohort"
```

---

## Task 12: `/bot-status/telegram-signals` endpoint

**Files:**
- Modify: `backend/app/api/schemas.py` (add `TelegramSignalOut`)
- Modify: `backend/app/api/routes/bot_status.py` (add the route, following `recent_trades`'s exact pattern)
- Test: `backend/tests/integration/test_bot_status_telegram_signals.py` (new)

**Interfaces:**
- Produces: `GET /bot-status/telegram-signals?limit=&direction=&symbol_source=` → `list[TelegramSignalOut]`. Consumed by Task 13's frontend API client.

- [ ] **Step 1: Read `recent_trades` and `RecentTradeOut` in full for the exact pattern to mirror**

Run: `sed -n '649,700p' backend/app/api/routes/bot_status.py` and `grep -n "class RecentTradeOut" -A 15 backend/app/api/schemas.py`

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/integration/test_bot_status_telegram_signals.py
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

# Follow this repo's existing integration-test fixture pattern for
# bot_status routes -- check an existing bot_status integration test
# (e.g. test_bot_status_recent_trades.py, if present) for the exact
# app/client/session fixture setup and reuse it verbatim.


@pytest.mark.asyncio
async def test_telegram_signals_endpoint_returns_dispatched_rows(async_client, session_factory) -> None:
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO telegram_signals "
            "(id, user_id, symbol, direction, sent_at, payload, response, symbol_source) "
            "VALUES (:id, 1, 'FOO/USDT', 'LONG', :sent_at, :payload, NULL, 'futures_poll')"
        ), {
            "id": "sig-1", "sent_at": datetime.now(timezone.utc),
            "payload": json.dumps({
                "entry_price": 100.0, "stop_loss_price": 95.0, "take_profit_price": 110.0,
                "rr_ratio": 2.0, "confidence_pct": 70.0,
                "qvol_24h": 25_000_000.0, "spread_bps": 2.0, "depth_0_5pct_usdt": 80_000.0,
            }),
        })
        await session.commit()

    resp = await async_client.get("/bot-status/telegram-signals")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "FOO/USDT"
    assert body[0]["symbol_source"] == "futures_poll"
    assert body[0]["status"] is None  # response column was NULL -- pending


@pytest.mark.asyncio
async def test_telegram_signals_filters_by_symbol_source(async_client, session_factory) -> None:
    async with session_factory() as session:
        for sym, src in [("FOO/USDT", "futures_poll"), ("BTC/USDT", "spot_ws")]:
            await session.execute(sa.text(
                "INSERT INTO telegram_signals "
                "(id, user_id, symbol, direction, sent_at, payload, symbol_source) "
                "VALUES (:id, 1, :sym, 'LONG', :sent_at, '{}', :src)"
            ), {"id": f"sig-{sym}", "sym": sym, "sent_at": datetime.now(timezone.utc), "src": src})
        await session.commit()

    resp = await async_client.get("/bot-status/telegram-signals?symbol_source=futures_poll")
    body = resp.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "FOO/USDT"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/integration/test_bot_status_telegram_signals.py -v`
Expected: FAIL — 404, route doesn't exist.

- [ ] **Step 4: Add `TelegramSignalOut` to schemas.py**

Add to `backend/app/api/schemas.py`, near `RecentTradeOut`:

```python
class TelegramSignalOut(BaseModel):
    signal_id: str
    symbol: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    rr_ratio: float
    confidence_pct: float
    sent_at: datetime
    status: Literal["approved", "skipped", "timeout", "error"] | None
    symbol_source: Literal["spot_ws", "futures_poll"]
    qvol_24h: float | None = None
    spread_bps: float | None = None
    depth_0_5pct_usdt: float | None = None
```

- [ ] **Step 5: Add the route**

Add to `backend/app/api/routes/bot_status.py`, following `recent_trades`'s exact shape:

```python
@router.get("/telegram-signals", response_model=list[TelegramSignalOut])
async def telegram_signals(
    limit: int = Query(default=100, ge=1, le=500),
    direction: Literal["LONG", "SHORT"] | None = Query(default=None),
    symbol_source: Literal["spot_ws", "futures_poll"] | None = Query(default=None),
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[TelegramSignalOut]:
    """Read-only, sourced directly from telegram_signals -- never
    recomputed. Newest first."""
    where: list[str] = ["user_id = :user_id"]
    params: dict[str, Any] = {"user_id": current_user.id}
    if direction is not None:
        where.append("direction = :direction")
        params["direction"] = direction
    if symbol_source is not None:
        where.append("symbol_source = :symbol_source")
        params["symbol_source"] = symbol_source

    sql = (
        "SELECT id, symbol, direction, sent_at, payload, response, symbol_source "
        "FROM telegram_signals "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY sent_at DESC LIMIT :limit"
    )
    params["limit"] = limit
    rows = await session.execute(sa.text(sql), params)
    out: list[TelegramSignalOut] = []
    for r in rows:
        payload = json.loads(r.payload) if isinstance(r.payload, str) else (r.payload or {})
        sent_at = r.sent_at
        if isinstance(sent_at, str):
            sent_at = datetime.fromisoformat(sent_at)
        out.append(TelegramSignalOut(
            signal_id=r.id,
            symbol=r.symbol,
            direction=r.direction,  # type: ignore[arg-type]
            entry_price=payload.get("entry_price", 0.0),
            stop_loss_price=payload.get("stop_loss_price", 0.0),
            take_profit_price=payload.get("take_profit_price", 0.0),
            rr_ratio=payload.get("rr_ratio", 0.0),
            confidence_pct=payload.get("confidence_pct", 0.0),
            sent_at=sent_at,
            status=r.response,  # type: ignore[arg-type]
            symbol_source=r.symbol_source,  # type: ignore[arg-type]
            qvol_24h=payload.get("qvol_24h"),
            spread_bps=payload.get("spread_bps"),
            depth_0_5pct_usdt=payload.get("depth_0_5pct_usdt"),
        ))
    return out
```

Add `TelegramSignalOut` to the imports from `app.api.schemas` at the top of `bot_status.py`, and confirm `import json` is present in the file (add if not).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/integration/test_bot_status_telegram_signals.py -v`
Expected: PASS, both tests.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/schemas.py backend/app/api/routes/bot_status.py backend/tests/integration/test_bot_status_telegram_signals.py
git commit -m "feat(api): add /bot-status/telegram-signals endpoint (Phase 4 app view)"
```

---

## Task 13: Frontend API client additions

**Files:**
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Produces: `TelegramSignal` type, `TelegramSignalsFilters` type, `api.telegramSignals(filters)` function. Consumed by Task 14's hook.

- [ ] **Step 1: Add the type and function**

In `frontend/src/lib/api.ts`, add near `RecentTrade`/`recentTrades` (matching the file's existing type-then-function grouping convention):

```typescript
export interface TelegramSignal {
  signal_id: string;
  symbol: string;
  direction: "LONG" | "SHORT";
  entry_price: number;
  stop_loss_price: number;
  take_profit_price: number;
  rr_ratio: number;
  confidence_pct: number;
  sent_at: string;
  status: "approved" | "skipped" | "timeout" | "error" | null;
  symbol_source: "spot_ws" | "futures_poll";
  qvol_24h: number | null;
  spread_bps: number | null;
  depth_0_5pct_usdt: number | null;
}

export interface TelegramSignalsFilters {
  limit?: number;
  direction?: "LONG" | "SHORT";
  symbol_source?: "spot_ws" | "futures_poll";
}
```

Inside the `export const api = { ... }` object, add:

```typescript
  telegramSignals: (f: TelegramSignalsFilters = {}) => {
    const qs = new URLSearchParams();
    if (f.limit != null) qs.set("limit", String(f.limit));
    if (f.direction) qs.set("direction", f.direction);
    if (f.symbol_source) qs.set("symbol_source", f.symbol_source);
    const tail = qs.toString();
    return fetchJson<TelegramSignal[]>(
      `/bot-status/telegram-signals${tail ? "?" + tail : ""}`,
    );
  },
```

- [ ] **Step 2: Type-check the frontend**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new type errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(frontend): add telegramSignals API client (Phase 4)"
```

---

## Task 14: `useTelegramSignals` auto-refresh hook

**Files:**
- Create: `frontend/src/tabs/Tab4Signals/hooks/useTelegramSignals.ts`

**Interfaces:**
- Consumes: `api.telegramSignals` (Task 13).
- Produces: `useTelegramSignals(opts) -> { data, error, isLoading, refetch }`. Consumed by Task 15.

- [ ] **Step 1: Write the hook, mirroring `useScannerRadar.ts` exactly (same auto-refresh + manual-refetch shape, per the operator's own "match the scanner's ~2min precedent" instruction)**

```typescript
// frontend/src/tabs/Tab4Signals/hooks/useTelegramSignals.ts
// Phase 4: auto-refresh fetch hook for the Signals tab. Mirrors
// useScannerRadar's shape exactly -- same polling + manual-refetch
// pattern, per the operator's explicit "match the scanner's ~2min
// precedent" instruction.

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type TelegramSignal, type TelegramSignalsFilters } from "@/lib/api";

export interface UseTelegramSignalsOptions extends TelegramSignalsFilters {
  /** Polling interval in milliseconds. Default: 120_000 (2 minutes). */
  refreshIntervalMs?: number;
}

export interface UseTelegramSignalsResult {
  data: TelegramSignal[] | null;
  error: Error | null;
  isLoading: boolean;
  refetch: () => Promise<void>;
}

const DEFAULT_INTERVAL_MS = 120_000;

export function useTelegramSignals(
  opts: UseTelegramSignalsOptions = {},
): UseTelegramSignalsResult {
  const [data, setData] = useState<TelegramSignal[] | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isLoading, setLoading] = useState<boolean>(true);

  const optsRef = useRef<UseTelegramSignalsOptions>(opts);
  optsRef.current = opts;

  const refetch = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const args: TelegramSignalsFilters = {};
      const cur = optsRef.current;
      if (cur.limit !== undefined) args.limit = cur.limit;
      if (cur.direction !== undefined) args.direction = cur.direction;
      if (cur.symbol_source !== undefined) args.symbol_source = cur.symbol_source;
      const r = await api.telegramSignals(args);
      setData(r);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
    const ms = opts.refreshIntervalMs ?? DEFAULT_INTERVAL_MS;
    const id = window.setInterval(() => {
      void refetch();
    }, ms);
    return () => window.clearInterval(id);
  }, [opts.limit, opts.direction, opts.symbol_source, opts.refreshIntervalMs, refetch]);

  return { data, error, isLoading, refetch };
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new type errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/tabs/Tab4Signals/hooks/useTelegramSignals.ts
git commit -m "feat(frontend): add useTelegramSignals hook (Phase 4)"
```

---

## Task 15: `Tab4Signals` component

**Files:**
- Create: `frontend/src/tabs/Tab4Signals/index.tsx`
- Create: `frontend/src/tabs/Tab4Signals/SignalRow.tsx`

**Interfaces:**
- Consumes: `useTelegramSignals` (Task 14), `TelegramSignal` type (Task 13).
- Produces: `Tab4Signals` React component. Consumed by Task 16.

- [ ] **Step 1: Write `SignalRow.tsx` — one row, full-precision values, unmissable cohort badge**

```typescript
// frontend/src/tabs/Tab4Signals/SignalRow.tsx
// Phase 4: one row in the Signals tab. Full-precision entry/SL/TP (the
// operator retypes these into Binance by hand -- a rounded display
// value is a real trading-error risk per the operator's own
// instruction, not cosmetic). Cohort badge is a distinct color block,
// not a small text label, so it can't be skimmed past.

import type { TelegramSignal } from "@/lib/api";

interface Props {
  signal: TelegramSignal;
}

function fmtFullPrecision(n: number): string {
  // No rounding -- show what the API returned, full precision.
  return String(n);
}

export function SignalRow({ signal: s }: Props) {
  const isFuturesOnly = s.symbol_source === "futures_poll";
  return (
    <tr className={isFuturesOnly ? "bg-amber-950/30" : undefined}>
      <td className="px-2 py-1 whitespace-nowrap">
        {new Date(s.sent_at).toLocaleString()}
      </td>
      <td className="px-2 py-1 font-mono">{s.symbol}</td>
      <td className={`px-2 py-1 ${s.direction === "LONG" ? "text-green" : "text-red"}`}>
        {s.direction}
      </td>
      <td className="px-2 py-1 font-mono">{fmtFullPrecision(s.entry_price)}</td>
      <td className="px-2 py-1 font-mono">{fmtFullPrecision(s.stop_loss_price)}</td>
      <td className="px-2 py-1 font-mono">{fmtFullPrecision(s.take_profit_price)}</td>
      <td className="px-2 py-1">{s.rr_ratio.toFixed(2)}</td>
      <td className="px-2 py-1">{s.confidence_pct.toFixed(0)}%</td>
      <td className="px-2 py-1">{s.status ?? "pending"}</td>
      <td className="px-2 py-1">
        {isFuturesOnly ? (
          <span className="inline-block px-2 py-0.5 rounded bg-amber-600 text-white text-[10px] font-bold uppercase">
            🆕 New Cohort
          </span>
        ) : (
          <span className="text-text-tertiary text-[10px]">established</span>
        )}
      </td>
      <td className="px-2 py-1 text-[10px] text-text-tertiary">
        {isFuturesOnly && s.qvol_24h != null
          ? `vol $${s.qvol_24h.toLocaleString()} • ${s.spread_bps?.toFixed(1)}bps • depth $${s.depth_0_5pct_usdt?.toLocaleString()}`
          : "—"}
      </td>
    </tr>
  );
}
```

- [ ] **Step 2: Write `index.tsx` — the tab page, following `Tab3Scanner/index.tsx`'s toolbar-plus-hook shape**

```typescript
// frontend/src/tabs/Tab4Signals/index.tsx
// Phase 4: Signals tab -- the operator's primary workflow surface for
// manually trading these signals. Read-only, sourced from
// telegram_signals via useTelegramSignals, never recomputed.
// Auto-refreshes every ~2min (matches the scanner's precedent) plus a
// manual refresh control.

import { useState } from "react";
import { useTelegramSignals } from "./hooks/useTelegramSignals";
import { SignalRow } from "./SignalRow";

type DirectionFilter = "all" | "LONG" | "SHORT";
type CohortFilter = "all" | "spot_ws" | "futures_poll";

const DEFAULT_REFRESH_MIN = 2;

export function Tab4Signals() {
  const [directionFilter, setDirectionFilter] = useState<DirectionFilter>("all");
  const [cohortFilter, setCohortFilter] = useState<CohortFilter>("all");

  const { data, error, isLoading, refetch } = useTelegramSignals({
    limit: 100,
    direction: directionFilter === "all" ? undefined : directionFilter,
    symbol_source: cohortFilter === "all" ? undefined : cohortFilter,
    refreshIntervalMs: DEFAULT_REFRESH_MIN * 60_000,
  });

  return (
    <div className="h-full flex flex-col bg-bg-base">
      <div className="flex items-center gap-2 p-2 border-b border-border">
        <select
          value={directionFilter}
          onChange={(e) => setDirectionFilter(e.target.value as DirectionFilter)}
          className="text-xs bg-bg-surface border border-border rounded px-2 py-1"
        >
          <option value="all">All directions</option>
          <option value="LONG">LONG</option>
          <option value="SHORT">SHORT</option>
        </select>
        <select
          value={cohortFilter}
          onChange={(e) => setCohortFilter(e.target.value as CohortFilter)}
          className="text-xs bg-bg-surface border border-border rounded px-2 py-1"
        >
          <option value="all">All cohorts</option>
          <option value="spot_ws">Established only</option>
          <option value="futures_poll">New cohort only</option>
        </select>
        <button
          onClick={() => void refetch()}
          className="text-xs bg-bg-surface border border-border rounded px-2 py-1 ml-auto"
        >
          ⟳ Refresh
        </button>
        {isLoading && <span className="text-[10px] text-text-tertiary">loading…</span>}
      </div>

      {error && (
        <div className="p-2 text-red text-xs">Failed to load signals: {error.message}</div>
      )}

      <div className="flex-1 overflow-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-bg-base">
            <tr className="text-left text-text-tertiary uppercase text-[10px]">
              <th className="px-2 py-1">Sent</th>
              <th className="px-2 py-1">Symbol</th>
              <th className="px-2 py-1">Dir</th>
              <th className="px-2 py-1">Entry</th>
              <th className="px-2 py-1">SL</th>
              <th className="px-2 py-1">TP</th>
              <th className="px-2 py-1">RR</th>
              <th className="px-2 py-1">Conf</th>
              <th className="px-2 py-1">Status</th>
              <th className="px-2 py-1">Cohort</th>
              <th className="px-2 py-1">Liquidity</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((s) => (
              <SignalRow key={s.signal_id} signal={s} />
            ))}
          </tbody>
        </table>
        {data && data.length === 0 && (
          <div className="p-4 text-center text-text-tertiary text-xs">No signals yet</div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new type errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/tabs/Tab4Signals/index.tsx frontend/src/tabs/Tab4Signals/SignalRow.tsx
git commit -m "feat(frontend): add Tab4Signals component (Phase 4 app view)"
```

---

## Task 16: Register the new tab in `App.tsx`

**Files:**
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `Tab4Signals` (Task 15).

- [ ] **Step 1: Read the existing tab-registration pattern**

Run: `grep -n "Tab1LivePrediction\|Tab3Scanner\|tab ===" frontend/src/App.tsx`

- [ ] **Step 2: Add the import and route**

Add `import { Tab4Signals } from "@/tabs/Tab4Signals";` alongside the existing tab imports. Add the tab to whatever tab-switch/nav-list mechanism the existing `tab === "scanner" ? <Tab3Scanner /> :` line uses — add a new `tab === "signals" ? <Tab4Signals /> :` branch and the corresponding nav entry (find and match the existing nav-button list's exact shape before editing).

- [ ] **Step 3: Type-check and build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(frontend): register Signals tab in navigation (Phase 4)"
```

---

## Task 17: Wire `futures_poll_task` into `main.py`

**Files:**
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `start_futures_poll_task` (Task 8).

- [ ] **Step 1: Read the existing lifespan wiring for `start_keepalive_task`**

Run: `grep -n "start_keepalive_task\|start_background_worker" backend/app/main.py`

- [ ] **Step 2: Add the import and lifespan call**

Add `from app.ws.futures_poll import start_futures_poll_task` alongside the existing `from app.ws.keepalive import start_keepalive_task` import. In the lifespan function, immediately after the existing `start_keepalive_task(...)` call, add:

```python
        futures_poll_worker = start_futures_poll_task(session_factory)
```

Add the corresponding cancellation in the lifespan's shutdown block, mirroring exactly how `keepalive_worker`/`live_worker` are already cancelled there (find the exact pattern with `grep -n "keepalive_worker.cancel\|live_worker.cancel" backend/app/main.py` first).

- [ ] **Step 3: Write an integration test confirming the worker starts and registers a heartbeat**

```python
# backend/tests/integration/test_main_futures_poll_wiring.py
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_futures_poll_task_is_registered_in_worker_registry() -> None:
    from app.ops.worker_registry import WORKER_REGISTRY  # adjust import to actual registry location
    from app.ws.futures_poll import WORKER_NAME

    assert WORKER_NAME in {w.name for w in WORKER_REGISTRY}
```

Adjust the import path to match `app/ops/worker_registry.py`'s actual exported symbol — check with `grep -n "^WORKER_REGISTRY\|^_REGISTRY\|def.*registry" backend/app/ops/worker_registry.py` first, since Task 17 also requires **registering** `futures_poll_task` in the worker registry (mirroring the existing `ws_keepalive_task`/`live_worker` entries) so the watchdog and worker-heartbeat census both see it — add that registration entry in `worker_registry.py` alongside the existing `ws_keepalive_task` entry before writing this test.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider tests/integration/test_main_futures_poll_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full backend suite for a final regression check**

Run: `cd backend && python -m pytest --no-cov -p no:cacheprovider -q`
Expected: the same 6 known pre-existing failures, nothing new.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/app/ops/worker_registry.py backend/tests/integration/test_main_futures_poll_wiring.py
git commit -m "feat(phase4): wire futures_poll_task into app lifespan + worker registry"
```

---

## Task 18: Staging rollout verification (manual checklist, not code)

> **Redrafted 2026-08-17.** The original checklist verified a fixed N=8 futures-only cohort against `symbol_source IN ('spot_ws', 'futures_poll')` and ended with "start at N=8, widen to 20-25 after one clean week" — a sizing ladder the liquidity-floor selector supersedes entirely (there is no N to widen; the floor determines membership directly). Replaced with verification against the actual three-way cohort split, and the addendum's cost-check gate substituted for the old widen-after-a-week step.

**Files:** none — this task is a documented manual verification pass against the staging deployment, per the spec's rollout section.

- [ ] **Step 1: Deploy Tasks 2-17 to staging** (Task 1 has already shipped and soaked separately per the Global Constraints).

- [ ] **Step 2: Verify candles arriving for every symbol the selector actually chose, across all three cohorts**

Run the `sql-select` ops-debug probe: `SELECT symbol_source, symbol, timeframe, MAX(ts) AS last_pred, COUNT(*) AS n FROM predictions WHERE ts >= NOW() - INTERVAL '24h' GROUP BY symbol_source, symbol, timeframe ORDER BY symbol_source, symbol` against staging.
Expected: rows across all three `symbol_source` values, each symbol with a recent `last_pred`. Compare the per-cohort symbol COUNT against `live_fleet_universe`'s latest snapshot (`SELECT cohort, COUNT(*) FROM live_fleet_universe WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM live_fleet_universe) GROUP BY cohort`) — they should match exactly. Do not expect a fixed number here; expect it to match whatever the floor selected that day (per the decision record's 2026-08-15 measurement, roughly 34 spot-backed + 8-11 futures-only, but this drifts daily by design).

- [ ] **Step 3: Verify `shadow_trades.symbol_source` is populated, not just `predictions`**

`SELECT symbol_source, COUNT(*) FROM shadow_trades WHERE opened_at >= NOW() - INTERVAL '24h' GROUP BY symbol_source` — confirm non-`established_top20` rows actually appear for any `liquidity_added_spot`/`futures_poll` symbol shadow is tracking (per Task 9's open dependency note, this may legitimately be zero for `futures_poll` if shadow doesn't track those symbols yet — confirm which case this is, don't assume).

- [ ] **Step 4: Verify the Telegram card renders correctly end-to-end**

Trigger a real signal on staging (or force one via the `test-trade-open`-class ops-debug probe against staging) for a `liquidity_added_spot` symbol AND a `futures_poll` symbol and visually inspect both actual rendered Telegram messages — confirm the cohort banner, liquidity numbers, and limitation sentence all appear correctly for both, not just that the code path completed without erroring.

- [ ] **Step 5: Verify the app view renders, three-way filterable**

Load the Signals tab against staging, confirm rows appear across all three cohorts, filtering by cohort works (not just by direction), auto-refresh fires after ~2 minutes, manual refresh works, full-precision values display correctly (compare against the raw API response, not a rounded eyeball check).

- [ ] **Step 6: Verify `established_top20` symbols are unaffected**

Compare pre-deploy and post-deploy prediction cadence for 3-4 symbols that were already in the legacy top-20 (same `sql-select` pattern as Step 2, filtered to `symbol_source = 'established_top20'`) — confirm no gap, no format change. **Also explicitly check ADA and NEAR** (the decision record's named symbols that stably fail the new floor) — confirm they either still appear tagged `established_top20` (if the hysteresis exit hasn't fired yet — expected on day one, unanimous 5/5 fail takes at least one full refresh) or have cleanly dropped out with no error, not a half-state.

- [ ] **Step 7: Verify the open-position override, not just the happy path**

Manually open a staging position (testnet) on a symbol that's currently borderline/failing the floor, force a refresh, confirm the symbol is retained in `live_fleet_universe` and its WS/poll child is NOT cancelled, per addendum (a)'s hard requirement — Task 5b/8's unit tests cover this in isolation; this step is the end-to-end confirmation against a real refresh cycle.

- [ ] **Step 8: Hold 24h, then re-run Steps 2-7**

Confirm no gap-count escalations, no failure-streak escalations, no rate-limit-wait warnings indicating real contention (check both `ws_keepalive_task`'s and `futures_poll_task`'s heartbeat `details` JSONB via the worker-heartbeat census). **Also run the cost-check gate from the addendum**: `docker stats tr-staging-backend` before/after enabling the new selector, across this same 24h window including a candle-close burst — this is the addendum's explicit go/no-go gate for whether N≈42 fits, not an assumption.

- [ ] **Step 9: Promote to main following the standing soak-class + promotion-checklist discipline**, including the FU-42-motivated settings-diff step from `backend/docs/PROMOTION_CHECKLIST.md`.

- [ ] **Step 10: No sizing ladder to walk — the floor determines membership directly, there is no N to widen.** If the Step 8 cost-check gate showed N≈42 doesn't fit, apply the addendum's fallback (`FUTURES_POLL_SAFETY_MAX_N` / an equivalent cap on the spot side, ranked by liquidity — qvol desc, spread asc, depth desc — never volume) rather than reverting to a fixed-N ladder.

---

## Self-Review Notes

**Spec coverage**: every section of the design spec maps to a task — Step 0 → Task 1; data model → Tasks 2-3; liquidity floor → Task 4; universe selection + hysteresis → Task 5; spot-fleet wiring → Task 5b (new, 2026-08-17); poller (idempotency/gap/fail-loud/rate-limit) → Tasks 6-8; cohort tagging (four tables, three-way) → Task 9; dispatch-time re-check + suppression → Task 10; visual distinction → Task 11; app view backend → Task 12; app view frontend → Tasks 13-16; wiring → Task 17; rollout → Task 18. **Open-position retention (spec's dedicated section) is now a hard requirement, covered by Task 5b's and Task 8's retention tests — corrected 2026-08-17; the original draft had this backwards (mirror-cancellation, explicitly not retention).** The manual-execution measurement limitation (spec's "Known limitation" section) requires no task — it's a documentation-only finding already recorded in the spec itself.

**Deferred items** (matching the spec's own "Deferred" section, intentionally no task here): rate-limiter priority reweighting, degraded-vs-suppressed card alternative. **Shadow-side tracking of the futures-only cohort is no longer purely deferred** — Task 9's shadow_trades write depends on it for the `futures_poll` cohort specifically; flagged there, still not built in this plan, but no longer non-blocking the way the original spec framed it.
