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

Error semantics: the helper does NOT swallow exceptions. Callers
decide their own contract:

- Dispatcher pre-card gate — **fail-open** on DB error. A card is
  only a notification; the operator remains the approval step; a
  transient DB error should not veto every dispatch. Wraps the
  call in try/except and returns None on error, matching the other
  gate modules (`symbol_allowlist_gate`, `cooldown_gate`).

- Telegram approve-time gate — **fail-closed** on DB error. This
  gate exists to prevent position doubling; on a DB failure "allow
  the order" causes exactly the harm it was built to prevent, at
  the moment the system is least healthy. Missed trade cost is
  near-zero (net-zero edge, signal recurs); doubled position is
  2× risk on one symbol with an undesigned exit level. Wraps the
  call in try/except and logs `blocked_position_check_failed`
  (distinct from `blocked_symbol_position_open`) so the operator
  can see it was a verification failure, not a real duplicate.
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

    Raises whatever DB exception the query raises. The caller owns
    the fail-open vs fail-closed policy — see the module docstring.
    """
    row = (await session.execute(
        sa.text(
            "SELECT id FROM live_trades "
            "WHERE user_id = :u AND symbol = :s AND status = 'open' "
            "LIMIT 1"
        ),
        {"u": user_id, "s": symbol},
    )).first()
    return int(row.id) if row is not None else None
