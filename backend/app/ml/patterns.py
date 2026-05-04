"""Nightly pattern_stats updater (SP-1 spec §4.3).

Joins ``predictions`` x ``shadow_trades`` on ``signal_id`` (which equals the
prediction's ``inputs_hash``), extracts ``pattern_id`` markers from each
prediction's ``layer_scores.L2.patterns`` array, counts wins
(``exit_reason='TAKE_PROFIT'``) vs total samples, and upserts one
``pattern_stats`` row per (pattern_id, symbol, timeframe) triple.

Cold-start gating (n_samples < 50) is handled by the GENERATED ``accuracy``
column in the table definition (returns the 0.5 prior until the threshold
is crossed), so this module only writes raw counts.

NOTE — SP-0 / SP-1 ship without an L2 pattern detector, so the join will
return zero rows in production today. The job is in place so that when SP-2
lands the candlestick-pattern layer it begins populating ``pattern_stats``
automatically with no further wiring.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


def _extract_patterns(layer_scores_json: str | None) -> list[str]:
    """Pull pattern IDs from a prediction row's layer_scores JSON.

    Looks at ``layer_scores["L2"]["patterns"]`` — an array of string ids.
    Returns ``[]`` for any malformed / missing payload (SP-0 / SP-1 path).
    """
    if not layer_scores_json:
        return []
    try:
        data = json.loads(layer_scores_json)
    except (json.JSONDecodeError, TypeError):
        return []
    l2 = data.get("L2") if isinstance(data, dict) else None
    if not isinstance(l2, dict):
        return []
    pats = l2.get("patterns") or []
    if not isinstance(pats, list):
        return []
    return [p for p in pats if isinstance(p, str)]


async def update_pattern_stats(session: AsyncSession) -> int:
    """Recompute ``pattern_stats`` from current predictions × shadow_trades join.

    Returns the number of ``pattern_stats`` rows upserted (i.e. the number of
    distinct ``(pattern_id, symbol, timeframe)`` triples that had at least one
    closed shadow trade joined to a prediction carrying that pattern).
    """
    sql = sa.text(
        "SELECT p.symbol AS symbol, p.timeframe AS timeframe, "
        "p.layer_scores AS layer_scores, t.exit_reason AS exit_reason "
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
    now_iso = datetime.now(timezone.utc).isoformat()
    for (pat, sym, tf), (n_total, n_win) in counts.items():
        # SQLite + Postgres both accept this ON CONFLICT form against the
        # (pattern_id, symbol, timeframe) UNIQUE constraint.
        await session.execute(
            sa.text(
                "INSERT INTO pattern_stats "
                "(pattern_id, symbol, timeframe, n_samples, n_correct, "
                "last_updated) "
                "VALUES (:p, :s, :tf, :n, :w, :u) "
                "ON CONFLICT (pattern_id, symbol, timeframe) DO UPDATE SET "
                "n_samples = excluded.n_samples, "
                "n_correct = excluded.n_correct, "
                "last_updated = excluded.last_updated"
            ),
            {"p": pat, "s": sym, "tf": tf, "n": n_total, "w": n_win, "u": now_iso},
        )
        n_upserted += 1

    if n_upserted:
        log.info("pattern_stats: upserted %d rows", n_upserted)
    return n_upserted
