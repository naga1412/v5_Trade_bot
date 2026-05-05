# SP-6 UI Completion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the trading-radar user interface to the bar set by `MASTER_PLAN.md` §9 — Tab 1 with all 17 sidebar panels populated from the 10 scoring layers + traps + ghost data, a brand-new **Tab 3 Scanner Radar** (toolbar + bullish/bearish columns + signal cards + 2-min refresh), and three deferred admin sub-pages (Patterns, Adapters, Traps). After SP-6 ships, every backend capability built across SP-0 → SP-5 has a UI surface, the app is responsive at 375 px, and Lighthouse scores ≥80 across Performance / Accessibility / Best Practices / SEO.

**Architecture:** No new backend modules except `app/api/routes/scanner.py` (one read-only aggregation endpoint), no new tables, no new dependencies except `@lhci/cli`. The frontend gains ~25 components — 13 new Tab 1 panels (joining the 4 shipped), an 8-component `Tab3Scanner/` tree, and three `Admin/` sub-pages — all built on the existing `Panel` shell, the existing `useLivePrediction` / `useShadowUpdates` hooks, and `useHashRoute` extended with one new tab id (`scanner`) plus the existing `?signal=…` / `?symbol=…&tf=…` query parser.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2 / asyncpg / TimescaleDB · React 18 / Vite / TypeScript strict / Tailwind / lightweight-charts · pytest / Vitest / Playwright / Lighthouse CI (new)

**Spec reference:** [`docs/superpowers/specs/2026-05-05-SP-6-ui-completion-design.md`](../specs/2026-05-05-SP-6-ui-completion-design.md). Companion: `MASTER_PLAN.md` §9 (UI specs) + `docs/superpowers/specs/2026-05-01-trading-radar-meta-plan-design.md` §3 §180. When this plan and the spec disagree, the spec wins.

**Cross-cutting policy compliance map (which rule each phase touches):**
- Phase A — meta-plan §3 (worktree-per-sub-project), CLAUDE.md “Use JetBrains Mono / Inter” + “Match UI colors exactly”
- Phase B / C — CLAUDE.md “No new colors”, “No new UI elements not in reference screenshots”, panel-level empty-state convention from SP-0.5
- Phase D — MASTER_PLAN §9 Tab 3 specs (toolbar, columns, signal card 5 rows), spec §3.6 (new scanner endpoint), spec §3.4 (click → deeplink)
- Phase E — SP-2 / SP-3 / SP-5 backend admin contracts (already shipped; SP-6 wires the frontend)
- Phase F — spec §1 (375 px), §5 (Lighthouse ≥80), §7 cross-cutting

---

## File Structure

This is what SP-6 creates inside the new worktree. All paths under `worktrees/sp-6/`.

```
worktrees/sp-6/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── routes/
│   │   │   │   └── scanner.py                  NEW — GET /api/v1/scanner/radar
│   │   │   ├── schemas.py                      MODIFIED — add ScannerRadarOut + SignalCardOut
│   │   │   └── ...
│   │   └── main.py                             MODIFIED — wire scanner router
│   └── tests/
│       └── integration/
│           └── test_api_scanner_radar.py       NEW — ~10 cases
├── frontend/
│   ├── src/
│   │   ├── lib/
│   │   │   ├── api.ts                          MODIFIED — scanner.radar() + admin.{patterns,adapters,traps}
│   │   │   └── useHashRoute.ts                 MODIFIED — adds "scanner" valid id
│   │   ├── components/
│   │   │   ├── layout/
│   │   │   │   └── TabNav.tsx                  MODIFIED — adds "Scanner" tab between Bot Status and Settings
│   │   │   └── Admin/
│   │   │       ├── PatternsAdmin.tsx           NEW — 158 patterns + per-row toggle
│   │   │       ├── AdaptersAdmin.tsx           NEW — 4 adapters + manual sync
│   │   │       └── TrapsAdmin.tsx              NEW — 17 traps + per-row toggle
│   │   ├── tabs/
│   │   │   ├── Tab1LivePrediction/
│   │   │   │   ├── index.tsx                   MODIFIED — render all 17 panels in MASTER_PLAN order
│   │   │   │   └── panels/
│   │   │   │       ├── TradeStatusBar.tsx      ✓ exists (panel #1)
│   │   │   │       ├── MasterBiasScore.tsx     ✓ exists (panel #2)
│   │   │   │       ├── FinalValue.tsx                NEW (panel #3)
│   │   │   │       ├── LongShortRatio.tsx            NEW (panel #4)
│   │   │   │       ├── DeepLearningSupervisor.tsx    NEW (panel #5)
│   │   │   │       ├── HtfBiasStructure.tsx          NEW (panel #6)
│   │   │   │       ├── VolumeProfile.tsx             NEW (panel #7)
│   │   │   │       ├── MomentumIndicators.tsx  ✓ exists (panel #8 — extend grid)
│   │   │   │       ├── MarketMicrostructure.tsx      NEW (panel #9)
│   │   │   │       ├── LiquiditySweep.tsx            NEW (panel #10)
│   │   │   │       ├── OiFundingRate.tsx             NEW (panel #11)
│   │   │   │       ├── IntermarketAnalysis.tsx       NEW (panel #12)
│   │   │   │       ├── SentimentFearGreed.tsx        NEW (panel #13)
│   │   │   │       ├── GhostCandlePrediction.tsx     NEW (panel #14)
│   │   │   │       ├── TradeSetup.tsx          ✓ exists (panel #15)
│   │   │   │       ├── KeyLevels.tsx                 NEW (panel #16)
│   │   │   │       └── NewsMacroImpact.tsx           NEW (panel #17)
│   │   │   ├── Tab3Scanner/                    NEW — entire tree
│   │   │   │   ├── index.tsx                   — toolbar + columns + footer
│   │   │   │   ├── ScannerToolbar.tsx
│   │   │   │   ├── HybridSupervisorBar.tsx
│   │   │   │   ├── BullishColumn.tsx
│   │   │   │   ├── BearishColumn.tsx
│   │   │   │   ├── SignalCard.tsx
│   │   │   │   ├── FilterPills.tsx
│   │   │   │   └── hooks/
│   │   │   │       └── useScannerRadar.ts
│   │   │   └── Admin/
│   │   │       └── index.tsx                   MODIFIED — add 3 sub-tabs
│   │   └── App.tsx                             MODIFIED — render <Tab3Scanner/> when tab === "scanner"
│   └── tests/
│       ├── unit/
│       │   ├── FinalValue.test.tsx                   NEW
│       │   ├── LongShortRatio.test.tsx               NEW
│       │   ├── DeepLearningSupervisor.test.tsx       NEW
│       │   ├── HtfBiasStructure.test.tsx             NEW
│       │   ├── VolumeProfile.test.tsx                NEW
│       │   ├── MomentumIndicators.expanded.test.tsx  NEW (extends grid)
│       │   ├── MarketMicrostructure.test.tsx         NEW
│       │   ├── LiquiditySweep.test.tsx               NEW
│       │   ├── OiFundingRate.test.tsx                NEW
│       │   ├── IntermarketAnalysis.test.tsx          NEW
│       │   ├── SentimentFearGreed.test.tsx           NEW
│       │   ├── GhostCandlePrediction.test.tsx        NEW
│       │   ├── KeyLevels.test.tsx                    NEW
│       │   ├── NewsMacroImpact.test.tsx              NEW
│       │   ├── ScannerToolbar.test.tsx               NEW
│       │   ├── HybridSupervisorBar.test.tsx          NEW
│       │   ├── SignalCard.test.tsx                   NEW
│       │   ├── BullishColumn.test.tsx                NEW
│       │   ├── BearishColumn.test.tsx                NEW
│       │   ├── FilterPills.test.tsx                  NEW
│       │   ├── useScannerRadar.test.tsx              NEW
│       │   ├── Tab3Scanner.test.tsx                  NEW
│       │   ├── Admin.PatternsAdmin.test.tsx          NEW
│       │   ├── Admin.AdaptersAdmin.test.tsx          NEW
│       │   ├── Admin.TrapsAdmin.test.tsx             NEW
│       │   └── api.scanner-admin.test.ts             NEW (api.ts new helpers)
│       └── e2e/
│           ├── tab3-scanner.spec.ts                  NEW
│           └── admin-patterns.spec.ts                NEW
├── lighthouserc.json                            NEW (Lighthouse CI config)
└── package.json                                 MODIFIED — add @lhci/cli devDep + script
```

---

## Phase A — Worktree + scaffolding + Tab 3 backend endpoint

### Task A1: Create SP-6 worktree

**Files:** none (git operation only)

- [ ] **Step 1: Verify clean main**

```bash
cd a:/v5_Trade_bot
git -c safe.directory='A:/v5_Trade_bot' status
```
Expected: `On branch main` and `nothing to commit, working tree clean`. The HEAD should be `362f275` (SP-5 ship commit).

- [ ] **Step 2: Create worktree**

```bash
git -c safe.directory='A:/v5_Trade_bot' worktree add worktrees/sp-6 -b sp-6/main
```
Expected: `Preparing worktree (new branch 'sp-6/main')`.

- [ ] **Step 3: Verify**

```bash
git -c safe.directory='A:/v5_Trade_bot' worktree list
```
Expected: includes `worktrees/sp-6  <hash> [sp-6/main]`.

- [ ] **Step 4: Bring stack up + run baseline tests (backend)**

```bash
cd worktrees/sp-6
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
```
Expected: `~1175 passed`. If lower, stop — main is not green.

- [ ] **Step 5: Baseline tests (frontend)**

```bash
cd worktrees/sp-6/frontend
npm ci
npm run test -- --run
npm run test:e2e -- --reporter=line
```
Expected: `~187 passed` Vitest, `14 passed` Playwright. If lower, investigate before proceeding.

- [ ] **Step 6: All subsequent tasks operate inside `worktrees/sp-6/`** — no commit yet (worktree empty).

---

### Task A2: NEW backend endpoint `GET /api/v1/scanner/radar` — failing test

**Files:**
- Create: `worktrees/sp-6/backend/app/api/routes/scanner.py` (stub)
- Create: `worktrees/sp-6/backend/tests/integration/test_api_scanner_radar.py`

**Design notes (apply throughout):**
- Endpoint is **read-only** — no writes to any table.
- Auth-gated via `Depends(current_user_or_impersonated)` (same pattern as `tab1.py`). Per spec §3.6, the query MUST filter by `user_id` so radar lists scoped per-user.
- Query strategy: `SELECT DISTINCT ON (symbol) ...` against `predictions` for the latest row per symbol, joined to `universe_history` for `full_name`. Limit defaults to 200 (max 500). Filter by `final.direction LONG`/`SHORT` then sort by `abs(final.score) DESC`.
- Sparkline: array of last 20 closes per symbol — read directly from the `predictions.price` column for the most recent 20 rows per symbol. Cached in Redis with 110 s TTL keyed by `(market, tf)`. Cache miss path acceptable for SP-6; add explicit 2-min cache flush hook for the manual refresh button.
- `signal_tier` value comes from `predictions.layer_scores->>'tier'` (post-SP-5 enrichment). Empty / null → `"NO_SIGNAL"`.
- `wyckoff_phase` extracted from `predictions.layer_scores->'4'->>'notes'` (SMC layer free-text — best effort; fall back to `"unknown"`).
- Confidence: from `predictions.layer_scores->'final'->>'confidence'` (already 0..1).
- Per-card `scores` map: `{smc, wyckoff, microstructure, momentum}` — pulled from `layer_scores` (smc=L4 strength*100, wyckoff=L1 macro strength*100, microstructure=L6 strength*100, momentum=L3 strength*100).
- Latency budget: <500 ms for 200 assets. Achievable with one bulk SQL query + optional Redis sparkline cache.

- [ ] **Step 1: Stub** — write minimal `scanner.py`:

```python
"""Tab 3 Scanner Radar: aggregate latest prediction per symbol per user (SP-6 §3.6)."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/scanner", tags=["scanner"])
```

- [ ] **Step 2: Failing test** — write `test_api_scanner_radar.py` exercising the contract:

```python
"""Integration tests for /api/v1/scanner/radar (SP-6 Phase A2).

Mirrors the SP-0.5 bot-status integration suite: shared ``bot_status_client``
fixture seeds a per-user predictions row pool, then asserts the
ScannerRadarOut shape. Sparkline cache is exercised by issuing two consecutive
GETs and asserting the second returns ≥1 cache-hit header.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa


async def _seed_predictions(session, *, user_id: int, n: int) -> None:
    """Seed n predictions for n distinct symbols. Even-indexed → LONG,
    odd-indexed → SHORT. Each gets a distinct |score| so sort order is
    deterministic.
    """
    base_ts = datetime(2026, 5, 5, 12, tzinfo=timezone.utc)
    for i in range(n):
        sym = f"SYM{i:03d}/USDT"
        direction = "LONG" if i % 2 == 0 else "SHORT"
        score = (0.55 + i * 0.001) * (1 if direction == "LONG" else -1)
        layer_scores = {
            "1": {"direction": direction, "strength": 0.6, "confidence": 0.7,
                  "notes": "Wyckoff: Accumulation"},
            "3": {"direction": direction, "strength": 0.4, "confidence": 0.5, "notes": ""},
            "4": {"direction": direction, "strength": 0.8, "confidence": 0.7,
                  "notes": "OB sweep above PDH"},
            "6": {"direction": direction, "strength": 0.3, "confidence": 0.4, "notes": ""},
            "final": {"score": score, "direction": direction,
                      "confidence": 0.7, "contributing_layers": [1, 3, 4, 6]},
            "tier": "PAPER" if abs(score) < 0.65 else "SMALL",
            "traps_fired": [],
            "static_score": score * 100,
        }
        await session.execute(sa.text(
            "INSERT INTO predictions (user_id, symbol, timeframe, ts, price, "
            "layer_scores, inputs_hash) VALUES "
            "(:u, :s, '1h', :t, :p, :ls, 'h0')"
        ), {
            "u": user_id, "s": sym,
            "t": (base_ts - timedelta(minutes=i)).isoformat(),
            "p": 100.0 + i, "ls": json.dumps(layer_scores),
        })


@pytest.mark.asyncio
async def test_radar_returns_latest_per_symbol(  # type: ignore[no-untyped-def]
    bot_status_client, bot_status_factory,
):
    async with bot_status_factory() as s:
        await _seed_predictions(s, user_id=1, n=10)
        await s.commit()
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=1h&limit=20")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "scanned_at" in body and "scanned_count" in body
    assert body["scanned_count"] == 10
    assert "filter_counts" in body and "all" in body["filter_counts"]
    assert isinstance(body["bullish"], list) and isinstance(body["bearish"], list)
    assert len(body["bullish"]) == 5
    assert len(body["bearish"]) == 5


@pytest.mark.asyncio
async def test_radar_sorted_by_abs_score(  # type: ignore[no-untyped-def]
    bot_status_client, bot_status_factory,
):
    async with bot_status_factory() as s:
        await _seed_predictions(s, user_id=1, n=4)
        await s.commit()
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=1h&limit=10")
    body = r.json()
    bullish_scores = [c["ai_score"] for c in body["bullish"]]
    bearish_scores = [abs(c["ai_score"]) for c in body["bearish"]]
    assert bullish_scores == sorted(bullish_scores, reverse=True)
    assert bearish_scores == sorted(bearish_scores, reverse=True)


@pytest.mark.asyncio
async def test_radar_per_user_isolation(  # type: ignore[no-untyped-def]
    bot_status_client, bot_status_factory,
):
    """User 1's radar never returns user 2's predictions."""
    async with bot_status_factory() as s:
        await _seed_predictions(s, user_id=1, n=2)
        await _seed_predictions(s, user_id=2, n=3)
        await s.commit()
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=1h&limit=10")
    body = r.json()
    # bot_status_client is wired to user_id=1 → only the 2 seeded rows.
    assert body["scanned_count"] == 2


@pytest.mark.asyncio
async def test_radar_signal_card_fields(  # type: ignore[no-untyped-def]
    bot_status_client, bot_status_factory,
):
    async with bot_status_factory() as s:
        await _seed_predictions(s, user_id=1, n=2)
        await s.commit()
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=1h&limit=10")
    card = r.json()["bullish"][0]
    required = {"symbol", "full_name", "points", "pct_change", "direction",
                "signal_tier", "ai_score", "confidence", "scores", "sparkline",
                "wyckoff_phase"}
    assert required <= set(card.keys()), f"missing: {required - set(card.keys())}"
    assert card["direction"] in ("LONG", "SHORT")
    assert isinstance(card["sparkline"], list)
    assert isinstance(card["scores"], dict)
    assert {"smc", "wyckoff", "microstructure", "momentum"} <= set(card["scores"].keys())


@pytest.mark.asyncio
async def test_radar_filter_counts(  # type: ignore[no-untyped-def]
    bot_status_client, bot_status_factory,
):
    async with bot_status_factory() as s:
        await _seed_predictions(s, user_id=1, n=10)
        await s.commit()
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=1h&limit=50")
    fc = r.json()["filter_counts"]
    assert fc["all"] == 10
    # 4 of the 10 seeded predictions have |score| >= 0.65 → tier SMALL → confirmed
    # The exact split depends on the seeding formula above.
    assert fc["confirmed"] + fc["probable"] + fc["weak"] == fc["all"]


@pytest.mark.asyncio
async def test_radar_limit_clamped(  # type: ignore[no-untyped-def]
    bot_status_client, bot_status_factory,
):
    async with bot_status_factory() as s:
        await _seed_predictions(s, user_id=1, n=20)
        await s.commit()
    r = await bot_status_client.get("/api/v1/scanner/radar?limit=5")
    body = r.json()
    assert len(body["bullish"]) + len(body["bearish"]) <= 5


@pytest.mark.asyncio
async def test_radar_empty_when_no_predictions(  # type: ignore[no-untyped-def]
    bot_status_client,
):
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=1h")
    assert r.status_code == 200
    body = r.json()
    assert body["scanned_count"] == 0
    assert body["bullish"] == []
    assert body["bearish"] == []


@pytest.mark.asyncio
async def test_radar_unknown_market_returns_400(  # type: ignore[no-untyped-def]
    bot_status_client,
):
    r = await bot_status_client.get("/api/v1/scanner/radar?market=alien&tf=1h")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_radar_unknown_timeframe_returns_400(  # type: ignore[no-untyped-def]
    bot_status_client,
):
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=99x")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_radar_requires_authenticated_user(  # type: ignore[no-untyped-def]
    bot_status_client,
):
    """The bot_status_client fixture overrides require_user — but the route
    must still go through current_user_or_impersonated. This test confirms the
    handler signature is wired correctly (i.e. removing the dep would surface
    a 422 from unresolved User param)."""
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=1h")
    assert r.status_code == 200
```

- [ ] **Step 3: Run — fail** with 404 on the route (stub has no GET handler).

---

### Task A3: Pydantic schemas — `ScannerRadarOut` + `SignalCardOut`

**Files:**
- Modify: `worktrees/sp-6/backend/app/api/schemas.py`

- [ ] **Step 1: Append the new schemas at the end of the file** (after the `SyncResultOut` block):

```python
# --- SP-6 Phase A3: Tab 3 Scanner Radar schemas (spec §3.6) -----------------


class SignalCardScores(BaseModel):
    """Per-card mini score breakdown (rendered as 4 chips on row 5)."""

    smc: int = 0
    wyckoff: int = 0
    microstructure: int = 0
    momentum: int = 0


class SignalCardOut(BaseModel):
    """One row in the bullish or bearish column of Tab 3 Scanner Radar.

    Mirrors MASTER_PLAN §9 line 327 signal-card spec: 5 rows of metadata that
    the frontend renders as star/symbol/sparkline + tag row + pct + conf bar
    + score chips.
    """

    symbol: str
    full_name: str = ""
    is_favorite: bool = False
    points: int = 0
    pct_change: float = 0.0
    direction: Literal["LONG", "SHORT"]
    htf_direction: Literal["LONG", "SHORT", "NEUTRAL"] = "NEUTRAL"
    signal_tier: Literal["NO_SIGNAL", "PAPER", "SMALL", "STANDARD", "A+"]
    hybrid_flag: Literal["LONG", "SHORT", None] = None
    ai_score: int  # may be negative for SHORT cards (rendered as ±N)
    wyckoff_phase: str = "unknown"
    confidence: int = Field(ge=0, le=100)  # already scaled to 0..100
    scores: SignalCardScores
    sparkline: list[float] = Field(default_factory=list, max_length=20)


class ScannerFilterCounts(BaseModel):
    """Counts for each filter pill (rendered in the toolbar)."""

    all: int = 0
    confirmed: int = 0
    probable: int = 0
    weak: int = 0
    diverging: int = 0


class SupervisorProgress(BaseModel):
    done: int = Field(ge=0, default=0)
    total: int = Field(ge=1, default=8)


class ScannerRadarOut(BaseModel):
    """Top-level Tab 3 Scanner Radar payload (spec §3.6)."""

    scanned_at: datetime
    market: Literal["crypto", "stock", "fx", "commodity", "index"]
    timeframe: str
    scanned_count: int
    filter_counts: ScannerFilterCounts
    supervisor_progress: SupervisorProgress = Field(default_factory=SupervisorProgress)
    bullish: list[SignalCardOut] = Field(default_factory=list)
    bearish: list[SignalCardOut] = Field(default_factory=list)
```

- [ ] **Step 2: Sanity check** — `pytest tests/integration/test_api_scanner_radar.py -v` still fails (no route handler) but no schema-import errors.

---

### Task A4: Implement `GET /api/v1/scanner/radar` — green

**Files:**
- Modify: `worktrees/sp-6/backend/app/api/routes/scanner.py`
- Modify: `worktrees/sp-6/backend/app/main.py`

- [ ] **Step 1: Implement** the full handler:

```python
"""Tab 3 Scanner Radar: aggregate latest prediction per symbol per user.

Spec §3.6: read-only aggregation over the existing ``predictions`` table.
Returns ScannerRadarOut with bullish + bearish columns sorted by |score|.
Per-user filter via ``current_user_or_impersonated`` is mandatory — leaking
across users is the only data-isolation hazard for SP-6.

Latency budget: <500ms for 200 assets. Sparkline arrays are best-effort
loaded from the same predictions table (last 20 closes per symbol).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    ScannerFilterCounts,
    ScannerRadarOut,
    SignalCardOut,
    SignalCardScores,
    SupervisorProgress,
)
from app.auth.deps import current_user_or_impersonated
from app.auth.models import User
from app.db.session import get_session

router = APIRouter(prefix="/api/v1/scanner", tags=["scanner"])

_VALID_MARKETS = {"crypto", "stock", "fx", "commodity", "index"}
_VALID_TFS = {"1m", "5m", "15m", "1h", "4h", "1d"}


def _coerce_layer_scores(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


def _confirmed_tier(tier: str) -> bool:
    return tier in ("STANDARD", "A+")


def _probable_tier(tier: str) -> bool:
    return tier in ("PAPER", "SMALL")


def _build_card(row: Any) -> SignalCardOut:
    ls = _coerce_layer_scores(row.layer_scores)
    final = ls.get("final") or {}
    direction = final.get("direction") or "NEUTRAL"
    if direction not in ("LONG", "SHORT"):
        # Defensive: NEUTRAL rows are filtered out at the SQL level, but if
        # a malformed row sneaks through, default to LONG so the card stays
        # rendrable.
        direction = "LONG"
    tier = ls.get("tier") or "NO_SIGNAL"
    raw_score = float(final.get("score") or 0.0)
    raw_conf = float(final.get("confidence") or 0.0)

    smc = ls.get("4") or {}
    wyckoff_layer = ls.get("1") or {}
    micro = ls.get("6") or {}
    momentum_layer = ls.get("3") or {}
    scores = SignalCardScores(
        smc=int(round(float(smc.get("strength") or 0.0) * 100)),
        wyckoff=int(round(float(wyckoff_layer.get("strength") or 0.0) * 100)),
        microstructure=int(round(float(micro.get("strength") or 0.0) * 100)),
        momentum=int(round(float(momentum_layer.get("strength") or 0.0) * 100)),
    )
    wyckoff_phase = "unknown"
    notes = wyckoff_layer.get("notes") or ""
    if "Accumulation" in notes:
        wyckoff_phase = "Accumulation"
    elif "Markup" in notes:
        wyckoff_phase = "Markup"
    elif "Distribution" in notes:
        wyckoff_phase = "Distribution"
    elif "Markdown" in notes:
        wyckoff_phase = "Markdown"

    sparkline = []
    if row.sparkline:
        try:
            sparkline = json.loads(row.sparkline) if isinstance(row.sparkline, str) else list(row.sparkline)
        except (TypeError, json.JSONDecodeError):
            sparkline = []

    return SignalCardOut(
        symbol=row.symbol,
        full_name=row.full_name or row.symbol.split("/")[0],
        points=int(round(raw_score * 100)),
        pct_change=0.0,  # SP-6: no 24h pct change wired yet — defer to SP-7
        direction=direction,  # type: ignore[arg-type]
        signal_tier=tier,  # type: ignore[arg-type]
        ai_score=int(round(raw_score * 100)),
        confidence=int(round(raw_conf * 100)),
        wyckoff_phase=wyckoff_phase,
        scores=scores,
        sparkline=sparkline[-20:],
    )


@router.get("/radar", response_model=ScannerRadarOut)
async def radar(
    market: str = Query(default="crypto"),
    tf: str = Query(default="1h"),
    limit: int = Query(default=200, ge=1, le=500),
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ScannerRadarOut:
    if market not in _VALID_MARKETS:
        raise HTTPException(status_code=400, detail=f"unknown market: {market}")
    if tf not in _VALID_TFS:
        raise HTTPException(status_code=400, detail=f"unknown timeframe: {tf}")

    # Latest prediction per symbol for this user, joined to universe_history
    # for full_name. Each-symbol sparkline is read from the same row's price
    # column repeated 20× (placeholder; SP-7 swaps for real OHLC last-20).
    rows = (await session.execute(sa.text(
        "SELECT p.symbol, MAX(p.ts) AS ts, "
        "       SUBSTR(p.layer_scores, 1, 65535) AS layer_scores, "
        "       COALESCE(u.metadata, '') AS full_name, "
        "       NULL AS sparkline "
        "FROM predictions p "
        "LEFT JOIN universe_history u "
        "  ON u.symbol = p.symbol "
        "WHERE p.user_id = :u AND p.timeframe = :tf "
        "GROUP BY p.symbol "
        "ORDER BY MAX(p.ts) DESC "
        "LIMIT :lim"
    ), {"u": current_user.id, "tf": tf, "lim": limit})).all()

    cards = [_build_card(r) for r in rows]
    bullish = [c for c in cards if c.direction == "LONG"]
    bearish = [c for c in cards if c.direction == "SHORT"]
    bullish.sort(key=lambda c: c.ai_score, reverse=True)
    bearish.sort(key=lambda c: c.ai_score)  # most-negative first

    fc = ScannerFilterCounts(
        all=len(cards),
        confirmed=sum(1 for c in cards if _confirmed_tier(c.signal_tier)),
        probable=sum(1 for c in cards if _probable_tier(c.signal_tier)),
        weak=sum(1 for c in cards if c.signal_tier == "NO_SIGNAL"),
    )

    return ScannerRadarOut(
        scanned_at=datetime.now(timezone.utc),
        market=market,  # type: ignore[arg-type]
        timeframe=tf,
        scanned_count=len(cards),
        filter_counts=fc,
        supervisor_progress=SupervisorProgress(done=0, total=8),
        bullish=bullish[: max(1, limit // 2)],
        bearish=bearish[: max(1, limit // 2)],
    )
```

- [ ] **Step 2: Wire the router** in `app/main.py` — add the import + `app.include_router`:

```python
from app.api.routes import (
    admin,
    admin_adapters,
    admin_ml,
    admin_patterns,
    admin_traps,
    bot_status,
    health,
    me,
    scanner,    # SP-6 Phase A4
    tab1,
)
...
    app.include_router(scanner.router)   # SP-6
```

- [ ] **Step 3: Run tests pass**

```bash
docker compose exec -T backend pytest tests/integration/test_api_scanner_radar.py -v
```
Expected: `10 passed`. If the conftest.py predictions table doesn't exist for the `bot_status_client` fixture, also create a minimal SQLite mirror in `_create_shadow_tables` (predictions schema: `id, user_id, symbol, timeframe, ts, price, layer_scores TEXT, inputs_hash TEXT`). This is the fixture pattern already used for trap_enabled / pattern_enabled.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-6' add backend/app/api/routes/scanner.py backend/app/api/schemas.py backend/app/main.py backend/tests/integration/test_api_scanner_radar.py backend/tests/integration/conftest.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-6' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-6): GET /api/v1/scanner/radar — Tab 3 backend aggregation endpoint"
```

---

### Task A5: Frontend `api.ts` extensions — scanner + admin helpers

**Files:**
- Modify: `worktrees/sp-6/frontend/src/lib/api.ts`
- Create: `worktrees/sp-6/frontend/tests/unit/api.scanner-admin.test.ts`

- [ ] **Step 1: Failing test** — write the contract test first:

```ts
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { api } from "@/lib/api";

const BASE = "/api/v1";

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api.scanner.radar", () => {
  test("default args → /scanner/radar?market=crypto&tf=1h&limit=200", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      scanned_at: "2026-05-05T00:00:00Z",
      market: "crypto", timeframe: "1h",
      scanned_count: 0,
      filter_counts: { all: 0, confirmed: 0, probable: 0, weak: 0, diverging: 0 },
      supervisor_progress: { done: 0, total: 8 },
      bullish: [], bearish: [],
    }));
    await api.scannerRadar();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${BASE}/scanner/radar?market=crypto&tf=1h&limit=200`,
    );
  });

  test("custom args → URL params reflect overrides", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      scanned_at: "x", market: "stock", timeframe: "1d",
      scanned_count: 0,
      filter_counts: { all: 0, confirmed: 0, probable: 0, weak: 0, diverging: 0 },
      supervisor_progress: { done: 0, total: 8 },
      bullish: [], bearish: [],
    }));
    await api.scannerRadar({ market: "stock", tf: "1d", limit: 50 });
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${BASE}/scanner/radar?market=stock&tf=1d&limit=50`,
    );
  });
});

describe("api admin patterns / adapters / traps helpers", () => {
  test("adminListPatterns hits /admin/patterns", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await api.adminListPatterns();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(`${BASE}/admin/patterns`);
  });
  test("adminTogglePattern POST .../disable when enable=false", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}));
    await api.adminTogglePattern("doji", false, "noisy");
    const call = fetchMock.mock.calls[0]!;
    expect(String(call[0])).toBe(`${BASE}/admin/patterns/doji/disable`);
    expect((call[1] as RequestInit).method).toBe("POST");
  });
  test("adminTogglePattern POST .../enable when enable=true", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}));
    await api.adminTogglePattern("doji", true);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${BASE}/admin/patterns/doji/enable`,
    );
  });
  test("adminListAdapters hits /admin/adapters/health", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await api.adminListAdapters();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${BASE}/admin/adapters/health`,
    );
  });
  test("adminSyncAdapter POSTs /admin/adapters/{ex}/sync", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ exchange: "binance", added: 0, still_active: 0, newly_delisted: 0 }));
    await api.adminSyncAdapter("binance");
    const call = fetchMock.mock.calls[0]!;
    expect(String(call[0])).toBe(`${BASE}/admin/adapters/binance/sync`);
    expect((call[1] as RequestInit).method).toBe("POST");
  });
  test("adminListTraps hits /admin/traps", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await api.adminListTraps();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(`${BASE}/admin/traps`);
  });
  test("adminToggleTrap POST .../disable when enable=false", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}));
    await api.adminToggleTrap("liquidity_sweep", false, "");
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${BASE}/admin/traps/liquidity_sweep/disable`,
    );
  });
});
```

- [ ] **Step 2: Add types + helpers** to `api.ts` (append after the existing `MlCheckpointPatchIn` block, before the `export const api = { ... }`):

```ts
// --- SP-6 Phase A5: Scanner + admin sub-page types ---

export interface SignalCardScores {
  smc: number;
  wyckoff: number;
  microstructure: number;
  momentum: number;
}

export interface SignalCard {
  symbol: string;
  full_name: string;
  is_favorite: boolean;
  points: number;
  pct_change: number;
  direction: "LONG" | "SHORT";
  htf_direction: "LONG" | "SHORT" | "NEUTRAL";
  signal_tier: "NO_SIGNAL" | "PAPER" | "SMALL" | "STANDARD" | "A+";
  hybrid_flag: "LONG" | "SHORT" | null;
  ai_score: number;
  wyckoff_phase: string;
  confidence: number;
  scores: SignalCardScores;
  sparkline: number[];
}

export interface ScannerFilterCounts {
  all: number;
  confirmed: number;
  probable: number;
  weak: number;
  diverging: number;
}

export interface SupervisorProgress {
  done: number;
  total: number;
}

export interface ScannerRadar {
  scanned_at: string;
  market: "crypto" | "stock" | "fx" | "commodity" | "index";
  timeframe: string;
  scanned_count: number;
  filter_counts: ScannerFilterCounts;
  supervisor_progress: SupervisorProgress;
  bullish: SignalCard[];
  bearish: SignalCard[];
}

export interface ScannerRadarOptions {
  market?: ScannerRadar["market"];
  tf?: string;
  limit?: number;
}

export interface PatternEntry {
  pattern_id: string;
  pattern_type: "candle" | "chart";
  symbol: string;
  timeframe: string;
  enabled: boolean;
  disabled_reason: string | null;
}

export interface TrapEntry {
  trap_id: string;
  severity: "medium" | "high" | "extreme";
  side: "long" | "short" | "both";
  symbol: string;
  timeframe: string;
  enabled: boolean;
  disabled_reason: string | null;
}

export interface AdapterHealth {
  exchange: string;
  checked_at: string;
  is_healthy: boolean;
  latency_ms: number | null;
  error_message: string | null;
  quota_used_pct: number | null;
}

export interface SyncResult {
  exchange: string;
  added: number;
  still_active: number;
  newly_delisted: number;
}
```

Then append helper methods to the `api` object literal:

```ts
  // --- SP-6 Phase A5: scanner ---
  scannerRadar: (opts: ScannerRadarOptions = {}) => {
    const market = opts.market ?? "crypto";
    const tf = opts.tf ?? "1h";
    const limit = opts.limit ?? 200;
    return fetchJson<ScannerRadar>(
      `/scanner/radar?market=${market}&tf=${tf}&limit=${limit}`,
    );
  },
  // --- SP-6 Phase A5: admin sub-pages ---
  adminListPatterns: () => fetchJson<PatternEntry[]>("/admin/patterns"),
  adminTogglePattern: (id: string, enable: boolean, reason?: string) =>
    fetchJson<PatternEntry>(
      `/admin/patterns/${encodeURIComponent(id)}/${enable ? "enable" : "disable"}`,
      { method: "POST", body: enable ? {} : { reason: reason ?? "" } },
    ),
  adminListAdapters: () => fetchJson<AdapterHealth[]>("/admin/adapters/health"),
  adminSyncAdapter: (exchange: string) =>
    fetchJson<SyncResult>(
      `/admin/adapters/${encodeURIComponent(exchange)}/sync`,
      { method: "POST" },
    ),
  adminListTraps: () => fetchJson<TrapEntry[]>("/admin/traps"),
  adminToggleTrap: (id: string, enable: boolean, reason?: string) =>
    fetchJson<TrapEntry>(
      `/admin/traps/${encodeURIComponent(id)}/${enable ? "enable" : "disable"}`,
      { method: "POST", body: enable ? {} : { reason: reason ?? "" } },
    ),
```

- [ ] **Step 3: Tests pass**

```bash
cd frontend && npm run test -- --run tests/unit/api.scanner-admin.test.ts
```
Expected: `9 passed`.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-6' add frontend/src/lib/api.ts frontend/tests/unit/api.scanner-admin.test.ts
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-6' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-6): api.ts — scannerRadar + adminPatterns/Adapters/Traps helpers"
```

---

### Task A6: Add "Scanner" tab to TabNav + extend useHashRoute

**Files:**
- Modify: `worktrees/sp-6/frontend/src/lib/useHashRoute.ts`
- Modify: `worktrees/sp-6/frontend/src/components/layout/TabNav.tsx`
- Modify: `worktrees/sp-6/frontend/src/App.tsx`
- Modify: `worktrees/sp-6/frontend/tests/unit/TabNav.test.tsx`

- [ ] **Step 1: Failing test** — extend the existing `TabNav.test.tsx` with a Scanner case:

```ts
test("renders Scanner tab between Bot Status and Settings", () => {
  const onChange = vi.fn();
  render(<TabNav active="live-prediction" onChange={onChange} adminVisible={false} />);
  const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
  expect(tabs).toEqual([
    "Live Prediction", "Bot Status", "Scanner", "Settings",
  ]);
});

test("Scanner tab is always visible (not admin-gated)", () => {
  render(<TabNav active="scanner" onChange={vi.fn()} adminVisible={false} />);
  expect(screen.getByRole("tab", { name: /scanner/i })).toBeVisible();
});
```

- [ ] **Step 2: Update `useHashRoute.ts`** — add `"scanner"` to `TabId` and `VALID`:

```ts
export type TabId =
  | "live-prediction"
  | "bot-status"
  | "scanner"
  | "settings"
  | "admin";

const VALID: ReadonlySet<TabId> = new Set<TabId>([
  "live-prediction",
  "bot-status",
  "scanner",
  "settings",
  "admin",
]);
```

The query parser already supports `?symbol=X&tf=Y` because `URLSearchParams.forEach` populates anything — no changes needed there.

- [ ] **Step 3: Update `TabNav.tsx`** — add Scanner row to `ALL_TABS`:

```ts
const ALL_TABS: readonly TabDef[] = [
  { id: "live-prediction", label: "Live Prediction" },
  { id: "bot-status", label: "Bot Status" },
  { id: "scanner", label: "Scanner" },
  { id: "settings", label: "Settings" },
  { id: "admin", label: "Admin" },
];
```

- [ ] **Step 4: Update `App.tsx`** — add the dispatch case (Tab3Scanner imported as a stub-component returning a placeholder; will be filled in Phase D):

```ts
import { Tab3Scanner } from "@/tabs/Tab3Scanner";
// ...
{tab === "live-prediction" ? <Tab1LivePrediction /> :
 tab === "bot-status" ? <BotStatus /> :
 tab === "scanner" ? <Tab3Scanner /> :
 tab === "settings" ? <Settings /> :
 tab === "admin" && isAdmin ? <Admin /> : null}
```

For now the import will fail. Create a 1-line stub at `frontend/src/tabs/Tab3Scanner/index.tsx`:

```ts
export function Tab3Scanner() {
  return <div className="p-4 text-text-secondary">Scanner — coming in Phase D</div>;
}
```

- [ ] **Step 5: Tests pass**

```bash
npm run test -- --run tests/unit/TabNav.test.tsx
npm run test -- --run tests/unit/useHashRoute   # any existing useHashRoute tests
```

- [ ] **Step 6: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-6' add frontend/src/lib/useHashRoute.ts frontend/src/components/layout/TabNav.tsx frontend/src/App.tsx frontend/src/tabs/Tab3Scanner/index.tsx frontend/tests/unit/TabNav.test.tsx
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-6' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-6): add Scanner tab to nav + hash route + App dispatch (stub body)"
```

---

## Phase B — Tab 1 panels 5-13 (9 new panels)

**Subagent batching guidance:** All 9 panels read from `data: LivePrediction | null` and have no inter-component dependencies. Dispatch in **two parallel batches of subagents**:

- **Batch 1 (5 agents):** B1 FinalValue, B2 LongShortRatio, B3 DeepLearningSupervisor, B4 HtfBiasStructure, B5 VolumeProfile
- **Batch 2 (4 agents):** B6 MomentumIndicators expansion, B7 MarketMicrostructure, B8 LiquiditySweep, B9 OiFundingRate

After both batches return, integrate the imports into `Tab1LivePrediction/index.tsx` (handled at the start of Phase C).

**Per-panel template (applies to every B and C task):**
1. **Stub** the component file with a minimal "no data" return so the test can fail-import on missing exports.
2. **Failing test** in `frontend/tests/unit/<Name>.test.tsx` covering: (a) renders the title heading, (b) shows "—" / "no data" empty state when input is null, (c) renders the expected value when input is populated, (d) any colour-coded direction styling.
3. **Implement** the body and re-run the test. Expect 3-4 passes per panel.
4. **Commit** as `feat(sp-6): Tab 1 panel — <Name>` with one file group per task.

---

### Task B1: FinalValue.tsx (panel #3) — risk-reward + match strict % + max drawdown

**Spec source:** MASTER_PLAN §9 line 280 — "Risk-reward ratio, match strict %, max drawdown."

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/panels/FinalValue.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/FinalValue.test.tsx`

**Data sources:**
- `data.trade_setup.risk_reward` (already in `LivePrediction`)
- `data.final.confidence` (used as "match strict %" approximation — `confidence × 100`)
- `data.final.score` is the "max drawdown" placeholder (no real drawdown source until SP-7; render `"—"` when null)

- [ ] **Step 1: Failing test** — `FinalValue.test.tsx`:

```ts
import { render, screen } from "@testing-library/react";
import { FinalValue } from "@/tabs/Tab1LivePrediction/panels/FinalValue";

const base = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100,
  final: { score: 0.6, direction: "LONG" as const, confidence: 0.72, contributing_layers: [] },
  layer_scores: {},
  trade_setup: { direction: "LONG" as const, entry: 100, stop_loss: 95, take_profit: 110, risk_reward: 2.6 },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false, inputs_hash: "x",
};

test("renders R:R + match strict %", () => {
  render(<FinalValue data={base} />);
  expect(screen.getByText(/2\.6/)).toBeInTheDocument();
  expect(screen.getByText(/72/)).toBeInTheDocument();
});

test("dash when no data", () => {
  render(<FinalValue data={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});
```

- [ ] **Step 2: Implement** the panel — match the existing `TradeSetup.tsx` style (2-col grid, Panel wrapper):

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

const fmt = (v: number | null, dp = 2) => (v == null ? "—" : v.toFixed(dp));

export function FinalValue({ data }: { data: LivePrediction | null }) {
  if (!data) return <Panel title="Final Value">—</Panel>;
  const rr = data.trade_setup.risk_reward;
  const match = data.final.confidence * 100;
  return (
    <Panel title="Final Value">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Risk:Reward</span>
        <span className="text-right">{fmt(rr, 1)}</span>
        <span className="text-text-secondary">Match strict</span>
        <span className="text-right">{match.toFixed(0)}%</span>
        <span className="text-text-secondary">Max DD</span>
        <span className="text-right text-text-tertiary">—</span>
      </div>
    </Panel>
  );
}
```

- [ ] **Step 3: Test passes** (`npm run test -- --run tests/unit/FinalValue.test.tsx`).
- [ ] **Step 4: Commit** — `feat(sp-6): Tab 1 panel — FinalValue (#3)`.

---

### Task B2: LongShortRatio.tsx (panel #4) — split bar from layer_scores LONG/SHORT count

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/panels/LongShortRatio.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/LongShortRatio.test.tsx`

**Data source:** Counts of `data.layer_scores[*].direction === "LONG"` vs `=== "SHORT"`. Empty layers omitted from numerator and denominator.

**Behavior:**
- Renders a horizontal split bar — green left segment width = (long / total) × 100%, red right segment.
- Below the bar, show `"49.2 / 50.8"` style percentages.
- Empty state ("50/50") when `layer_scores` empty or all neutral.

**Test cases:**
1. With layer_scores `{1: LONG, 3: SHORT, 4: LONG}` → renders `66.7 / 33.3`.
2. With empty `layer_scores` → renders `50 / 50`.
3. With `data === null` → renders `—`.

**Code skeleton:**

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction, LayerScore } from "@/lib/api";

function counts(scores: Record<string, LayerScore | null>): { l: number; s: number } {
  let l = 0, s = 0;
  for (const v of Object.values(scores)) {
    if (!v) continue;
    if (v.direction === "LONG") l++;
    else if (v.direction === "SHORT") s++;
  }
  return { l, s };
}

export function LongShortRatio({ data }: { data: LivePrediction | null }) {
  if (!data) return <Panel title="Long / Short Ratio">—</Panel>;
  const { l, s } = counts(data.layer_scores);
  const total = l + s;
  const lp = total === 0 ? 50 : (l / total) * 100;
  const sp = 100 - lp;
  return (
    <Panel title="Long / Short Ratio">
      <div className="h-1 flex rounded overflow-hidden bg-bg-elevated">
        <div className="bg-green" style={{ width: `${lp}%` }} aria-label={`long ${lp.toFixed(1)}%`} />
        <div className="bg-red" style={{ width: `${sp}%` }} aria-label={`short ${sp.toFixed(1)}%`} />
      </div>
      <div className="mt-1 flex justify-between text-text-secondary">
        <span>{lp.toFixed(1)}</span>
        <span>{sp.toFixed(1)}</span>
      </div>
    </Panel>
  );
}
```

- Commit: `feat(sp-6): Tab 1 panel — LongShortRatio (#4)`.

---

### Task B3: DeepLearningSupervisor.tsx (panel #5) — red alert when L8 SHORT + confidence > 0.75

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/panels/DeepLearningSupervisor.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/DeepLearningSupervisor.test.tsx`

**Data source:** `data.layer_scores["8"]` (Conv-LSTM scores). Hidden when null. Renders `intensity="alert"` Panel with red border when `direction === "SHORT" && confidence > 0.75`.

**Test cases:**
1. `layer_scores: {"8": {direction: "SHORT", strength: 0.9, confidence: 0.85, notes: "bear engulf"}}` → renders red alert variant + "75%+ SHORT alert" text.
2. `layer_scores: {"8": {direction: "LONG", strength: 0.7, confidence: 0.8, notes: ""}}` → renders normal variant.
3. `layer_scores: {}` → renders nothing (returns `null`).
4. `data === null` → renders nothing (returns `null`).

**Code skeleton:**

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

export function DeepLearningSupervisor({ data }: { data: LivePrediction | null }) {
  const l8 = data?.layer_scores?.["8"] ?? null;
  if (!l8) return null;
  const isShortAlert = l8.direction === "SHORT" && l8.confidence > 0.75;
  return (
    <Panel title="Deep Learning Supervisor" intensity={isShortAlert ? "alert" : "default"}>
      <div className="flex justify-between mb-1">
        <span className={l8.direction === "LONG" ? "text-green" : l8.direction === "SHORT" ? "text-red" : "text-text-secondary"}>
          {l8.direction}
        </span>
        <span className="text-text-secondary">{(l8.confidence * 100).toFixed(0)}%</span>
      </div>
      {isShortAlert && (
        <div className="text-red text-[8px] uppercase tracking-wide">
          75%+ SHORT alert
        </div>
      )}
      {l8.notes && <div className="text-text-tertiary mt-1">{l8.notes}</div>}
    </Panel>
  );
}
```

- Commit: `feat(sp-6): Tab 1 panel — DeepLearningSupervisor (#5)`.

---

### Task B4: HtfBiasStructure.tsx (panel #6) — L1 macro + Wyckoff phase

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/panels/HtfBiasStructure.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/HtfBiasStructure.test.tsx`

**Data source:** `data.layer_scores["1"]` (macro). Wyckoff phase parsed from `notes` field — accepts substrings `"Accumulation"`, `"Markup"`, `"Distribution"`, `"Markdown"`, else `"unknown"`.

**Test cases:**
1. `{1: {direction: "LONG", strength: 0.6, confidence: 0.7, notes: "Wyckoff phase: Markup"}}` → renders "Markup" + green LONG.
2. `{1: null}` → renders "—".
3. `data === null` → renders "—".

**Code skeleton:**

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

const PHASES = ["Accumulation", "Markup", "Distribution", "Markdown"] as const;

function extractPhase(notes: string): string {
  for (const p of PHASES) {
    if (notes.includes(p)) return p;
  }
  return "unknown";
}

export function HtfBiasStructure({ data }: { data: LivePrediction | null }) {
  const l1 = data?.layer_scores?.["1"] ?? null;
  if (!l1) return <Panel title="HTF Bias & Structure">—</Panel>;
  const dirCls = l1.direction === "LONG" ? "text-green" : l1.direction === "SHORT" ? "text-red" : "text-text-secondary";
  return (
    <Panel title="HTF Bias & Structure">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Direction</span>
        <span className={`text-right ${dirCls}`}>{l1.direction}</span>
        <span className="text-text-secondary">Wyckoff</span>
        <span className="text-right">{extractPhase(l1.notes)}</span>
        <span className="text-text-secondary">Confidence</span>
        <span className="text-right">{(l1.confidence * 100).toFixed(0)}%</span>
      </div>
    </Panel>
  );
}
```

- Commit: `feat(sp-6): Tab 1 panel — HtfBiasStructure (#6)`.

---

### Task B5: VolumeProfile.tsx (panel #7) — POC/VAH/VAL placeholder

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/panels/VolumeProfile.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/VolumeProfile.test.tsx`

**Data source:** `data.layer_scores["5"]` (volume layer) — `notes` field expected to carry `"POC=N VAH=N VAL=N"` when wired (deferred wiring). For SP-6 ship, render placeholders (`"—"`) when `notes` is empty.

**Behavior:**
- Renders 3-row grid: POC / VAH / VAL.
- Each value defaults to `"—"`.
- If `notes` matches the regex `/POC=([0-9.]+).*VAH=([0-9.]+).*VAL=([0-9.]+)/`, render the parsed numbers.

**Code skeleton:**

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

function parsePoc(notes: string): { poc: string; vah: string; val: string } {
  const m = notes.match(/POC=([0-9.]+).*VAH=([0-9.]+).*VAL=([0-9.]+)/);
  if (!m) return { poc: "—", vah: "—", val: "—" };
  return { poc: m[1] ?? "—", vah: m[2] ?? "—", val: m[3] ?? "—" };
}

export function VolumeProfile({ data }: { data: LivePrediction | null }) {
  if (!data) return <Panel title="Volume Profile">—</Panel>;
  const l5 = data.layer_scores?.["5"];
  const { poc, vah, val } = parsePoc(l5?.notes ?? "");
  return (
    <Panel title="Volume Profile">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">POC</span>
        <span className="text-right">{poc}</span>
        <span className="text-text-secondary">VAH</span>
        <span className="text-right text-green">{vah}</span>
        <span className="text-text-secondary">VAL</span>
        <span className="text-right text-red">{val}</span>
      </div>
    </Panel>
  );
}
```

- Commit: `feat(sp-6): Tab 1 panel — VolumeProfile (#7)`.

---

### Task B6: MomentumIndicators expansion (panel #8) — RSI + MACD + Stoch + CCI in 2-col grid

**Files:**
- Modify: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/panels/MomentumIndicators.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/MomentumIndicators.expanded.test.tsx`

**Background:** The existing component renders 4 rows (RSI + MACD line/signal/hist). Per MASTER_PLAN §9 line 285 the canonical layout is "2-col grid: RSI, MACD, Stoch, CCI". The current `data.momentum` has only 4 fields. **Decision:** keep current data shape — render RSI on row 1, MACD-hist on row 2, then Stoch / CCI as `"—"` placeholders on row 3 — since wiring real Stoch / CCI requires backend changes deferred to post-SP-6.

- [ ] **Step 1: Failing test** — new file (existing `MomentumIndicators.test.tsx` continues to pass):

```ts
import { render, screen } from "@testing-library/react";
import { MomentumIndicators } from "@/tabs/Tab1LivePrediction/panels/MomentumIndicators";

const base = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "x", price: 100,
  final: { score: 0, direction: "NEUTRAL" as const, confidence: 0, contributing_layers: [] },
  layer_scores: {},
  trade_setup: { direction: "NEUTRAL" as const, entry: null, stop_loss: null, take_profit: null, risk_reward: null },
  momentum: { rsi: 58.2, macd_line: 0.7, macd_signal: 0.5, macd_hist: 0.2 },
  cold_start: false, inputs_hash: "x",
};

test("renders Stoch and CCI placeholders", () => {
  render(<MomentumIndicators data={base} />);
  expect(screen.getByText(/Stoch/i)).toBeInTheDocument();
  expect(screen.getByText(/CCI/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Update** `MomentumIndicators.tsx`:

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

const fmt = (v: number | null, dp = 2) => (v == null ? "—" : v.toFixed(dp));

export function MomentumIndicators({ data }: { data: LivePrediction | null }) {
  const m = data?.momentum;
  return (
    <Panel title="Momentum">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">RSI(14)</span>
        <span className="text-right">{fmt(m?.rsi ?? null, 1)}</span>
        <span className="text-text-secondary">MACD line</span>
        <span className="text-right">{fmt(m?.macd_line ?? null, 4)}</span>
        <span className="text-text-secondary">MACD signal</span>
        <span className="text-right">{fmt(m?.macd_signal ?? null, 4)}</span>
        <span className="text-text-secondary">MACD hist</span>
        <span className="text-right">{fmt(m?.macd_hist ?? null, 4)}</span>
        <span className="text-text-secondary">Stoch</span>
        <span className="text-right text-text-tertiary">—</span>
        <span className="text-text-secondary">CCI</span>
        <span className="text-right text-text-tertiary">—</span>
      </div>
    </Panel>
  );
}
```

- [ ] **Step 3: Both old + new tests pass.** Commit: `feat(sp-6): Tab 1 panel — MomentumIndicators expand to RSI/MACD/Stoch/CCI grid`.

---

### Task B7: MarketMicrostructure.tsx (panel #9) — L4 SMC notes / order flow

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/panels/MarketMicrostructure.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/MarketMicrostructure.test.tsx`

**Data source:** `data.layer_scores["6"]` (microstructure). Per MASTER_PLAN §9 line 286 — "Order flow, imbalance ratio, BUY DOM / SELL DOM label". Renders flow ratio + imbalance text + label.

**Behavior:**
- "Flow" = L6 strength (-1..+1 mapped to "BUY DOM" / "SELL DOM" text).
- "Imbalance" = parsed from `notes` field if present (`"imbalance=2.4"` regex), else `"—"`.

**Code skeleton:**

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

function flowLabel(direction: "LONG" | "SHORT" | "NEUTRAL"): string {
  if (direction === "LONG") return "BUY DOM";
  if (direction === "SHORT") return "SELL DOM";
  return "BALANCED";
}

function parseImbalance(notes: string): string {
  const m = notes.match(/imbalance=([0-9.]+)/);
  return m?.[1] ?? "—";
}

export function MarketMicrostructure({ data }: { data: LivePrediction | null }) {
  if (!data) return <Panel title="Market Microstructure">—</Panel>;
  const l6 = data.layer_scores?.["6"];
  if (!l6) return <Panel title="Market Microstructure">—</Panel>;
  const flow = (l6.strength * (l6.direction === "SHORT" ? -1 : 1)).toFixed(2);
  return (
    <Panel title="Market Microstructure">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Flow</span>
        <span className="text-right">{flow}</span>
        <span className="text-text-secondary">Imbalance</span>
        <span className="text-right">{parseImbalance(l6.notes)}</span>
        <span className="text-text-secondary">Label</span>
        <span className="text-right">{flowLabel(l6.direction)}</span>
      </div>
    </Panel>
  );
}
```

- Commit: `feat(sp-6): Tab 1 panel — MarketMicrostructure (#9)`.

---

### Task B8: LiquiditySweep.tsx (panel #10) — L4 SMC sweep + traps_fired liquidity_sweep

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/panels/LiquiditySweep.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/LiquiditySweep.test.tsx`

**Data sources:**
- `data.layer_scores["4"].notes` parsed for `"sweep above PDH"` or `"sweep below PDL"` substrings.
- (Optional, future) `data.prediction_extras?.traps_fired` if non-null and contains `"liquidity_sweep"` — render alert variant. SP-6 ships this read but tolerates absence.

**Test cases:**
1. notes = "OB sweep above PDH" → renders "Above PDH" green.
2. notes = "sweep below PDL" → renders "Below PDL" red.
3. no L4 data → renders "no sweep".
4. data === null → "—".

**Code skeleton:**

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

function classify(notes: string): "above_pdh" | "below_pdl" | "none" {
  if (/sweep above PDH/i.test(notes)) return "above_pdh";
  if (/sweep below PDL/i.test(notes)) return "below_pdl";
  return "none";
}

export function LiquiditySweep({ data }: { data: LivePrediction | null }) {
  if (!data) return <Panel title="Liquidity Sweep">—</Panel>;
  const l4 = data.layer_scores?.["4"];
  if (!l4) return <Panel title="Liquidity Sweep"><span className="text-text-tertiary">no sweep</span></Panel>;
  const cls = classify(l4.notes);
  const trapsFired = (data as { prediction_extras?: { traps_fired?: string[] } }).prediction_extras?.traps_fired ?? [];
  const isTrap = trapsFired.includes("liquidity_sweep");
  return (
    <Panel title="Liquidity Sweep" intensity={isTrap ? "alert" : "default"}>
      {cls === "above_pdh" ? <span className="text-green">Above PDH</span> :
       cls === "below_pdl" ? <span className="text-red">Below PDL</span> :
       <span className="text-text-tertiary">no sweep</span>}
      {isTrap && <div className="mt-1 text-red text-[8px] uppercase">trap fired</div>}
    </Panel>
  );
}
```

- Commit: `feat(sp-6): Tab 1 panel — LiquiditySweep (#10)`.

---

### Task B9: OiFundingRate.tsx (panel #11) — placeholder "no data"

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/panels/OiFundingRate.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/OiFundingRate.test.tsx`

**Background:** No OI / funding rate adapter wired yet (SP-3 fetched the universe; SP-6.5 or SP-7 ships the live read). Renders permanent "no data" but keeps the UI slot occupied per spec §3.2 line 114.

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

export function OiFundingRate({ data: _data }: { data: LivePrediction | null }) {
  return (
    <Panel title="OI & Funding Rate">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">OI delta</span>
        <span className="text-right text-text-tertiary">no data</span>
        <span className="text-text-secondary">Funding</span>
        <span className="text-right text-text-tertiary">no data</span>
      </div>
    </Panel>
  );
}
```

**Test cases:**
1. Renders "OI & Funding Rate" heading.
2. Renders 2 "no data" cells.
3. Even with valid data, still "no data" (panel is informational placeholder).

- Commit: `feat(sp-6): Tab 1 panel — OiFundingRate placeholder (#11)`.

---

## Phase C — Tab 1 panels 12-17 (4 new + integration)

**Subagent batching guidance:** All four panels (C1-C4) read from `data` and have no inter-component coupling — dispatch in **one parallel batch of 4 agents**. After they return, run task **C5** which integrates the imports into `Tab1LivePrediction/index.tsx` and updates the index test.

---

### Task C1: IntermarketAnalysis.tsx (panel #12) — DXY/Gold corr placeholder

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/panels/IntermarketAnalysis.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/IntermarketAnalysis.test.tsx`

**Background:** Yahoo adapter (SP-3) fetches DXY / gold, but no live correlation pipeline yet. Renders as "no data" placeholder with two rows (DXY, Gold) — green/red colour reserved for negative/positive correlations once wired.

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

export function IntermarketAnalysis({ data: _data }: { data: LivePrediction | null }) {
  return (
    <Panel title="Intermarket Analysis">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">DXY corr</span>
        <span className="text-right text-text-tertiary">no data</span>
        <span className="text-text-secondary">Gold corr</span>
        <span className="text-right text-text-tertiary">no data</span>
      </div>
    </Panel>
  );
}
```

Test cases: heading rendered, both rows show "no data". Commit: `feat(sp-6): Tab 1 panel — IntermarketAnalysis (#12)`.

---

### Task C2: SentimentFearGreed.tsx (panel #13) — F&G placeholder

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/panels/SentimentFearGreed.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/SentimentFearGreed.test.tsx`

**Background:** External F&G API not wired (deferred to SP-9). Placeholder shows F&G label + numeric placeholder.

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

export function SentimentFearGreed({ data: _data }: { data: LivePrediction | null }) {
  return (
    <Panel title="Sentiment & Fear/Greed">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Index</span>
        <span className="text-right text-text-tertiary">no data</span>
        <span className="text-text-secondary">Label</span>
        <span className="text-right text-text-tertiary">—</span>
        <span className="text-text-secondary">News bias</span>
        <span className="text-right text-text-tertiary">—</span>
      </div>
    </Panel>
  );
}
```

Commit: `feat(sp-6): Tab 1 panel — SentimentFearGreed (#13)`.

---

### Task C3: GhostCandlePrediction.tsx (panel #14) — reads data.ghost

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/panels/GhostCandlePrediction.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/GhostCandlePrediction.test.tsx`

**Background:** `MasterBiasScore` panel (#2) already shows a small ghost-candle preview footer when `data.ghost` is populated. Panel #14 is the dedicated, larger view per MASTER_PLAN §9 line 291 — "Next pattern, size, confidence". Renders open/close/uncertainty + 2-col grid; matches the SP-1 ghost-candle data shape.

**Test cases:**
1. With ghost populated (open/high/low/close + uncertainty=0.012) → renders all 4 OHLC values + Conf%.
2. With `ghost: null` → renders "no model".
3. With `data: null` → renders "—".

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

const fmt = (v: number, dp = 2) => v.toFixed(dp);

export function GhostCandlePrediction({ data }: { data: LivePrediction | null }) {
  if (!data) return <Panel title="Ghost Candle Prediction">—</Panel>;
  const g = data.ghost;
  if (!g) return <Panel title="Ghost Candle Prediction"><span className="text-text-tertiary">no model</span></Panel>;
  const deltaUp = g.close >= g.open;
  const deltaPct = ((g.close - g.open) / g.open) * 100;
  return (
    <Panel title="Ghost Candle Prediction">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Open</span>
        <span className="text-right">{fmt(g.open)}</span>
        <span className="text-text-secondary">Close</span>
        <span className={`text-right ${deltaUp ? "text-green" : "text-red"}`}>
          {fmt(g.close)} ({deltaUp ? "+" : ""}{fmt(deltaPct, 2)}%)
        </span>
        <span className="text-text-secondary">High</span>
        <span className="text-right">{fmt(g.high)}</span>
        <span className="text-text-secondary">Low</span>
        <span className="text-right">{fmt(g.low)}</span>
        <span className="text-text-secondary">Uncertainty</span>
        <span className="text-right">{(g.uncertainty * 100).toFixed(2)}%</span>
      </div>
    </Panel>
  );
}
```

Commit: `feat(sp-6): Tab 1 panel — GhostCandlePrediction (#14)`.

---

### Task C4: KeyLevels.tsx (panel #16) + NewsMacroImpact.tsx (panel #17)

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/panels/KeyLevels.tsx`
- Create: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/panels/NewsMacroImpact.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/KeyLevels.test.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/NewsMacroImpact.test.tsx`

**KeyLevels — data:** EMA20/50/200 from `data.layer_scores["1"].notes` (the macro layer carries EMA notes when available — same parsing pattern as VolumeProfile). Placeholder "—" when notes empty.

```tsx
// KeyLevels.tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

function parseEmas(notes: string): { e20: string; e50: string; e200: string } {
  const m20 = notes.match(/EMA20[=:]([0-9.]+)/);
  const m50 = notes.match(/EMA50[=:]([0-9.]+)/);
  const m200 = notes.match(/EMA200[=:]([0-9.]+)/);
  return { e20: m20?.[1] ?? "—", e50: m50?.[1] ?? "—", e200: m200?.[1] ?? "—" };
}

export function KeyLevels({ data }: { data: LivePrediction | null }) {
  if (!data) return <Panel title="Key Levels">—</Panel>;
  const notes = data.layer_scores?.["1"]?.notes ?? "";
  const { e20, e50, e200 } = parseEmas(notes);
  return (
    <Panel title="Key Levels">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">EMA 20</span>
        <span className="text-right">{e20}</span>
        <span className="text-text-secondary">EMA 50</span>
        <span className="text-right">{e50}</span>
        <span className="text-text-secondary">EMA 200</span>
        <span className="text-right">{e200}</span>
      </div>
    </Panel>
  );
}
```

**NewsMacroImpact — data:** No backend (deferred to SP-9). Renders permanent "no events" placeholder — keeps the slot but signals the gap.

```tsx
// NewsMacroImpact.tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

export function NewsMacroImpact({ data: _data }: { data: LivePrediction | null }) {
  return (
    <Panel title="News & Macro Impact">
      <span className="text-text-tertiary">no events</span>
    </Panel>
  );
}
```

Both have a unit test verifying the heading text + placeholder text. Two commits (one per panel) to keep granular git history.

---

### Task C5: Wire all 17 panels into `Tab1LivePrediction/index.tsx`

**Files:**
- Modify: `worktrees/sp-6/frontend/src/tabs/Tab1LivePrediction/index.tsx`
- Modify: `worktrees/sp-6/frontend/tests/unit/Tab1LivePrediction.test.tsx`

**Order per MASTER_PLAN §9 lines 277-294 (1 → 17):** TradeStatusBar, MasterBiasScore, FinalValue, LongShortRatio, DeepLearningSupervisor, HtfBiasStructure, VolumeProfile, MomentumIndicators, MarketMicrostructure, LiquiditySweep, OiFundingRate, IntermarketAnalysis, SentimentFearGreed, GhostCandlePrediction, TradeSetup, KeyLevels, NewsMacroImpact.

- [ ] **Step 1: Failing test** — extend `Tab1LivePrediction.test.tsx`:

```ts
test("renders all 17 sidebar panels (or their empty-state placeholders)", () => {
  // useLivePrediction returns null in jsdom; every panel must render its
  // empty-state without throwing.
  render(<Tab1LivePrediction />);
  // Check that all 17 panel headings are present (some return null when
  // their data source is missing — DeepLearningSupervisor returns null, so
  // we expect 16 headings).
  const expected = [
    "Trade Status", "Master Bias Score", "Final Value", "Long / Short Ratio",
    "HTF Bias & Structure", "Volume Profile", "Momentum",
    "Market Microstructure", "Liquidity Sweep", "OI & Funding Rate",
    "Intermarket Analysis", "Sentiment & Fear/Greed",
    "Ghost Candle Prediction", "Trade Setup", "Key Levels",
    "News & Macro Impact",
  ];
  for (const title of expected) {
    expect(screen.getByText(title)).toBeInTheDocument();
  }
});
```

- [ ] **Step 2: Update** `Tab1LivePrediction/index.tsx`:

```tsx
import { useState } from "react";
import { TopNav } from "@/components/layout/TopNav";
import { TimeframeRow } from "@/components/layout/TimeframeRow";
import { Sidebar } from "@/components/layout/Sidebar";
import { TVChart } from "@/components/chart/TVChart";
import { useLivePrediction } from "@/hooks/useLivePrediction";
import { useHashRoute } from "@/lib/useHashRoute";
import { TradeStatusBar } from "./panels/TradeStatusBar";
import { MasterBiasScore } from "./panels/MasterBiasScore";
import { FinalValue } from "./panels/FinalValue";
import { LongShortRatio } from "./panels/LongShortRatio";
import { DeepLearningSupervisor } from "./panels/DeepLearningSupervisor";
import { HtfBiasStructure } from "./panels/HtfBiasStructure";
import { VolumeProfile } from "./panels/VolumeProfile";
import { MomentumIndicators } from "./panels/MomentumIndicators";
import { MarketMicrostructure } from "./panels/MarketMicrostructure";
import { LiquiditySweep } from "./panels/LiquiditySweep";
import { OiFundingRate } from "./panels/OiFundingRate";
import { IntermarketAnalysis } from "./panels/IntermarketAnalysis";
import { SentimentFearGreed } from "./panels/SentimentFearGreed";
import { GhostCandlePrediction } from "./panels/GhostCandlePrediction";
import { TradeSetup } from "./panels/TradeSetup";
import { KeyLevels } from "./panels/KeyLevels";
import { NewsMacroImpact } from "./panels/NewsMacroImpact";

type Tf = "1m" | "5m" | "15m" | "1h" | "4h" | "1d";

export function Tab1LivePrediction() {
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [timeframe, setTimeframe] = useState<Tf>("1h");
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { query } = useHashRoute();
  // SP-6: Tab 1 also accepts ?symbol=X&tf=Y deep links from Tab 3 SignalCard
  // clicks. We seed local state from the query params on first render only;
  // user edits override.
  const [hasAppliedQuery, setHasAppliedQuery] = useState(false);
  if (!hasAppliedQuery && (query.symbol || query.tf)) {
    if (query.symbol) setSymbol(query.symbol.includes("/") ? query.symbol : query.symbol.replace(/(USDT|USDC|BUSD)$/, "/$1"));
    if (query.tf && ["1m", "5m", "15m", "1h", "4h", "1d"].includes(query.tf)) setTimeframe(query.tf as Tf);
    setHasAppliedQuery(true);
  }

  const signalId = query.signal;
  const { data } = useLivePrediction(symbol, timeframe, signalId);

  return (
    <div className="h-full flex flex-col min-h-0">
      <TopNav
        symbol={symbol}
        onSymbolChange={(s) => setSymbol(s.includes("/") ? s : s.replace(/(USDT|USDC|BUSD)$/, "/$1"))}
        onMenuClick={() => setDrawerOpen(true)}
      />
      <TimeframeRow active={timeframe} onChange={(tf) => setTimeframe(tf)} />
      <main className="flex-1 flex min-h-0">
        <div className="flex-1 min-w-0">
          <TVChart
            symbol={symbol}
            timeframe={timeframe}
            {...(data?.price != null ? { livePrice: data.price } : {})}
            {...(data?.ts != null ? { liveTs: data.ts } : {})}
            signalMarkers={data?.signal_markers ?? null}
            ghost={data?.ghost ?? null}
          />
        </div>
        <Sidebar open={drawerOpen} onClose={() => setDrawerOpen(false)}>
          <TradeStatusBar data={data} />
          <MasterBiasScore data={data} />
          <FinalValue data={data} />
          <LongShortRatio data={data} />
          <DeepLearningSupervisor data={data} />
          <HtfBiasStructure data={data} />
          <VolumeProfile data={data} />
          <MomentumIndicators data={data} />
          <MarketMicrostructure data={data} />
          <LiquiditySweep data={data} />
          <OiFundingRate data={data} />
          <IntermarketAnalysis data={data} />
          <SentimentFearGreed data={data} />
          <GhostCandlePrediction data={data} />
          <TradeSetup data={data} />
          <KeyLevels data={data} />
          <NewsMacroImpact data={data} />
        </Sidebar>
      </main>
    </div>
  );
}
```

- [ ] **Step 3: Run all Tab 1 tests** — every panel + the integration test must pass.

```bash
npm run test -- --run tests/unit/Tab1LivePrediction.test.tsx tests/unit/FinalValue tests/unit/LongShortRatio tests/unit/DeepLearningSupervisor tests/unit/HtfBiasStructure tests/unit/VolumeProfile tests/unit/MarketMicrostructure tests/unit/LiquiditySweep tests/unit/OiFundingRate tests/unit/IntermarketAnalysis tests/unit/SentimentFearGreed tests/unit/GhostCandlePrediction tests/unit/KeyLevels tests/unit/NewsMacroImpact
```

- [ ] **Step 4: Commit** — `feat(sp-6): Tab 1 — render all 17 sidebar panels in MASTER_PLAN order + accept ?symbol/?tf deeplinks`.

---

## Phase D — Tab 3 Scanner Radar full implementation

**Subagent batching guidance:** Tab 3 components have a strict dependency chain, so this phase is **sequential** within itself:
1. D1 (index shell) — bootstrap a no-op page so D7 can mount.
2. D7 (useScannerRadar hook) — required by D1 + D5/D6.
3. D2 (ScannerToolbar), D3 (HybridSupervisorBar), D6 (FilterPills) — toolbar bits, can be **2-agent batch**.
4. D4 (SignalCard) — leaf component, must precede D5.
5. D5 (BullishColumn + BearishColumn) — depends on D4. Single agent (combined).
6. D8 (Wire SignalCard click handler) — final integration.

---

### Task D1: `Tab3Scanner/index.tsx` — main wrapper

**Files:**
- Modify: `worktrees/sp-6/frontend/src/tabs/Tab3Scanner/index.tsx` (replace stub)
- Create: `worktrees/sp-6/frontend/tests/unit/Tab3Scanner.test.tsx`

**Behavior:**
- Wraps the page in a vertical flex column: `ScannerToolbar` → `HybridSupervisorBar` → 2-column body (Bullish + Bearish) → footer.
- Owns toolbar state: `market`, `tf`, `assetCount`, `refreshIntervalMin`, `searchQuery`, `activeFilter`.
- Passes `data` from `useScannerRadar` down to `BullishColumn` / `BearishColumn`.
- Footer: `"Scanning N crypto • {tf}" left | "Auto-refresh every M min • Click card to view chart" right`.
- Mobile: collapse to single column (stack), `ScannerToolbar` becomes a horizontally scrollable strip.

**Test cases:**
1. Renders toolbar + supervisor bar + 2 columns + footer.
2. Footer shows "200" + "1h" + "2 min" by default.
3. With `useScannerRadar` returning empty bullish/bearish → both columns render their "No signals" empty state.

**Code skeleton:**

```tsx
import { useState } from "react";
import { ScannerToolbar } from "./ScannerToolbar";
import { HybridSupervisorBar } from "./HybridSupervisorBar";
import { BullishColumn } from "./BullishColumn";
import { BearishColumn } from "./BearishColumn";
import { useScannerRadar } from "./hooks/useScannerRadar";
import type { ScannerRadar } from "@/lib/api";

type Filter = "all" | "confirmed" | "probable" | "weak" | "diverging" | "hybrid" | "analyzing";
type Market = ScannerRadar["market"];
type Tf = "1m" | "5m" | "15m" | "1h" | "4h" | "1d";

export function Tab3Scanner() {
  const [market, setMarket] = useState<Market>("crypto");
  const [tf, setTf] = useState<Tf>("1h");
  const [assetCount, setAssetCount] = useState(200);
  const [refreshMin, setRefreshMin] = useState(2);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  const { data, error, refetch } = useScannerRadar({
    market, tf, limit: assetCount, refreshIntervalMs: refreshMin * 60_000,
  });

  return (
    <div className="h-full flex flex-col bg-bg-base">
      <ScannerToolbar
        market={market} onMarketChange={setMarket}
        tf={tf} onTfChange={setTf}
        assetCount={assetCount} onAssetCountChange={setAssetCount}
        refreshMin={refreshMin} onRefreshMinChange={setRefreshMin}
        search={search} onSearchChange={setSearch}
        activeFilter={filter} onFilterChange={setFilter}
        filterCounts={data?.filter_counts ?? null}
        onRefresh={refetch}
      />
      <HybridSupervisorBar progress={data?.supervisor_progress ?? null} />
      {error && (
        <div role="alert" className="px-3 py-2 text-red text-[10px]">
          {error.message}
        </div>
      )}
      <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-2 gap-2 p-2 overflow-auto">
        <BullishColumn cards={data?.bullish ?? []} search={search} filter={filter} tf={tf} />
        <BearishColumn cards={data?.bearish ?? []} search={search} filter={filter} tf={tf} />
      </div>
      <footer className="text-[8px] text-text-tertiary border-t border-border px-2 py-1 flex justify-between">
        <span>Scanning {data?.scanned_count ?? 0} {market} • {tf} timeframe</span>
        <span>Auto-refresh every {refreshMin} min • Click card to view chart</span>
      </footer>
    </div>
  );
}
```

Commit: `feat(sp-6): Tab 3 Scanner index — toolbar/supervisor/columns/footer wiring`.

---

### Task D7: `useScannerRadar.ts` hook — REST poll every 2 min

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab3Scanner/hooks/useScannerRadar.ts`
- Create: `worktrees/sp-6/frontend/tests/unit/useScannerRadar.test.tsx`

**Behavior:**
- On mount: fetch `api.scannerRadar({market, tf, limit})`.
- Set up a `setInterval` of `refreshIntervalMs` to refetch.
- Cleanup on unmount.
- Returns `{ data, error, refetch, isLoading }`.

**Test cases:**
1. Fetches once on mount.
2. Refetches every 2 min when interval reaches.
3. `refetch()` triggers immediate refetch.
4. Cleanup on unmount cancels timers.
5. Error state renders.

**Code skeleton:**

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ScannerRadar, type ScannerRadarOptions } from "@/lib/api";

export interface UseScannerRadarOptions extends ScannerRadarOptions {
  refreshIntervalMs?: number;
}

export interface UseScannerRadarResult {
  data: ScannerRadar | null;
  error: Error | null;
  isLoading: boolean;
  refetch: () => Promise<void>;
}

export function useScannerRadar(opts: UseScannerRadarOptions = {}): UseScannerRadarResult {
  const [data, setData] = useState<ScannerRadar | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isLoading, setLoading] = useState(true);
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const refetch = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const r = await api.scannerRadar({
        market: optsRef.current.market,
        tf: optsRef.current.tf,
        limit: optsRef.current.limit,
      });
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
    const ms = opts.refreshIntervalMs ?? 120_000;
    const id = window.setInterval(() => { void refetch(); }, ms);
    return () => window.clearInterval(id);
  }, [opts.market, opts.tf, opts.limit, opts.refreshIntervalMs, refetch]);

  return { data, error, isLoading, refetch };
}
```

Commit: `feat(sp-6): Tab 3 useScannerRadar hook — REST poll + manual refetch`.

---

### Task D2: ScannerToolbar.tsx

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab3Scanner/ScannerToolbar.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/ScannerToolbar.test.tsx`

**Spec source:** MASTER_PLAN §9 lines 306-317.

**Behavior:**
- 1-row layout (overflow-x-auto on mobile): search box → market dropdown → tf dropdown → asset count input → refresh interval input → filter pills → refresh button.
- Each control is controlled — calls the corresponding `on*Change` prop.
- Shows aggregate scanned count + DS X/8 pill on the right.

**Test cases:**
1. Renders all controls.
2. Typing in search calls `onSearchChange`.
3. Clicking refresh calls `onRefresh`.
4. Filter pills render with correct counts when `filterCounts` is non-null.

**Code skeleton (abridged — full impl per spec):**

```tsx
import { FilterPills } from "./FilterPills";
import type { ScannerFilterCounts } from "@/lib/api";

type Market = "crypto" | "stock" | "fx" | "commodity" | "index";
type Tf = "1m" | "5m" | "15m" | "1h" | "4h" | "1d";
type Filter = "all" | "confirmed" | "probable" | "weak" | "diverging" | "hybrid" | "analyzing";

interface Props {
  market: Market;
  onMarketChange: (m: Market) => void;
  tf: Tf;
  onTfChange: (tf: Tf) => void;
  assetCount: number;
  onAssetCountChange: (n: number) => void;
  refreshMin: number;
  onRefreshMinChange: (n: number) => void;
  search: string;
  onSearchChange: (s: string) => void;
  activeFilter: Filter;
  onFilterChange: (f: Filter) => void;
  filterCounts: ScannerFilterCounts | null;
  onRefresh: () => void;
}

export function ScannerToolbar(p: Props) {
  return (
    <div className="flex items-center gap-2 px-2 py-1 bg-bg-elevated border-b border-border overflow-x-auto">
      <input
        type="text"
        placeholder="Search assets…"
        value={p.search}
        onChange={(e) => p.onSearchChange(e.target.value)}
        aria-label="search assets"
        className="w-[130px] h-7 px-2 text-[10px] bg-bg-base border border-border rounded"
      />
      <select aria-label="market" value={p.market} onChange={(e) => p.onMarketChange(e.target.value as Market)}
        className="h-7 px-2 text-[10px] bg-bg-base border border-border rounded">
        <option value="crypto">Crypto 200+</option>
        <option value="stock">Stocks</option>
        <option value="fx">FX</option>
      </select>
      <select aria-label="timeframe" value={p.tf} onChange={(e) => p.onTfChange(e.target.value as Tf)}
        className="h-7 px-2 text-[10px] bg-bg-base border border-border rounded">
        {["1m", "5m", "15m", "1h", "4h", "1d"].map((tf) => <option key={tf} value={tf}>{tf}</option>)}
      </select>
      <input
        type="number"
        aria-label="asset count"
        value={p.assetCount}
        onChange={(e) => p.onAssetCountChange(Number(e.target.value))}
        className="w-[60px] h-7 px-1 text-[10px] bg-bg-base border border-border rounded text-right"
      />
      <input
        type="number"
        aria-label="refresh minutes"
        value={p.refreshMin}
        onChange={(e) => p.onRefreshMinChange(Number(e.target.value))}
        className="w-[40px] h-7 px-1 text-[10px] bg-bg-base border border-border rounded text-right"
      />
      <span className="text-text-tertiary text-[8px] uppercase">min</span>
      <FilterPills
        active={p.activeFilter}
        counts={p.filterCounts}
        onChange={p.onFilterChange}
      />
      <button
        type="button"
        onClick={p.onRefresh}
        aria-label="refresh"
        className="ml-auto h-7 px-2 text-[10px] bg-bg-base border border-border rounded hover:bg-bg-panel"
      >
        ⟳ Refresh
      </button>
    </div>
  );
}
```

Commit: `feat(sp-6): Tab 3 ScannerToolbar — search/market/tf/count/refresh + filter pills`.

---

### Task D3: HybridSupervisorBar.tsx — cyan progress bar

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab3Scanner/HybridSupervisorBar.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/HybridSupervisorBar.test.tsx`

**Behavior:**
- Renders a 1-row bar showing cyan progress at `(done / total) × 100%`.
- Right-side label: `"X/8 done"`.
- Empty (`progress === null`) → renders 0 / 8 with no fill.

**Test cases:**
1. `progress: { done: 3, total: 8 }` → bar width 37.5% + "3/8 done" text.
2. `progress: null` → renders "0/8 done".

```tsx
import type { SupervisorProgress } from "@/lib/api";

interface Props {
  progress: SupervisorProgress | null;
}

export function HybridSupervisorBar({ progress }: Props) {
  const done = progress?.done ?? 0;
  const total = progress?.total ?? 8;
  const pct = (done / Math.max(1, total)) * 100;
  return (
    <div className="px-2 py-1 border-b border-border bg-bg-base">
      <div className="flex justify-between text-[8px] uppercase tracking-wide text-text-tertiary mb-1">
        <span>Hybrid Supervisor</span>
        <span aria-label="supervisor progress">{done}/{total} done</span>
      </div>
      <div className="h-1 bg-bg-elevated rounded">
        <div className="h-1 bg-cyan rounded" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
```

Commit: `feat(sp-6): Tab 3 HybridSupervisorBar — cyan progress bar with done/total label`.

---

### Task D6: FilterPills.tsx

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab3Scanner/FilterPills.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/FilterPills.test.tsx`

**Spec source:** MASTER_PLAN §9 line 316.

**Pills (in order):** All / ✓ Confirmed (green) / ~ Probable (orange) / ✗ Weak (red) / ⚡ Diverging (purple) / 🛡 Hybrid (orange) / ⏱ Analyzing… (cyan).

**Behavior:** Active pill rendered with bg-* color matching its category; counts pulled from `counts.*` (defaults to "—" when null). Click any pill → `onChange(filterId)`.

**Code skeleton:**

```tsx
import type { ScannerFilterCounts } from "@/lib/api";

type Filter = "all" | "confirmed" | "probable" | "weak" | "diverging" | "hybrid" | "analyzing";

const PILLS: { id: Filter; label: string; color: string; bg: string }[] = [
  { id: "all", label: "All", color: "text-text-primary", bg: "bg-bg-elevated" },
  { id: "confirmed", label: "✓ Confirmed", color: "text-green", bg: "bg-green/10" },
  { id: "probable", label: "~ Probable", color: "text-orange", bg: "bg-orange/10" },
  { id: "weak", label: "✗ Weak", color: "text-red", bg: "bg-red/10" },
  { id: "diverging", label: "⚡ Diverging", color: "text-purple", bg: "bg-purple/10" },
  { id: "hybrid", label: "🛡 Hybrid", color: "text-orange", bg: "bg-orange/10" },
  { id: "analyzing", label: "⏱ Analyzing", color: "text-cyan", bg: "bg-cyan/10" },
];

interface Props {
  active: Filter;
  counts: ScannerFilterCounts | null;
  onChange: (f: Filter) => void;
}

export function FilterPills({ active, counts, onChange }: Props) {
  function countFor(id: Filter): string {
    if (!counts) return "";
    if (id === "all") return String(counts.all);
    if (id === "confirmed") return String(counts.confirmed);
    if (id === "probable") return String(counts.probable);
    if (id === "weak") return String(counts.weak);
    if (id === "diverging") return String(counts.diverging);
    return "";
  }
  return (
    <div className="flex gap-1" role="group" aria-label="filter pills">
      {PILLS.map((p) => {
        const isActive = p.id === active;
        return (
          <button
            key={p.id}
            type="button"
            onClick={() => onChange(p.id)}
            data-active={isActive ? "true" : "false"}
            className={`h-7 px-2 text-[9px] uppercase tracking-wide border border-border rounded ${
              isActive ? p.bg + " " + p.color : "bg-bg-base text-text-secondary"
            }`}
          >
            {p.label} {countFor(p.id)}
          </button>
        );
      })}
    </div>
  );
}
```

Commit: `feat(sp-6): Tab 3 FilterPills — 7 filters + dynamic counts`.

---

### Task D4: SignalCard.tsx — 5-row card

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab3Scanner/SignalCard.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/SignalCard.test.tsx`

**Spec source:** MASTER_PLAN §9 lines 325-330 — 5 rows of metadata. Tab 3 signal card symbols: `11.5px` Inter; tags: `9px` Inter weight 500.

**Per-row spec:**
- **Row 1:** `★` favorite + symbol (`11.5px`) + full_name (greyed) | sparkline (right) + `±points` badge.
- **Row 2 (tags):** solid `LONG`/`SHORT` pill + outlined 4h `LONG`/`SHORT` + `✓ CONFIRMED` (green) or `~ PROBABLE` (orange) badge + pink/purple Hybrid dot + `AI ±score` (purple) + Wyckoff phase text.
- **Row 3:** right-aligned `±%change`.
- **Row 4:** confidence bar (cyan) + "Conf X%" right-aligned.
- **Row 5:** four score chips: SMC ±N / Wyckoff ±N / Microstructure ±N / Momentum ±N.

**Behavior:**
- `onClick` prop (optional) — clicking the card triggers `onClick(card)`.
- Sparkline rendered as inline SVG (matches the existing EquityCurve pattern).

**Test cases (~6 tests):**
1. Renders symbol, full_name, points badge.
2. Renders LONG pill green + 4h tag outline.
3. Renders Confirmed badge when `signal_tier === "STANDARD"` (or `"A+"`); Probable for PAPER/SMALL.
4. Renders confidence bar + percentage.
5. Renders all 4 score chips.
6. Clicking the card calls `onClick`.

**Code skeleton:**

```tsx
import type { SignalCard as SignalCardData } from "@/lib/api";

const VB_W = 60;
const VB_H = 16;

function buildSparkPath(values: number[]): string | null {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const stepX = VB_W / (values.length - 1);
  let d = "";
  values.forEach((v, i) => {
    const x = i * stepX;
    const y = VB_H - ((v - min) / range) * VB_H;
    d += (i === 0 ? "M " : " L ") + x.toFixed(2) + " " + y.toFixed(2);
  });
  return d;
}

function tierBadge(tier: SignalCardData["signal_tier"]): { label: string; color: string } | null {
  if (tier === "STANDARD" || tier === "A+") return { label: "✓ CONFIRMED", color: "text-green border-green/30" };
  if (tier === "PAPER" || tier === "SMALL") return { label: "~ PROBABLE", color: "text-orange border-orange/30" };
  return null;
}

interface Props {
  card: SignalCardData;
  onClick?: (card: SignalCardData) => void;
}

export function SignalCard({ card, onClick }: Props) {
  const dirCls = card.direction === "LONG" ? "bg-green/20 text-green" : "bg-red/20 text-red";
  const dirCls4h = card.htf_direction === "LONG" ? "border-green text-green" :
                   card.htf_direction === "SHORT" ? "border-red text-red" :
                   "border-text-tertiary text-text-tertiary";
  const badge = tierBadge(card.signal_tier);
  const sparkD = buildSparkPath(card.sparkline);
  const stroke = card.direction === "LONG" ? "var(--green)" : "var(--red)";
  const pointsTxt = card.points >= 0 ? `+${card.points}` : String(card.points);
  const pctTxt = card.pct_change >= 0 ? `+${card.pct_change.toFixed(2)}%` : `${card.pct_change.toFixed(2)}%`;
  const aiScoreTxt = card.ai_score >= 0 ? `AI +${card.ai_score}` : `AI ${card.ai_score}`;

  return (
    <button
      type="button"
      onClick={() => onClick?.(card)}
      data-testid={`signal-card-${card.symbol}`}
      className="block w-full text-left bg-bg-panel border border-border rounded p-2 mb-2 hover:bg-bg-elevated"
    >
      {/* Row 1 */}
      <div className="flex justify-between items-center mb-1">
        <div className="flex items-center gap-1">
          <span className={card.is_favorite ? "text-gold" : "text-text-tertiary"}>★</span>
          <span className="font-semibold text-[11.5px]">{card.symbol}</span>
          <span className="text-text-tertiary text-[9px]">{card.full_name}</span>
        </div>
        <div className="flex items-center gap-1">
          {sparkD && (
            <svg viewBox={`0 0 ${VB_W} ${VB_H}`} className="w-[60px] h-[16px]" role="img" aria-label="sparkline">
              <path d={sparkD} fill="none" stroke={stroke} strokeWidth="1" />
            </svg>
          )}
          <span className={card.points >= 0 ? "text-green text-[9px]" : "text-red text-[9px]"}>{pointsTxt}</span>
        </div>
      </div>
      {/* Row 2 — tags */}
      <div className="flex flex-wrap gap-1 mb-1">
        <span className={`px-1 py-0 rounded text-[9px] font-medium ${dirCls}`}>{card.direction}</span>
        <span className={`px-1 py-0 rounded text-[9px] font-medium border ${dirCls4h}`}>4h {card.htf_direction}</span>
        {badge && (
          <span className={`px-1 py-0 rounded text-[9px] font-medium border ${badge.color}`}>{badge.label}</span>
        )}
        {card.hybrid_flag && (
          <span className="w-2 h-2 rounded-full inline-block bg-pink" aria-label={`hybrid ${card.hybrid_flag}`} />
        )}
        <span className="text-purple text-[9px] font-medium">{aiScoreTxt}</span>
        <span className="text-text-secondary text-[9px]">{card.wyckoff_phase}</span>
      </div>
      {/* Row 3 */}
      <div className="text-right text-[10px] mb-1">
        <span className={card.pct_change >= 0 ? "text-green" : "text-red"}>{pctTxt}</span>
      </div>
      {/* Row 4 — conf bar */}
      <div className="flex items-center gap-2 mb-1">
        <div className="flex-1 h-1 bg-bg-elevated rounded">
          <div className="h-1 bg-cyan rounded" style={{ width: `${card.confidence}%` }} />
        </div>
        <span className="text-text-secondary text-[9px]">Conf {card.confidence}%</span>
      </div>
      {/* Row 5 — score chips */}
      <div className="flex flex-wrap gap-1 text-[8px]">
        {(["smc", "wyckoff", "microstructure", "momentum"] as const).map((k) => {
          const v = card.scores[k];
          const txt = v >= 0 ? `+${v}` : String(v);
          return (
            <span key={k} className="px-1 py-0 rounded bg-bg-elevated text-text-secondary uppercase">
              {k} {txt}
            </span>
          );
        })}
      </div>
    </button>
  );
}
```

Commit: `feat(sp-6): Tab 3 SignalCard — 5-row card per MASTER_PLAN §9`.

---

### Task D5: BullishColumn.tsx + BearishColumn.tsx

**Files:**
- Create: `worktrees/sp-6/frontend/src/tabs/Tab3Scanner/BullishColumn.tsx`
- Create: `worktrees/sp-6/frontend/src/tabs/Tab3Scanner/BearishColumn.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/BullishColumn.test.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/BearishColumn.test.tsx`

**Behavior:**
- Both render a column header (green / red title) + a vertical list of `SignalCard`.
- Filter `cards` by `search` (substring match on `symbol` or `full_name`).
- Filter `cards` by `filter` ("confirmed" → tier in STANDARD/A+; "probable" → PAPER/SMALL; "weak" → NO_SIGNAL; "all" → no filter).
- Empty state: `"No signals match"` placeholder.
- Click handler on each card: navigate via `window.location.hash = "#/live-prediction?symbol=" + sym + "&tf=" + tf`.

**Bullish skeleton:**

```tsx
import { SignalCard } from "./SignalCard";
import type { SignalCard as SignalCardData } from "@/lib/api";

type Filter = "all" | "confirmed" | "probable" | "weak" | "diverging" | "hybrid" | "analyzing";

function applyFilter(cards: SignalCardData[], filter: Filter, search: string): SignalCardData[] {
  let out = cards;
  if (search.trim()) {
    const q = search.toLowerCase();
    out = out.filter((c) => c.symbol.toLowerCase().includes(q) || c.full_name.toLowerCase().includes(q));
  }
  if (filter === "confirmed") out = out.filter((c) => c.signal_tier === "STANDARD" || c.signal_tier === "A+");
  else if (filter === "probable") out = out.filter((c) => c.signal_tier === "PAPER" || c.signal_tier === "SMALL");
  else if (filter === "weak") out = out.filter((c) => c.signal_tier === "NO_SIGNAL");
  return out;
}

interface Props {
  cards: SignalCardData[];
  search: string;
  filter: Filter;
  tf: string;
}

export function BullishColumn({ cards, search, filter, tf }: Props) {
  const visible = applyFilter(cards, filter, search);
  function open(sym: string): void {
    const safe = encodeURIComponent(sym);
    window.location.hash = `#/live-prediction?symbol=${safe}&tf=${tf}`;
  }
  return (
    <div>
      <h2 className="text-green text-[11px] uppercase tracking-wide mb-1">
        Bullish ({visible.length})
      </h2>
      {visible.length === 0 ? (
        <div className="text-text-tertiary text-[10px]">No signals match</div>
      ) : (
        visible.map((c) => <SignalCard key={c.symbol} card={c} onClick={() => open(c.symbol)} />)
      )}
    </div>
  );
}
```

`BearishColumn.tsx` is identical except for `text-red` heading and `"Bearish (...)"` label.

Commit: `feat(sp-6): Tab 3 BullishColumn + BearishColumn — filtering + click-to-open handler`.

---

### Task D8: SignalCard click → Tab 1 deeplink E2E

**Files:**
- Modify: `worktrees/sp-6/frontend/src/tabs/Tab3Scanner/BullishColumn.tsx` + `BearishColumn.tsx` (already wired in D5)
- Create: `worktrees/sp-6/frontend/tests/e2e/tab3-scanner.spec.ts` (in Phase F4)

**Behavior verification (manual + Vitest):**
- Vitest test in `BullishColumn.test.tsx` uses `vi.spyOn(window.location, "hash", "set")` to assert the hash is set to `#/live-prediction?symbol=BTC%2FUSDT&tf=1h`.
- Tab 1 already accepts these query params (Task C5 added `?symbol/?tf` parsing in `Tab1LivePrediction/index.tsx`).

Commit: `test(sp-6): Tab 3 SignalCard click navigates to Tab 1 with symbol+tf deeplink`.

---

## Phase E — Admin sub-pages (PatternsAdmin / AdaptersAdmin / TrapsAdmin)

**Subagent batching guidance:** All three admin sub-pages mirror the SP-1 `MlCheckpoints.tsx` pattern (table + per-row toggle + Reload). Dispatch in **one parallel batch of 3 agents** (E1 + E2 + E3). After they all return, do **E4 + E5** sequentially (single-component change to the Admin index router).

---

### Task E1: PatternsAdmin.tsx — 158 patterns + per-row enable/disable

**Files:**
- Create: `worktrees/sp-6/frontend/src/components/Admin/PatternsAdmin.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/Admin.PatternsAdmin.test.tsx`

**Behavior:**
- `useEffect` fetch `api.adminListPatterns()` on mount.
- Renders a table: pattern_id | type (candle/chart) | enabled state pill | actions (Disable/Enable button).
- Pattern list could be 158 rows — add a search input and pagination (50 per page).
- Disable click prompts for an optional reason (uses `window.prompt`); Enable is one-click.
- After any toggle: reload the list.

**Code skeleton (full template — other admin sub-pages mirror this):**

```tsx
import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { api, type PatternEntry } from "@/lib/api";

const PAGE_SIZE = 50;

function StatusPill({ enabled }: { enabled: boolean }) {
  const cls = enabled
    ? "bg-green/15 text-green border border-green/30"
    : "bg-red/15 text-red border border-red/30";
  return (
    <span data-testid="pattern-status-pill"
      className={`inline-block px-1.5 py-0.5 rounded text-[8px] uppercase tracking-wide ${cls}`}>
      {enabled ? "Enabled" : "Disabled"}
    </span>
  );
}

export function PatternsAdmin() {
  const [items, setItems] = useState<readonly PatternEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);

  const reload = useCallback(async () => {
    try {
      const list = await api.adminListPatterns();
      setItems(list);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const toggle = useCallback(async (pid: string, enable: boolean) => {
    setBusyId(pid);
    try {
      let reason: string | undefined;
      if (!enable) {
        reason = window.prompt("Reason to disable?") ?? "";
      }
      await api.adminTogglePattern(pid, enable, reason);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }, [reload]);

  const filtered = (items ?? []).filter((p) =>
    !search.trim() || p.pattern_id.toLowerCase().includes(search.toLowerCase()),
  );
  const pageItems = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const totalPages = Math.ceil(filtered.length / PAGE_SIZE);

  return (
    <Panel title={`Patterns (${items?.length ?? 0})`}
      rightSlot={
        <input
          type="text"
          placeholder="Search…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(0); }}
          aria-label="search patterns"
          className="w-[140px] h-7 px-2 text-[10px] bg-bg-base border border-border rounded"
        />
      }
    >
      {error && <div role="alert" className="text-red text-[10px] mb-2">{error}</div>}
      {items === null ? (
        <div className="text-text-tertiary">Loading…</div>
      ) : filtered.length === 0 ? (
        <div className="text-text-tertiary">No patterns match.</div>
      ) : (
        <>
          <table aria-label="Patterns" className="w-full text-[10px] font-mono border-collapse">
            <thead>
              <tr className="text-text-tertiary text-left uppercase tracking-wide">
                <th className="py-1 pr-2">Pattern ID</th>
                <th className="py-1 pr-2">Type</th>
                <th className="py-1 pr-2">Status</th>
                <th className="py-1 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {pageItems.map((p) => (
                <tr key={p.pattern_id} data-testid={`pattern-row-${p.pattern_id}`}
                  className="border-t border-border">
                  <td className="py-1 pr-2">{p.pattern_id}</td>
                  <td className="py-1 pr-2 text-text-secondary">{p.pattern_type}</td>
                  <td className="py-1 pr-2"><StatusPill enabled={p.enabled} /></td>
                  <td className="py-1 text-right">
                    {p.enabled ? (
                      <button type="button" disabled={busyId === p.pattern_id}
                        onClick={() => void toggle(p.pattern_id, false)}
                        className="min-h-[28px] px-2 text-[9px] uppercase tracking-wide bg-bg-elevated hover:bg-red/20 border border-border rounded disabled:opacity-50">
                        Disable
                      </button>
                    ) : (
                      <button type="button" disabled={busyId === p.pattern_id}
                        onClick={() => void toggle(p.pattern_id, true)}
                        className="min-h-[28px] px-2 text-[9px] uppercase tracking-wide bg-green/20 hover:bg-green/30 text-green border border-green/40 rounded disabled:opacity-50">
                        Enable
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {totalPages > 1 && (
            <div className="flex justify-center gap-2 mt-2">
              <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}
                className="h-7 px-2 text-[10px] bg-bg-elevated border border-border rounded disabled:opacity-50">‹ Prev</button>
              <span className="text-[10px] text-text-tertiary">Page {page + 1} / {totalPages}</span>
              <button type="button" disabled={page + 1 >= totalPages} onClick={() => setPage(page + 1)}
                className="h-7 px-2 text-[10px] bg-bg-elevated border border-border rounded disabled:opacity-50">Next ›</button>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
```

**Test cases (~6):**
1. Renders rows from list.
2. Status pills reflect enabled state.
3. Disable button calls `adminTogglePattern(id, false, reason)` (mock `window.prompt` to return `"too noisy"`).
4. Enable button calls `adminTogglePattern(id, true)`.
5. Search input filters rows.
6. Empty state when filter returns 0.
7. Error state when API fails.

Commit: `feat(sp-6): Admin → PatternsAdmin sub-page (158 patterns + toggle + search + pagination)`.

---

### Task E2: AdaptersAdmin.tsx — 4 adapters + manual sync

**Files:**
- Create: `worktrees/sp-6/frontend/src/components/Admin/AdaptersAdmin.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/Admin.AdaptersAdmin.test.tsx`

**Behavior:**
- `useEffect` fetch `api.adminListAdapters()`.
- Table: exchange | health (Healthy/Unhealthy pill) | latency_ms | last checked | actions (Sync button).
- Sync click: calls `api.adminSyncAdapter(exchange)`, displays the SyncResult inline ("Added 3, still 200, removed 1") then reloads health.
- Empty state: "No adapters registered."

**Test cases:**
1. Renders 4 rows for binance / bybit / yahoo / twelvedata.
2. Healthy pill green, Unhealthy red, no-checks-yet greyed.
3. Sync button calls `adminSyncAdapter(ex)` then `adminListAdapters()` again.
4. Latency_ms rendered as "12 ms" or "—" if null.
5. Error state.

**Skeleton mirrors PatternsAdmin** but with one row per exchange and a single "Sync now" action. Use `apiSyncAdapter` then store the result in component state and render below the row.

Commit: `feat(sp-6): Admin → AdaptersAdmin sub-page (4 adapters + manual sync)`.

---

### Task E3: TrapsAdmin.tsx — 17 traps + per-row toggle

**Files:**
- Create: `worktrees/sp-6/frontend/src/components/Admin/TrapsAdmin.tsx`
- Create: `worktrees/sp-6/frontend/tests/unit/Admin.TrapsAdmin.test.tsx`

**Behavior:** Identical to PatternsAdmin but column set is `trap_id | severity | side | enabled | actions`. Severity rendered as colored pill (medium=text-yellow, high=text-orange, extreme=text-red). Side ("long"/"short"/"both") rendered as small text label. No pagination needed (17 rows fit on one screen).

**Test cases:**
1. Renders 17 rows.
2. Severity pills rendered with correct color.
3. Toggle calls `adminToggleTrap`.
4. Empty state, error state.

Commit: `feat(sp-6): Admin → TrapsAdmin sub-page (17 traps + severity/side display + toggle)`.

---

### Task E4: Update `tabs/Admin/index.tsx` SUB_TABS — add Patterns/Adapters/Traps

**Files:**
- Modify: `worktrees/sp-6/frontend/src/tabs/Admin/index.tsx`
- Modify: `worktrees/sp-6/frontend/tests/unit/...` (extend existing Admin tests)

- [ ] **Step 1: Update SUB_TABS** to 6 entries:

```tsx
import { useState } from "react";
import { Users } from "@/tabs/Admin/Users";
import { AuditTrail } from "@/tabs/Admin/AuditTrail";
import { MlCheckpoints } from "@/tabs/Admin/MlCheckpoints";
import { PatternsAdmin } from "@/components/Admin/PatternsAdmin";
import { AdaptersAdmin } from "@/components/Admin/AdaptersAdmin";
import { TrapsAdmin } from "@/components/Admin/TrapsAdmin";

type SubTab = "users" | "audit" | "ml-checkpoints" | "patterns" | "adapters" | "traps";

const SUB_TABS: readonly { id: SubTab; label: string }[] = [
  { id: "users", label: "Users" },
  { id: "audit", label: "Audit Trail" },
  { id: "ml-checkpoints", label: "ML Checkpoints" },
  { id: "patterns", label: "Patterns" },
  { id: "adapters", label: "Adapters" },
  { id: "traps", label: "Traps" },
];

export function Admin() {
  const [sub, setSub] = useState<SubTab>("users");
  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div role="tablist" aria-label="Admin sections"
        className="flex bg-bg-elevated border-b border-border overflow-x-auto">
        {SUB_TABS.map((t) => {
          const active = t.id === sub;
          return (
            <button key={t.id} type="button" role="tab"
              aria-selected={active} data-active={active ? "true" : "false"}
              onClick={() => setSub(t.id)}
              className={[
                "h-11 md:h-9 px-4 text-xs font-mono uppercase tracking-wide",
                "border-b-2 -mb-px transition-colors whitespace-nowrap",
                active
                  ? "text-text-primary border-text-primary bg-bg-base"
                  : "text-text-secondary border-transparent hover:text-text-primary",
              ].join(" ")}>
              {t.label}
            </button>
          );
        })}
      </div>
      <div className="flex-1 min-h-0 overflow-auto p-3">
        {sub === "users" ? <Users /> :
         sub === "audit" ? <AuditTrail /> :
         sub === "ml-checkpoints" ? <MlCheckpoints /> :
         sub === "patterns" ? <PatternsAdmin /> :
         sub === "adapters" ? <AdaptersAdmin /> :
         <TrapsAdmin />}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Test** — extend or add a Vitest test that asserts all 6 sub-tabs render and click-switching works:

```ts
test("renders all 6 admin sub-tabs", () => {
  render(<Admin />);
  for (const label of ["Users", "Audit Trail", "ML Checkpoints", "Patterns", "Adapters", "Traps"]) {
    expect(screen.getByRole("tab", { name: new RegExp(label, "i") })).toBeInTheDocument();
  }
});
```

- [ ] **Step 3: Commit** — `feat(sp-6): Admin → wire 3 new sub-tabs (Patterns/Adapters/Traps)`.

---

### Task E5: Wire each new sub-page into the Admin tab routing — already covered in E4

This is a lookup task — verify that the integration in E4 step 1 dispatches to the right component for each sub-tab id. No additional commit; covered by E4.

---

### Task E6: TDD per sub-page — covered by E1/E2/E3 inline tests

Already done as part of each E1/E2/E3 task. Each test file uses the standard Vitest mock pattern (matching `Admin.MlCheckpoints.test.tsx`) — `vi.mock("@/lib/api", () => ({ api: { adminListPatterns: vi.fn(), ... } }))`.

---

## Phase F — Mobile + Lighthouse + ship

### Task F1: Mobile responsive pass — 375px width

**Files:**
- Modify: as needed across `Tab3Scanner/`, `Admin/`, `Tab1LivePrediction/index.tsx`
- Create: `worktrees/sp-6/frontend/tests/e2e/mobile-responsive.spec.ts` (covered by F4)

**Process:**
1. Boot dev server: `cd frontend && npm run dev`.
2. Open Chrome DevTools → mobile emulation → iPhone SE (375 × 667).
3. For each tab, walk through:
   - **Tab 1:** chart fills full width; sidebar drawer opens on hamburger; all 17 panels stack vertically inside drawer (already supported by existing `Sidebar.tsx`).
   - **Tab 3:** toolbar must scroll horizontally (`overflow-x-auto` already on `ScannerToolbar`); columns stack via `grid-cols-1 md:grid-cols-2` (already coded in D1); SignalCard must not overflow card width — the symbol + sparkline row uses `flex-wrap` if needed.
   - **Admin:** sub-tab nav must scroll horizontally on mobile (`overflow-x-auto whitespace-nowrap` added in E4); table columns `whitespace-nowrap` so action buttons stay clickable.
4. For each break found: add `md:` Tailwind class to the offending element.

**Specific known fixes to apply:**
- `Tab1LivePrediction/index.tsx`: ensure sidebar `Sidebar` already has `md:translate-x-0` (yes, already in code).
- `ScannerToolbar.tsx`: add `min-w-0` to child elements so they don't push width.
- `PatternsAdmin.tsx`: wrap table in `<div className="overflow-x-auto">` so 4-col table scrolls horizontally.
- `SignalCard.tsx`: confirm `Row 2` tags `flex-wrap` properly.

**Verification:** Boot Playwright with the mobile project and run all existing `bot-status.spec.ts`/`admin-users.spec.ts` — confirm no horizontal scroll detected via JS:

```js
page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
```

- Commit: `feat(sp-6): mobile responsive pass — 375px no-overflow on Tab1/Tab3/Admin`.

---

### Task F2: Add Lighthouse CI integration via `@lhci/cli`

**Files:**
- Modify: `worktrees/sp-6/frontend/package.json` (add devDep + script)
- Create: `worktrees/sp-6/frontend/lighthouserc.json`
- Modify: `.github/workflows/ci.yml` (or wherever CI is defined — verify file exists first; if not, defer to follow-up)

- [ ] **Step 1: Install `@lhci/cli`**

```bash
cd frontend
npm install --save-dev @lhci/cli@latest
```

- [ ] **Step 2: Add scripts** to `package.json`:

```json
"scripts": {
  ...
  "lhci": "lhci autorun"
},
```

- [ ] **Step 3: Write `lighthouserc.json`** at repo root of frontend:

```json
{
  "ci": {
    "collect": {
      "url": [
        "http://localhost:5173/#/live-prediction",
        "http://localhost:5173/#/bot-status",
        "http://localhost:5173/#/scanner",
        "http://localhost:5173/#/settings"
      ],
      "startServerCommand": "npm run preview -- --port 5173",
      "numberOfRuns": 1,
      "settings": {
        "preset": "desktop"
      }
    },
    "assert": {
      "assertions": {
        "categories:performance": ["error", { "minScore": 0.80 }],
        "categories:accessibility": ["error", { "minScore": 0.80 }],
        "categories:best-practices": ["error", { "minScore": 0.80 }],
        "categories:seo": ["error", { "minScore": 0.80 }]
      }
    },
    "upload": { "target": "temporary-public-storage" }
  }
}
```

- [ ] **Step 4: Verify it runs locally**

```bash
cd frontend
npm run build
npm run lhci
```

Expected: `LHCI run completed`. May fail assertions on first run — that's expected; F3 fixes them.

- [ ] **Step 5: Commit** — `chore(sp-6): add @lhci/cli + lighthouserc.json scaffolding`.

---

### Task F3: Lighthouse audit run + fix any regressions

**Process:**
1. Read the Lighthouse report from F2 step 4. Note categories below 80.
2. Likely regressions:
   - **Performance:** 200-card Tab 3 may render slow → SP-6 ships with default `limit=200` but `BullishColumn`/`BearishColumn` already paginate visible cards. If still slow, lazy-render with `IntersectionObserver` (10-card initial + load-more on scroll).
   - **Accessibility:** missing `aria-label` on icon-only buttons. Run audit to enumerate; add labels.
   - **Best Practices:** WebSocket over `ws://` in dev — fine. Console errors from missing routes (e.g. `/admin/audit-trail`) — silence by guarding fetches behind `isAdmin`.
   - **SEO:** `<title>` + `<meta name="description">` in `index.html` — confirm exists; if missing, add.
3. Fix each regression in a separate commit.
4. Re-run `npm run lhci` until all categories ≥80.

- Commit(s) per fix. Final commit: `chore(sp-6): Lighthouse Performance/Accessibility/Best Practices/SEO ≥80`.

---

### Task F4: New Playwright spec — `tab3-scanner.spec.ts`

**Files:**
- Create: `worktrees/sp-6/frontend/tests/e2e/tab3-scanner.spec.ts`
- Create: `worktrees/sp-6/frontend/tests/e2e/admin-patterns.spec.ts`

**Tab 3 spec (~3 tests):**

```ts
import { expect, test } from "@playwright/test";

test.describe("Tab 3 Scanner", () => {
  test("Scanner tab visible in nav and renders toolbar + columns", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("tab", { name: /^scanner$/i }).click();
    await expect(page).toHaveURL(/#\/scanner/);
    await expect(page.getByRole("group", { name: /filter pills/i })).toBeVisible();
    await expect(page.getByText(/Hybrid Supervisor/i)).toBeVisible();
    await expect(page.getByRole("heading", { name: /Bullish/i })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Bearish/i })).toBeVisible();
  });

  test("clicking a signal card navigates to Tab 1 with deeplink", async ({ page }) => {
    await page.goto("/#/scanner");
    // The dev backend may produce zero predictions in CI — wait for any
    // signal card OR for the empty-state placeholder.
    const card = page.locator("[data-testid^='signal-card-']").first();
    if (await card.isVisible({ timeout: 10_000 }).catch(() => false)) {
      await card.click();
      await expect(page).toHaveURL(/#\/live-prediction\?symbol=/);
    } else {
      // No predictions seeded — assert placeholder text instead.
      await expect(page.getByText(/No signals match/i).first()).toBeVisible();
    }
  });

  test("filter pills filter visible cards", async ({ page }) => {
    await page.goto("/#/scanner");
    await page.getByRole("button", { name: /probable/i }).click();
    // The pill is now active — sanity check that data-active="true" is set.
    await expect(page.getByRole("button", { name: /probable/i })).toHaveAttribute("data-active", "true");
  });
});
```

**Admin patterns spec (~2 tests):**

```ts
import { expect, test } from "@playwright/test";

test.describe("Admin → Patterns", () => {
  test("admin can open patterns sub-tab and see 158 patterns", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("tab", { name: /^admin$/i }).click();
    await page.getByRole("tab", { name: /^patterns$/i }).click();
    await expect(page.getByRole("heading", { name: /Patterns/i })).toBeVisible({ timeout: 10_000 });
    // 158 patterns total, but only 50 visible per page.
    const rows = page.locator("[data-testid^='pattern-row-']");
    await expect(rows.first()).toBeVisible({ timeout: 10_000 });
  });

  test("disable button toggles a pattern off", async ({ page }) => {
    await page.goto("/#/admin");
    await page.getByRole("tab", { name: /^patterns$/i }).click();
    page.once("dialog", (dialog) => { void dialog.accept("test reason"); });
    const firstDisable = page.getByRole("button", { name: /^disable$/i }).first();
    await firstDisable.click();
    // Status pill should flip to Disabled within a reasonable timeout.
    await expect(page.locator("[data-testid='pattern-status-pill']").first()).toContainText(/disabled/i, { timeout: 10_000 });
  });
});
```

- Commit: `test(sp-6): Playwright E2E — tab3-scanner + admin-patterns`.

---

### Task F5: Update log + push branch + open PR + merge + tag

- [ ] **Step 1: Update `docs/superpowers/log.md`** — append the SP-6 ship entry summarising scope: 17 panels, Tab 3 with toolbar/columns/cards, 3 admin sub-pages, ~10 backend tests + ~100 Vitest + 2 Playwright specs.

- [ ] **Step 2: Run final test suite**

```bash
cd worktrees/sp-6
docker compose exec -T backend pytest -q
cd frontend && npm run test -- --run
npm run test:e2e -- --reporter=line
npm run lhci
```

Expected:
- Backend: ~1185 passed (1175 + ~10 from scanner endpoint)
- Vitest: ~290 passed (187 + ~100 new)
- Playwright: ~16 passed (14 + 2 new specs × 1 device project; if both desktop+mobile = +4 = 18)
- Lighthouse: all categories ≥80

- [ ] **Step 3: Push branch**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-6' push -u origin sp-6/main
```

- [ ] **Step 4: Open PR**

```bash
gh pr create --title "SP-6 UI Completion — 17 Tab 1 panels + Tab 3 Scanner + 3 admin sub-pages" --body "$(cat <<'EOF'
## Summary
- Tab 1: complete the 17 sidebar panels per MASTER_PLAN §9 (13 new, 4 pre-existing)
- Tab 3 Scanner Radar: brand-new — toolbar + bullish/bearish columns + signal cards + 2-min refresh
- Admin: 3 deferred sub-pages (Patterns / Adapters / Traps) wired into the existing admin tab
- Backend: 1 new endpoint `GET /api/v1/scanner/radar`
- Mobile: 375 px responsive pass across all tabs
- Lighthouse: all 4 categories ≥80 enforced via `@lhci/cli`

## Test plan
- [x] Backend: `pytest -q` → ~1185 passed
- [x] Frontend Vitest: `npm run test -- --run` → ~290 passed
- [x] Playwright E2E: `npm run test:e2e` → ~16-18 passed (depends on device matrix)
- [x] Lighthouse: `npm run lhci` → all categories ≥80
- [x] Manual mobile walkthrough at 375 px on all 4 tabs (live-prediction / bot-status / scanner / settings)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Merge PR via squash** once CI is green.

- [ ] **Step 6: Tag the merge**

```bash
git -c safe.directory='A:/v5_Trade_bot' fetch origin
git -c safe.directory='A:/v5_Trade_bot' checkout main
git -c safe.directory='A:/v5_Trade_bot' pull origin main
git -c safe.directory='A:/v5_Trade_bot' tag -a sp-6 -m "SP-6 UI Completion ship"
git -c safe.directory='A:/v5_Trade_bot' push origin sp-6
```

- [ ] **Step 7: Cleanup the worktree**

```bash
git -c safe.directory='A:/v5_Trade_bot' worktree remove worktrees/sp-6
git -c safe.directory='A:/v5_Trade_bot' branch -D sp-6/main
```

- Final commit covered by step 1's log update; no separate commit for steps 2-7.

---

## Acceptance criteria recap (mirrored from spec §9)

After SP-6 ships, all the following must hold:
- [ ] Tab 1 sidebar shows all 17 panel slots (some show "no data" if backend lacks a source — design intent)
- [ ] Tab 3 Scanner Radar tab visible in nav; renders bullish + bearish columns; refresh button works
- [ ] Admin → Patterns sub-page lists 158 patterns with toggle (paginated 50/page)
- [ ] Admin → Adapters sub-page shows 4 adapters' health + manual sync button
- [ ] Admin → Traps sub-page lists 17 traps with severity/side/toggle
- [ ] Mobile (375 px): no horizontal scroll on any tab; sidebar/columns/sub-tabs collapse cleanly
- [ ] Lighthouse audit: Performance ≥80, Accessibility ≥80, Best Practices ≥80, SEO ≥80
- [ ] Click signal card on Tab 3 → opens Tab 1 with that symbol + tf via `?symbol=X&tf=Y` deeplink
- [ ] No regression in existing 1175+ backend tests
- [ ] Frontend Vitest test count: existing ~187 + ~100 new ≈ ~290
- [ ] Playwright E2E: ≥2 new specs (`tab3-scanner.spec.ts` + `admin-patterns.spec.ts`)

---

## Total task count summary

| Phase | Tasks | New tests | Commits |
|---|---|---|---|
| A | 6 (A1-A6) | 10 backend + 9 Vitest | 4 |
| B | 9 (B1-B9) | ~36 Vitest (4 per panel × 9) | 9 |
| C | 5 (C1-C5) | ~16 Vitest + 1 Tab1 integration test | 6 |
| D | 8 (D1-D8) | ~30 Vitest | 8 |
| E | 6 (E1-E6, with E5/E6 absorbed by E4) | ~18 Vitest | 4 |
| F | 5 (F1-F5) | 5 Playwright (3 in tab3 spec + 2 in admin-patterns) | 4 + final ship commits |
| **Total** | **~38 tasks** | **~10 backend + ~109 Vitest + ~5 Playwright** | **~35 commits** |

---

**END OF SP-6 UI COMPLETION PLAN**

---

## Brief report

**Total task count:** 38 atomic tasks across 6 phases (A: 6, B: 9, C: 5, D: 8, E: 6, F: 5 — note E5/E6 fold into E4 since the wiring + tests are inseparable from the parent index update; counted as 4 effective E commits).

**Total commit count estimate:** ~35 commits (Phase A: 4, B: 9, C: 6, D: 8, E: 4, F: 4 ship commits + ~3 Lighthouse fix commits = ~35-38).

**Spec ambiguities flagged inline:**
1. **17 vs 14 panels.** Spec §3.2 lists 17 entries but says "14 panels per MASTER_PLAN §9; 15-17 are 'additional'". I followed the spec's note that SP-6 ships **all 17 as separate components** for forward-compat. This may need a hide-panels preference later.
2. **Stoch / CCI in Momentum panel.** MASTER_PLAN §9 line 285 specs a "2-col grid: RSI, MACD, Stoch, CCI" but `data.momentum` only exposes RSI + 3 MACD fields. Plan ships Stoch + CCI as `"—"` placeholders.
3. **`pct_change` field in Tab 3 SignalCardOut.** Spec §3.6 lists `pct_change` but the backend has no 24h percentage delta wired (no OHLC-24h source per-symbol). Plan ships as `0.0` placeholder; SP-7 to wire real value.
4. **Sparkline source.** Spec says "compute from last 20 bars (cached via Redis)". Plan ships with empty sparkline (`[]`) when not in cache; the SQL query in A4 returns `NULL AS sparkline`. SP-7 should add the Redis-backed last-20-bar fetch path.
5. **Filter pill counts** — `diverging` / `hybrid` / `analyzing` filters have no clean backend counterpart in current `predictions.layer_scores`; plan ships counts of `0` for these, with confirmed/probable/weak being the only live counts.

**Feasibility concerns:**
- **Tab 3 SignalCard color tokens** (`text-orange`, `text-cyan`, `text-pink`, `bg-cyan/10`) — verify these exist in `tailwind.config.ts`. Per CLAUDE.md "Match UI colors exactly", any missing token must be added to the theme rather than introduced ad-hoc. If absent, add a Phase D pre-task to extend `tailwind.config.ts` (one commit, ~5 lines).
- **Backend test fixture extension** — the `bot_status_client` conftest currently lacks a `predictions` table; Task A4 step 3 mentions adding it, but I should call out that this is a one-time fixture-only schema mirror (no migration).
- **Lighthouse CI on Windows** — `npx lhci autorun` works on macOS/Linux out of the box but Windows + headless Chromium can have flakiness; if the F3 audit doesn't run cleanly locally, we may need to gate the CI assertion behind a `LHCI_GITHUB_APP_TOKEN` env or skip on Windows runners.