"""Tab 3 Scanner Radar: aggregate latest prediction per symbol per user.

Spec §3.6: read-only aggregation over the existing ``predictions`` table.
Returns ScannerRadarOut with bullish + bearish columns sorted by |score|.
Per-user filter via ``current_user_or_impersonated`` is mandatory - leaking
across users is the only data-isolation hazard for SP-6.

Latency budget: <500ms for 200 assets. Sparkline arrays are best-effort
loaded from the same predictions table (last 20 closes per symbol).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, cast

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
_TF_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900,
    "1h": 3600, "4h": 14400, "1d": 86400,
}
# A card is flagged is_stale when the underlying prediction is older than
# 2 * timeframe. Display-only: dispatcher acts on the same predictions row.
_STALE_MULTIPLIER = 2


def _coerce_layer_scores(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
            return cast(dict[str, Any], decoded) if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    if isinstance(raw, dict):
        return cast(dict[str, Any], raw)
    return {}


def _parse_row_ts(raw: Any) -> datetime | None:
    """Coerce a raw ``predictions.ts`` value to a tz-aware UTC datetime.

    Postgres asyncpg returns a real ``datetime`` (naive or aware); SQLite
    returns a string. Anything else — or an unparseable string — yields
    ``None`` and the caller treats the card as ``is_stale=False``.
    """
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _confirmed_tier(tier: str) -> bool:
    return tier in ("STANDARD", "A+")


def _probable_tier(tier: str) -> bool:
    return tier in ("PAPER", "SMALL")


def _wyckoff_phase_from_notes(notes: str) -> str:
    if "Accumulation" in notes:
        return "Accumulation"
    if "Markup" in notes:
        return "Markup"
    if "Distribution" in notes:
        return "Distribution"
    if "Markdown" in notes:
        return "Markdown"
    return "unknown"


def _build_card(
    row: Any,
    *,
    now: datetime | None = None,
    stale_after_seconds: float | None = None,
) -> SignalCardOut | None:
    ls = _coerce_layer_scores(row.layer_scores)
    # direction / final_score / confidence live on dedicated top-level
    # columns of the predictions row. Read them from there first.
    # Historical fallback: very old rows MAY only have these in
    # layer_scores["final"] as a dict. But _build_extras (current
    # writer) stores layer_scores["final"] as a SCALAR FLOAT, not a
    # dict. Calling .get("direction") on a float crashed production
    # with AttributeError on 2026-05-12 — only protect the fallback
    # path when the JSONB value really is a dict.
    direction_raw = getattr(row, "direction", None)
    if not direction_raw:
        fallback_final = ls.get("final")
        if isinstance(fallback_final, dict):
            direction_raw = fallback_final.get("direction")
    direction = direction_raw or "NEUTRAL"
    if direction not in ("LONG", "SHORT"):
        # Defensive: NEUTRAL rows are filtered out at this point so the
        # bullish/bearish split downstream stays clean.
        return None
    tier_raw = ls.get("tier")
    if not isinstance(tier_raw, str) or tier_raw not in (
        "NO_SIGNAL", "PAPER", "SMALL", "STANDARD", "A+",
    ):
        tier = "NO_SIGNAL"
    else:
        tier = tier_raw
    score_raw = getattr(row, "final_score", None)
    conf_raw = getattr(row, "confidence", None)
    if score_raw is None or conf_raw is None:
        fallback_final = ls.get("final")
        if isinstance(fallback_final, dict):
            if score_raw is None:
                score_raw = fallback_final.get("score")
            if conf_raw is None:
                conf_raw = fallback_final.get("confidence")
    raw_score = float(score_raw or 0.0)
    raw_conf = float(conf_raw or 0.0)

    # Defensive: layer entries in layer_scores can be dicts, None, or
    # (post-extras-merge) raw scalars (floats, strings). Coerce non-dict
    # values to {} so the .get(...) calls below never explode.
    def _as_dict(v: Any) -> dict[str, Any]:
        return cast(dict[str, Any], v) if isinstance(v, dict) else {}

    smc = _as_dict(ls.get("4"))
    wyckoff_layer = _as_dict(ls.get("1"))
    micro = _as_dict(ls.get("6"))
    momentum_layer = _as_dict(ls.get("3"))
    scores = SignalCardScores(
        smc=int(round(float(smc.get("strength") or 0.0) * 100)),
        wyckoff=int(round(float(wyckoff_layer.get("strength") or 0.0) * 100)),
        microstructure=int(round(float(micro.get("strength") or 0.0) * 100)),
        momentum=int(round(float(momentum_layer.get("strength") or 0.0) * 100)),
    )
    notes_raw = wyckoff_layer.get("notes")
    notes = notes_raw if isinstance(notes_raw, str) else ""
    wyckoff_phase = _wyckoff_phase_from_notes(notes)

    sparkline: list[float] = []
    raw_spark = getattr(row, "sparkline", None)
    if raw_spark:
        try:
            if isinstance(raw_spark, str):
                decoded = json.loads(raw_spark)
                sparkline = [float(x) for x in decoded] if isinstance(decoded, list) else []
            else:
                sparkline = [float(x) for x in raw_spark]
        except (TypeError, ValueError, json.JSONDecodeError):
            sparkline = []

    full_name_raw = getattr(row, "full_name", None) or ""
    full_name = full_name_raw if full_name_raw else row.symbol.split("/")[0]

    ts_value = _parse_row_ts(getattr(row, "ts", None))
    is_stale = (
        ts_value is not None
        and now is not None
        and stale_after_seconds is not None
        and (now - ts_value).total_seconds() > stale_after_seconds
    )

    return SignalCardOut(
        symbol=row.symbol,
        full_name=full_name,
        points=int(round(raw_score * 100)),
        pct_change=0.0,  # SP-6: no 24h pct change wired yet - defer to SP-7
        direction=direction,  # type: ignore[arg-type]
        signal_tier=tier,  # type: ignore[arg-type]
        ai_score=int(round(raw_score * 100)),
        confidence=int(round(raw_conf * 100)),
        wyckoff_phase=wyckoff_phase,
        scores=scores,
        sparkline=sparkline[-20:],
        ts=ts_value,
        is_stale=is_stale,
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
    # for full_name. Sparkline is left NULL here (SP-7 will swap in real OHLC
    # last-20).
    #
    # CTE pattern (latest_ts → join back) instead of MAX(layer_scores). Both
    # layer_scores and universe_history.metadata are JSONB in Postgres prod,
    # and Postgres has no default MAX(jsonb) operator → would 500 every call
    # (SQLite happily compared the TEXT but production didn't). The CTE is
    # portable and has the same row count.
    rows = (await session.execute(sa.text(
        "WITH latest AS ( "
        "  SELECT symbol, MAX(ts) AS max_ts FROM predictions "
        "  WHERE user_id = :u AND timeframe = :tf "
        "  GROUP BY symbol "
        ") "
        "SELECT p.symbol AS symbol, p.ts AS ts, p.layer_scores AS layer_scores, "
        "       p.direction AS direction, "
        "       p.final_score AS final_score, "
        "       p.confidence AS confidence, "
        # Cast JSONB metadata to text so COALESCE-with-text works in
        # Postgres (without the cast, '' is parsed as JSON and fails:
        # 'invalid input syntax for type json'). CAST(.. AS TEXT) is
        # portable; ::text is Postgres-only and breaks SQLite tests.
        "       COALESCE(CAST(u.metadata AS TEXT), '') AS full_name, "
        "       NULL AS sparkline "
        "FROM predictions p "
        "JOIN latest l ON l.symbol = p.symbol AND l.max_ts = p.ts "
        "LEFT JOIN universe_history u ON u.symbol = p.symbol "
        "WHERE p.user_id = :u AND p.timeframe = :tf "
        "ORDER BY p.ts DESC "
        "LIMIT :lim"
    ), {"u": current_user.id, "tf": tf, "lim": limit})).all()

    now = datetime.now(timezone.utc)
    stale_after_seconds = _TF_SECONDS[tf] * _STALE_MULTIPLIER
    cards_unfiltered = [
        _build_card(r, now=now, stale_after_seconds=stale_after_seconds)
        for r in rows
    ]
    cards: list[SignalCardOut] = [c for c in cards_unfiltered if c is not None]
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

    half = max(1, limit // 2)
    return ScannerRadarOut(
        scanned_at=datetime.now(timezone.utc),
        market=market,  # type: ignore[arg-type]
        timeframe=tf,
        scanned_count=len(cards),
        filter_counts=fc,
        supervisor_progress=SupervisorProgress(done=0, total=8),
        bullish=bullish[:half],
        bearish=bearish[:half],
    )
