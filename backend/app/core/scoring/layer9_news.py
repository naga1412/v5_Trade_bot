"""L9: news sentiment aggregator (SP-9 Phase E1).

Replaces the SP-5 placeholder. Aggregates per-asset sentiment scores from
``news_items`` over a recent lookback window (default 60 minutes), weighted
by ``impact_score`` and squashed via ``tanh`` to produce a magnitude in
``[0, 1]``.

Returns ``None`` when no news items are found (the layer abstains and the
aggregator redistributes weight per the SP-5 spec).

Cross-dialect storage:
- Postgres: ``affected_assets`` is ``TEXT[]`` queried via ``ANY()``.
- SQLite (test fixture): ``affected_assets`` is a JSON-encoded TEXT column
  (e.g. ``["BTC","ETH"]``); we LIKE-match the JSON encoding ``%"BTC"%``.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scoring.types import Direction, LayerScore


_LOOKBACK_MINUTES_DEFAULT: int = 60
_DEFAULT_IMPACT: float = 0.5
_DEADBAND: float = 0.1
_TANH_GAIN: float = 1.5
_CONFIDENCE_DIVISOR: float = 5.0


async def score(
    *,
    symbol: str,
    session: AsyncSession,
    lookback_minutes: int = _LOOKBACK_MINUTES_DEFAULT,
    now: datetime | None = None,
) -> LayerScore | None:
    """Compute the L9 ``LayerScore`` from recent news items affecting ``symbol``.

    Args:
        symbol: trading pair like ``"BTC/USDT"`` — the base asset (``BTC``) is
            extracted to filter ``news_items.affected_assets``.
        session: async DB session bound to the news_items store.
        lookback_minutes: how far back to look (default 60).
        now: optional clock injection for tests.

    Returns:
        ``LayerScore`` when at least one matching article exists with a
        non-null ``sentiment_score``; ``None`` otherwise (layer abstains).
    """
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        minutes=lookback_minutes,
    )
    base = symbol.split("/")[0].upper()

    dialect = session.bind.dialect.name if session.bind else "postgresql"
    is_pg = dialect.startswith("postgres")

    params: dict[str, object]
    if is_pg:
        sql = sa.text(
            "SELECT sentiment_score, impact_score FROM news_items "
            "WHERE published_at >= :cutoff "
            "AND :base = ANY(affected_assets) "
            "AND sentiment_score IS NOT NULL"
        )
        params = {"cutoff": cutoff, "base": base}
    else:
        # SQLite test fixture: affected_assets is a JSON-encoded string
        # (see app/news/persistence.py B5); LIKE-match the quoted token so
        # ``BTC`` doesn't accidentally match ``BTCBULL`` etc.
        sql = sa.text(
            "SELECT sentiment_score, impact_score FROM news_items "
            "WHERE published_at >= :cutoff "
            "AND affected_assets LIKE :pat "
            "AND sentiment_score IS NOT NULL"
        )
        params = {"cutoff": cutoff, "pat": f'%"{base}"%'}

    rows = (await session.execute(sql, params)).all()
    if not rows:
        return None

    weighted_sum = sum(
        float(r.sentiment_score) * float(
            r.impact_score if r.impact_score is not None else _DEFAULT_IMPACT,
        )
        for r in rows
    )
    weight_total = sum(
        float(r.impact_score if r.impact_score is not None else _DEFAULT_IMPACT)
        for r in rows
    )
    if weight_total == 0:
        return None

    avg = weighted_sum / weight_total  # in [-1, +1]
    strength = abs(math.tanh(avg * _TANH_GAIN))
    confidence = min(1.0, len(rows) / _CONFIDENCE_DIVISOR)

    if avg > _DEADBAND:
        direction = Direction.LONG
    elif avg < -_DEADBAND:
        direction = Direction.SHORT
    else:
        direction = Direction.NEUTRAL

    return LayerScore(
        direction=direction,
        strength=strength,
        confidence=confidence,
        notes=(
            f"{len(rows)} news items in last {lookback_minutes}min, "
            f"avg_score={avg:.3f}"
        ),
    )
