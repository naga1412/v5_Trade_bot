# SP-9 — News + Sentiment Design Spec

**Date:** 2026-05-06
**Status:** Approved (autonomous-mode default; user can redirect)
**Implementation target:** Sub-project SP-9 (after meta-plan SP-7 ship; independent of SP-1.1/SP-4/SP-8)
**Depends on:** SP-5 (L9 placeholder slot in scoring), SP-6 (UI placeholders SentimentFearGreed + NewsMacroImpact panels)
**Companion specs:** `MASTER_PLAN.md` §M6/M7 (news aggregator + FinBERT-LSTM)

---

## 1. Purpose

Fill the **L9 placeholder slot** added in SP-5 with real news + sentiment intelligence. Ingest crypto news from CryptoPanic (free tier 500 calls/day) + macro news from Yahoo RSS, run FinBERT sentiment classification on each article, aggregate per-asset sentiment into the L9 layer score, and populate the `SentimentFearGreed` + `NewsMacroImpact` panels in Tab 1 with live data.

After SP-9 ships, the bot's L9 layer contributes a measurable signal to FINAL_SCORE (currently L9 returns None → weight redistributed to other layers); UI panels show real news context instead of "no data".

### Non-goals

- **No on-chain sentiment** (Glassnode etc.) — defer to SP-3.5
- **No social media sentiment** (Twitter/Reddit) — defer to SP-9.5; APIs are flaky/expensive
- **No real-time WS push** — news polled every 5 min; UI refreshes via existing live_prediction WS payload
- **No translation layer** — assume English news; foreign-language news skipped
- **No long-form article body analysis** — sentiment runs on title only (FinBERT handles 512 tokens; titles fit easily)
- **No retraining of FinBERT** — use the pre-trained `ProsusAI/finbert` model from HuggingFace

---

## 2. Locked decisions

| # | Decision | Value |
|---|---|---|
| 1 | News source #1 | **CryptoPanic** — free tier 500 calls/day; REST API at `https://cryptopanic.com/api/v1/posts/` |
| 2 | News source #2 | **Yahoo RSS** — free, no API key; macro feeds (DXY, equities) |
| 3 | Sentiment model | **`ProsusAI/finbert`** — DistilBERT-based, ~440MB, 3-class (positive/negative/neutral) |
| 4 | Inference infra | **HuggingFace transformers** + CPU (no GPU needed for ~100 articles/day) |
| 5 | Inference cadence | **On every fetched article**, batched in groups of 16 |
| 6 | News fetch cadence | **Every 5 minutes** for crypto + every 30 min for macro |
| 7 | Per-article fields | source, url, title, body (optional), published_at, fetched_at, sentiment_score [-1,+1], sentiment_label (positive/negative/neutral), impact_score [0,1], category, affected_assets |
| 8 | Auto-delete cadence | **Nightly at 04:00 UTC** — delete `news_items` older than 20 days (per MASTER_PLAN §631) |
| 9 | L9 score formula | Per-asset weighted average of last-1h news sentiment scores; weighted by `impact_score`; squashed via tanh to LayerScore.strength |
| 10 | L9 confidence | Based on volume: more articles → higher confidence (cap at 1.0 with `min(1.0, n_articles / 5)`) |
| 11 | F&G API | **alternative.me/api/fng/** — free, no auth, JSON; cache 1h |
| 12 | Frontend wire | Existing `SentimentFearGreed.tsx` panel reads new `data.sentiment.{fng_value, fng_label, news_bias}` fields; `NewsMacroImpact.tsx` reads new `data.news.{recent_count, top_headline, impact}` fields |
| 13 | Backend extension | `LivePredictionOut` Pydantic schema gets `sentiment` + `news` optional fields; populated by predictor |
| 14 | Heavy dep gate | FinBERT is heavy (~440MB on first download). Backend Docker image grows ~1.5GB. Acceptable for production; document trade-off. |

---

## 3. Architecture

### 3.1 Module layout

```
backend/app/
├── news/                                NEW
│   ├── __init__.py
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── _base.py                     — NewsAdapter Protocol + NewsArticle dataclass
│   │   ├── cryptopanic.py               — CryptoPanic adapter (httpx + rate-limit via SP-3 RateLimitedClient)
│   │   └── yahoo_rss.py                 — Yahoo RSS adapter (feedparser)
│   ├── sentiment.py                     — FinBERT loader + classify_batch(titles) → list[SentimentResult]
│   ├── persistence.py                   — INSERT/SELECT for news_items + 20-day auto-delete
│   ├── ingest_worker.py                 — Background task: fetch every 5min/30min + classify + persist
│   └── fear_greed.py                    — alternative.me F&G fetcher with 1h cache
├── core/scoring/
│   └── layer9_news.py                   — REPLACE placeholder; compute L9 from news_items + per-asset filter
└── api/routes/
    └── admin_news.py                    NEW — REST: GET /api/v1/admin/news?since=... + POST /admin/news/refresh
```

### 3.2 NewsAdapter Protocol

```python
@dataclass(frozen=True)
class NewsArticle:
    source: str
    url: str
    title: str
    body: str | None
    published_at: datetime
    category: str | None
    affected_assets: tuple[str, ...]    # e.g. ("BTC", "ETH")

class NewsAdapter(Protocol):
    name: str

    async def fetch_recent(self, *, since: datetime) -> list[NewsArticle]:
        """Fetch articles published since `since`."""
```

### 3.3 FinBERT sentiment

```python
# app/news/sentiment.py
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

_MODEL_NAME = "ProsusAI/finbert"
_tokenizer = None
_model = None

def _load() -> tuple:
    global _tokenizer, _model
    if _tokenizer is None:
        _tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
        _model = AutoModelForSequenceClassification.from_pretrained(_MODEL_NAME)
        _model.eval()
    return _tokenizer, _model

@dataclass(frozen=True)
class SentimentResult:
    score: float                # [-1, +1] — negative→-1, neutral→0, positive→+1 weighted
    label: Literal["positive", "negative", "neutral"]
    confidence: float           # [0, 1] — softmax max

def classify_batch(titles: list[str], batch_size: int = 16) -> list[SentimentResult]:
    """FinBERT inference on a batch of titles. Returns one SentimentResult per title."""
    tokenizer, model = _load()
    results = []
    for i in range(0, len(titles), batch_size):
        batch = titles[i:i+batch_size]
        inputs = tokenizer(batch, padding=True, truncation=True, return_tensors="pt", max_length=128)
        with torch.no_grad():
            logits = model(**inputs).logits
            probs = torch.softmax(logits, dim=-1)  # (batch, 3): [positive, negative, neutral]
        for j in range(probs.shape[0]):
            p_pos, p_neg, p_neu = probs[j].tolist()
            score = p_pos - p_neg                # weighted score in [-1, +1]
            label = ["positive","negative","neutral"][int(probs[j].argmax())]
            confidence = float(probs[j].max())
            results.append(SentimentResult(score=score, label=label, confidence=confidence))
    return results
```

First model load downloads ~440MB to `~/.cache/huggingface/`. Cached after that.

### 3.4 L9 layer score

```python
# app/core/scoring/layer9_news.py
async def score(
    bars: pd.DataFrame, *,
    symbol: str,
    session: AsyncSession,
    lookback_minutes: int = 60,
) -> LayerScore | None:
    """L9: aggregate recent news sentiment for `symbol` into a LayerScore."""
    cutoff = datetime.now(UTC) - timedelta(minutes=lookback_minutes)
    base = symbol.split("/")[0].upper()  # BTC/USDT → BTC
    rows = (await session.execute(
        sa.text(
            "SELECT sentiment_score, impact_score FROM news_items "
            "WHERE published_at >= :cutoff "
            "AND :base = ANY(affected_assets) "
            "AND sentiment_score IS NOT NULL"
        ),
        {"cutoff": cutoff, "base": base},
    )).all()
    if not rows:
        return None  # no news → layer abstains
    weighted_sum = sum(r.sentiment_score * (r.impact_score or 0.5) for r in rows)
    weight_total = sum((r.impact_score or 0.5) for r in rows)
    if weight_total == 0:
        return None
    avg = weighted_sum / weight_total  # [-1, +1]
    strength = abs(math.tanh(avg * 1.5))  # squash; tune divisor for sensitivity
    confidence = min(1.0, len(rows) / 5.0)
    direction = (
        Direction.LONG if avg > 0.1
        else Direction.SHORT if avg < -0.1
        else Direction.NEUTRAL
    )
    return LayerScore(
        direction=direction, strength=strength, confidence=confidence,
        notes=f"{len(rows)} news items in last {lookback_minutes}min",
    )
```

### 3.5 Ingest worker

`app/news/ingest_worker.py:run_news_ingest_loop(session_factory)` — async task that wakes every 5 min:
1. Fetch new articles from CryptoPanic since `last_fetch_ts`
2. Fetch new articles from Yahoo RSS (every 30min)
3. Filter dedupe by URL
4. Run `classify_batch(titles)` → enrich with sentiment
5. Persist via `persist_news_items(session, articles)`
6. Update `last_fetch_ts` in module state

Wired into `app/main.py:lifespan` alongside other background workers; gated on `settings.env not in {"test","ci"}`.

### 3.6 Fear & Greed

`app/news/fear_greed.py`:
```python
async def get_fear_greed_index() -> FngResult:
    """alternative.me F&G API; cached 1h in module state."""
    if _cache and (time.time() - _cache_ts) < 3600:
        return _cache
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.alternative.me/fng/")
        data = resp.json()["data"][0]
    _cache = FngResult(
        value=int(data["value"]),
        label=data["value_classification"],  # "Extreme Fear" / "Fear" / "Neutral" / "Greed" / "Extreme Greed"
        timestamp=datetime.fromtimestamp(int(data["timestamp"]), tz=UTC),
    )
    _cache_ts = time.time()
    return _cache
```

### 3.7 LivePredictionOut extensions

```python
class SentimentSummary(BaseModel):
    fng_value: int                        # 0-100
    fng_label: Literal["Extreme Fear","Fear","Neutral","Greed","Extreme Greed"]
    news_bias: Literal["Bullish","Bearish","Neutral"]    # derived from L9 sentiment

class NewsSummary(BaseModel):
    recent_count: int                     # articles in last hour
    top_headline: str | None              # most-impactful article title
    impact: Literal["LOW","MEDIUM","HIGH"]

class LivePredictionOut(BaseModel):
    # ... existing fields ...
    sentiment: SentimentSummary | None = None
    news: NewsSummary | None = None
```

Predictor populates these fields in `build_prediction()` from L9's notes + recent news_items query + F&G fetch.

---

## 4. Data model

### 4.1 New table: `news_items`

```sql
CREATE TABLE news_items (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,                         -- 'cryptopanic' | 'yahoo_rss'
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    published_at TIMESTAMPTZ NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sentiment_score DOUBLE PRECISION,             -- [-1, +1]
    sentiment_label TEXT,                         -- 'positive' | 'negative' | 'neutral'
    sentiment_confidence DOUBLE PRECISION,        -- [0, 1]
    impact_score DOUBLE PRECISION,                -- [0, 1] — heuristic from category + source
    category TEXT,                                -- 'regulatory' | 'exchange' | 'macro' | 'whale' | 'project' | 'social'
    affected_assets TEXT[]                        -- ['BTC', 'ETH']
);
CREATE INDEX news_items_published_idx ON news_items (published_at DESC);
CREATE INDEX news_items_assets_gin_idx ON news_items USING GIN (affected_assets);
```

Migration 0013.

### 4.2 No other table changes

`predictions.layer_scores["9"]` already exists (from SP-5); now populated.

---

## 5. Frontend wire-up

Existing `frontend/src/tabs/Tab1LivePrediction/panels/SentimentFearGreed.tsx` (Phase C of SP-6, currently shows "no data"):
- Read `data.sentiment.{fng_value, fng_label, news_bias}`
- Render F&G value with color (red ≤25, orange 26-45, gray 46-55, green ≥56)
- Render news bias label

Existing `frontend/src/tabs/Tab1LivePrediction/panels/NewsMacroImpact.tsx`:
- Read `data.news.{recent_count, top_headline, impact}`
- Render top headline (truncate to 60 chars)
- Render impact badge (color by HIGH/MEDIUM/LOW)
- Red border when impact=HIGH

No new frontend components — just data wire-up + tests.

---

## 6. Sub-project sequencing

SP-9 implementation order:

- **Phase A — Worktree + scaffolding + migration 0013** (~5 tasks)
- **Phase B — News adapters (CryptoPanic + Yahoo RSS) + persistence** (~6 tasks)
- **Phase C — FinBERT sentiment + classify_batch** (~3 tasks)
- **Phase D — Ingest worker + F&G fetcher + lifespan wiring** (~4 tasks)
- **Phase E — L9 layer score + predictor integration** (~3 tasks)
- **Phase F — Pydantic schema extensions + frontend wire-up + admin endpoint + ship** (~6 tasks)

---

## 7. Cross-cutting policy compliance

| Policy | How SP-9 satisfies it |
|---|---|
| §5.14 audit hash chain | news_items NOT chained (it's external truth — not user-affecting state) |
| §5.15 rate limits | CryptoPanic uses RateLimitedClient with 500/day budget |
| §2.6 Cloudflare Access | New admin news endpoints inherit `Depends(require_admin)` |
| Per-user (SP-0.7) | News is global (same articles for all users); user_id NOT added to news_items |

---

## 8. Risk + fallback plan

| Failure mode | Detection | Fallback |
|---|---|---|
| FinBERT model download fails on first deploy (HF rate limit / network) | Backend startup logs | Pre-bake model into Docker image (~440MB); document |
| FinBERT inference too slow (>2s per batch) | Inference latency log | Reduce batch_size to 8; OR fall back to keyword-based sentiment as v2 |
| CryptoPanic 500/day quota exhausted | RateLimitedClient warning | Reduce poll cadence to 10min; OR add Yahoo RSS as redundant source |
| F&G API down | Cached fallback for 24h | After 24h: return None; UI shows "—" |
| Translation issue (foreign news) | Empty sentiment_score | Skip rows with `sentiment_score IS NULL` in L9 query — already handled |
| 20-day auto-delete fails | Manual nightly check | Add manual `DELETE FROM news_items WHERE published_at < NOW() - INTERVAL '20 days'` |

**SP-9 failure does NOT brick the bot.** L9 returns None → weight redistributed → bot continues with 9 layers (existing behavior since SP-5).

---

## 9. Acceptance criteria

- [ ] `news_items` table exists with the spec'd columns + indexes
- [ ] CryptoPanic adapter fetches articles + persists with FinBERT sentiment
- [ ] Yahoo RSS adapter fetches macro news
- [ ] FinBERT classify_batch produces sensible scores on 5 sample headlines (smoke test)
- [ ] L9 layer score populated for BTC/USDT when ≥3 articles in last hour
- [ ] F&G value + label populated in `LivePredictionOut.sentiment`
- [ ] Tab 1 SentimentFearGreed panel renders F&G value + color + news_bias label
- [ ] Tab 1 NewsMacroImpact panel renders top headline + impact badge
- [ ] Auto-delete cron deletes news_items older than 20 days
- [ ] Admin REST: `GET /api/v1/admin/news?since=...` returns recent articles
- [ ] No regression in existing 1450+ backend tests
- [ ] At minimum 50+ new tests

---

## 10. Implementation cost estimate

- Sub-project size: **~25-30 tasks across 6 phases**
- Wall-clock: **~3-4 weeks of subagent-driven work**
- New backend modules: `app/news/{adapters/{cryptopanic,yahoo_rss,_base},sentiment,persistence,ingest_worker,fear_greed}.py`, `app/api/routes/admin_news.py`
- New tests: ~50-70
- Database migrations: 1 (0013 — news_items)
- New runtime deps: `transformers==4.46.0`, `feedparser==6.0.11`
- Backend Docker image growth: ~1.5GB (FinBERT model + transformers); document trade-off

---

## 11. Reference

- MASTER_PLAN: `files/MASTER_PLAN.md` §M6, §M7, §6 (12-trap), §613 (news_items schema)
- Meta-plan: `docs/superpowers/specs/2026-05-01-trading-radar-meta-plan-design.md`
- ProsusAI/finbert: https://huggingface.co/ProsusAI/finbert
- CryptoPanic API: https://cryptopanic.com/developers/api/
- alternative.me F&G: https://alternative.me/crypto/fear-and-greed-index/

---

**END OF SP-9 NEWS + SENTIMENT DESIGN SPEC**
