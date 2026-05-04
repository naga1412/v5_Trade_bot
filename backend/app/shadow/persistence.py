import json
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import insert_with_chain
from app.shadow.engine import Direction, ShadowPosition
from app.shadow.exit_monitor import ExitReason


async def persist_open_position(session: AsyncSession, pos: ShadowPosition) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO shadow_open_positions "
            "(symbol, direction, entry_price, stop_loss, take_profit, "
            "position_size_usdt, entry_score, entry_confidence, entry_atr, "
            "bars_held, opened_at, last_check_at, signal_id) "
            "VALUES (:s, :d, :ep, :sl, :tp, :ps, :es, :ec, :ea, :bh, :oa, :lc, :sig)"
        ),
        {
            "s": pos.symbol, "d": pos.direction.value,
            "ep": pos.entry_price, "sl": pos.stop_loss, "tp": pos.take_profit,
            "ps": pos.position_size_usdt, "es": pos.entry_score,
            "ec": pos.entry_confidence, "ea": pos.entry_atr,
            "bh": pos.bars_held,
            "oa": pos.opened_at.isoformat(), "lc": pos.last_check_at.isoformat(),
            "sig": pos.signal_id,
        },
    )


async def list_open_positions(session: AsyncSession) -> list[ShadowPosition]:
    result = await session.execute(
        sa.text("SELECT * FROM shadow_open_positions ORDER BY opened_at ASC")
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


async def delete_open_position(session: AsyncSession, symbol: str) -> None:
    await session.execute(
        sa.text("DELETE FROM shadow_open_positions WHERE symbol = :s"),
        {"s": symbol},
    )


async def persist_closed_trade(
    session: AsyncSession,
    pos: ShadowPosition,
    *,
    exit_price: float,
    exit_reason: ExitReason,
    closed_at: datetime,
    bars_held: int,
    inputs_hash: str,
) -> str:
    """Insert a row in shadow_trades, hash-chained per §5.14. Returns row_hash."""
    if pos.direction is Direction.LONG:
        pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100.0
    else:
        pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100.0
    pnl_usdt = pos.position_size_usdt * pnl_pct / 100.0

    payload = {
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


async def set_cooldown(session: AsyncSession, symbol: str, until: datetime) -> None:
    """Upsert cooldown for an asset."""
    await session.execute(
        sa.text(
            "INSERT INTO shadow_cooldowns (symbol, cooldown_until) "
            "VALUES (:s, :u) "
            "ON CONFLICT(symbol) DO UPDATE SET cooldown_until = excluded.cooldown_until"
        ),
        {"s": symbol, "u": until.isoformat()},
    )


async def load_cooldowns(session: AsyncSession) -> dict[str, datetime]:
    result = await session.execute(
        sa.text("SELECT symbol, cooldown_until FROM shadow_cooldowns")
    )
    return {r.symbol: datetime.fromisoformat(r.cooldown_until) for r in result}
