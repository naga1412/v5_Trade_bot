"""PR8 cooldown gate — dispatcher pre-condition.

Reads `live_cooldowns` for `(user_id, symbol)` and emits
`DispatchResult(blocked_cooldown)` if the cooldown is active per
`is_cooldown_blocked`.

**Fail-open contract:** any error from the DB read path is logged
(warn) and returns None (let the trade proceed). A cooldown gate that
erred to-blocked would shut down trading indefinitely on a single
DB blip — the same fail-open philosophy PR2's MTF gate uses.

The gate is split out of `dispatcher.py` for testability (the gate has
its own integration tests) and to keep dispatcher.py focused on
sequencing rather than each gate's internals.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.live_cooldowns import load_cooldown
from app.trading.cooldown_compute import is_cooldown_blocked


log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _apply_cooldown_gate(
    *,
    proposal,
    user_id: int,
    session: AsyncSession,
    settings,
    now_fn: Callable[[], datetime] = _utc_now,
):
    """Return DispatchResult to block; None to let the trade proceed.

    None on: gate disabled, no cooldown row, calendar expired + fresh
    MTF (when applicable), OR any DB error (fail-open).

    Imported lazily inside `dispatcher.py` to avoid an import cycle
    (dispatcher defines `DispatchResult` which the gate emits).
    """
    from app.trading.execution.dispatcher import DispatchResult

    try:
        row = await load_cooldown(
            session, user_id=user_id, symbol=proposal.symbol,
        )
    except Exception as e:  # noqa: BLE001
        log.warning(
            "cooldown_gate DB read failed for user=%s symbol=%s; "
            "failing open: %s",
            user_id, proposal.symbol, e,
        )
        return None

    blocked, reason = is_cooldown_blocked(
        now=now_fn(),
        cooldown_row=row,  # type: ignore[arg-type]  # LiveCooldown structurally satisfies _CooldownRowProto
        new_mtf_agreement=getattr(proposal, "mtf_agreement", None),
        settings=settings,
    )
    if not blocked:
        return None
    return DispatchResult(
        outcome="blocked_cooldown",
        detail=f"cooldown: {reason}",
    )
