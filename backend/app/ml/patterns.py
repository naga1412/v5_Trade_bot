"""Nightly pattern_stats updater (SP-1 spec §4.3).

Reads closed ``shadow_trades``, extracts ``pattern_id`` markers from each
trade's own L2 layer output, counts wins (``exit_reason='TAKE_PROFIT'``)
vs total samples, and upserts one ``pattern_stats`` row per
(pattern_id, symbol, timeframe) triple.

Cold-start gating (n_samples < 50) is handled by the GENERATED ``accuracy``
column in the table definition (returns the 0.5 prior until the threshold
is crossed), so this module only writes raw counts.

THREE COMPOUNDING BUGS FOUND AND FIXED (2026-08-20, card-review
pattern-layer investigation) -- this job had never written a single row
since it shipped:

1. **Schema mismatch.** ``_extract_patterns`` originally looked for
   ``layer_scores["L2"]["patterns"]`` as a flat array of string ids -- a
   schema that never shipped. Written against the SP-0/SP-1 stub era's
   ASSUMED shape for SP-2's not-yet-built L2 layer; SP-2 shipped a
   different real shape and this was never updated to match, so it
   silently returned ``[]`` for every row (by its own documented design
   -- "malformed/missing payload" -- not a crash).
2. **Never scheduled.** ``update_pattern_stats`` was never wired into
   any worker (confirmed via a full-history repo search — only
   referenced from its own definition and its unit test). Even a
   correct extractor would never have run. See
   ``app/workers/pattern_stats_refresh.py`` for the fix.
3. **Broken join.** The original query joined ``predictions p ... ON
   t.signal_id = p.inputs_hash`` -- but ``shadow_trades.signal_id`` is a
   standalone, self-generated id (``app/shadow/engine.py::
   _gen_signal_id``) with zero relationship to ``predictions.
   inputs_hash``. Confirmed against real data: 3,736 shadow_trades carry
   a signal_id, 45,622 predictions carry an inputs_hash, exactly ZERO
   ever matched -- this join was never going to return a row regardless
   of bug 1. No join is actually needed: ``shadow_trades.layer_scores``
   is already populated directly from the same prediction at trade-open
   time, so this now reads it straight from ``shadow_trades``.

LOOKAHEAD WARNING for anyone building a backtest/replay that touches this
table: pattern_stats.accuracy reflects ALL history up to ``last_updated``,
recomputed from scratch on every refresh (not an incrementally-updated
rolling window). Using it to score trades from BEFORE that refresh in a
backtest is lookahead bias -- the trade's own future outcome (and every
other later trade's) is baked into the "historical" rate being tested
against. Valid for live/forward scoring only. The backfill run performed
after this fix shipped (2026-08-20) populated this table from the FULL
trade history to date in one shot specifically to close the "wait months
for real data" gap -- that backfill is the same kind of full-history
recompute this job always does, not a special one-time exception, but it's
worth naming explicitly here since it's the reason the table has non-empty
rows with `n_samples` covering dates before the backfill's own run date.

NOTE — L2's live SCORING path (app/core/scoring/layer2_patterns.py) does
NOT read the fixed real data yet as of this fix landing -- that's a
deliberate, separate, explicitly-gated follow-up (operator ruling
2026-08-20: populate and report the delta first, wire to live scoring
only after that's reviewed).
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


def _extract_patterns(layer_scores: str | dict[str, Any] | None) -> list[str]:
    """Pull pattern IDs from a prediction row's real L2 layer output.

    Real shape (confirmed against live production data, 2026-08-20):
    ``layer_scores["2"]`` (bare stringified int key, all layers) is a dict
    ``{"direction":..., "strength":..., "confidence":..., "notes": <JSON
    STRING>}`` -- see ``LayerScoreOut.model_dump()`` /
    ``layer2_patterns.py::_build_notes``. ``notes`` is a JSON-ENCODED
    STRING (not a nested dict) that itself parses to
    ``{"n": int, "patterns": [{"id": str, "dir": str, "s": float,
    "c": float}, ...]}``. Each pattern is a dict keyed by ``"id"``, not a
    bare string.

    ``layer_scores`` may arrive as either a JSON string or an
    already-parsed dict depending on the caller's DB access path
    (SQLAlchemy's asyncpg dialect auto-deserializes JSONB columns at the
    connection level for some query shapes but not others -- observed
    both ways from ``sa.text()`` queries against this same column this
    session) -- handled defensively rather than assuming one.

    ``notes`` can also be a REAL, genuinely truncated JSON string in
    production: ``layer2_patterns.NOTES_MAX_CHARS = 500`` caps it at
    write time, and a bar with enough pattern fires (confirmed: a real
    row with 10 fires truncates mid-object) produces an unparseable
    ``notes`` string. Returns ``[]`` for that row rather than raising --
    the trade's OTHER data is still valid, only its pattern attribution
    for this one row is lost.

    Returns ``[]`` for any malformed / missing payload.
    """
    if not layer_scores:
        return []
    if isinstance(layer_scores, str):
        try:
            data: Any = json.loads(layer_scores)
        except (json.JSONDecodeError, TypeError):
            return []
    else:
        data = layer_scores
    l2 = data.get("2") if isinstance(data, dict) else None
    if not isinstance(l2, dict):
        return []
    notes = l2.get("notes")
    if not isinstance(notes, str):
        return []
    try:
        parsed = json.loads(notes)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []
    pats = parsed.get("patterns") or []
    if not isinstance(pats, list):
        return []
    out: list[str] = []
    for p in pats:
        if isinstance(p, dict):
            pid = p.get("id")
            if isinstance(pid, str):
                out.append(pid)
    return out


async def update_pattern_stats(session: AsyncSession) -> int:
    """Recompute ``pattern_stats`` from closed shadow_trades.

    Returns the number of ``pattern_stats`` rows upserted (i.e. the number of
    distinct ``(pattern_id, symbol, timeframe)`` triples that had at least one
    closed shadow trade carrying that pattern).

    THIRD bug found alongside the schema fix (2026-08-20): this used to
    join ``predictions p ... ON t.signal_id = p.inputs_hash`` -- but
    ``shadow_trades.signal_id`` is a standalone, self-generated id
    (``app/shadow/engine.py::_gen_signal_id``) with no relationship to
    ``predictions.inputs_hash`` at all. Confirmed against real data:
    3,736 shadow_trades carry a signal_id, 45,622 predictions carry an
    inputs_hash, exactly ZERO of them ever matched. The join was never
    going to return a row, regardless of the extraction bug. No join is
    actually needed: shadow_trades.layer_scores is already populated
    directly from the same prediction at trade-open time
    (app/shadow/worker.py's ``self.evaluator.evaluate(..., layer_scores=
    layer_scores, ...)``) -- reading it straight from shadow_trades is
    both correct and simpler than reconstructing a relationship that was
    never wired.
    """
    sql = sa.text(
        "SELECT s.symbol AS symbol, s.timeframe AS timeframe, "
        "s.layer_scores AS layer_scores, s.exit_reason AS exit_reason "
        "FROM shadow_trades s "
        "WHERE s.exit_reason IS NOT NULL"
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
    now = datetime.now(timezone.utc)
    for (pat, sym, tf), (n_total, n_win) in counts.items():
        # SQLite + Postgres both accept this ON CONFLICT form against the
        # (pattern_id, symbol, timeframe) UNIQUE constraint. Bind the
        # datetime object directly — asyncpg strict-binds TIMESTAMPTZ.
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
            {"p": pat, "s": sym, "tf": tf, "n": n_total, "w": n_win, "u": now},
        )
        n_upserted += 1

    if n_upserted:
        log.info("pattern_stats: upserted %d rows", n_upserted)
    return n_upserted
