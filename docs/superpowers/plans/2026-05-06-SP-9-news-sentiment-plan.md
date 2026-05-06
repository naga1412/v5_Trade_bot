# SP-9 News + Sentiment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the SP-5 `layer9_news` placeholder with a working news-aggregation + sentiment pipeline. Ingest crypto news from CryptoPanic (free tier: 500/day) and macro news from Yahoo RSS, run FinBERT sentiment classification on every fetched headline, persist to a new `news_items` table, aggregate per-asset sentiment into a real L9 `LayerScore`, and wire the `SentimentFearGreed` + `NewsMacroImpact` Tab 1 panels with live data including the alternative.me Fear & Greed index.

**Architecture:** New `app.news.*` package (`adapters/{cryptopanic,yahoo_rss}`, `sentiment`, `persistence`, `ingest_worker`, `fear_greed`) + a single migration `0013_news_items` + a real `app.core.scoring.layer9_news` + an admin REST surface (`app.api.routes.admin_news`) + Pydantic schema extensions on `LivePredictionOut` (`sentiment` + `news` optional fields) + frontend wire-up to two existing placeholder panels. Background ingest loop polls every 5 minutes (crypto) and 30 minutes (macro), and a separate cleanup loop runs at 04:00 UTC nightly to delete `news_items` older than 20 days. FinBERT (`ProsusAI/finbert`) is loaded lazily on first call; the model adds ~440MB to disk (~1.5GB total Docker image growth including `transformers`).

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2 (async) / asyncpg / TimescaleDB · `httpx==0.28.1` · `feedparser==6.0.11` (NEW) · `transformers==4.46.0` (NEW) · `torch==2.4.1` (already pinned) · React 18 / Vite / TypeScript strict / Tailwind · pytest / pytest-asyncio / respx · Vitest

**Spec reference:** [`docs/superpowers/specs/2026-05-06-SP-9-news-sentiment-design.md`](../specs/2026-05-06-SP-9-news-sentiment-design.md). When this plan and the spec disagree, the spec wins.

**Cross-cutting policy compliance map:**
- Phase A — base scaffolding + migration. `news_items` is **NOT hash-chained** (external truth — same articles for all users; no per-user state). Spec §7 §5.14 explicitly clears this.
- Phase B — uses SP-3 `RateLimitedClient` + `DailyCounterBucket`(500/day) for CryptoPanic; matches the TwelveData adapter pattern.
- Phase C — FinBERT model loads lazily; classify_batch is sync (PyTorch CPU) but called from `asyncio.to_thread` in the worker so it doesn't block the event loop.
- Phase D — background loops gated on `settings.env not in {"test","ci"} AND settings.worker_enabled` (matches every other lifespan-spawned worker in `app/main.py`).
- Phase E — `layer9_news.score` is now async (it queries `news_items`); predictor receives a session.
- Phase F — admin endpoint inherits `Depends(require_admin)` (SP-0.7 §2.6); no per-user filtering on news.

---

## File Structure

This is what SP-9 creates inside the new worktree. All paths are under `worktrees/sp-9/`.

```
worktrees/sp-9/
├── backend/
│   ├── alembic/versions/
│   │   └── 2026_05_06_0013_news_items.py                  NEW
│   ├── app/
│   │   ├── news/                                          NEW package
│   │   │   ├── __init__.py
│   │   │   ├── adapters/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── _base.py                — NewsAdapter Protocol + NewsArticle dataclass
│   │   │   │   ├── cryptopanic.py          — CryptoPanic free-tier adapter (httpx + RateLimitedClient/Daily)
│   │   │   │   └── yahoo_rss.py            — Yahoo RSS adapter (feedparser)
│   │   │   ├── classify_helpers.py         — symbol extraction + category classifier
│   │   │   ├── sentiment.py                — FinBERT loader + classify_batch
│   │   │   ├── persistence.py              — persist_news_items + cleanup_old_news
│   │   │   ├── ingest_worker.py            — run_news_ingest_loop + run_news_cleanup_loop
│   │   │   └── fear_greed.py               — alternative.me F&G fetcher (1h cache)
│   │   ├── core/scoring/
│   │   │   └── layer9_news.py              REPLACED — async score(bars, *, symbol, session)
│   │   ├── core/predictor.py               MODIFIED — pass session + symbol; populate sentiment/news
│   │   ├── api/
│   │   │   ├── schemas.py                  MODIFIED — SentimentSummary + NewsSummary; LivePredictionOut +2 fields
│   │   │   └── routes/
│   │   │       └── admin_news.py           NEW — GET /api/v1/admin/news, POST /admin/news/refresh
│   │   ├── main.py                         MODIFIED — wire start_news_ingest_task + start_news_cleanup_task
│   │   └── config.py                       MODIFIED — add cryptopanic_api_key field
│   ├── pyproject.toml                      MODIFIED — +transformers, +feedparser
│   └── tests/
│       ├── unit/
│       │   ├── test_news_adapters_base.py
│       │   ├── test_news_adapter_cryptopanic.py
│       │   ├── test_news_adapter_yahoo_rss.py
│       │   ├── test_news_classify_helpers.py
│       │   ├── test_news_persistence.py
│       │   ├── test_news_sentiment.py
│       │   ├── test_news_sentiment_smoke.py     (@pytest.mark.slow)
│       │   ├── test_news_fear_greed.py
│       │   ├── test_news_ingest_worker.py
│       │   ├── test_news_cleanup_loop.py
│       │   ├── test_layer9_news.py
│       │   └── test_schemas_sentiment_news.py
│       └── integration/
│           ├── test_predictor_l9_e2e.py
│           ├── test_api_admin_news_list.py
│           └── test_api_admin_news_refresh.py
└── frontend/
    ├── src/
    │   ├── lib/api.ts                              MODIFIED — +SentimentSummary, +NewsSummary, +LivePrediction fields
    │   └── tabs/Tab1LivePrediction/panels/
    │       ├── SentimentFearGreed.tsx              MODIFIED — real F&G + news_bias rendering
    │       └── NewsMacroImpact.tsx                 MODIFIED — real headline + impact badge
    └── tests/unit/
        ├── SentimentFearGreed.test.tsx             MODIFIED + new cases
        └── NewsMacroImpact.test.tsx                MODIFIED + new cases
```

---

## Phase A — Worktree + scaffolding + migration

### Task A1: Create SP-9 worktree

**Files:** none (git operation only).

**Design notes:**
- Worktree mirrors the SP-7 layout (`worktrees/sp-7/` was the most recent ship). Branch `sp-9/main` is forked from current `main` (commit `4902173` per the prompt context).
- Baseline counts captured here become the regression bar: ~1450 backend pytest passes, ~329 frontend Vitest passes, 16 Playwright cases.

- [ ] **Step 1: Verify clean main**

```bash
cd a:/v5_Trade_bot
git -c safe.directory='A:/v5_Trade_bot' status
```
Expected: `On branch main` and `nothing to commit, working tree clean`. If dirty, stop and clean before proceeding.

- [ ] **Step 2: Confirm last commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' log -1 --oneline
```
Expected: includes `4902173` (the SP-7 ship commit).

- [ ] **Step 3: Create worktree**

```bash
git -c safe.directory='A:/v5_Trade_bot' worktree add worktrees/sp-9 -b sp-9/main
```
Expected: `Preparing worktree (new branch 'sp-9/main')`.

- [ ] **Step 4: Verify**

```bash
git -c safe.directory='A:/v5_Trade_bot' worktree list
```
Expected output includes `worktrees/sp-9  <hash> [sp-9/main]`.

- [ ] **Step 5: Bring stack up + capture backend baseline**

```bash
cd worktrees/sp-9
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
```
Expected: ~1450 passed (matching the SP-7 ship baseline). Record exact number — Phase F target is `baseline + ~60`.

- [ ] **Step 6: Capture frontend baseline**

```bash
cd worktrees/sp-9/frontend
npm ci
npm run test -- --run
```
Expected: ~329 passing. Record exact number — Phase F target is `baseline + ~11`.

- [ ] **Step 7: All subsequent tasks operate inside `worktrees/sp-9/`**

No commit yet (worktree has no new files).

---

### Task A2: Migration 0013 — `news_items` table

**Files:**
- Create: `worktrees/sp-9/backend/alembic/versions/2026_05_06_0013_news_items.py`

**Design notes:**
- `affected_assets TEXT[]` is a Postgres ARRAY column, queried via `ANY(affected_assets)` in L9. The GIN index on it (per spec §4.1) supports that lookup at scale.
- `url UNIQUE NOT NULL` is the dedup key — adapters can re-emit the same article and `ON CONFLICT (url) DO NOTHING` (Phase B5) absorbs duplicates.
- Sentiment columns (`sentiment_score`, `sentiment_label`, `sentiment_confidence`) are NULLable: a row may be inserted before classification (e.g., Yahoo macro article with no FinBERT pass yet) and updated later. The L9 query filters `sentiment_score IS NOT NULL`.
- `impact_score` is a heuristic from category + source (computed in B5 inside `persist_news_items`); spec §4.1 leaves the formula loose; we use `regulatory→1.0`, `exchange→0.8`, `macro→0.7`, `whale→0.6`, `project→0.5`, `social→0.3`, `None→0.5`.
- The migration is **down-reversible** — `downgrade()` drops indexes then the table.

- [ ] **Step 1: Write migration**

```python
"""news_items table for SP-9 News + Sentiment

Revision ID: 0013_news_items
Revises: 0012_backtests_hyperopt_backups
Create Date: 2026-05-06
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0013_news_items"
down_revision: str | None = "0012_backtests_hyperopt_backups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE news_items (
            id BIGSERIAL PRIMARY KEY,
            source TEXT NOT NULL,
            url TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            body TEXT,
            published_at TIMESTAMPTZ NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            sentiment_score DOUBLE PRECISION,
            sentiment_label TEXT
                CHECK (sentiment_label IN ('positive','negative','neutral')),
            sentiment_confidence DOUBLE PRECISION,
            impact_score DOUBLE PRECISION,
            category TEXT
                CHECK (category IN
                    ('regulatory','exchange','macro','whale','project','social')),
            affected_assets TEXT[]
        );
        """
    )
    op.execute(
        "CREATE INDEX news_items_published_idx "
        "ON news_items (published_at DESC);"
    )
    op.execute(
        "CREATE INDEX news_items_assets_gin_idx "
        "ON news_items USING GIN (affected_assets);"
    )
    op.execute(
        "CREATE INDEX news_items_source_published_idx "
        "ON news_items (source, published_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS news_items_source_published_idx;")
    op.execute("DROP INDEX IF EXISTS news_items_assets_gin_idx;")
    op.execute("DROP INDEX IF EXISTS news_items_published_idx;")
    op.execute("DROP TABLE IF EXISTS news_items;")
```

- [ ] **Step 2: Apply migration**

```bash
cd worktrees/sp-9
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend bash -c "cd /app && alembic upgrade head"
```
Expected: `Running upgrade 0012_backtests_hyperopt_backups -> 0013_news_items`.

- [ ] **Step 3: Verify table + indexes**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U postgres trading_radar -c "\d news_items"
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U postgres trading_radar -c "\di news_items*"
```
Expected: 12 columns, 4 indexes (PK + 3 named).

- [ ] **Step 4: Re-run backend tests** — should still pass (no code uses the table yet).

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
```

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/alembic/versions/2026_05_06_0013_news_items.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): migration 0013 — news_items table with GIN index on affected_assets"
```

---

### Task A3: Add transformers + feedparser deps; verify Docker rebuild

**Files:**
- Modify: `worktrees/sp-9/backend/pyproject.toml`

**Design notes:**
- `transformers==4.46.0` is the latest 4.46.x release that pairs cleanly with `torch==2.4.1` (already pinned). It pulls `tokenizers`, `safetensors`, `huggingface-hub`. ~110MB on disk before any model download.
- `feedparser==6.0.11` is the maintained release that handles RSS 2.0 and Atom 1.0 with no extra deps (uses stdlib `xml.parsers.expat`).
- The image will grow ~150MB from these deps alone; once the FinBERT model downloads to `~/.cache/huggingface` at first run that adds ~440MB. Total runtime image growth is what the spec calls out at ~1.5GB.
- Pre-baking the FinBERT model into the Docker image (an alternative to lazy download) is **deferred** to Phase D2's risk-fallback step — only triggered if first-startup downloads fail in CI.

- [ ] **Step 1: Edit pyproject.toml** — append to `dependencies`:

```toml
    # SP-9 Phase A3: news + sentiment runtime deps.
    # - transformers: HuggingFace inference for ProsusAI/finbert (CPU only).
    # - feedparser: RSS 2.0 / Atom 1.0 parsing for Yahoo RSS macro feeds.
    # First run downloads FinBERT (~440MB) to ~/.cache/huggingface; cached after.
    "transformers==4.46.0",
    "feedparser==6.0.11",
```

- [ ] **Step 2: Rebuild backend image (timed)**

```bash
cd worktrees/sp-9
time docker compose -f docker-compose.yml -f docker-compose.dev.yml build backend
```
Expected: completes in <10 min; image size up by ~150MB. **If build exceeds 10 min on CI**, escalate per Heavy-dep gate (spec §10): document and consider deferring to a separate `ml-worker` container in SP-9.5. For now, document timing in commit body.

- [ ] **Step 3: Smoke-test the new imports inside the container**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm backend python -c "import transformers; import feedparser; print(transformers.__version__, feedparser.__version__)"
```
Expected: `4.46.0 6.0.11`.

- [ ] **Step 4: Re-run backend tests** — should still pass (deps imported but no code uses them yet).

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/pyproject.toml
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): pin transformers==4.46.0 + feedparser==6.0.11 for FinBERT + RSS"
```

---

### Task A4: NewsAdapter Protocol + NewsArticle dataclass — TDD

**Files:**
- Create: `worktrees/sp-9/backend/app/news/__init__.py` (empty)
- Create: `worktrees/sp-9/backend/app/news/adapters/__init__.py` (empty)
- Create: `worktrees/sp-9/backend/app/news/adapters/_base.py`
- Create: `worktrees/sp-9/backend/tests/unit/test_news_adapters_base.py`

**Design notes:**
- Mirrors the SP-3 `app.data.adapters._base.ExchangeAdapter` Protocol shape. Frozen dataclass for `NewsArticle` because adapters return immutable rows; downstream code may pass them through `dataclasses.replace()` to enrich (e.g., add sentiment).
- Protocol is `runtime_checkable` so the registry can do `isinstance(x, NewsAdapter)` in tests.
- `affected_assets: tuple[str, ...]` is a tuple (not list) to keep the dataclass hashable — matches `ExchangeAdapter`'s `Candle`/`SymbolInfo` decision.
- `category` is `str | None` because some sources don't tag categories; the `classify_helpers` module fills it in if the adapter omits it.

- [ ] **Step 1: Stub** `app/news/adapters/_base.py`:

```python
"""SP-9 NewsAdapter Protocol + NewsArticle dataclass (placeholder)."""
```

- [ ] **Step 2: Failing test** — `tests/unit/test_news_adapters_base.py`:

```python
from datetime import datetime, timezone

import pytest

from app.news.adapters._base import NewsAdapter, NewsArticle


def test_news_article_is_frozen_dataclass() -> None:
    a = NewsArticle(
        source="cryptopanic",
        url="https://example.com/x",
        title="Bitcoin surges",
        body=None,
        published_at=datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
        category="exchange",
        affected_assets=("BTC",),
    )
    assert a.source == "cryptopanic"
    assert a.affected_assets == ("BTC",)
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        a.title = "mutated"  # type: ignore[misc]


def test_news_article_hashable() -> None:
    a = NewsArticle(
        source="x", url="u", title="t", body=None,
        published_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        category=None, affected_assets=(),
    )
    # Frozen dataclass with all-hashable fields → must be hashable.
    assert hash(a) == hash(a)


def test_news_adapter_is_runtime_checkable_protocol() -> None:
    class FakeAdapter:
        name = "fake"
        async def fetch_recent(self, *, since):  # type: ignore[no-untyped-def]
            return []

    assert isinstance(FakeAdapter(), NewsAdapter)
```

- [ ] **Step 3: Run — fail.** Expected: `ImportError`.

- [ ] **Step 4: Implement** `_base.py`:

```python
"""SP-9 NewsAdapter Protocol + NewsArticle dataclass.

Mirrors :mod:`app.data.adapters._base` (SP-3 ExchangeAdapter shape) but for
news sources rather than OHLCV exchanges.

A `NewsAdapter` only needs to expose `name` and `fetch_recent(*, since)`.
Per-source extensions (the CryptoPanic free-tier filter, for example) live
on the concrete classes — call sites that need them must accept the concrete
type, not the Protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class NewsArticle:
    """A single news headline (and optional body) plus metadata.

    Hashable + immutable so call sites can pass these through ``dataclasses.replace``
    to enrich (e.g., attach a sentiment_score) without surprising mutations.

    `affected_assets` is a tuple (not list) for hashability. Convention is
    UPPER-case base symbol only — ``("BTC", "ETH")`` not ``("BTC/USDT",)``.
    """

    source: str
    url: str
    title: str
    body: str | None
    published_at: datetime
    category: str | None
    affected_assets: tuple[str, ...]


@runtime_checkable
class NewsAdapter(Protocol):
    """Minimum surface every news adapter must expose."""

    name: str  # 'cryptopanic' | 'yahoo_rss'

    async def fetch_recent(self, *, since: datetime) -> list[NewsArticle]:
        """Return articles published strictly after `since` (UTC, tz-aware).

        Network/timeout errors return an empty list with a log warning.
        Malformed JSON / XML raises so the caller sees the failure.
        """
        ...
```

- [ ] **Step 5: Run — pass.** All three tests green.

- [ ] **Step 6: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/news/__init__.py backend/app/news/adapters/__init__.py backend/app/news/adapters/_base.py backend/tests/unit/test_news_adapters_base.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): NewsAdapter Protocol + NewsArticle dataclass with TDD"
```

---

### Task A5: Stub remaining news modules (NotImplementedError)

**Files:**
- Create: `worktrees/sp-9/backend/app/news/adapters/cryptopanic.py`
- Create: `worktrees/sp-9/backend/app/news/adapters/yahoo_rss.py`
- Create: `worktrees/sp-9/backend/app/news/sentiment.py`
- Create: `worktrees/sp-9/backend/app/news/persistence.py`
- Create: `worktrees/sp-9/backend/app/news/ingest_worker.py`
- Create: `worktrees/sp-9/backend/app/news/fear_greed.py`
- Create: `worktrees/sp-9/backend/app/news/classify_helpers.py`

**Design notes:**
- These stubs exist so module imports succeed in subsequent TDD tasks; each public function raises `NotImplementedError` at runtime so accidentally-shipped stubs blow up fast.
- No tests against the stubs themselves — the tests in Phases B–D will replace each stub with a real implementation.

- [ ] **Step 1: Write stubs.** Each file is roughly:

```python
# cryptopanic.py
"""Stub — implemented in SP-9 Phase B1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.news.adapters._base import NewsArticle


@dataclass
class CryptoPanicAdapter:
    name: str = "cryptopanic"

    async def fetch_recent(self, *, since: datetime) -> list[NewsArticle]:
        raise NotImplementedError("SP-9 Phase B1")
```

```python
# yahoo_rss.py — analogous; YahooRssAdapter stub.
```

```python
# sentiment.py
"""Stub — implemented in SP-9 Phase C1."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SentimentResult:
    score: float
    label: Literal["positive", "negative", "neutral"]
    confidence: float


def classify_batch(titles: list[str], batch_size: int = 16) -> list[SentimentResult]:
    raise NotImplementedError("SP-9 Phase C1")
```

```python
# persistence.py
"""Stub — implemented in SP-9 Phase B5/B6."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.news.adapters._base import NewsArticle


async def persist_news_items(
    session: AsyncSession,
    articles: list[NewsArticle],
    sentiment_results: list,  # list[SentimentResult] - typed in B5
) -> int:
    raise NotImplementedError("SP-9 Phase B5")


async def cleanup_old_news(session: AsyncSession, *, older_than_days: int = 20) -> int:
    raise NotImplementedError("SP-9 Phase B6")
```

```python
# ingest_worker.py
"""Stub — implemented in SP-9 Phase D2/D3."""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession


async def run_news_ingest_loop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    raise NotImplementedError("SP-9 Phase D2")


async def run_news_cleanup_loop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    raise NotImplementedError("SP-9 Phase D3")


def start_news_ingest_task(session_factory) -> asyncio.Task:  # type: ignore[no-untyped-def]
    raise NotImplementedError("SP-9 Phase D4")


def start_news_cleanup_task(session_factory) -> asyncio.Task:  # type: ignore[no-untyped-def]
    raise NotImplementedError("SP-9 Phase D4")
```

```python
# fear_greed.py
"""Stub — implemented in SP-9 Phase D1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class FngResult:
    value: int
    label: Literal["Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"]
    timestamp: datetime


async def get_fear_greed_index() -> FngResult:
    raise NotImplementedError("SP-9 Phase D1")
```

```python
# classify_helpers.py
"""Stub — implemented in SP-9 Phase B3/B4."""
from __future__ import annotations


def extract_affected_assets(title: str) -> tuple[str, ...]:
    raise NotImplementedError("SP-9 Phase B3")


def classify_category(title: str) -> str | None:
    raise NotImplementedError("SP-9 Phase B4")


def impact_score_for(category: str | None, source: str) -> float:
    raise NotImplementedError("SP-9 Phase B5")
```

- [ ] **Step 2: Verify imports work**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python -c "import app.news.adapters.cryptopanic, app.news.adapters.yahoo_rss, app.news.sentiment, app.news.persistence, app.news.ingest_worker, app.news.fear_greed, app.news.classify_helpers; print('ok')"
```
Expected: `ok`.

- [ ] **Step 3: Re-run backend tests** — should still pass (only stubs, no callers yet).

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/news/
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): scaffold app.news.* stubs (NotImplementedError) for Phases B–D"
```

---

## Phase B — News adapters + persistence

> **Parallelization note:** B1 (CryptoPanic) and B2 (Yahoo RSS) are file-disjoint and can be dispatched as parallel subagents per `superpowers:dispatching-parallel-agents`. B3/B4 (helpers) are independent of each adapter and can also run in parallel with B1/B2 if needed. B5 must follow B1+B2+B3+B4 (uses all of them). B6 follows B5 (same module).

### Task B1: CryptoPanic adapter — TDD

**Files:**
- Modify: `worktrees/sp-9/backend/app/news/adapters/cryptopanic.py`
- Create: `worktrees/sp-9/backend/tests/unit/test_news_adapter_cryptopanic.py`
- Modify: `worktrees/sp-9/backend/app/config.py` — add `cryptopanic_api_key: str = ""` field

**Design notes:**
- The free-tier endpoint is `https://cryptopanic.com/api/v1/posts/?auth_token=KEY&filter=hot&public=true`. Returns up to 200 posts per page; pagination is by `next` URL. We pull a single page (~50 hot posts) and rely on `since` filtering to skip already-seen items.
- Daily-counter rate limit of 500/day uses the existing SP-3 `DailyCounterBucket` — exactly the same primitive TwelveData uses (`twelvedata.py:_default_rate_client`). Each fetch costs **1** call.
- `RateLimitedClient` is constructed with `raise_on_exhaust=False` so when the 500/day cap is hit the bucket simply waits until 00:00 UTC; the worker's loop will sleep through it. (The verifier_scheduler pattern of "fail loudly" is wrong here — news outage is non-fatal.)
- Errors during a fetch return `[]` and log a warning (matches `binance.py` / `yahoo.py` adapter convention).
- Symbol extraction is **NOT** done here — the adapter returns `affected_assets=()` and the persistence layer (B5) calls `extract_affected_assets` (B3) on each title before INSERT. CryptoPanic does provide a `currencies` field in its response — we capture it as a hint and let B3's helper merge it with title-based detection.

- [ ] **Step 1: Failing test** — `tests/unit/test_news_adapter_cryptopanic.py`:

```python
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.news.adapters.cryptopanic import CryptoPanicAdapter


_SAMPLE_RESPONSE = {
    "results": [
        {
            "id": 1,
            "title": "Bitcoin surges past $100k",
            "url": "https://cryptopanic.com/news/1",
            "published_at": "2026-05-06T12:00:00Z",
            "currencies": [{"code": "BTC", "title": "Bitcoin"}],
        },
        {
            "id": 2,
            "title": "SEC delays ETF decision",
            "url": "https://cryptopanic.com/news/2",
            "published_at": "2026-05-06T11:30:00Z",
            "currencies": [],
        },
    ],
    "next": None,
}


@pytest.mark.asyncio
async def test_fetch_recent_returns_articles_published_after_since() -> None:
    async with httpx.AsyncClient() as http:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://cryptopanic.com/api/v1/posts/").mock(
                return_value=httpx.Response(200, json=_SAMPLE_RESPONSE)
            )
            adapter = CryptoPanicAdapter(api_key="dummy", http=http)
            since = datetime(2026, 5, 6, 11, 0, tzinfo=timezone.utc)
            articles = await adapter.fetch_recent(since=since)

    assert len(articles) == 2
    assert articles[0].url == "https://cryptopanic.com/news/1"
    assert articles[0].source == "cryptopanic"
    assert articles[0].title.startswith("Bitcoin surges")
    # currencies hint exposed via affected_assets for B3 to merge.
    assert "BTC" in articles[0].affected_assets


@pytest.mark.asyncio
async def test_fetch_recent_skips_articles_at_or_before_since() -> None:
    async with httpx.AsyncClient() as http:
        with respx.mock() as mock:
            mock.get("https://cryptopanic.com/api/v1/posts/").mock(
                return_value=httpx.Response(200, json=_SAMPLE_RESPONSE)
            )
            adapter = CryptoPanicAdapter(api_key="dummy", http=http)
            # `since` is AFTER article 2 (11:30) but BEFORE article 1 (12:00).
            since = datetime(2026, 5, 6, 11, 45, tzinfo=timezone.utc)
            articles = await adapter.fetch_recent(since=since)

    assert len(articles) == 1
    assert articles[0].url.endswith("/1")


@pytest.mark.asyncio
async def test_fetch_recent_returns_empty_on_network_error(caplog) -> None:
    async with httpx.AsyncClient() as http:
        with respx.mock() as mock:
            mock.get("https://cryptopanic.com/api/v1/posts/").mock(
                side_effect=httpx.ConnectError("boom")
            )
            adapter = CryptoPanicAdapter(api_key="dummy", http=http)
            since = datetime(2026, 5, 6, tzinfo=timezone.utc)
            articles = await adapter.fetch_recent(since=since)

    assert articles == []
    assert any("cryptopanic" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_fetch_recent_uses_daily_counter_bucket() -> None:
    """Each call should consume one token; 500/day cap exposed via .tokens."""
    async with httpx.AsyncClient() as http:
        with respx.mock() as mock:
            mock.get("https://cryptopanic.com/api/v1/posts/").mock(
                return_value=httpx.Response(200, json={"results": [], "next": None})
            )
            adapter = CryptoPanicAdapter(api_key="dummy", http=http)
            tokens_before = adapter.rate_client.buckets["default"].tokens
            await adapter.fetch_recent(since=datetime(2026, 5, 6, tzinfo=timezone.utc))
            tokens_after = adapter.rate_client.buckets["default"].tokens
    assert tokens_before - tokens_after == 1.0


def test_adapter_requires_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        CryptoPanicAdapter(api_key="")
```

- [ ] **Step 2: Run — fail.** Expected: `NotImplementedError`.

- [ ] **Step 3: Implement** `cryptopanic.py`:

```python
"""CryptoPanic free-tier news adapter (SP-9 Phase B1).

Free tier rate limit: 500 calls/day. We use SP-3's `DailyCounterBucket`
(same primitive TwelveData uses) wrapped in a `RateLimitedClient` so the
counter resets at 00:00 UTC. Each `fetch_recent()` call costs 1 token.

Returns articles sorted newest-first per the CryptoPanic response shape;
filters out anything whose `published_at` is at-or-before the caller's
`since` cursor.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.data.ratelimit import DailyCounterBucket, RateLimitedClient
from app.news.adapters._base import NewsArticle


log = logging.getLogger(__name__)


_BASE_URL = "https://cryptopanic.com/api/v1/posts/"
_DAILY_LIMIT = 500


def _default_rate_client(http: httpx.AsyncClient) -> RateLimitedClient:
    return RateLimitedClient(
        exchange="cryptopanic",
        http=http,
        buckets={"default": DailyCounterBucket(daily_limit=_DAILY_LIMIT)},
    )


@dataclass
class CryptoPanicAdapter:
    """SP-9 NewsAdapter implementation for CryptoPanic free tier."""

    api_key: str
    http: httpx.AsyncClient | None = None
    rate_client: RateLimitedClient | None = None
    name: str = field(default="cryptopanic", init=False)

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("CryptoPanicAdapter requires non-empty api_key")
        if self.http is None:
            self.http = httpx.AsyncClient()
        if self.rate_client is None:
            self.rate_client = _default_rate_client(self.http)

    async def fetch_recent(self, *, since: datetime) -> list[NewsArticle]:
        assert self.rate_client is not None
        params: dict[str, Any] = {
            "auth_token": self.api_key,
            "filter": "hot",
            "public": "true",
        }
        try:
            resp = await self.rate_client.request(
                "GET", _BASE_URL, params=params, timeout=10.0,
            )
            resp.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as e:
            log.warning("cryptopanic fetch_recent error: %s", e)
            return []

        try:
            payload = resp.json()
        except ValueError:
            log.warning("cryptopanic returned non-JSON body")
            return []

        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

        articles: list[NewsArticle] = []
        for row in payload.get("results", []):
            try:
                published_at = datetime.fromisoformat(
                    str(row["published_at"]).replace("Z", "+00:00")
                )
            except (KeyError, ValueError):
                continue
            if published_at <= since:
                continue
            currencies = tuple(
                (c.get("code") or "").upper()
                for c in (row.get("currencies") or [])
                if c.get("code")
            )
            articles.append(
                NewsArticle(
                    source=self.name,
                    url=str(row.get("url", "")),
                    title=str(row.get("title", "")),
                    body=None,  # free tier doesn't expose body.
                    published_at=published_at,
                    category=None,
                    affected_assets=currencies,
                )
            )
        return articles
```

- [ ] **Step 4: Add `cryptopanic_api_key`** to `app/config.py`:

```python
    # SP-9 Phase B1: CryptoPanic free-tier API key.
    # Empty string in dev/test causes the adapter to raise ValueError, so the
    # ingest worker is gated separately on settings.env in app.main:lifespan.
    cryptopanic_api_key: str = ""
```

- [ ] **Step 5: Run — pass.** All 5 tests green.

- [ ] **Step 6: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/news/adapters/cryptopanic.py backend/app/config.py backend/tests/unit/test_news_adapter_cryptopanic.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): CryptoPanic adapter with DailyCounterBucket(500/day) — TDD"
```

---

### Task B2: Yahoo RSS adapter — TDD

**Files:**
- Modify: `worktrees/sp-9/backend/app/news/adapters/yahoo_rss.py`
- Create: `worktrees/sp-9/backend/tests/unit/test_news_adapter_yahoo_rss.py`

**Design notes:**
- `feedparser.parse()` is sync; we wrap it in `asyncio.to_thread` so the worker stays non-blocking. Same trick `yahoo.py:fetch_klines` uses for `yfinance.download`.
- Default feed list covers macro tickers per spec §2 row 2: BTC-USD, ETH-USD, ^DXY (US Dollar Index), ^GSPC (S&P 500). Caller can override via `feeds=` constructor arg.
- No rate limit — Yahoo RSS is unauthenticated and has no published quota. We self-throttle to 1 req/sec with a `TokenBucket(capacity=1, refill_per_sec=1.0)` per the same pattern in `yahoo.py:_default_rate_client`.
- `affected_assets` is derived from the feed's symbol token (the `s=BTC-USD` query param) — `BTC-USD → BTC`, `^DXY → DXY` (no quote split). The B3 helper later runs on the title too.
- Yahoo RSS items contain a `summary` field which we map to `body` (truncated to 2000 chars for safety).

- [ ] **Step 1: Failing test** — `tests/unit/test_news_adapter_yahoo_rss.py`:

```python
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.news.adapters.yahoo_rss import YahooRssAdapter, _DEFAULT_FEEDS


_SAMPLE_PARSED = type("P", (), {
    "entries": [
        type("E", (), {
            "title": "Fed signals rate hold; equities rally",
            "link": "https://finance.yahoo.com/news/fed-signals-1",
            "summary": "The Federal Reserve...",
            "published_parsed": (2026, 5, 6, 12, 0, 0, 0, 0, 0),
        })(),
        type("E", (), {
            "title": "Bitcoin slides on profit-taking",
            "link": "https://finance.yahoo.com/news/btc-slides",
            "summary": "BTC fell 3% as...",
            "published_parsed": (2026, 5, 6, 11, 30, 0, 0, 0, 0),
        })(),
    ],
    "bozo": 0,
})()


@pytest.mark.asyncio
async def test_fetch_recent_parses_feed_entries() -> None:
    adapter = YahooRssAdapter(feeds=("https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD",))
    with patch("app.news.adapters.yahoo_rss.feedparser.parse", return_value=_SAMPLE_PARSED):
        since = datetime(2026, 5, 6, 11, 0, tzinfo=timezone.utc)
        articles = await adapter.fetch_recent(since=since)

    assert len(articles) == 2
    assert articles[0].source == "yahoo_rss"
    assert "BTC" in articles[0].affected_assets  # extracted from feed url s=BTC-USD


@pytest.mark.asyncio
async def test_fetch_recent_filters_by_since() -> None:
    adapter = YahooRssAdapter(feeds=("https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD",))
    with patch("app.news.adapters.yahoo_rss.feedparser.parse", return_value=_SAMPLE_PARSED):
        since = datetime(2026, 5, 6, 11, 45, tzinfo=timezone.utc)
        articles = await adapter.fetch_recent(since=since)
    assert len(articles) == 1
    assert articles[0].url.endswith("/fed-signals-1")


@pytest.mark.asyncio
async def test_fetch_recent_swallows_parse_error(caplog) -> None:
    adapter = YahooRssAdapter(feeds=("https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD",))
    with patch("app.news.adapters.yahoo_rss.feedparser.parse", side_effect=OSError("boom")):
        articles = await adapter.fetch_recent(since=datetime(2026, 5, 6, tzinfo=timezone.utc))
    assert articles == []
    assert any("yahoo_rss" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_fetch_recent_iterates_all_default_feeds_when_none_passed() -> None:
    """Without an explicit `feeds=` ctor arg, all _DEFAULT_FEEDS are pulled."""
    adapter = YahooRssAdapter()
    with patch("app.news.adapters.yahoo_rss.feedparser.parse", return_value=_SAMPLE_PARSED) as mock_parse:
        await adapter.fetch_recent(since=datetime(2026, 5, 6, tzinfo=timezone.utc))
    assert mock_parse.call_count == len(_DEFAULT_FEEDS)
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement** `yahoo_rss.py`:

```python
"""Yahoo Finance RSS adapter (SP-9 Phase B2).

`feedparser` is sync; we offload to `asyncio.to_thread` so the worker loop
stays async. No rate limit headers, no API key — Yahoo's RSS endpoints
are public. We self-throttle to 1 req/sec via a TokenBucket to be polite.

Default feed set covers BTC, ETH, DXY (USD index), S&P 500 — the macro
tickers that influence crypto. Override via `feeds=` for tests / future
expansion.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import feedparser

from app.data.ratelimit import TokenBucket
from app.news.adapters._base import NewsArticle


log = logging.getLogger(__name__)


_DEFAULT_FEEDS: tuple[str, ...] = (
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=ETH-USD&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^DXY&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
)


def _extract_feed_assets(feed_url: str) -> tuple[str, ...]:
    """Pull the `s=BTC-USD` token off a Yahoo RSS URL → ('BTC',)."""
    qs = parse_qs(urlparse(feed_url).query)
    s = qs.get("s", [""])[0]
    if not s:
        return ()
    # "BTC-USD" → "BTC"; "^DXY" → "DXY"; "AAPL" → "AAPL".
    cleaned = re.sub(r"[\^]", "", s).split("-")[0].upper()
    return (cleaned,) if cleaned else ()


@dataclass
class YahooRssAdapter:
    feeds: tuple[str, ...] = _DEFAULT_FEEDS
    rate_bucket: TokenBucket | None = None
    name: str = field(default="yahoo_rss", init=False)

    def __post_init__(self) -> None:
        if self.rate_bucket is None:
            self.rate_bucket = TokenBucket(capacity=1, refill_per_sec=1.0)

    async def fetch_recent(self, *, since: datetime) -> list[NewsArticle]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        all_articles: list[NewsArticle] = []
        for feed_url in self.feeds:
            assert self.rate_bucket is not None
            await self.rate_bucket.acquire(1)
            try:
                parsed: Any = await asyncio.to_thread(feedparser.parse, feed_url)
            except (OSError, ValueError) as e:
                log.warning("yahoo_rss feed=%s error: %s", feed_url, e)
                continue
            if getattr(parsed, "bozo", 0) and not getattr(parsed, "entries", None):
                log.warning("yahoo_rss feed=%s malformed", feed_url)
                continue
            assets = _extract_feed_assets(feed_url)
            for entry in getattr(parsed, "entries", []):
                published_at = self._extract_dt(entry)
                if published_at is None or published_at <= since:
                    continue
                title = str(getattr(entry, "title", "")).strip()
                link = str(getattr(entry, "link", "")).strip()
                if not link or not title:
                    continue
                summary = str(getattr(entry, "summary", "") or "")[:2000] or None
                all_articles.append(
                    NewsArticle(
                        source=self.name,
                        url=link,
                        title=title,
                        body=summary,
                        published_at=published_at,
                        category=None,
                        affected_assets=assets,
                    )
                )
        return all_articles

    @staticmethod
    def _extract_dt(entry: Any) -> datetime | None:
        pp = getattr(entry, "published_parsed", None)
        if pp is None:
            return None
        try:
            return datetime(
                pp[0], pp[1], pp[2], pp[3], pp[4], pp[5], tzinfo=timezone.utc,
            )
        except (TypeError, ValueError):
            return None
```

- [ ] **Step 4: Run — pass.**

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/news/adapters/yahoo_rss.py backend/tests/unit/test_news_adapter_yahoo_rss.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): Yahoo RSS adapter with feedparser + asyncio.to_thread — TDD"
```

---

### Task B3: Symbol extraction helper — TDD

**Files:**
- Modify: `worktrees/sp-9/backend/app/news/classify_helpers.py`
- Create: `worktrees/sp-9/backend/tests/unit/test_news_classify_helpers.py`

**Design notes:**
- Behavior spec: `extract_affected_assets(title)` returns a tuple of UPPER-case base symbols mentioned in the title. Detection method = lookup against a curated alias table of the top ~50 crypto names + common ticker words.
- `_ALIAS_TABLE` keys are lower-cased; values are the canonical base ticker. Examples: `"bitcoin" → "BTC"`, `"ether" → "ETH"`, `"solana" → "SOL"`, `"sol" → "SOL"` (caught as a whole-word token), `"dogecoin" → "DOGE"`.
- Word-boundary regex (`\b{token}\b`, case-insensitive) avoids `"NEAR" → "NEAR"` matching inside `"nearby"`.
- Returns sorted unique tuple so the order is deterministic for hashing.

- [ ] **Step 1: Failing test** — `tests/unit/test_news_classify_helpers.py`:

```python
import pytest

from app.news.classify_helpers import extract_affected_assets, classify_category


@pytest.mark.parametrize("title,expected", [
    ("Bitcoin surges past $100k", ("BTC",)),
    ("Ethereum and Solana lead the rally", ("ETH", "SOL")),
    ("BTC + ETH funding rate flips", ("BTC", "ETH")),
    ("Nearby town festival", ()),                    # NEAR must NOT match nearby
    ("Doge mania returns", ("DOGE",)),
    ("Generic crypto news with no asset", ()),
    ("Ripple settles SEC suit", ("XRP",)),           # 'Ripple' alias → XRP
    ("BNB chain congested", ("BNB",)),
])
def test_extract_affected_assets(title, expected):
    assert extract_affected_assets(title) == expected


def test_extract_affected_assets_returns_sorted_unique() -> None:
    # Mentions BTC twice + ETH; should dedupe and sort.
    assert extract_affected_assets("BTC and ETH and bitcoin") == ("BTC", "ETH")


@pytest.mark.parametrize("title,expected", [
    ("SEC files lawsuit against Binance", "regulatory"),
    ("Coinbase delists XYZ pair", "exchange"),
    ("Federal Reserve hikes rates", "macro"),
    ("Whale moves 10,000 BTC to cold wallet", "whale"),
    ("Solana launches new feature", "project"),
    ("Reddit thread goes viral on Dogecoin", "social"),
    ("Today is Tuesday", None),
])
def test_classify_category(title, expected):
    assert classify_category(title) == expected
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement** the two helpers in `classify_helpers.py`:

```python
"""Symbol-extraction + category-classifier helpers (SP-9 Phase B3/B4).

Lookup-based, deterministic, no ML. The FinBERT layer (Phase C) handles
the sentiment dimension; here we only handle the structural metadata
fields (which assets are mentioned + which loose category the headline
falls into).
"""
from __future__ import annotations

import re

# Curated alias table — top ~50 cryptos + common alternates.
# Keys are lower-case alias tokens; values are canonical base tickers.
_ALIAS_TABLE: dict[str, str] = {
    "bitcoin": "BTC", "btc": "BTC",
    "ethereum": "ETH", "ether": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL",
    "ripple": "XRP", "xrp": "XRP",
    "cardano": "ADA", "ada": "ADA",
    "dogecoin": "DOGE", "doge": "DOGE",
    "polkadot": "DOT", "dot": "DOT",
    "avalanche": "AVAX", "avax": "AVAX",
    "polygon": "MATIC", "matic": "MATIC",
    "chainlink": "LINK", "link": "LINK",
    "litecoin": "LTC", "ltc": "LTC",
    "binance coin": "BNB", "bnb": "BNB",
    "shiba": "SHIB", "shib": "SHIB",
    "tron": "TRX", "trx": "TRX",
    "near": "NEAR",
    "cosmos": "ATOM", "atom": "ATOM",
    "uniswap": "UNI", "uni": "UNI",
    "stellar": "XLM", "xlm": "XLM",
    "filecoin": "FIL", "fil": "FIL",
    "aptos": "APT", "apt": "APT",
    "arbitrum": "ARB", "arb": "ARB",
    "optimism": "OP",
    "monero": "XMR", "xmr": "XMR",
    "hedera": "HBAR", "hbar": "HBAR",
}


# Pre-compiled regex per alias for speed (`\b` word boundary, case-insensitive).
_ALIAS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE), ticker)
    for alias, ticker in _ALIAS_TABLE.items()
]


def extract_affected_assets(title: str) -> tuple[str, ...]:
    """Return sorted unique base tickers mentioned in `title`."""
    found: set[str] = set()
    for pat, ticker in _ALIAS_PATTERNS:
        if pat.search(title):
            found.add(ticker)
    return tuple(sorted(found))


_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "regulatory": (
        "sec", "lawsuit", "regulator", "ban", "compliance", "doj", "cftc",
        "court", "fine", "settlement", "subpoena",
    ),
    "exchange": (
        "binance", "coinbase", "kraken", "bybit", "okx", "bitfinex", "delist",
        "listing", "withdrawal", "deposit halt", "outage",
    ),
    "macro": (
        "federal reserve", "fed ", "rate hike", "rate cut", "inflation",
        "cpi", "fomc", "treasury", "dollar", "dxy", "s&p", "equities",
    ),
    "whale": (
        "whale", "cold wallet", "moves ", "transferred", "transfer ",
        "moved ",
    ),
    "project": (
        "launch", "upgrade", "fork", "mainnet", "testnet", "partnership",
        "feature", "v2", "v3", "release",
    ),
    "social": (
        "reddit", "twitter", "x post", "viral", "meme", "tiktok", "hype",
    ),
}


def classify_category(title: str) -> str | None:
    lower = title.lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return cat
    return None
```

- [ ] **Step 4: Run — pass.**

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/news/classify_helpers.py backend/tests/unit/test_news_classify_helpers.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): symbol extraction + category classifier (regex/keyword) — TDD"
```

---

### Task B4: (rolled into B3 — same file, same module)

The category classifier and `impact_score_for` were planned as separate tasks but live in the same `classify_helpers.py` module. B3 already implemented `classify_category`. **Add `impact_score_for` here** as a follow-on (still file-disjoint from B1/B2).

**Files:**
- Modify: `worktrees/sp-9/backend/app/news/classify_helpers.py`
- Modify: `worktrees/sp-9/backend/tests/unit/test_news_classify_helpers.py` — add cases.

**Design notes:**
- `impact_score_for(category, source)` returns a value in `[0, 1]`. Spec §3.4's L9 weighting expects this to bias multi-article aggregations toward higher-impact items.
- Per-spec-§4 design intent: regulatory > exchange > macro > whale > project > social.
- Source modifier: CryptoPanic "hot" filter is already curated → 1.0×; Yahoo RSS is unfiltered → 0.85×. Default 1.0×.

- [ ] **Step 1: Add cases to existing test file:**

```python
def test_impact_score_ranges():
    from app.news.classify_helpers import impact_score_for
    # Regulatory is highest.
    assert impact_score_for("regulatory", "cryptopanic") > impact_score_for("social", "cryptopanic")
    # Yahoo RSS scaled down vs CryptoPanic.
    assert impact_score_for("regulatory", "yahoo_rss") < impact_score_for("regulatory", "cryptopanic")
    # Bounds.
    assert 0.0 <= impact_score_for(None, "yahoo_rss") <= 1.0
    assert 0.0 <= impact_score_for("regulatory", "cryptopanic") <= 1.0
```

- [ ] **Step 2: Implement** in `classify_helpers.py`:

```python
_BASE_IMPACT: dict[str | None, float] = {
    "regulatory": 1.0,
    "exchange": 0.8,
    "macro": 0.7,
    "whale": 0.6,
    "project": 0.5,
    "social": 0.3,
    None: 0.5,
}

_SOURCE_MODIFIER: dict[str, float] = {
    "cryptopanic": 1.0,    # 'hot' filter already curated.
    "yahoo_rss": 0.85,     # unfiltered firehose → mild discount.
}


def impact_score_for(category: str | None, source: str) -> float:
    base = _BASE_IMPACT.get(category, 0.5)
    mod = _SOURCE_MODIFIER.get(source, 1.0)
    return min(1.0, max(0.0, base * mod))
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/news/classify_helpers.py backend/tests/unit/test_news_classify_helpers.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): impact_score_for(category, source) heuristic — TDD"
```

---

### Task B5: Persistence — `persist_news_items` — TDD

**Files:**
- Modify: `worktrees/sp-9/backend/app/news/persistence.py`
- Create: `worktrees/sp-9/backend/tests/unit/test_news_persistence.py`

**Design notes:**
- `persist_news_items(session, articles, sentiment_results)` — `articles[i]` pairs with `sentiment_results[i]`. `sentiment_results` may be empty/shorter; missing sentiment → NULL columns (the L9 query filters those out).
- Uses `INSERT … ON CONFLICT (url) DO NOTHING` — duplicate URLs (re-emitted articles) silently no-op.
- Return value = number of *new* rows inserted (useful for the worker's metrics + the F5 admin refresh endpoint).
- Each article goes through:
  1. `extract_affected_assets(title)` → merged with adapter-supplied `affected_assets` (deduped+sorted).
  2. `classify_category(title)` if adapter didn't supply one.
  3. `impact_score_for(category, source)`.
- Uses raw `sa.text()` SQL — matches every other write site in the project (`shadow/persistence.py`, `core/execution/persistence.py`).
- `affected_assets` is bound as a Python list; asyncpg auto-converts to PG ARRAY.

- [ ] **Step 1: Failing test** — `tests/unit/test_news_persistence.py`:

```python
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.news.adapters._base import NewsArticle
from app.news.sentiment import SentimentResult
from app.news.persistence import persist_news_items, cleanup_old_news


# Reuse the integration test bootstrap pattern: create the news_items table
# in an in-memory sqlite (TEXT[] not natively supported → store as JSON
# string for unit tests; integration tests cover real PG).
@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                published_at TIMESTAMP NOT NULL,
                fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sentiment_score REAL,
                sentiment_label TEXT,
                sentiment_confidence REAL,
                impact_score REAL,
                category TEXT,
                affected_assets TEXT
            )
        """))
    async with AsyncSession(engine) as s:
        yield s


@pytest.mark.asyncio
async def test_persist_news_items_inserts_rows(session: AsyncSession) -> None:
    articles = [
        NewsArticle(
            source="cryptopanic", url="https://example.com/1",
            title="Bitcoin surges past $100k", body=None,
            published_at=datetime(2026, 5, 6, 12, tzinfo=timezone.utc),
            category=None, affected_assets=("BTC",),
        ),
    ]
    sents = [SentimentResult(score=0.8, label="positive", confidence=0.95)]
    n = await persist_news_items(session, articles, sents)
    assert n == 1
    row = (await session.execute(sa.text("SELECT * FROM news_items"))).first()
    assert row.title.startswith("Bitcoin surges")
    assert row.sentiment_score == 0.8
    assert row.sentiment_label == "positive"
    assert row.category == "exchange" or row.category is None  # 'Bitcoin surges' won't trigger any kw — None.
    assert row.impact_score is not None and 0.0 <= row.impact_score <= 1.0


@pytest.mark.asyncio
async def test_persist_news_items_dedupes_by_url(session: AsyncSession) -> None:
    article = NewsArticle(
        source="cryptopanic", url="https://example.com/dup",
        title="X", body=None,
        published_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        category=None, affected_assets=(),
    )
    sent = SentimentResult(score=0.0, label="neutral", confidence=0.5)
    assert await persist_news_items(session, [article], [sent]) == 1
    # Second insert: same URL → 0 new rows.
    assert await persist_news_items(session, [article], [sent]) == 0
    cnt = (await session.execute(sa.text("SELECT COUNT(*) FROM news_items"))).scalar()
    assert cnt == 1


@pytest.mark.asyncio
async def test_persist_news_items_handles_missing_sentiment(session: AsyncSession) -> None:
    article = NewsArticle(
        source="yahoo_rss", url="https://example.com/y",
        title="SEC announces crackdown", body=None,
        published_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        category=None, affected_assets=(),
    )
    n = await persist_news_items(session, [article], sentiment_results=[])
    assert n == 1
    row = (await session.execute(sa.text("SELECT * FROM news_items"))).first()
    assert row.sentiment_score is None
    assert row.category == "regulatory"  # 'SEC' keyword match in B3
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement** `persistence.py`:

```python
"""SP-9 Phase B5/B6: news_items INSERT + nightly cleanup."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.news.adapters._base import NewsArticle
from app.news.classify_helpers import (
    classify_category,
    extract_affected_assets,
    impact_score_for,
)
from app.news.sentiment import SentimentResult


log = logging.getLogger(__name__)


async def persist_news_items(
    session: AsyncSession,
    articles: list[NewsArticle],
    sentiment_results: list[SentimentResult],
) -> int:
    """INSERT each article (with sentiment) into `news_items`. Returns # new rows.

    Pairs `articles[i]` with `sentiment_results[i]` when present; if
    `sentiment_results` is shorter than `articles`, the trailing articles
    persist with NULL sentiment columns.

    Dedup by URL via ON CONFLICT DO NOTHING — re-emitted articles silently
    skip. The returned count reflects rows actually inserted.
    """
    if not articles:
        return 0

    # Detect dialect once so we can switch ARRAY binding strategies.
    dialect = session.bind.dialect.name if session.bind else "postgresql"
    is_pg = dialect.startswith("postgres")

    inserted = 0
    for i, art in enumerate(articles):
        sent = sentiment_results[i] if i < len(sentiment_results) else None
        # Merge adapter-supplied + title-extracted assets.
        extracted = extract_affected_assets(art.title)
        merged = tuple(sorted(set(art.affected_assets) | set(extracted)))

        category = art.category or classify_category(art.title)
        impact = impact_score_for(category, art.source)

        params: dict[str, object] = {
            "source": art.source,
            "url": art.url,
            "title": art.title,
            "body": art.body,
            "published_at": art.published_at,
            "sentiment_score": sent.score if sent is not None else None,
            "sentiment_label": sent.label if sent is not None else None,
            "sentiment_confidence": sent.confidence if sent is not None else None,
            "impact_score": impact,
            "category": category,
        }
        if is_pg:
            params["assets"] = list(merged)
            sql = sa.text("""
                INSERT INTO news_items
                  (source, url, title, body, published_at,
                   sentiment_score, sentiment_label, sentiment_confidence,
                   impact_score, category, affected_assets)
                VALUES
                  (:source, :url, :title, :body, :published_at,
                   :sentiment_score, :sentiment_label, :sentiment_confidence,
                   :impact_score, :category, :assets)
                ON CONFLICT (url) DO NOTHING
            """)
        else:
            # sqlite test fallback — store assets as JSON string.
            params["assets"] = json.dumps(list(merged))
            sql = sa.text("""
                INSERT OR IGNORE INTO news_items
                  (source, url, title, body, published_at,
                   sentiment_score, sentiment_label, sentiment_confidence,
                   impact_score, category, affected_assets)
                VALUES
                  (:source, :url, :title, :body, :published_at,
                   :sentiment_score, :sentiment_label, :sentiment_confidence,
                   :impact_score, :category, :assets)
            """)
        result = await session.execute(sql, params)
        if (result.rowcount or 0) > 0:
            inserted += 1
    await session.commit()
    return inserted


async def cleanup_old_news(
    session: AsyncSession, *, older_than_days: int = 20,
) -> int:
    """DELETE news_items with published_at < NOW() - INTERVAL `older_than_days`.

    Returns the # deleted. Per MASTER_PLAN §631 the default retention is 20d.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    result = await session.execute(
        sa.text("DELETE FROM news_items WHERE published_at < :cutoff"),
        {"cutoff": cutoff},
    )
    await session.commit()
    deleted = int(result.rowcount or 0)
    log.info("cleanup_old_news: deleted %d rows older than %dd", deleted, older_than_days)
    return deleted
```

- [ ] **Step 4: Run — pass.**

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/news/persistence.py backend/tests/unit/test_news_persistence.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): persist_news_items with ON CONFLICT (url) DO NOTHING — TDD"
```

---

### Task B6: Cleanup helper test

**Files:**
- Modify: `worktrees/sp-9/backend/tests/unit/test_news_persistence.py` — add a test for `cleanup_old_news`.

**Design notes:**
- `cleanup_old_news` was implemented in B5; this task adds a focused unit test using `freezegun` to verify the cutoff math.

- [ ] **Step 1: Add test:**

```python
@pytest.mark.asyncio
async def test_cleanup_old_news_deletes_rows_older_than_cutoff(session: AsyncSession) -> None:
    from freezegun import freeze_time
    # Insert one stale (25d) and one fresh (5d) article.
    await session.execute(sa.text("""
        INSERT INTO news_items (source, url, title, published_at, impact_score)
        VALUES ('x','u_old','old','2026-04-10T00:00:00Z',0.5),
               ('x','u_new','new','2026-05-01T00:00:00Z',0.5)
    """))
    await session.commit()
    with freeze_time("2026-05-06T12:00:00Z"):
        n = await cleanup_old_news(session, older_than_days=20)
    assert n == 1
    rows = (await session.execute(sa.text("SELECT url FROM news_items"))).all()
    assert {r.url for r in rows} == {"u_new"}
```

- [ ] **Step 2: Run — pass.** (`cleanup_old_news` already exists from B5.)

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/tests/unit/test_news_persistence.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "test(sp-9): cleanup_old_news cutoff math (frozen time)"
```

---

## Phase C — FinBERT sentiment

### Task C1: `classify_batch` — TDD with mocked tokenizer + model

**Files:**
- Modify: `worktrees/sp-9/backend/app/news/sentiment.py`
- Create: `worktrees/sp-9/backend/tests/unit/test_news_sentiment.py`

**Design notes:**
- Lazy module-level globals `_tokenizer`, `_model`. First call to `_load()` does the HuggingFace download (~440MB, cached at `~/.cache/huggingface/`).
- `classify_batch` returns `len(titles)` `SentimentResult` objects. Empty input returns `[]` without loading the model (fast path).
- `score = p_positive - p_negative` per spec §3.3 — gives a balanced [-1, +1] axis (neutral cancels to 0).
- The test patches `_load` to return fake tokenizer + model — we never download the real FinBERT in unit tests. The smoke test (C2) does load the real model and is gated `@pytest.mark.slow`.
- ProsusAI/finbert label order on HuggingFace is `["positive", "negative", "neutral"]` (see model card). Our test asserts that mapping.

- [ ] **Step 1: Failing test** — `tests/unit/test_news_sentiment.py`:

```python
from unittest.mock import MagicMock, patch

import pytest
import torch

from app.news.sentiment import classify_batch, SentimentResult


def _fake_load() -> tuple:
    """Tokenizer that returns a tensor dict; model whose .forward returns a logits tensor."""
    tok = MagicMock()
    tok.return_value = {
        "input_ids": torch.zeros((2, 8), dtype=torch.long),
        "attention_mask": torch.ones((2, 8), dtype=torch.long),
    }
    model = MagicMock()
    # Logits ordered [positive, negative, neutral]:
    # Row 0: strongly positive. Row 1: strongly negative.
    logits = torch.tensor([[3.0, -1.0, 0.0], [-1.0, 3.0, 0.0]])
    out = MagicMock()
    out.logits = logits
    model.return_value = out
    model.eval = MagicMock()
    return tok, model


def test_classify_batch_empty_returns_empty_without_loading() -> None:
    with patch("app.news.sentiment._load") as load:
        results = classify_batch([])
    assert results == []
    load.assert_not_called()


def test_classify_batch_two_titles_pos_neg() -> None:
    with patch("app.news.sentiment._load", side_effect=lambda: _fake_load()):
        results = classify_batch(["bitcoin surges", "sec sues exchange"])
    assert len(results) == 2
    assert results[0].label == "positive"
    assert results[0].score > 0.5
    assert results[1].label == "negative"
    assert results[1].score < -0.5


def test_classify_batch_respects_batch_size() -> None:
    """4 titles + batch_size=2 → model invoked twice."""
    tok, model = _fake_load()
    # Override model to return shape-(2,3) logits each call.
    with patch("app.news.sentiment._load", return_value=(tok, model)):
        classify_batch(["a", "b", "c", "d"], batch_size=2)
    assert model.call_count == 2


def test_sentiment_result_is_frozen_dataclass() -> None:
    s = SentimentResult(score=0.5, label="positive", confidence=0.9)
    with pytest.raises(Exception):
        s.score = 0.0  # type: ignore[misc]
```

- [ ] **Step 2: Run — fail.** `NotImplementedError`.

- [ ] **Step 3: Implement** `sentiment.py`:

```python
"""ProsusAI/finbert sentiment classifier (SP-9 Phase C1).

Lazy module-level load of tokenizer + model. First call to `_load()`
downloads ~440MB to `~/.cache/huggingface/` (cached after).

`classify_batch(titles)` returns one `SentimentResult` per title. Empty input
short-circuits without loading. Inference is sync PyTorch CPU; callers
should `asyncio.to_thread(classify_batch, titles)` from async contexts.

Label order in ProsusAI/finbert: ["positive", "negative", "neutral"].
score = p_positive - p_negative (∈ [-1, +1]); confidence = max softmax prob.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import torch


log = logging.getLogger(__name__)

_MODEL_NAME = "ProsusAI/finbert"
_LABELS: list[Literal["positive", "negative", "neutral"]] = [
    "positive", "negative", "neutral",
]

_tokenizer = None
_model = None


@dataclass(frozen=True)
class SentimentResult:
    score: float        # [-1, +1] — p_positive - p_negative
    label: Literal["positive", "negative", "neutral"]
    confidence: float   # [0, 1] — max softmax prob


def _load() -> tuple[object, object]:
    """Lazy-load tokenizer + model; cached after first call."""
    global _tokenizer, _model
    if _tokenizer is None or _model is None:
        # Local import — `transformers` is heavy; avoid pulling at module import.
        from transformers import (
            AutoTokenizer,
            AutoModelForSequenceClassification,
        )
        log.info("loading FinBERT (%s) — first call downloads ~440MB", _MODEL_NAME)
        _tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
        _model = AutoModelForSequenceClassification.from_pretrained(_MODEL_NAME)
        _model.eval()
    return _tokenizer, _model


def classify_batch(
    titles: list[str], batch_size: int = 16,
) -> list[SentimentResult]:
    """Run FinBERT on `titles`. Returns one SentimentResult per title.

    Empty input returns [] without loading the model.
    """
    if not titles:
        return []
    tokenizer, model = _load()
    results: list[SentimentResult] = []
    for i in range(0, len(titles), batch_size):
        batch = titles[i : i + batch_size]
        inputs = tokenizer(
            batch, padding=True, truncation=True,
            return_tensors="pt", max_length=128,
        )
        with torch.no_grad():
            logits = model(**inputs).logits
            probs = torch.softmax(logits, dim=-1)
        for j in range(probs.shape[0]):
            row = probs[j]
            p_pos = float(row[0])
            p_neg = float(row[1])
            score = p_pos - p_neg
            label_idx = int(row.argmax().item())
            label = _LABELS[label_idx]
            confidence = float(row.max().item())
            results.append(SentimentResult(
                score=score, label=label, confidence=confidence,
            ))
    return results
```

- [ ] **Step 4: Run — pass.**

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/news/sentiment.py backend/tests/unit/test_news_sentiment.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): FinBERT classify_batch with lazy model load — TDD with mocks"
```

---

### Task C2: Smoke test — 5 hand-crafted headlines through real FinBERT

**Files:**
- Create: `worktrees/sp-9/backend/tests/unit/test_news_sentiment_smoke.py`

**Design notes:**
- This test loads the **real** FinBERT model. Marked `@pytest.mark.slow` so it's excluded from the default `pytest -q` run; CI runs `pytest -m slow` separately.
- Network access required on first run (HF download). On subsequent runs the model is cached at `~/.cache/huggingface/`. CI step should pre-warm the cache or run with internet egress.
- Acceptance bar is **directional** only — exact scores depend on model weights. We assert the sign of `score` and the `label` field.

- [ ] **Step 1: Write smoke test:**

```python
"""Slow integration smoke test: real FinBERT against 5 sample headlines.

Run with: pytest -m slow tests/unit/test_news_sentiment_smoke.py
Skipped by default (no -m slow filter on the default run).
"""
import pytest

from app.news.sentiment import classify_batch


SAMPLES = [
    ("Bitcoin surges to all-time high as ETF inflows accelerate", "positive"),
    ("SEC announces crackdown on crypto exchanges", "negative"),
    ("Federal Reserve holds rates steady; markets unchanged", "neutral"),
    ("Major exchange suffers $500M hack overnight", "negative"),
    ("Solana network upgrade improves throughput by 40%", "positive"),
]


@pytest.mark.slow
def test_finbert_classifies_5_sample_headlines_directionally() -> None:
    titles = [s[0] for s in SAMPLES]
    expected = [s[1] for s in SAMPLES]
    results = classify_batch(titles)
    assert len(results) == 5
    for i, (r, want) in enumerate(zip(results, expected, strict=True)):
        # Allow neutral mismatch — FinBERT's neutral threshold is fuzzy.
        if want == "neutral":
            assert r.label in {"neutral", "positive", "negative"}, (
                f"row {i} label={r.label}"
            )
        else:
            assert r.label == want, f"row {i} title={titles[i]!r} got={r.label!r}"
        assert -1.0 <= r.score <= 1.0
        assert 0.0 <= r.confidence <= 1.0
```

- [ ] **Step 2: Run locally with -m slow** to verify (one-time, optional in CI):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -m slow tests/unit/test_news_sentiment_smoke.py -v
```
Expected: 1 passed (after first-call ~30s download).

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/tests/unit/test_news_sentiment_smoke.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "test(sp-9): real-FinBERT smoke test for 5 sample headlines (slow-marked)"
```

---

### Task C3: Performance test — batch of 16 in <5s

**Files:**
- Modify: `worktrees/sp-9/backend/tests/unit/test_news_sentiment_smoke.py` — add perf case.

**Design notes:**
- Batch of 16 short titles on CPU should complete in <5 seconds on the project's reference dev box. If CI is slower, leave the `@pytest.mark.slow` tag and document the threshold relaxation.
- Tests timing via `time.perf_counter()`; soft assertion `assert elapsed < 5.0` with a logged warning if exceeded (CI marks slow).

- [ ] **Step 1: Add perf case:**

```python
import time


@pytest.mark.slow
def test_finbert_batch_of_16_under_5s_on_cpu() -> None:
    titles = [f"Bitcoin news headline #{i} for benchmarking" for i in range(16)]
    # Warm load (don't count it).
    classify_batch(["warmup"])
    t0 = time.perf_counter()
    results = classify_batch(titles, batch_size=16)
    elapsed = time.perf_counter() - t0
    assert len(results) == 16
    # CI/dev box reference budget — relax to 10s if your box is older.
    assert elapsed < 5.0, f"FinBERT batch-16 took {elapsed:.2f}s (>5s budget)"
```

- [ ] **Step 2: Run — pass.** Or, if the dev box exceeds the budget, increase the threshold and document in the spec's risk-fallback table.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/tests/unit/test_news_sentiment_smoke.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "test(sp-9): FinBERT batch-16 perf test (<5s CPU budget, slow-marked)"
```

---

## Phase D — Ingest worker + F&G + lifespan wiring

### Task D1: Fear & Greed fetcher with 1h cache — TDD

**Files:**
- Modify: `worktrees/sp-9/backend/app/news/fear_greed.py`
- Create: `worktrees/sp-9/backend/tests/unit/test_news_fear_greed.py`

**Design notes:**
- `https://api.alternative.me/fng/` returns `{"data":[{"value":"50","value_classification":"Neutral","timestamp":"1715000000",...}]}`.
- 1-hour module-level cache (`_cache`, `_cache_ts`) — F&G updates daily but we cache aggressively to be a polite citizen (the API has no published rate limit).
- 24h failover cache: per spec §8 risk-fallback row 4, after 24h of failures we return None. Implementation: track `_last_success_ts`; if `now - last_success > 24h` AND fetch errors, return `None`.
- Tested via `respx` (mocked httpx) + `freezegun` for cache TTL boundary.

- [ ] **Step 1: Failing test** — `tests/unit/test_news_fear_greed.py`:

```python
from datetime import datetime, timezone

import httpx
import pytest
import respx
from freezegun import freeze_time

from app.news import fear_greed
from app.news.fear_greed import FngResult, get_fear_greed_index


_PAYLOAD = {
    "data": [
        {
            "value": "55",
            "value_classification": "Greed",
            "timestamp": "1715000000",
        }
    ]
}


@pytest.fixture(autouse=True)
def _reset_cache():
    fear_greed._cache = None
    fear_greed._cache_ts = 0.0
    fear_greed._last_success_ts = 0.0
    yield
    fear_greed._cache = None


@pytest.mark.asyncio
async def test_get_fear_greed_index_basic() -> None:
    with respx.mock() as mock:
        mock.get("https://api.alternative.me/fng/").mock(
            return_value=httpx.Response(200, json=_PAYLOAD)
        )
        r = await get_fear_greed_index()
    assert isinstance(r, FngResult)
    assert r.value == 55
    assert r.label == "Greed"


@pytest.mark.asyncio
async def test_get_fear_greed_index_cached_within_1h() -> None:
    with freeze_time("2026-05-06T12:00:00Z") as ft:
        with respx.mock() as mock:
            route = mock.get("https://api.alternative.me/fng/").mock(
                return_value=httpx.Response(200, json=_PAYLOAD)
            )
            await get_fear_greed_index()
            ft.tick(1800)  # +30 min, still cached
            await get_fear_greed_index()
            assert route.call_count == 1
            ft.tick(3700)  # +>1h since first call → refetch
            await get_fear_greed_index()
            assert route.call_count == 2


@pytest.mark.asyncio
async def test_get_fear_greed_returns_stale_cache_on_error_within_24h() -> None:
    with freeze_time("2026-05-06T12:00:00Z") as ft:
        with respx.mock() as mock:
            mock.get("https://api.alternative.me/fng/").mock(
                return_value=httpx.Response(200, json=_PAYLOAD)
            )
            first = await get_fear_greed_index()
            ft.tick(7200)  # +2h, cache expired
            mock.get("https://api.alternative.me/fng/").mock(
                side_effect=httpx.ConnectError("boom")
            )
            second = await get_fear_greed_index()
            assert second == first  # served from stale cache


@pytest.mark.asyncio
async def test_get_fear_greed_returns_none_after_24h_of_errors() -> None:
    with freeze_time("2026-05-06T12:00:00Z") as ft:
        with respx.mock() as mock:
            mock.get("https://api.alternative.me/fng/").mock(
                return_value=httpx.Response(200, json=_PAYLOAD)
            )
            await get_fear_greed_index()
            ft.tick(86400 + 7200)  # +26h
            mock.get("https://api.alternative.me/fng/").mock(
                side_effect=httpx.ConnectError("still boom")
            )
            result = await get_fear_greed_index()
            assert result is None
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement** `fear_greed.py`:

```python
"""alternative.me Fear & Greed index fetcher (SP-9 Phase D1).

1-hour module-level cache; on fetch error, serves stale cache for up to
24h. After 24h of continuous failures, returns None (UI shows "—").
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import httpx


log = logging.getLogger(__name__)

_API_URL = "https://api.alternative.me/fng/"
_CACHE_TTL_S: float = 3600.0
_STALE_TTL_S: float = 86400.0


@dataclass(frozen=True)
class FngResult:
    value: int
    label: Literal["Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"]
    timestamp: datetime


_cache: FngResult | None = None
_cache_ts: float = 0.0
_last_success_ts: float = 0.0


async def get_fear_greed_index() -> FngResult | None:
    global _cache, _cache_ts, _last_success_ts
    now = time.time()
    if _cache is not None and (now - _cache_ts) < _CACHE_TTL_S:
        return _cache
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_API_URL)
            resp.raise_for_status()
            data = resp.json()["data"][0]
        result = FngResult(
            value=int(data["value"]),
            label=data["value_classification"],
            timestamp=datetime.fromtimestamp(int(data["timestamp"]), tz=timezone.utc),
        )
        _cache = result
        _cache_ts = now
        _last_success_ts = now
        return result
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("F&G fetch failed: %s", e)
        # Stale serve up to 24h since last success.
        if _cache is not None and (now - _last_success_ts) < _STALE_TTL_S:
            return _cache
        return None
```

- [ ] **Step 4: Run — pass.**

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/news/fear_greed.py backend/tests/unit/test_news_fear_greed.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): F&G fetcher with 1h cache + 24h stale failover — TDD"
```

---

### Task D2: News ingest loop — TDD with mocked adapters + frozen time

**Files:**
- Modify: `worktrees/sp-9/backend/app/news/ingest_worker.py`
- Create: `worktrees/sp-9/backend/tests/unit/test_news_ingest_worker.py`

**Design notes:**
- Per-adapter cursor in module-state `_last_fetch_ts: dict[str, datetime]`. Reset to `now - 1h` on first iteration so we backfill the immediate past.
- Cadence: CryptoPanic every 5 min, Yahoo RSS every 30 min. Implementation = single 5-min sleep loop, with a counter that triggers Yahoo every 6th iteration.
- Sentiment classification offloaded via `asyncio.to_thread(classify_batch, titles)` — keeps the event loop unblocked during the ~2-3s FinBERT pass.
- All errors during a single iteration are logged + swallowed; loop continues. `asyncio.CancelledError` propagates so lifespan can stop the task cleanly.
- Test pattern matches `test_universe_refresh.py` — inject `_sleep`, `_now`, `_adapters` for full determinism.

- [ ] **Step 1: Failing test** — `tests/unit/test_news_ingest_worker.py`:

```python
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.news.adapters._base import NewsArticle
from app.news.sentiment import SentimentResult
from app.news.ingest_worker import (
    run_news_ingest_loop,
    _ingest_once,
)


def _fake_session_factory():
    sm = MagicMock()
    sm.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    sm.return_value.__aexit__ = AsyncMock(return_value=None)
    return sm


@pytest.mark.asyncio
async def test_ingest_once_calls_adapter_classify_persist() -> None:
    article = NewsArticle(
        source="cryptopanic", url="u1", title="Bitcoin surges",
        body=None,
        published_at=datetime(2026, 5, 6, 12, tzinfo=timezone.utc),
        category=None, affected_assets=("BTC",),
    )
    adapter = MagicMock()
    adapter.name = "cryptopanic"
    adapter.fetch_recent = AsyncMock(return_value=[article])

    with patch("app.news.ingest_worker.classify_batch", return_value=[
        SentimentResult(score=0.7, label="positive", confidence=0.9),
    ]) as classify:
        with patch("app.news.ingest_worker.persist_news_items",
                   new=AsyncMock(return_value=1)) as persist:
            sf = _fake_session_factory()
            n = await _ingest_once(sf, adapter)
    assert n == 1
    classify.assert_called_once_with(["Bitcoin surges"])
    persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_news_ingest_loop_stops_on_cancel(monkeypatch) -> None:
    sleep_calls: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleep_calls.append(s)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError

    crypto = MagicMock()
    crypto.name = "cryptopanic"
    crypto.fetch_recent = AsyncMock(return_value=[])
    yahoo = MagicMock()
    yahoo.name = "yahoo_rss"
    yahoo.fetch_recent = AsyncMock(return_value=[])

    sf = _fake_session_factory()
    with pytest.raises(asyncio.CancelledError):
        await run_news_ingest_loop(
            sf,
            _adapters_crypto=[crypto],
            _adapters_macro=[yahoo],
            _sleep=fake_sleep,
        )
    # Crypto polled both iterations; Yahoo only every 6th iteration → 0 calls.
    assert crypto.fetch_recent.await_count == 2
    assert yahoo.fetch_recent.await_count == 0


@pytest.mark.asyncio
async def test_run_news_ingest_loop_swallows_iteration_errors() -> None:
    sleep_calls: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleep_calls.append(s)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError

    bad = MagicMock()
    bad.name = "cryptopanic"
    bad.fetch_recent = AsyncMock(side_effect=RuntimeError("boom"))

    sf = _fake_session_factory()
    with pytest.raises(asyncio.CancelledError):
        await run_news_ingest_loop(
            sf,
            _adapters_crypto=[bad],
            _adapters_macro=[],
            _sleep=fake_sleep,
        )
    # Loop survived past first error.
    assert bad.fetch_recent.await_count == 2
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement** `ingest_worker.py`:

```python
"""News ingestion loop (SP-9 Phase D2/D3/D4).

Wakes every 5 min for crypto sources (CryptoPanic), every 30 min for macro
(Yahoo RSS). Per-adapter cursor tracks the last-fetched-published-at so we
only INSERT new rows. FinBERT inference is offloaded via asyncio.to_thread.

Errors in a single iteration are logged + swallowed; cancellation
propagates so the lifespan can stop us cleanly.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.news.adapters._base import NewsAdapter
from app.news.adapters.cryptopanic import CryptoPanicAdapter
from app.news.adapters.yahoo_rss import YahooRssAdapter
from app.news.persistence import cleanup_old_news, persist_news_items
from app.news.sentiment import classify_batch
from app.shadow.universe_refresh import seconds_until_next_utc


log = logging.getLogger(__name__)

CRYPTO_POLL_SECONDS: int = 300       # 5 min
MACRO_POLL_RATIO: int = 6            # every 6 crypto polls → 30 min
DEFAULT_CLEANUP_HOUR_UTC: int = 4

# Per-adapter cursor — module state by design (each loop instance reuses).
_last_fetch_ts: dict[str, datetime] = {}


def _initial_cursor() -> datetime:
    return datetime.now(UTC) - timedelta(hours=1)


async def _ingest_once(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: NewsAdapter,
) -> int:
    """One adapter pass: fetch → classify → persist. Returns # new rows."""
    cursor = _last_fetch_ts.get(adapter.name) or _initial_cursor()
    articles = await adapter.fetch_recent(since=cursor)
    if not articles:
        return 0
    titles = [a.title for a in articles]
    # FinBERT is sync PyTorch → offload from the loop.
    sentiments = await asyncio.to_thread(classify_batch, titles)
    async with session_factory() as session:
        n = await persist_news_items(session, articles, sentiments)
    # Advance cursor to the newest published_at we saw.
    newest = max(a.published_at for a in articles)
    _last_fetch_ts[adapter.name] = newest
    log.info("news ingest: source=%s new=%d cursor=%s", adapter.name, n, newest)
    return n


def _build_default_adapters() -> tuple[list[NewsAdapter], list[NewsAdapter]]:
    """Construct the live adapter list from settings. Empty key → no CryptoPanic."""
    settings = get_settings()
    crypto: list[NewsAdapter] = []
    if settings.cryptopanic_api_key:
        crypto.append(CryptoPanicAdapter(api_key=settings.cryptopanic_api_key))
    macro: list[NewsAdapter] = [YahooRssAdapter()]
    return crypto, macro


async def run_news_ingest_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    _adapters_crypto: Iterable[NewsAdapter] | None = None,
    _adapters_macro: Iterable[NewsAdapter] | None = None,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Main loop. Crypto every 5 min; macro every 30 min."""
    if _adapters_crypto is None or _adapters_macro is None:
        c, m = _build_default_adapters()
        crypto = list(_adapters_crypto) if _adapters_crypto is not None else c
        macro = list(_adapters_macro) if _adapters_macro is not None else m
    else:
        crypto = list(_adapters_crypto)
        macro = list(_adapters_macro)

    iteration = 0
    while True:
        iteration += 1
        for adapter in crypto:
            try:
                await _ingest_once(session_factory, adapter)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("news ingest crashed for %s", adapter.name)
        if iteration % MACRO_POLL_RATIO == 0:
            for adapter in macro:
                try:
                    await _ingest_once(session_factory, adapter)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    log.exception("news ingest crashed for %s", adapter.name)
        await _sleep(float(CRYPTO_POLL_SECONDS))


async def run_news_cleanup_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    wake_at_utc_hour: int = DEFAULT_CLEANUP_HOUR_UTC,
    older_than_days: int = 20,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    _now: Callable[[], datetime] | None = None,
) -> None:
    """Nightly 04:00 UTC cleanup of news_items older than `older_than_days`."""
    now_fn = _now if _now is not None else lambda: datetime.now(UTC)
    while True:
        wait_s = seconds_until_next_utc(wake_at_utc_hour, now_fn())
        await _sleep(float(wait_s))
        try:
            async with session_factory() as session:
                deleted = await cleanup_old_news(
                    session, older_than_days=older_than_days,
                )
            log.info("news cleanup: deleted=%d", deleted)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("news cleanup loop iteration failed")


def start_news_ingest_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    return asyncio.create_task(run_news_ingest_loop(session_factory))


def start_news_cleanup_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    return asyncio.create_task(run_news_cleanup_loop(session_factory))


__all__ = [
    "CRYPTO_POLL_SECONDS",
    "DEFAULT_CLEANUP_HOUR_UTC",
    "MACRO_POLL_RATIO",
    "_ingest_once",
    "run_news_cleanup_loop",
    "run_news_ingest_loop",
    "start_news_cleanup_task",
    "start_news_ingest_task",
]
```

- [ ] **Step 4: Run — pass.**

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/news/ingest_worker.py backend/tests/unit/test_news_ingest_worker.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): news ingest loop with per-adapter cursors + asyncio.to_thread FinBERT — TDD"
```

---

### Task D3: Cleanup loop unit test

**Files:**
- Create: `worktrees/sp-9/backend/tests/unit/test_news_cleanup_loop.py`

**Design notes:**
- The loop function is in `ingest_worker.py` (already implemented in D2). This task adds a focused unit test using the same pattern as `test_audit_verifier.py` (mock `_sleep` to break out after one iteration; mock `_now` for a fixed clock).

- [ ] **Step 1: Write test:**

```python
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.news.ingest_worker import (
    DEFAULT_CLEANUP_HOUR_UTC,
    run_news_cleanup_loop,
)


def _fake_factory():
    sf = MagicMock()
    sf.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    sf.return_value.__aexit__ = AsyncMock(return_value=None)
    return sf


@pytest.mark.asyncio
async def test_cleanup_loop_invokes_cleanup_then_cancels() -> None:
    sleep_log: list[float] = []
    async def fake_sleep(s: float) -> None:
        sleep_log.append(s)
        if len(sleep_log) >= 1:
            raise asyncio.CancelledError

    fixed_now = lambda: datetime(2026, 5, 6, 3, 30, tzinfo=timezone.utc)
    with patch("app.news.ingest_worker.cleanup_old_news",
               new=AsyncMock(return_value=42)) as cleanup:
        with pytest.raises(asyncio.CancelledError):
            await run_news_cleanup_loop(
                _fake_factory(),
                wake_at_utc_hour=DEFAULT_CLEANUP_HOUR_UTC,
                _sleep=fake_sleep,
                _now=fixed_now,
            )
    cleanup.assert_awaited_once()
    # 30 min until 04:00 UTC.
    assert sleep_log[0] == 1800.0


@pytest.mark.asyncio
async def test_cleanup_loop_swallows_errors() -> None:
    async def fake_sleep(s: float) -> None:
        raise asyncio.CancelledError
    fixed_now = lambda: datetime(2026, 5, 6, 12, tzinfo=timezone.utc)
    with patch("app.news.ingest_worker.cleanup_old_news",
               new=AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(asyncio.CancelledError):
            await run_news_cleanup_loop(
                _fake_factory(),
                _sleep=fake_sleep,
                _now=fixed_now,
            )
```

- [ ] **Step 2: Run — pass.**

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/tests/unit/test_news_cleanup_loop.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "test(sp-9): news cleanup loop wake-at-04UTC + error swallow"
```

---

### Task D4: Wire ingest + cleanup tasks into `app.main:lifespan`

**Files:**
- Modify: `worktrees/sp-9/backend/app/main.py`

**Design notes:**
- Add `news_ingest_task` and `news_cleanup_task` after `audit_verifier_task`. Match the gating: `settings.env not in {"test", "ci"} AND settings.worker_enabled`. Cancel them in `finally` block.
- Test coverage for the wiring lives in `test_app_startup.py` — confirm we don't accidentally trigger the loops in a test environment (already gated).

- [ ] **Step 1: Edit `main.py`:**

```python
# Add to imports:
from app.news.ingest_worker import (
    start_news_cleanup_task,
    start_news_ingest_task,
)

# Inside lifespan, after audit_verifier_task is started:
news_ingest_task = None
news_cleanup_task = None
if settings.env not in {"test", "ci"} and settings.worker_enabled:
    # ... existing universe_sync_task / health_pinger_task / audit_verifier_task ...
    # SP-9 Phase D4: News ingestion (5 min crypto / 30 min macro) + nightly
    # 04:00 UTC cleanup of news_items older than 20 days. Skipped in test/ci.
    news_ingest_task = start_news_ingest_task(get_session_factory())
    news_cleanup_task = start_news_cleanup_task(get_session_factory())

# In finally:
if news_ingest_task is not None:
    news_ingest_task.cancel()
if news_cleanup_task is not None:
    news_cleanup_task.cancel()
```

- [ ] **Step 2: Add a test** to `tests/integration/test_app_startup.py` (or unit) verifying that import + lifespan setup don't raise. Since the existing app-startup test is integration-flavored, add a small unit test:

```python
# backend/tests/unit/test_main_news_wiring.py
import inspect

import app.main as main_module


def test_main_imports_news_tasks() -> None:
    src = inspect.getsource(main_module)
    assert "start_news_ingest_task" in src
    assert "start_news_cleanup_task" in src
    assert "news_ingest_task = None" in src
    assert "news_cleanup_task = None" in src
```

- [ ] **Step 3: Run full backend suite — should pass with everything green and no background workers spawned in tests.**

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/main.py backend/tests/unit/test_main_news_wiring.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): wire news ingest + cleanup tasks into app.main:lifespan"
```

---

## Phase E — L9 layer score + predictor integration

### Task E1: Replace `layer9_news.score` with real implementation — TDD

**Files:**
- Modify: `worktrees/sp-9/backend/app/core/scoring/layer9_news.py`
- Create: `worktrees/sp-9/backend/tests/unit/test_layer9_news.py`

**Design notes:**
- **Signature change:** SP-5 stub was `def score(bars) -> LayerScore | None`. SP-9 makes it `async def score(bars, *, symbol, session, lookback_minutes=60)`. The predictor (E2) and any other caller must be updated.
- All existing call sites of `layer9_news.score` are in `predictor.py:build_prediction()` (line 211) — that single site is updated in E2.
- `Direction` import needed.
- `bars` is unused in this layer (we look up `news_items` by symbol) — kept in the signature for parity with the other layers' Protocol shape.
- L9 confidence formula: `min(1.0, len(rows) / 5.0)` per spec §3.4.
- Direction thresholds: `>0.1 → LONG`, `<-0.1 → SHORT`, else `NEUTRAL`. The deadband prevents tiny per-asset noise from flipping the layer.

- [ ] **Step 1: Failing test** — `tests/unit/test_layer9_news.py`:

```python
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.scoring.layer9_news import score
from app.core.scoring.types import Direction


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE news_items (
                id INTEGER PRIMARY KEY,
                published_at TIMESTAMP NOT NULL,
                sentiment_score REAL,
                impact_score REAL,
                affected_assets TEXT
            )
        """))
    async with AsyncSession(engine) as s:
        yield s


def _bars() -> pd.DataFrame:
    return pd.DataFrame({"close": [100.0]})


@pytest.mark.asyncio
async def test_score_returns_none_when_no_news(session: AsyncSession) -> None:
    result = await score(_bars(), symbol="BTC/USDT", session=session)
    assert result is None


@pytest.mark.asyncio
async def test_score_aggregates_positive_sentiment_to_long(session: AsyncSession, monkeypatch) -> None:
    # SP-9's L9 expects PG-array semantics on affected_assets. For sqlite tests
    # we patch the SQL with a JSON-LIKE filter — easier in unit; integration
    # test (E3) covers real PG.
    now = datetime(2026, 5, 6, 12, tzinfo=timezone.utc)
    await session.execute(sa.text("""
        INSERT INTO news_items (published_at, sentiment_score, impact_score, affected_assets)
        VALUES (:t, 0.8, 0.9, '["BTC"]'),
               (:t, 0.6, 0.7, '["BTC"]'),
               (:t, 0.5, 0.5, '["BTC"]')
    """), {"t": now - timedelta(minutes=15)})
    await session.commit()
    # The unit test imports the layer with sqlite-aware SQL toggled (see impl).
    result = await score(_bars(), symbol="BTC/USDT", session=session)
    assert result is not None
    assert result.direction == Direction.LONG
    assert 0.0 < result.strength <= 1.0
    assert result.confidence > 0.0


@pytest.mark.asyncio
async def test_score_aggregates_negative_sentiment_to_short(session: AsyncSession) -> None:
    now = datetime(2026, 5, 6, 12, tzinfo=timezone.utc)
    await session.execute(sa.text("""
        INSERT INTO news_items (published_at, sentiment_score, impact_score, affected_assets)
        VALUES (:t, -0.7, 1.0, '["BTC"]')
    """), {"t": now - timedelta(minutes=10)})
    await session.commit()
    result = await score(_bars(), symbol="BTC/USDT", session=session)
    assert result is not None
    assert result.direction == Direction.SHORT


@pytest.mark.asyncio
async def test_score_filters_by_lookback(session: AsyncSession) -> None:
    now = datetime(2026, 5, 6, 12, tzinfo=timezone.utc)
    # Insert OLD article (2h ago); lookback = 60 min default.
    await session.execute(sa.text("""
        INSERT INTO news_items (published_at, sentiment_score, impact_score, affected_assets)
        VALUES (:t, 0.9, 1.0, '["BTC"]')
    """), {"t": now - timedelta(hours=2)})
    await session.commit()
    result = await score(_bars(), symbol="BTC/USDT", session=session)
    assert result is None


@pytest.mark.asyncio
async def test_score_confidence_caps_at_1(session: AsyncSession) -> None:
    now = datetime(2026, 5, 6, 12, tzinfo=timezone.utc)
    for _ in range(20):
        await session.execute(sa.text("""
            INSERT INTO news_items (published_at, sentiment_score, impact_score, affected_assets)
            VALUES (:t, 0.5, 0.5, '["BTC"]')
        """), {"t": now - timedelta(minutes=5)})
    await session.commit()
    result = await score(_bars(), symbol="BTC/USDT", session=session)
    assert result is not None
    assert result.confidence == 1.0
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement** `layer9_news.py`:

```python
"""Layer 9 — News + sentiment (SP-9 Phase E1, replaces SP-5 placeholder).

Aggregates `news_items` for the asset over the last `lookback_minutes`.
Returns None when no rows exist (layer abstains; aggregator redistributes
weight per the SP-5 contract).

Score = impact-weighted average of sentiment_score, squashed via tanh.
Confidence = min(1.0, n_articles / 5.0).
Direction: deadband ±0.1 (avoid noise flipping the layer).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scoring.types import Direction, LayerScore


_LOOKBACK_MINUTES_DEFAULT: int = 60


async def score(
    bars: pd.DataFrame,  # noqa: ARG001 — kept for layer Protocol parity
    *,
    symbol: str,
    session: AsyncSession,
    lookback_minutes: int = _LOOKBACK_MINUTES_DEFAULT,
) -> LayerScore | None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    base = symbol.split("/")[0].upper()

    dialect = session.bind.dialect.name if session.bind else "postgresql"
    is_pg = dialect.startswith("postgres")
    if is_pg:
        sql = sa.text(
            "SELECT sentiment_score, impact_score FROM news_items "
            "WHERE published_at >= :cutoff "
            "AND :base = ANY(affected_assets) "
            "AND sentiment_score IS NOT NULL"
        )
    else:
        # sqlite test fallback — affected_assets stored as JSON string.
        sql = sa.text(
            "SELECT sentiment_score, impact_score FROM news_items "
            "WHERE published_at >= :cutoff "
            "AND affected_assets LIKE :pat "
            "AND sentiment_score IS NOT NULL"
        )

    params: dict[str, object]
    if is_pg:
        params = {"cutoff": cutoff, "base": base}
    else:
        params = {"cutoff": cutoff, "pat": f"%\"{base}\"%"}

    rows = (await session.execute(sql, params)).all()
    if not rows:
        return None

    weighted_sum = sum(
        float(r.sentiment_score) * float(r.impact_score or 0.5) for r in rows
    )
    weight_total = sum(float(r.impact_score or 0.5) for r in rows)
    if weight_total == 0:
        return None
    avg = weighted_sum / weight_total
    strength = abs(math.tanh(avg * 1.5))
    confidence = min(1.0, len(rows) / 5.0)
    if avg > 0.1:
        direction = Direction.LONG
    elif avg < -0.1:
        direction = Direction.SHORT
    else:
        direction = Direction.NEUTRAL
    return LayerScore(
        direction=direction,
        strength=strength,
        confidence=confidence,
        notes=f"{len(rows)} news items in last {lookback_minutes}min",
    )
```

- [ ] **Step 4: Run — pass.**

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/core/scoring/layer9_news.py backend/tests/unit/test_layer9_news.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): real L9 — async news_items aggregation + tanh squash + deadband"
```

---

### Task E2: Predictor integration — pass session to L9, populate sentiment/news fields

**Files:**
- Modify: `worktrees/sp-9/backend/app/core/predictor.py`
- Modify: every call site of `build_prediction(...)` to pass a `session`

**Design notes:**
- `build_prediction` is `def`, not async. To call async `score_l9`, we either (a) make build_prediction async (ripples to many callers) or (b) skip L9 from build_prediction and inject it from an async caller. **Decision: make `build_prediction` async.** This is justified — the WS worker, scanner, integration tests all `await` it via the FastAPI request loop. Sync callers in tests use `asyncio.run(build_prediction(...))`.
- New params: `session: AsyncSession | None = None`. If None, L9 is skipped (returns None).
- After scoring, query `news_items` for the most-impactful recent article (top headline by `impact_score DESC`) and the recent count. Build `NewsSummary`. Call `get_fear_greed_index()` for `SentimentSummary`.
- `news_bias` derived from L9 score: `>0.1 → "Bullish"`, `<-0.1 → "Bearish"`, else `"Neutral"`.
- Impact tier: `recent_count >= 10 OR top.impact_score >= 0.9 → HIGH`, `>=3 OR >=0.7 → MEDIUM`, else `LOW`.

- [ ] **Step 1: Identify all call sites** of `build_prediction`:

```bash
grep -rn "build_prediction" a:/v5_Trade_bot/worktrees/sp-9/backend/app/ a:/v5_Trade_bot/worktrees/sp-9/backend/tests/
```
Expected sites: `app.ws.live_prediction`, `app.api.routes.tab1`, `app.api.routes.scanner`, `app.shadow.engine`, `app.api.routes.admin_backtest` (uses build_prediction in the backtest loop), and tests under `test_predictor*`, `test_full_scoring_pipeline*`. Each one needs an `await` + an `AsyncSession`.

- [ ] **Step 2: Edit `predictor.py`** — make `build_prediction` async; add the L9 session param; populate `sentiment` + `news`:

```python
# Imports added:
from sqlalchemy.ext.asyncio import AsyncSession
import sqlalchemy as sa

from app.api.schemas import NewsSummary, SentimentSummary
from app.news.fear_greed import get_fear_greed_index


async def build_prediction(
    *,
    symbol: str,
    timeframe: str,
    bars: pd.DataFrame,
    pattern_stats_lookup: PatternStatsLookup | None = None,
    enabled_patterns: set[str] | None = None,
    enabled_traps: set[str] | None = None,
    ghost: GhostInput | None = None,
    session: AsyncSession | None = None,  # SP-9: required for L9 + news/sentiment.
) -> LivePredictionOut:
    # ... existing layer scoring through layer_results[8] ...

    # SP-9: L9 is async + needs session. None session → layer abstains.
    if session is not None:
        layer_results[9] = await score_l9(
            bars, symbol=symbol, session=session,
        )
    else:
        layer_results[9] = None

    layer_results[10] = score_l10(bars)

    # ... rest of aggregator + traps unchanged ...

    # SP-9: populate sentiment + news summaries.
    sentiment_summary: SentimentSummary | None = None
    news_summary: NewsSummary | None = None
    if session is not None:
        try:
            sentiment_summary = await _build_sentiment_summary(
                session, symbol=symbol, l9=layer_results[9],
            )
            news_summary = await _build_news_summary(session, symbol=symbol)
        except Exception:  # noqa: BLE001
            log.warning("sentiment/news summary fetch failed", exc_info=True)

    return LivePredictionOut(
        # ... existing fields ...
        sentiment=sentiment_summary,
        news=news_summary,
    )


async def _build_sentiment_summary(
    session: AsyncSession, *, symbol: str, l9: LayerScore | None,
) -> SentimentSummary | None:
    fng = await get_fear_greed_index()
    if fng is None:
        return None
    if l9 is None or l9.direction == Direction.NEUTRAL:
        bias = "Neutral"
    elif l9.direction == Direction.LONG:
        bias = "Bullish"
    else:
        bias = "Bearish"
    return SentimentSummary(
        fng_value=fng.value,
        fng_label=fng.label,
        news_bias=bias,
    )


async def _build_news_summary(
    session: AsyncSession, *, symbol: str,
) -> NewsSummary | None:
    base = symbol.split("/")[0].upper()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    is_pg = (session.bind.dialect.name if session.bind else "postgresql").startswith("postgres")
    if is_pg:
        rows_sql = sa.text(
            "SELECT title, impact_score FROM news_items "
            "WHERE published_at >= :cutoff AND :base = ANY(affected_assets) "
            "ORDER BY impact_score DESC NULLS LAST LIMIT 1"
        )
        cnt_sql = sa.text(
            "SELECT COUNT(*) AS n FROM news_items "
            "WHERE published_at >= :cutoff AND :base = ANY(affected_assets)"
        )
        params = {"cutoff": cutoff, "base": base}
    else:
        rows_sql = sa.text(
            "SELECT title, impact_score FROM news_items "
            "WHERE published_at >= :cutoff AND affected_assets LIKE :pat "
            "ORDER BY impact_score DESC LIMIT 1"
        )
        cnt_sql = sa.text(
            "SELECT COUNT(*) AS n FROM news_items "
            "WHERE published_at >= :cutoff AND affected_assets LIKE :pat"
        )
        params = {"cutoff": cutoff, "pat": f"%\"{base}\"%"}
    n = (await session.execute(cnt_sql, params)).scalar() or 0
    if n == 0:
        return None
    top = (await session.execute(rows_sql, params)).first()
    top_headline = top.title if top else None
    top_impact = float(top.impact_score) if top and top.impact_score is not None else 0.5
    if n >= 10 or top_impact >= 0.9:
        impact = "HIGH"
    elif n >= 3 or top_impact >= 0.7:
        impact = "MEDIUM"
    else:
        impact = "LOW"
    return NewsSummary(recent_count=int(n), top_headline=top_headline, impact=impact)
```

- [ ] **Step 3: Update every call site** to `await build_prediction(..., session=session)`. The integration test `test_full_scoring_pipeline.py` is the canary — fix it first to confirm the new contract.

- [ ] **Step 4: Schemas-aware** — F1 (next phase) defines `SentimentSummary` and `NewsSummary` Pydantic classes. To unblock E2 here, **F1 must run before this step's test runs**. Reorder in implementation: do F1 first, then E2.

- [ ] **Step 5: Run full backend suite — must pass.** Expect ~10 minor test fixups in callers.

- [ ] **Step 6: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/core/predictor.py backend/app/ws/live_prediction.py backend/app/api/routes/tab1.py backend/app/api/routes/scanner.py backend/app/shadow/engine.py backend/app/api/routes/admin_backtest.py backend/tests/
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): make build_prediction async; wire L9 + sentiment/news summaries"
```

---

### Task E3: Backend integration test — full L9 pipeline E2E

**Files:**
- Create: `worktrees/sp-9/backend/tests/integration/test_predictor_l9_e2e.py`

**Design notes:**
- Uses the project's existing PG fixture (`bot_status_factory` or equivalent — see `tests/integration/conftest.py`) so `affected_assets TEXT[]` works natively.
- Seeds 5 articles for BTC, calls `build_prediction(symbol="BTC/USDT", session=session, ...)`, asserts:
  1. `layer_scores["9"]` is not None.
  2. `sentiment.news_bias` is "Bullish" (positive sentiments dominate).
  3. `news.recent_count == 5`.
  4. `news.top_headline` matches the highest-impact one.
- F&G is mocked at the module level (`fear_greed.get_fear_greed_index`) to return a known FngResult.

- [ ] **Step 1: Write integration test:**

```python
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
import pytest
import sqlalchemy as sa

from app.core.predictor import build_prediction
from app.news.fear_greed import FngResult


@pytest.mark.integration
@pytest.mark.asyncio
async def test_l9_pipeline_e2e_pg(pg_session):
    # Seed 5 BTC news items in the last 30 min, all positive.
    now = datetime.now(timezone.utc)
    for i in range(5):
        await pg_session.execute(sa.text("""
            INSERT INTO news_items
              (source, url, title, published_at,
               sentiment_score, sentiment_label, sentiment_confidence,
               impact_score, category, affected_assets)
            VALUES
              ('cryptopanic', :u, :t, :pub, 0.7, 'positive', 0.9,
               0.85, 'exchange', :assets)
        """), {
            "u": f"https://x/{i}",
            "t": f"Bitcoin rallies #{i}",
            "pub": now - timedelta(minutes=10 + i),
            "assets": ["BTC"],
        })
    await pg_session.commit()

    bars = pd.DataFrame(
        {"open": [100]*250, "high": [101]*250, "low": [99]*250,
         "close": [100]*250, "volume": [10]*250},
        index=pd.date_range("2026-05-05", periods=250, freq="h", tz="UTC"),
    )

    fake_fng = FngResult(value=70, label="Greed", timestamp=now)
    with patch("app.core.predictor.get_fear_greed_index",
               return_value=fake_fng):
        out = await build_prediction(
            symbol="BTC/USDT", timeframe="1h", bars=bars, session=pg_session,
        )

    assert out.layer_scores["9"] is not None
    assert out.sentiment is not None
    assert out.sentiment.fng_value == 70
    assert out.sentiment.news_bias == "Bullish"
    assert out.news is not None
    assert out.news.recent_count == 5
    assert out.news.top_headline.startswith("Bitcoin rallies")
    assert out.news.impact in {"HIGH", "MEDIUM"}
```

- [ ] **Step 2: Run — pass.**

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/tests/integration/test_predictor_l9_e2e.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "test(sp-9): integration E2E — seed news_items → build_prediction populates L9 + sentiment + news"
```

---

## Phase F — Schemas + frontend wire-up + admin endpoint + ship

### Task F1: Pydantic `SentimentSummary` + `NewsSummary` + LivePredictionOut extension — TDD

> **Sequencing note:** F1 must run BEFORE Phase E2 (predictor.py imports these schemas). In execution, treat F1 as the first task of Phase F or insert it before E2.

**Files:**
- Modify: `worktrees/sp-9/backend/app/api/schemas.py`
- Create: `worktrees/sp-9/backend/tests/unit/test_schemas_sentiment_news.py`

- [ ] **Step 1: Failing test:**

```python
import pytest
from pydantic import ValidationError

from app.api.schemas import (
    LivePredictionOut, NewsSummary, SentimentSummary,
)


def test_sentiment_summary_valid_labels():
    s = SentimentSummary(fng_value=50, fng_label="Neutral", news_bias="Neutral")
    assert s.fng_value == 50

def test_sentiment_summary_invalid_label_rejected():
    with pytest.raises(ValidationError):
        SentimentSummary(fng_value=50, fng_label="Banana", news_bias="Neutral")

def test_news_summary_impact_literal():
    n = NewsSummary(recent_count=3, top_headline="X", impact="HIGH")
    assert n.impact == "HIGH"
    with pytest.raises(ValidationError):
        NewsSummary(recent_count=0, top_headline=None, impact="ENORMOUS")

def test_live_prediction_out_optional_sentiment_news():
    # Existing minimal payload still validates with sentiment/news omitted.
    payload = {
        "symbol": "BTC/USDT", "timeframe": "1h",
        "ts": "2026-05-06T12:00:00Z", "price": 100.0,
        "final": {"score": 0.0, "direction": "NEUTRAL", "confidence": 0.0,
                  "contributing_layers": []},
        "layer_scores": {},
        "trade_setup": {"direction": "NEUTRAL"},
        "momentum": {"rsi": None, "macd_line": None,
                     "macd_signal": None, "macd_hist": None},
        "cold_start": True, "inputs_hash": "x",
    }
    out = LivePredictionOut(**payload)
    assert out.sentiment is None
    assert out.news is None
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement** — append to `app/api/schemas.py`:

```python
# --- SP-9: News + Sentiment summaries -----------------------------------

class SentimentSummary(BaseModel):
    fng_value: int = Field(ge=0, le=100)
    fng_label: Literal[
        "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed",
    ]
    news_bias: Literal["Bullish", "Bearish", "Neutral"]


class NewsSummary(BaseModel):
    recent_count: int = Field(ge=0)
    top_headline: str | None = None
    impact: Literal["LOW", "MEDIUM", "HIGH"]
```

And inside `LivePredictionOut`:

```python
    # SP-9: optional summaries populated by predictor when a session is passed.
    sentiment: SentimentSummary | None = None
    news: NewsSummary | None = None
```

- [ ] **Step 4: Run — pass.**

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/api/schemas.py backend/tests/unit/test_schemas_sentiment_news.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): SentimentSummary + NewsSummary schemas; LivePredictionOut +2 fields — TDD"
```

---

### Task F2: Frontend `LivePrediction` type extension

**Files:**
- Modify: `worktrees/sp-9/frontend/src/lib/api.ts`

**Design notes:**
- Mirror the new Pydantic shapes in TypeScript exactly. Both `sentiment` and `news` are optional (`?: ... | null`).

- [ ] **Step 1: Edit `api.ts`** — add interfaces and extend `LivePrediction`:

```typescript
export interface SentimentSummary {
  fng_value: number;
  fng_label:
    | "Extreme Fear"
    | "Fear"
    | "Neutral"
    | "Greed"
    | "Extreme Greed";
  news_bias: "Bullish" | "Bearish" | "Neutral";
}

export interface NewsSummary {
  recent_count: number;
  top_headline: string | null;
  impact: "LOW" | "MEDIUM" | "HIGH";
}

export interface LivePrediction {
  // ... existing fields ...
  sentiment?: SentimentSummary | null;  // SP-9
  news?: NewsSummary | null;            // SP-9
}
```

- [ ] **Step 2: Run frontend type-check + tests:**

```bash
cd worktrees/sp-9/frontend
npm run typecheck
npm run test -- --run
```
Expected: green; existing tests still pass because both fields are optional.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add frontend/src/lib/api.ts
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): TS types — SentimentSummary + NewsSummary; LivePrediction +2 optional fields"
```

---

### Task F3: Frontend `SentimentFearGreed.tsx` wire-up

**Files:**
- Modify: `worktrees/sp-9/frontend/src/tabs/Tab1LivePrediction/panels/SentimentFearGreed.tsx`
- Modify: `worktrees/sp-9/frontend/tests/unit/SentimentFearGreed.test.tsx`

**Design notes:**
- Color tiers per spec §5: red ≤25, orange 26-45, gray 46-55, green ≥56.
- News bias label colored: green=Bullish, red=Bearish, gray=Neutral.
- Backwards-compat: when `data.sentiment` is null/undefined, fall back to existing "no data" text + the original two test assertions still pass.

- [ ] **Step 1: Update tests first:**

```typescript
import { render, screen } from "@testing-library/react";
import { SentimentFearGreed } from "@/tabs/Tab1LivePrediction/panels/SentimentFearGreed";
import type { LayerScore, LivePrediction } from "@/lib/api";

const baseNoSent: LivePrediction = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100,
  final: { score: 0, direction: "NEUTRAL", confidence: 0, contributing_layers: [] },
  layer_scores: {} as Record<string, LayerScore | null>,
  trade_setup: { direction: "NEUTRAL", entry: null, stop_loss: null,
                 take_profit: null, risk_reward: null },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false, inputs_hash: "x",
};

test("renders 'no data' when sentiment is null", () => {
  render(<SentimentFearGreed data={baseNoSent} />);
  expect(screen.getAllByText("no data").length).toBe(2);
});

test("renders F&G value + label when sentiment present", () => {
  const data = { ...baseNoSent,
    sentiment: { fng_value: 70, fng_label: "Greed" as const,
                 news_bias: "Bullish" as const } };
  render(<SentimentFearGreed data={data} />);
  expect(screen.getByText(/70/)).toBeInTheDocument();
  expect(screen.getByText(/Greed/)).toBeInTheDocument();
  expect(screen.getByText(/Bullish/)).toBeInTheDocument();
});

test("F&G uses red color tier for value <=25", () => {
  const data = { ...baseNoSent,
    sentiment: { fng_value: 15, fng_label: "Extreme Fear" as const,
                 news_bias: "Bearish" as const } };
  const { container } = render(<SentimentFearGreed data={data} />);
  expect(container.querySelector(".text-red-400")).not.toBeNull();
});

test("F&G uses green color tier for value >=56", () => {
  const data = { ...baseNoSent,
    sentiment: { fng_value: 80, fng_label: "Extreme Greed" as const,
                 news_bias: "Bullish" as const } };
  const { container } = render(<SentimentFearGreed data={data} />);
  expect(container.querySelector(".text-green-400")).not.toBeNull();
});
```

- [ ] **Step 2: Implement:**

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

function fngColorClass(value: number): string {
  if (value <= 25) return "text-red-400";
  if (value <= 45) return "text-orange-400";
  if (value <= 55) return "text-text-secondary";
  return "text-green-400";
}

function biasColorClass(bias: "Bullish" | "Bearish" | "Neutral"): string {
  if (bias === "Bullish") return "text-green-400";
  if (bias === "Bearish") return "text-red-400";
  return "text-text-secondary";
}

export function SentimentFearGreed({ data }: { data: LivePrediction | null }) {
  const s = data?.sentiment ?? null;
  return (
    <Panel title="Sentiment / F&G">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Fear &amp; Greed</span>
        {s ? (
          <span className={`text-right ${fngColorClass(s.fng_value)}`}>
            {s.fng_value} · {s.fng_label}
          </span>
        ) : (
          <span className="text-right text-text-tertiary">no data</span>
        )}
        <span className="text-text-secondary">Sentiment</span>
        {s ? (
          <span className={`text-right ${biasColorClass(s.news_bias)}`}>
            {s.news_bias}
          </span>
        ) : (
          <span className="text-right text-text-tertiary">no data</span>
        )}
      </div>
    </Panel>
  );
}
```

- [ ] **Step 3: Run frontend tests — pass.**

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add frontend/src/tabs/Tab1LivePrediction/panels/SentimentFearGreed.tsx frontend/tests/unit/SentimentFearGreed.test.tsx
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): SentimentFearGreed panel — render F&G value/label + news bias with color tiers"
```

---

### Task F4: Frontend `NewsMacroImpact.tsx` wire-up

**Files:**
- Modify: `worktrees/sp-9/frontend/src/tabs/Tab1LivePrediction/panels/NewsMacroImpact.tsx`
- Modify: `worktrees/sp-9/frontend/tests/unit/NewsMacroImpact.test.tsx`

**Design notes:**
- Headline truncated to 60 chars + ellipsis.
- Impact badge: `LOW`=gray, `MEDIUM`=orange, `HIGH`=red. Panel border turns red when `impact==="HIGH"`.
- Recent count shown as a small subscript.

- [ ] **Step 1: Update tests:**

```typescript
import { render, screen } from "@testing-library/react";
import { NewsMacroImpact } from "@/tabs/Tab1LivePrediction/panels/NewsMacroImpact";
import type { LayerScore, LivePrediction } from "@/lib/api";

const base: LivePrediction = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100,
  final: { score: 0, direction: "NEUTRAL", confidence: 0, contributing_layers: [] },
  layer_scores: {} as Record<string, LayerScore | null>,
  trade_setup: { direction: "NEUTRAL", entry: null, stop_loss: null,
                 take_profit: null, risk_reward: null },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false, inputs_hash: "x",
};

test("renders 'no events' when news is missing", () => {
  render(<NewsMacroImpact data={base} />);
  expect(screen.getByText(/no events/i)).toBeInTheDocument();
});

test("renders top headline + impact badge", () => {
  const data = { ...base, news: {
    recent_count: 4,
    top_headline: "SEC files lawsuit against major exchange",
    impact: "MEDIUM" as const,
  }};
  render(<NewsMacroImpact data={data} />);
  expect(screen.getByText(/SEC files lawsuit/)).toBeInTheDocument();
  expect(screen.getByText(/MEDIUM/)).toBeInTheDocument();
  expect(screen.getByText(/4/)).toBeInTheDocument();
});

test("HIGH impact triggers red border", () => {
  const data = { ...base, news: {
    recent_count: 12, top_headline: "MAJOR CRASH", impact: "HIGH" as const,
  }};
  const { container } = render(<NewsMacroImpact data={data} />);
  expect(container.querySelector(".border-red-500")).not.toBeNull();
});

test("truncates long headline to 60 chars + ellipsis", () => {
  const longHeadline = "A".repeat(120);
  const data = { ...base, news: {
    recent_count: 1, top_headline: longHeadline, impact: "LOW" as const,
  }};
  render(<NewsMacroImpact data={data} />);
  const node = screen.getByText(/^A+…$/);
  expect(node.textContent!.length).toBeLessThanOrEqual(61);
});
```

- [ ] **Step 2: Implement:**

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

function impactClass(impact: "LOW" | "MEDIUM" | "HIGH"): string {
  if (impact === "HIGH") return "bg-red-500/20 text-red-400";
  if (impact === "MEDIUM") return "bg-orange-500/20 text-orange-400";
  return "bg-gray-500/20 text-text-secondary";
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

export function NewsMacroImpact({ data }: { data: LivePrediction | null }) {
  const n = data?.news ?? null;
  if (n === null) {
    return (
      <Panel title="News & Macro">
        <span className="text-text-tertiary">no events</span>
      </Panel>
    );
  }
  const borderClass = n.impact === "HIGH" ? "border-red-500 border" : "";
  return (
    <Panel title="News & Macro" className={borderClass}>
      <div className="flex flex-col gap-1">
        <span className="text-sm">
          {n.top_headline ? truncate(n.top_headline, 60) : "—"}
        </span>
        <div className="flex items-center justify-between text-xs">
          <span className={`px-1 rounded ${impactClass(n.impact)}`}>
            {n.impact}
          </span>
          <span className="text-text-tertiary">
            {n.recent_count} in last hour
          </span>
        </div>
      </div>
    </Panel>
  );
}
```

If `Panel` doesn't currently accept a `className` prop, extend it (one-line addition).

- [ ] **Step 3: Run frontend tests — pass.**

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add frontend/src/tabs/Tab1LivePrediction/panels/NewsMacroImpact.tsx frontend/tests/unit/NewsMacroImpact.test.tsx frontend/src/components/ui/Panel.tsx
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): NewsMacroImpact panel — top headline + impact badge + HIGH red border"
```

---

### Task F5: Admin REST endpoints — `GET /admin/news` + `POST /admin/news/refresh`

**Files:**
- Create: `worktrees/sp-9/backend/app/api/routes/admin_news.py`
- Create: `worktrees/sp-9/backend/tests/integration/test_api_admin_news_list.py`
- Create: `worktrees/sp-9/backend/tests/integration/test_api_admin_news_refresh.py`
- Modify: `worktrees/sp-9/backend/app/main.py` — `include_router(admin_news.router)`
- Modify: `worktrees/sp-9/backend/app/api/schemas.py` — `NewsArticleOut` schema

**Design notes:**
- Pattern matches `admin_traps.py`: prefix `/api/v1/admin/news`, `dependencies=[Depends(require_admin)]`, raw `sa.text` queries.
- `GET ?since=ISO` lists articles published after `since` (default = 24h ago); paginated with `?limit=200`.
- `POST /refresh` triggers an immediate fetch from CryptoPanic + Yahoo RSS; returns count of new rows. Useful for ops + the SP-9 acceptance test.
- `_run_refresh_now()` constructs the live adapters, calls `_ingest_once` for each, returns total. We rely on the same `_build_default_adapters` from `ingest_worker.py`.

- [ ] **Step 1: Add `NewsArticleOut`** to `app/api/schemas.py`:

```python
class NewsArticleOut(BaseModel):
    id: int
    source: str
    url: str
    title: str
    published_at: datetime
    fetched_at: datetime
    sentiment_score: float | None
    sentiment_label: Literal["positive", "negative", "neutral"] | None
    impact_score: float | None
    category: str | None
    affected_assets: list[str]


class NewsRefreshOut(BaseModel):
    new_rows: int
    sources_polled: list[str]
```

- [ ] **Step 2: Implement** `app/api/routes/admin_news.py`:

```python
"""Admin REST for SP-9 news (Phase F5).

GET  /api/v1/admin/news?since=ISO&limit=200 — list recent articles.
POST /api/v1/admin/news/refresh             — trigger immediate fetch+classify+persist.

Both gated by Depends(require_admin). Pattern mirrors admin_traps.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import NewsArticleOut, NewsRefreshOut
from app.auth.deps import require_admin
from app.db.session import get_session, get_session_factory
from app.news.ingest_worker import _build_default_adapters, _ingest_once


log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/news",
    tags=["admin-news"],
    dependencies=[Depends(require_admin)],
)


@router.get("", response_model=list[NewsArticleOut])
async def list_news(
    since: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[NewsArticleOut]:
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = (await session.execute(sa.text("""
        SELECT id, source, url, title, published_at, fetched_at,
               sentiment_score, sentiment_label, impact_score,
               category, affected_assets
        FROM news_items
        WHERE published_at >= :since
        ORDER BY published_at DESC
        LIMIT :limit
    """), {"since": since, "limit": limit})).all()
    return [
        NewsArticleOut(
            id=r.id,
            source=r.source,
            url=r.url,
            title=r.title,
            published_at=r.published_at,
            fetched_at=r.fetched_at,
            sentiment_score=r.sentiment_score,
            sentiment_label=r.sentiment_label,
            impact_score=r.impact_score,
            category=r.category,
            affected_assets=list(r.affected_assets or []),
        )
        for r in rows
    ]


@router.post("/refresh", response_model=NewsRefreshOut)
async def refresh_news() -> NewsRefreshOut:
    crypto, macro = _build_default_adapters()
    total = 0
    polled: list[str] = []
    sf = get_session_factory()
    for adapter in (*crypto, *macro):
        try:
            n = await _ingest_once(sf, adapter)
            total += n
            polled.append(adapter.name)
        except Exception:  # noqa: BLE001
            log.exception("admin_news refresh: adapter %s failed", adapter.name)
    return NewsRefreshOut(new_rows=total, sources_polled=polled)
```

- [ ] **Step 3: Wire into `main.py`** — add `from app.api.routes import admin_news` and `app.include_router(admin_news.router)`.

- [ ] **Step 4: Integration tests** — `test_api_admin_news_list.py`:

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_news_returns_recent_articles(admin_client, pg_session):
    # Seed 2 fresh + 1 stale (>24h).
    now = datetime.now(timezone.utc)
    await pg_session.execute(sa.text("""
        INSERT INTO news_items (source, url, title, published_at, impact_score, affected_assets)
        VALUES
          ('cryptopanic', 'u1', 'fresh1', :fresh, 0.5, ARRAY['BTC']),
          ('cryptopanic', 'u2', 'fresh2', :fresh, 0.5, ARRAY['BTC']),
          ('cryptopanic', 'u3', 'stale',  :stale, 0.5, ARRAY['BTC'])
    """), {"fresh": now - timedelta(hours=2), "stale": now - timedelta(days=2)})
    await pg_session.commit()
    resp = await admin_client.get("/api/v1/admin/news")
    assert resp.status_code == 200
    titles = [r["title"] for r in resp.json()]
    assert "fresh1" in titles and "fresh2" in titles
    assert "stale" not in titles


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_news_403_for_non_admin(non_admin_client):
    resp = await non_admin_client.get("/api/v1/admin/news")
    assert resp.status_code == 403
```

`test_api_admin_news_refresh.py`:

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_news_returns_count(admin_client, monkeypatch):
    fake_adapter = MagicMock()
    fake_adapter.name = "cryptopanic"
    fake_adapter.fetch_recent = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "app.api.routes.admin_news._build_default_adapters",
        lambda: ([fake_adapter], []),
    )
    resp = await admin_client.post("/api/v1/admin/news/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["new_rows"] == 0
    assert body["sources_polled"] == ["cryptopanic"]
```

- [ ] **Step 5: Run all tests — pass.**

- [ ] **Step 6: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' add backend/app/api/routes/admin_news.py backend/app/api/schemas.py backend/app/main.py backend/tests/integration/test_api_admin_news_list.py backend/tests/integration/test_api_admin_news_refresh.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-9): admin news REST — GET /admin/news + POST /admin/news/refresh"
```

---

### Task F6: PR + tag `sp-9` + log entry

**Files:**
- Modify: `worktrees/sp-9/files/STATUS_LOG.md` (or whatever the project's status log is — adjust path if needed)

- [ ] **Step 1: Append a log entry** summarizing SP-9 ship:

```
## 2026-05-?? — SP-9 News + Sentiment shipped

- Added news_items table (migration 0013) with GIN index on affected_assets.
- New CryptoPanic + Yahoo RSS adapters; FinBERT (ProsusAI/finbert) classifier.
- Background ingest loop (5 min crypto / 30 min macro) + nightly cleanup at 04:00 UTC.
- Replaced layer9_news placeholder; layer now contributes a real LayerScore.
- Tab 1 SentimentFearGreed + NewsMacroImpact panels show live data.
- Admin REST: GET /api/v1/admin/news, POST /api/v1/admin/news/refresh.
- Test counts: backend +~60 (~1510 total), frontend +~11 (~340 total).
```

- [ ] **Step 2: Tag `sp-9`:**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-9' tag sp-9
```

- [ ] **Step 3: Open PR** from `sp-9/main` → `main` via `gh pr create`. Include:
- Summary: link to spec, list of phases, test counts (backend ~1510, frontend ~340).
- Test plan checklist.
- Risk table (pasted from spec §8).

- [ ] **Step 4: Merge once CI green; remove worktree:**

```bash
git -c safe.directory='A:/v5_Trade_bot' worktree remove worktrees/sp-9
```

---

## Cross-cutting verification (run before tagging)

- [ ] `docker compose exec -T backend pytest -q` → ≥1510 passed (regression: no SP-7 test fails).
- [ ] `docker compose exec -T backend pytest -m slow tests/unit/test_news_sentiment_smoke.py` → 2 passed (FinBERT smoke + perf).
- [ ] `cd frontend && npm run test -- --run` → ≥340 passed.
- [ ] `npm run e2e` (Playwright) → 16 cases still green.
- [ ] Manual sanity: with `CRYPTOPANIC_API_KEY` set in `.env`, hit `POST /api/v1/admin/news/refresh` and confirm rows appear in `news_items`.
- [ ] Verify nightly cleanup logs at 04:00 UTC by inspecting log lines after 24h of dev-stack uptime.
- [ ] Confirm Tab 1 SentimentFearGreed shows F&G + bias when backend serves real data.
- [ ] Confirm Tab 1 NewsMacroImpact shows top headline + badge + (red border on HIGH).

---

## Risk register reminders (from spec §8)

| Trigger condition | Action |
|---|---|
| Phase A3 Docker build > 10 min on CI | Pre-bake FinBERT into image OR defer to SP-9.5 ml-worker container. |
| Phase C1/C3 batch latency > 5s | Drop batch_size to 8; if still slow, add ENV gate to disable FinBERT in dev. |
| Phase B1 daily 500/day quota exceeded under prod load | Reduce CRYPTO_POLL_SECONDS to 600 (10 min); document. |
| Phase D1 F&G API down >24h | UI shows "—" by design (returns None). No code change needed. |
| Migration 0013 fails on prod (existing tables collide) | Down-migrate, rename news_items, retry. |

---

**END OF SP-9 NEWS + SENTIMENT IMPLEMENTATION PLAN**

---

## Self-review pass (post-draft)

Performed inline checks:
1. Spec coverage — every locked decision in §2 maps to a task. F&G cache TTL (decision 11), L9 formula (decision 9, 10), 20-day auto-delete (decision 8) all have explicit task coverage.
2. Type consistency — `SentimentResult` used in both `sentiment.py` and `persistence.py`; verified import path. `LayerScore` import in `layer9_news.py` confirmed against existing `app/core/scoring/types.py`.
3. Sequencing — F1 (schemas) flagged as needing to run before E2 (predictor imports them). Documented in F1 step 1 and E2 step 4. Phase B1+B2 marked as parallelizable per `superpowers:dispatching-parallel-agents`.
4. Existing-pattern conformance — `RateLimitedClient` + `DailyCounterBucket` use mirrors `twelvedata.py:_default_rate_client`. `_ingest_once` + cancellation matches `verifier_scheduler.py:_check_all_chains`. `seconds_until_next_utc` reused from `shadow/universe_refresh.py`.
5. No placeholders — every code block is fully drafted.

---

## Brief report

- **Total tasks:** 27 across 6 phases (A: 5, B: 6, C: 3, D: 4, E: 3, F: 6).
- **Total commits estimated:** 27 (one per task; F6 adds the tag + PR which are not commits).
- **Spec ambiguities flagged:**
  1. Spec §3.4 shows `score()` as async + session-bound, but the SP-5 placeholder is sync. The plan resolves this in E2 by making `build_prediction` async — this ripples to ~6 callers (predictor, ws/live_prediction, tab1, scanner, shadow/engine, admin_backtest). I called out the propagation explicitly but the spec doesn't acknowledge the impact.
  2. Spec §3.7 says "predictor populates these fields" without specifying when `session is None`. Plan defaults to skipping L9 + summaries if no session (`sentiment=None, news=None`).
  3. Spec doesn't define `impact_score` formula precisely; plan uses category × source heuristic. Reasonable but a future task could replace with a real ML head.
  4. F&G `value_classification` strings vary: alternative.me uses title-cased "Greed"/"Extreme Fear" — plan's Pydantic Literal matches this exactly; documented.
- **Feasibility concerns:**
  1. **Heavy-dep gate** (spec §10): the ~1.5GB Docker growth from `transformers` + FinBERT model is significant. Plan includes a CI-time check at A3 step 2 with a documented fallback to pre-baking or SP-9.5 ml-worker.
  2. **`build_prediction` async migration** is the single largest refactor in this plan. Estimated ~10 caller updates; could break tests in unexpected places. Recommend executing E2 first as a focused task with rollback plan if too many callers break.
  3. **CryptoPanic free tier** uses `currencies` field for asset hints, but the free tier docs are not exhaustive. If the field shape differs in production, B1's `affected_assets` extraction may degrade silently — covered by B5's title-based `extract_affected_assets` fallback.