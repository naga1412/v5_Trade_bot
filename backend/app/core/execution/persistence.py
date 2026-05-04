import json
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.execution.types import Trade
from app.db.audit import insert_with_chain


async def persist_trade(session: AsyncSession, trade: Trade) -> str:
    payload = {
        "symbol": trade.symbol,
        "direction": trade.direction.value,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "stop_loss": trade.stop_loss,
        "take_profit": trade.take_profit,
        "position_size": trade.position_size,
        "opened_at": trade.opened_at.isoformat(),
        "closed_at": trade.closed_at.isoformat(),
        "pnl_pct": trade.pnl_pct,
        "max_drawdown_during": None,
        "bars_held": trade.bars_held,
        "exit_reason": trade.exit_reason.value,
        "reasoning": json.dumps(trade.reasoning),
        "model_version": "sp-0",
    }
    return await insert_with_chain(session, "paper_trades", payload)


async def persist_prediction(session: AsyncSession, payload: dict) -> str:
    """Caller is responsible for shaping `payload` to match the predictions schema.

    SP-0.7 §7.3: predictions is a per-user table. The payload MUST include a
    `user_id` key — we raise eagerly so callers crash in tests rather than
    relying on the SQLAlchemy NOT NULL constraint surfacing the bug late.
    """
    if "user_id" not in payload:
        raise ValueError("persist_prediction payload missing user_id")
    return await insert_with_chain(session, "predictions", payload)
