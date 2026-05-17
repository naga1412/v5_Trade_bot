import json
import sqlalchemy as sa
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
        # Datetimes pass through raw — asyncpg/Postgres TIMESTAMPTZ
        # rejects ISO strings; SQLite tests still get TEXT auto-binding.
        "opened_at": trade.opened_at,
        "closed_at": trade.closed_at,
        "pnl_pct": trade.pnl_pct,
        "max_drawdown_during": None,
        "bars_held": trade.bars_held,
        "exit_reason": trade.exit_reason.value,
        "reasoning": json.dumps(trade.reasoning),
        "model_version": "sp-0",
    }
    return await insert_with_chain(session, "paper_trades", payload)


async def persist_prediction(
    session: AsyncSession, payload: dict
) -> tuple[int, str]:
    """Persist a prediction via the audit hash chain. Returns ``(id, row_hash)``.

    The ``id`` is required by callers who need to write a foreign-keyed row
    (e.g. ``prediction_validations.prediction_id``) in a SEPARATE transaction
    — see backend/app/ws/live_prediction.py:_persist_prediction_and_schedule_validation
    for the canonical caller and the 2026-05-17 hotfix that motivated this
    tuple return.

    SP-0.7 §7.3: predictions is a per-user table. The payload MUST include a
    ``user_id`` key — we raise eagerly so callers crash in tests rather than
    relying on the SQLAlchemy NOT NULL constraint surfacing the bug late.

    Returns
    -------
    tuple[int, str]
        ``(prediction_id, row_hash)`` — the autoincrement id of the newly
        inserted row, and the canonical row hash for audit chain replay.
        Both reads happen inside the caller's transaction (read-your-own-writes).
    """
    if "user_id" not in payload:
        raise ValueError("persist_prediction payload missing user_id")
    row_hash = await insert_with_chain(session, "predictions", payload)
    # Read the autoincrement id back. The hash chain guarantees row_hash
    # uniqueness (SHA-256 of prev_hash + canonical payload), so this picks
    # the just-inserted row. ORDER BY id DESC + LIMIT 1 is defensive against
    # any pathological hash collision (probability ~2^-256).
    pred_id = (
        await session.execute(
            sa.text(
                "SELECT id FROM predictions WHERE row_hash = :h "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"h": row_hash},
        )
    ).scalar_one()
    return int(pred_id), row_hash
