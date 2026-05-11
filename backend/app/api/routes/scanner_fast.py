"""Fast-scanner API endpoint (Feature 4 — sub-PR 2).

Serves the cache filled by ``app.scanner.batch.run_one_pass``. Per-user
auth via the standard impersonation dependency, but the scan results
themselves are not per-user (they're indicator math over public market
data). The auth gate is there to keep the endpoint behind Cloudflare
Access, not to scope data.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth.deps import current_user_or_impersonated
from app.auth.models import User
from app.scanner import batch as scanner_batch
from app.scanner.fast_scan import FastScanResult

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scanner", tags=["scanner-fast"])


class ModuleScoreOut(BaseModel):
    name: str
    score: float
    weight: float
    notes: str = ""


class ScanCardOut(BaseModel):
    symbol: str
    timeframe: str
    final_score: float
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    confidence: float
    tier: Literal["confirmed", "probable", "weak", "diverging", "neutral"]
    phase: Literal["markup", "markdown", "accumulation", "distribution", "neutral"]
    modules: list[ModuleScoreOut]
    last_close: float
    expected_move_pct: float | None
    rr_estimate: float | None
    scanned_at: str  # ISO datetime


class ScanRadarOut(BaseModel):
    timeframe: str
    cache_size: int
    bullish: list[ScanCardOut]
    bearish: list[ScanCardOut]
    by_tier: dict[str, int]


def _result_to_card(r: FastScanResult, scanned_at_iso: str) -> ScanCardOut:
    return ScanCardOut(
        symbol=r.symbol,
        timeframe=r.timeframe,
        final_score=r.final_score,
        direction=r.direction,
        confidence=r.confidence,
        tier=r.tier,
        phase=r.phase,
        modules=[
            ModuleScoreOut(name=m.name, score=m.score, weight=m.weight, notes=m.notes)
            for m in r.modules
        ],
        last_close=r.last_close,
        expected_move_pct=r.expected_move_pct,
        rr_estimate=r.rr_estimate,
        scanned_at=scanned_at_iso,
    )


@router.get("/fast", response_model=ScanRadarOut)
async def fast_radar(
    timeframe: str = Query(default="1h"),
    tier: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
) -> ScanRadarOut:
    """Return the cached fast-scan radar for the requested timeframe.

    Filters:
      - ``tier`` — keep only this tier (confirmed / probable / weak /
        diverging / neutral). Default: all.
      - ``limit`` — cap per side (bullish/bearish) for the response.
    """
    del current_user  # auth gate only; results are public-data
    snapshot = scanner_batch.get_cache_snapshot()

    cards: list[ScanCardOut] = []
    for (sym, tf), cached in snapshot.items():
        if tf != timeframe:
            continue
        if tier is not None and cached.result.tier != tier:
            continue
        cards.append(_result_to_card(
            cached.result, cached.scanned_at.isoformat(),
        ))

    cards.sort(key=lambda c: abs(c.final_score), reverse=True)
    bullish = [c for c in cards if c.direction == "LONG"][:limit]
    bearish = [c for c in cards if c.direction == "SHORT"][:limit]

    by_tier: dict[str, int] = {}
    for c in cards:
        by_tier[c.tier] = by_tier.get(c.tier, 0) + 1

    return ScanRadarOut(
        timeframe=timeframe,
        cache_size=len(snapshot),
        bullish=bullish,
        bearish=bearish,
        by_tier=by_tier,
    )
