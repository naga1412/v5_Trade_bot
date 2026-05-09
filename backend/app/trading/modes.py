"""SP-8 Phase B — mode state machine.

Spec §3-4. Three modes:

    manual              default; bot generates signals, user clicks
    telegram-approve    bot sends signals via Telegram, user taps Approve
    fully-auto          bot generates AND executes signals automatically

Promotion (manual → telegram-approve → fully-auto) requires the gates
in spec §4 to pass. Demotion is always allowed (downgrade is safe).
Every mode change is audited in mode_change_log (hash-chained).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import insert_with_chain
from app.trading.promotion import GateSnapshot

log = logging.getLogger(__name__)


Mode = Literal["manual", "telegram-approve", "fully-auto"]
TriggerSource = Literal["user", "auto-demote", "admin"]


_MODE_RANK: dict[Mode, int] = {
    "manual": 0,
    "telegram-approve": 1,
    "fully-auto": 2,
}


@dataclass(frozen=True)
class ModeChangeRefused:
    """Raised when an upgrade is blocked by a gate that hasn't passed."""

    requested_mode: Mode
    current_mode: Mode
    gate_failures: list[str]


class ModeChangeError(Exception):
    """Raised when set_mode refuses an upgrade because gates failed."""

    def __init__(self, refusal: ModeChangeRefused):
        self.refusal = refusal
        super().__init__(
            f"upgrade {refusal.current_mode} -> {refusal.requested_mode} "
            f"blocked: {', '.join(refusal.gate_failures)}"
        )


def is_upgrade(old: Mode, new: Mode) -> bool:
    return _MODE_RANK[new] > _MODE_RANK[old]


def is_downgrade(old: Mode, new: Mode) -> bool:
    return _MODE_RANK[new] < _MODE_RANK[old]


async def get_mode(session: AsyncSession, user_id: int) -> Mode:
    """Return the user's current trading_mode. Defaults to 'manual'."""
    row = (await session.execute(
        sa.text("SELECT trading_mode FROM users WHERE id = :u"),
        {"u": user_id},
    )).first()
    if row is None:
        return "manual"
    return row.trading_mode  # type: ignore[no-any-return]


async def set_mode(
    session: AsyncSession,
    *,
    user_id: int,
    new_mode: Mode,
    triggered_by: TriggerSource = "user",
    reason: str | None = None,
    gate_snapshot: GateSnapshot | None = None,
    now: datetime | None = None,
) -> str:
    """Change a user's trading_mode and write the audit row.

    Args:
        session: live AsyncSession; caller commits.
        user_id: target user.
        new_mode: requested new mode.
        triggered_by: who/what triggered the change.
        reason: free-text context for the audit row.
        gate_snapshot: GateSnapshot the change was based on (required when
            ``triggered_by == 'user'`` AND it's an upgrade — gates must
            actually have been computed and pass; the snapshot is stored
            in the audit row).
        now: timestamp; defaults to UTC now.

    Returns:
        The new mode_change_log row's row_hash.

    Raises:
        ModeChangeError: upgrade requested but gates didn't pass.
    """
    n = now or datetime.now(timezone.utc)
    old = await get_mode(session, user_id)

    if old == new_mode:
        log.info(
            "set_mode noop user=%d mode=%s — no change", user_id, new_mode,
        )
        # Still audit the no-op so we have evidence of the attempt.

    if is_upgrade(old, new_mode):
        if gate_snapshot is None:
            raise ModeChangeError(ModeChangeRefused(
                requested_mode=new_mode, current_mode=old,
                gate_failures=["no gate snapshot provided"],
            ))
        # Check gates apply to the requested target.
        failures = gate_snapshot.failures_for(new_mode)
        if failures:
            raise ModeChangeError(ModeChangeRefused(
                requested_mode=new_mode, current_mode=old,
                gate_failures=failures,
            ))

    await session.execute(
        sa.text("UPDATE users SET trading_mode = :m WHERE id = :u"),
        {"m": new_mode, "u": user_id},
    )

    snap_json = (
        json.dumps(gate_snapshot.to_dict()) if gate_snapshot is not None else None
    )
    row_hash = await insert_with_chain(
        session, "mode_change_log",
        {
            "user_id": user_id,
            "old_mode": old,
            "new_mode": new_mode,
            "triggered_by": triggered_by,
            "reason": reason,
            "gate_snapshot": snap_json,
            "changed_at": n,
        },
    )
    log.info(
        "mode_change user=%d %s -> %s (trigger=%s)",
        user_id, old, new_mode, triggered_by,
    )
    return row_hash


__all__ = [
    "Mode",
    "TriggerSource",
    "ModeChangeError",
    "ModeChangeRefused",
    "get_mode",
    "set_mode",
    "is_upgrade",
    "is_downgrade",
]
