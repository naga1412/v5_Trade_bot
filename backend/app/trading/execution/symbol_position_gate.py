"""Per-symbol open-position safety gate.

Fixes a class-1 live-money defect: the only prior position gate was
GLOBAL (`user.open_positions_count >= user.max_concurrent_positions`
in `dispatcher.py`). Under `max_concurrent_positions=5`, a symbol
that already had an open position (e.g. BTC/USDT LONG) could receive
a SECOND approved signal on the same symbol, causing
`place_with_sltp` to lay a second market entry + a second SL/TP pair
on the same Binance position. `closePosition=true` on the second SL
means the higher stop covers the full doubled qty, so the newer
tighter stop can prematurely liquidate the older entry at 2x size.

Fix: reject any new dispatch/approval when the (user_id, symbol)
tuple already has a `live_trades` row with `status='open'`. Same
predicate `status='open'` used by `build_user_context` open_count
(source of truth for "position currently on Binance" —
KNOWN_ISSUES / glue.py:build_user_context inline comment).

Defense-in-depth: enforced BOTH at dispatch-pre-card AND at
telegram approve-time, because a card can be sent, the operator can
tap Approve minutes later, and in the interim a different signal
(or a manual trade, or another approved card) may have opened a
position on the same symbol.

Fail-open contract: any unexpected DB error returns False (let the
trade proceed) — matches the other gate modules
(`symbol_allowlist_gate`, `cooldown_gate`). Justified because the
GLOBAL max-concurrent gate remains as backstop; a transient DB
error should not veto every trade.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


log = logging.getLogger(__name__)


async def get_open_position_trade_id(
    session: AsyncSession,
    *,
    user_id: int,
    symbol: str,
) -> int | None:
    """Return live_trades.id of an open position for (user, symbol), else None.

    `symbol` is the canonical `BASE/QUOTE` form (matches how
    `live_trades.symbol` is written by `_phase1_insert_pending_trade`
    and by fully-auto `_place_live_order`).

    Predicate is `status='open'` — the source of truth for "position
    currently on Binance" per glue.py:build_user_context inline note.
    Historic `closed_at IS NULL` alone had false positives from
    May-vintage rows with status='closed' AND closed_at NULL.
    """
    try:
        row = (await session.execute(
            sa.text(
                "SELECT id FROM live_trades "
                "WHERE user_id = :u AND symbol = :s AND status = 'open' "
                "LIMIT 1"
            ),
            {"u": user_id, "s": symbol},
        )).first()
    except Exception as exc:  # noqa: BLE001 — fail-open contract
        log.warning(
            "symbol_position_gate: DB error checking (user=%d, sym=%s): %s "
            "— failing open (max-concurrent gate is backstop)",
            user_id, symbol, exc,
        )
        return None
    return int(row.id) if row is not None else None
