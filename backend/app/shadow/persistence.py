"""Per-user shadow persistence helpers.

Spec §7.1 — every row in `shadow_open_positions`, `shadow_trades`, and
`shadow_cooldowns` is owned by exactly one user, and every read/mutation
must filter by `user_id`. SP-0.7's bootstrap admin (id=1) owns all rows
the single shadow worker produces; SP-8 will fan out workers per user.

The runtime guard in `app.auth.query_guard` enforces this contract at the
SQLAlchemy event layer so any future helper that forgets the predicate
raises in dev.
"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import insert_with_chain
from app.db.payload_builders import (
    _normalize_mtf_directions_json,
    build_shadow_trade_payload,
)
from app.shadow.engine import Direction, ShadowPosition
from app.shadow.exit_monitor import ExitReason


def _to_dt(value: Any) -> datetime:
    """Coerce a DB-returned timestamp into a datetime.

    asyncpg/PostgreSQL returns native datetime objects; SQLite returns
    ISO-8601 strings (since SQLite has no native datetime type).
    Tests run on SQLite, production on PostgreSQL — this normaliser
    makes both work.
    """
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


async def persist_open_position(
    session: AsyncSession, pos: ShadowPosition, *, user_id: int,
) -> None:
    """Insert a (user_id, symbol, timeframe)-unique open position row.

    PR3: writes pos.timeframe into the row. Pre-PR3 callers that pass
    a position with default timeframe='1h' produce a bit-identical
    `'1h'` row, so the schema change is invisible to them.

    PR-PLUMBING-1 Fix 3: also writes the 7 PR1 analytics fields when
    populated on ``pos`` (shadow_worker sets them between
    ``from_signal()`` and this call). ``getattr(..., None)`` keeps
    pre-PR-PLUMBING-1 tests / fixture rows working — when the column
    isn't on the dataclass, we INSERT NULL.
    """
    await session.execute(
        sa.text(
            "INSERT INTO shadow_open_positions "
            "(user_id, symbol, timeframe, direction, entry_price, stop_loss, "
            "take_profit, position_size_usdt, entry_score, entry_confidence, "
            "entry_atr, bars_held, opened_at, last_check_at, signal_id, "
            # PR-PLUMBING-1 Fix 3: 7 PR1 analytics columns. Nullable; the
            # alembic 0025 migration adds them as NULL on existing rows.
            "mtf_agreement, mtf_dominant_tf, mtf_directions_json, p_win, "
            "effective_score, realized_vol_20d, funding_directional_adj) "
            "VALUES (:uid, :s, :tf, :d, :ep, :sl, :tp, :ps, :es, :ec, :ea, "
            ":bh, :oa, :lc, :sig, "
            ":mtfa, :mtfd, :mtfj, :pw, :effs, :rv, :fda)"
        ),
        {
            "uid": user_id,
            "s": pos.symbol, "tf": pos.timeframe, "d": pos.direction.value,
            "ep": pos.entry_price, "sl": pos.stop_loss, "tp": pos.take_profit,
            "ps": pos.position_size_usdt, "es": pos.entry_score,
            "ec": pos.entry_confidence, "ea": pos.entry_atr,
            "bh": pos.bars_held,
            # Datetimes pass through raw — asyncpg/Postgres rejects ISO
            # strings on TIMESTAMPTZ; SQLite tests still get TEXT via SQLAlchemy.
            "oa": pos.opened_at, "lc": pos.last_check_at,
            "sig": pos.signal_id,
            # PR-PLUMBING-1 Fix 3: defensive getattr — NULL on pre-fix
            # callers / legacy ShadowPosition instances missing the
            # PR-strategy-1 fields.
            "mtfa": getattr(pos, "mtf_agreement", None),
            "mtfd": getattr(pos, "mtf_dominant_tf", None),
            "mtfj": getattr(pos, "mtf_directions_json", None),
            "pw": getattr(pos, "p_win", None),
            "effs": getattr(pos, "effective_score", None),
            "rv": getattr(pos, "realized_vol_20d", None),
            "fda": getattr(pos, "funding_directional_adj", None),
        },
    )


async def list_open_positions(
    session: AsyncSession, *, user_id: int,
) -> list[ShadowPosition]:
    """Load all open positions for a user. Each result carries its TF."""
    result = await session.execute(
        sa.text(
            "SELECT * FROM shadow_open_positions "
            "WHERE user_id = :uid ORDER BY opened_at ASC"
        ),
        {"uid": user_id},
    )
    out: list[ShadowPosition] = []
    for r in result:
        # Pre-PR3 rows backfilled to '1h' in alembic 0021. Future rows
        # carry their actual TF. getattr() with '1h' fallback covers
        # both cases without an extra schema check.
        tf = getattr(r, "timeframe", None) or "1h"
        out.append(ShadowPosition(
            symbol=r.symbol, direction=Direction(r.direction),
            entry_price=r.entry_price, stop_loss=r.stop_loss, take_profit=r.take_profit,
            position_size_usdt=r.position_size_usdt,
            entry_score=r.entry_score, entry_confidence=r.entry_confidence,
            entry_atr=r.entry_atr, layer_scores={},
            bars_held=r.bars_held,
            opened_at=_to_dt(r.opened_at),
            last_check_at=_to_dt(r.last_check_at),
            signal_id=r.signal_id,
            timeframe=tf,
            # PR-PLUMBING-1 Fix 3: read the 7 PR1 analytics columns back
            # from the row. ``getattr(..., None)`` keeps pre-fix test
            # fixtures (rows without these columns / SQLite mirrors that
            # haven't been updated yet) working without AttributeError.
            #
            # PR-MTF-DIRECTIONS-JSON-SERIALIZATION-FIX: the column is
            # JSONB on Postgres — asyncpg returns a Python dict on read.
            # Normalize back to the canonical JSON string the rest of
            # the code (and ShadowPosition's str|None contract) expects.
            mtf_agreement=getattr(r, "mtf_agreement", None),
            mtf_dominant_tf=getattr(r, "mtf_dominant_tf", None),
            mtf_directions_json=_normalize_mtf_directions_json(
                getattr(r, "mtf_directions_json", None),
            ),
            p_win=getattr(r, "p_win", None),
            effective_score=getattr(r, "effective_score", None),
            realized_vol_20d=getattr(r, "realized_vol_20d", None),
            funding_directional_adj=getattr(r, "funding_directional_adj", None),
        ))
    return out


async def delete_open_position(
    session: AsyncSession, *, user_id: int, symbol: str,
    timeframe: str = "1h",
) -> None:
    """Delete a single (user_id, symbol, timeframe) open position.

    PR3: timeframe is required to target the right TF row — a 1h close
    must NOT delete a co-existing 15m open position. Default '1h' keeps
    pre-PR3 callers working until they're migrated to pass the TF
    explicitly (Phase 4 worker call sites do this).
    """
    await session.execute(
        sa.text(
            "DELETE FROM shadow_open_positions "
            "WHERE user_id = :uid AND symbol = :s AND timeframe = :tf"
        ),
        {"uid": user_id, "s": symbol, "tf": timeframe},
    )


async def persist_closed_trade(
    session: AsyncSession,
    pos: ShadowPosition,
    *,
    user_id: int,
    exit_price: float,
    exit_reason: ExitReason,
    closed_at: datetime,
    bars_held: int,
    inputs_hash: str,
) -> str:
    """Insert a row in shadow_trades, hash-chained per §5.14. Returns row_hash.

    Spec §13: user_id participates in the canonical row hash payload so any
    cross-user tampering surfaces during chain verification.
    """
    payload = build_shadow_trade_payload(
        pos,
        user_id=user_id,
        exit_price=exit_price,
        exit_reason=exit_reason,
        closed_at=closed_at,
        bars_held=bars_held,
        inputs_hash=inputs_hash,
        # PR-strategy-1: thread the 7 PR1 analytics columns from `pos`
        # into the payload builder. Each field defaults to None on
        # ShadowPosition; restart-survivor positions (loaded from
        # `shadow_open_positions` which doesn't carry these cols) stay
        # None — same as today's behavior. Same-session opens populate
        # via shadow_worker.
        mtf_agreement=getattr(pos, "mtf_agreement", None),
        mtf_dominant_tf=getattr(pos, "mtf_dominant_tf", None),
        mtf_directions_json=getattr(pos, "mtf_directions_json", None),
        p_win=getattr(pos, "p_win", None),
        effective_score=getattr(pos, "effective_score", None),
        realized_vol_20d=getattr(pos, "realized_vol_20d", None),
        funding_directional_adj=getattr(pos, "funding_directional_adj", None),
    )
    return await insert_with_chain(session, "shadow_trades", payload)


async def set_cooldown(
    session: AsyncSession, *, user_id: int, symbol: str,
    timeframe: str = "1h", until: datetime,
) -> None:
    """Upsert cooldown for an asset, scoped to (user_id, symbol, timeframe).

    PR3: PK is now (user_id, symbol, timeframe) — see migration
    0021_pr3_shadow_per_tf. ON CONFLICT targets that composite so two
    users can hold independent cooldowns on the same symbol AND a single
    user can hold independent cooldowns on the same symbol per TF. Default
    `timeframe='1h'` preserves PR1/PR2 call-site contracts until those
    call sites are migrated (Phase 4 worker call sites pass TF explicitly).
    """
    await session.execute(
        sa.text(
            "INSERT INTO shadow_cooldowns (user_id, symbol, timeframe, cooldown_until) "
            "VALUES (:uid, :s, :tf, :u) "
            "ON CONFLICT(user_id, symbol, timeframe) DO UPDATE SET "
            "cooldown_until = excluded.cooldown_until"
        ),
        {"uid": user_id, "s": symbol, "tf": timeframe, "u": until},
    )


async def load_cooldowns(
    session: AsyncSession, *, user_id: int,
) -> dict[str, datetime]:
    """Return 1h-only cooldowns keyed by symbol.

    Phase 3 keeps the legacy shape (`dict[str, datetime]`) so the
    existing shadow_worker continues to work. The worker is currently
    1h-only — filtering to `timeframe='1h'` matches existing semantics
    exactly. Phase 4 (TF-aware ShadowWorker refactor) switches the
    worker to consume `load_cooldowns_per_tf` instead.
    """
    result = await session.execute(
        sa.text(
            "SELECT symbol, cooldown_until FROM shadow_cooldowns "
            "WHERE user_id = :uid AND timeframe = '1h'"
        ),
        {"uid": user_id},
    )
    return {r.symbol: _to_dt(r.cooldown_until) for r in result}


async def load_cooldowns_per_tf(
    session: AsyncSession, *, user_id: int,
) -> dict[tuple[str, str], datetime]:
    """Return cooldowns keyed by (symbol, timeframe).

    PR3 Phase 3 ships this alongside the legacy `load_cooldowns` so the
    Phase 4 ShadowWorker refactor can switch over without a flag day.
    Use this in new code; treat `load_cooldowns` as the 1h-only legacy
    shim until the worker is migrated.
    """
    result = await session.execute(
        sa.text(
            "SELECT symbol, timeframe, cooldown_until FROM shadow_cooldowns "
            "WHERE user_id = :uid"
        ),
        {"uid": user_id},
    )
    return {
        (r.symbol, getattr(r, "timeframe", None) or "1h"): _to_dt(r.cooldown_until)
        for r in result
    }


async def lookup_shadow_trade_id_by_row_hash(
    session: AsyncSession, *, row_hash: str,
) -> int | None:
    """Return `shadow_trades.id` for a given `row_hash`.

    Amendment 3 (2026-07-31): the breakeven-variant lane needs the id
    of a base row it just inserted so it can FK variant rows to it.
    `insert_with_chain` returns the row_hash (canonical for hash-chain
    integrity), but callers historically didn't need the id. Rather
    than change the widely-used `persist_closed_trade` signature, we
    look up by row_hash — which is unique per row by construction.

    Returns None on miss (e.g. the caller passed a stale hash from a
    row that was rolled back). Callers should treat None as "skip
    variant persistence for this signal" — never a fatal error.
    """
    row = (await session.execute(
        sa.text("SELECT id FROM shadow_trades WHERE row_hash = :h LIMIT 1"),
        {"h": row_hash},
    )).first()
    return int(row.id) if row is not None else None


async def insert_shadow_trade_variant(
    session: AsyncSession,
    *,
    base_shadow_trade_id: int,
    variant_name: str,
    trigger_r: float,
    armed: bool,
    exit_price: float,
    exit_reason: ExitReason,
    exit_ts: datetime,
    bars_held: int,
    pnl_pct: float,
) -> None:
    """Insert one row into `shadow_trade_variants`.

    Amendment 3 (2026-07-31). Not hash-chained (see design doc).
    Uses plain INSERT with `ON CONFLICT DO NOTHING` on the
    (base_shadow_trade_id, variant_name) UNIQUE so a retried close
    attempt does not surface as a duplicate error.

    Caller commits its own session. Failures propagate — the worker
    wraps the batch in try/except so variant persist errors never
    corrupt the base close.
    """
    await session.execute(
        sa.text(
            "INSERT INTO shadow_trade_variants "
            "(base_shadow_trade_id, variant_name, trigger_r, armed, "
            " exit_price, exit_reason, exit_ts, bars_held, pnl_pct) "
            "VALUES (:base_id, :vname, :trig, :armed, "
            "        :exit_p, :exit_r, :exit_ts, :bh, :pnl) "
            "ON CONFLICT (base_shadow_trade_id, variant_name) DO NOTHING"
        ),
        {
            "base_id": base_shadow_trade_id,
            "vname": variant_name,
            "trig": trigger_r,
            "armed": armed,
            "exit_p": exit_price,
            "exit_r": exit_reason.value,
            "exit_ts": exit_ts,
            "bh": bars_held,
            "pnl": pnl_pct,
        },
    )
