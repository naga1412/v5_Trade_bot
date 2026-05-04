"""Per-user shadow persistence helpers.

Spec §7.1 — every row in `shadow_open_positions`, `shadow_trades`, and
`shadow_cooldowns` is owned by exactly one user, and every read/mutation
must filter by `user_id`. SP-0.7's bootstrap admin (id=1) owns all rows
the single shadow worker produces; SP-8 will fan out workers per user.

The runtime guard in `app.auth.query_guard` enforces this contract at the
SQLAlchemy event layer so any future helper that forgets the predicate
raises in dev.
"""

import json
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import insert_with_chain
from app.shadow.engine import Direction, ShadowPosition
from app.shadow.exit_monitor import ExitReason


async def persist_open_position(
    session: AsyncSession, pos: ShadowPosition, *, user_id: int,
) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO shadow_open_positions "
            "(user_id, symbol, direction, entry_price, stop_loss, take_profit, "
            "position_size_usdt, entry_score, entry_confidence, entry_atr, "
            "bars_held, opened_at, last_check_at, signal_id) "
            "VALUES (:uid, :s, :d, :ep, :sl, :tp, :ps, :es, :ec, :ea, :bh, :oa, :lc, :sig)"
        ),
        {
            "uid": user_id,
            "s": pos.symbol, "d": pos.direction.value,
            "ep": pos.entry_price, "sl": pos.stop_loss, "tp": pos.take_profit,
            "ps": pos.position_size_usdt, "es": pos.entry_score,
            "ec": pos.entry_confidence, "ea": pos.entry_atr,
            "bh": pos.bars_held,
            "oa": pos.opened_at.isoformat(), "lc": pos.last_check_at.isoformat(),
            "sig": pos.signal_id,
        },
    )


async def list_open_positions(
    session: AsyncSession, *, user_id: int,
) -> list[ShadowPosition]:
    result = await session.execute(
        sa.text(
            "SELECT * FROM shadow_open_positions "
            "WHERE user_id = :uid ORDER BY opened_at ASC"
        ),
        {"uid": user_id},
    )
    out: list[ShadowPosition] = []
    for r in result:
        out.append(ShadowPosition(
            symbol=r.symbol, direction=Direction(r.direction),
            entry_price=r.entry_price, stop_loss=r.stop_loss, take_profit=r.take_profit,
            position_size_usdt=r.position_size_usdt,
            entry_score=r.entry_score, entry_confidence=r.entry_confidence,
            entry_atr=r.entry_atr, layer_scores={},
            bars_held=r.bars_held,
            opened_at=datetime.fromisoformat(r.opened_at),
            last_check_at=datetime.fromisoformat(r.last_check_at),
            signal_id=r.signal_id,
        ))
    return out


async def delete_open_position(
    session: AsyncSession, *, user_id: int, symbol: str,
) -> None:
    await session.execute(
        sa.text(
            "DELETE FROM shadow_open_positions "
            "WHERE user_id = :uid AND symbol = :s"
        ),
        {"uid": user_id, "s": symbol},
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
    if pos.direction is Direction.LONG:
        pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100.0
    else:
        pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100.0
    pnl_usdt = pos.position_size_usdt * pnl_pct / 100.0

    payload = {
        "user_id": user_id,
        "symbol": pos.symbol,
        "timeframe": "1h",
        "direction": pos.direction.value,
        "entry_price": pos.entry_price,
        "stop_loss": pos.stop_loss,
        "take_profit": pos.take_profit,
        "position_size_usdt": pos.position_size_usdt,
        "entry_score": pos.entry_score,
        "entry_confidence": pos.entry_confidence,
        "layer_scores": json.dumps(pos.layer_scores),
        "entry_atr": pos.entry_atr,
        "exit_price": exit_price,
        "exit_reason": exit_reason.value,
        "pnl_pct": pnl_pct,
        "pnl_usdt": pnl_usdt,
        "bars_held": bars_held,
        "opened_at": pos.opened_at.isoformat(),
        "closed_at": closed_at.isoformat(),
        "inputs_hash": inputs_hash,
        "model_version": "sp-0.5",
        "signal_id": pos.signal_id,
    }
    return await insert_with_chain(session, "shadow_trades", payload)


async def set_cooldown(
    session: AsyncSession, *, user_id: int, symbol: str, until: datetime,
) -> None:
    """Upsert cooldown for an asset, scoped to (user_id, symbol).

    PK is (user_id, symbol) — see migration 0005. ON CONFLICT targets that
    composite so two users can hold independent cooldowns on the same
    symbol without colliding.
    """
    await session.execute(
        sa.text(
            "INSERT INTO shadow_cooldowns (user_id, symbol, cooldown_until) "
            "VALUES (:uid, :s, :u) "
            "ON CONFLICT(user_id, symbol) DO UPDATE SET "
            "cooldown_until = excluded.cooldown_until"
        ),
        {"uid": user_id, "s": symbol, "u": until.isoformat()},
    )


async def load_cooldowns(
    session: AsyncSession, *, user_id: int,
) -> dict[str, datetime]:
    result = await session.execute(
        sa.text(
            "SELECT symbol, cooldown_until FROM shadow_cooldowns "
            "WHERE user_id = :uid"
        ),
        {"uid": user_id},
    )
    return {r.symbol: datetime.fromisoformat(r.cooldown_until) for r in result}
