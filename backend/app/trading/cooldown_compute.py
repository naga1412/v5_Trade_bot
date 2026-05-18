"""PR8 pure-function cooldown logic.

`compute_cooldown_duration` and `is_cooldown_blocked` are testable in
isolation — the DB-touching path lives in `cooldown_gate.py`. Keeping
the decision logic separate from I/O lets us exercise the matrix
without a real session, and makes the gate code trivially small.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol


_DEFAULT_TIMEOUT_HOURS = 4.0  # fall-back for unrecognized exit reasons


class _SettingsProto(Protocol):
    LIVE_COOLDOWN_ENABLED: bool
    LIVE_COOLDOWN_HOURS_BY_OUTCOME: dict[str, float]
    LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF: bool


class _CooldownRowProto(Protocol):
    cooldown_until: datetime
    last_exit_reason: str
    last_mtf_agreement: int | None


def compute_cooldown_duration(
    exit_reason: str, settings: _SettingsProto,
) -> timedelta:
    """Look up the configured duration for an outcome.

    Falls back to the 'timeout' baseline (4h) when the outcome string
    isn't in the dict — defensive against future enum extensions that
    ship before the config dict is updated. A test asserts that every
    enum value has a configured duration, so this fallback only fires
    if someone bypasses the enum.
    """
    hours = settings.LIVE_COOLDOWN_HOURS_BY_OUTCOME.get(
        exit_reason, _DEFAULT_TIMEOUT_HOURS,
    )
    return timedelta(hours=hours)


def is_cooldown_blocked(
    *,
    now: datetime,
    cooldown_row: _CooldownRowProto | None,
    new_mtf_agreement: int | None,
    settings: _SettingsProto,
) -> tuple[bool, str]:
    """Decide whether the dispatcher should block this signal on cooldown.

    Returns `(blocked, reason_tag)`. `reason_tag` is logged for ops; it
    is NOT user-facing. The shape mirrors the MTF gate so the dispatcher
    can log uniformly.

    Decision order:
      1. Gate disabled → not blocked.
      2. No cooldown row for (uid, sym) → not blocked.
      3. Calendar still active (now < cooldown_until) → blocked.
      4. Calendar expired, but SL + LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF on
         + new_mtf <= last_mtf → blocked (still stale).
      5. Otherwise → not blocked (cleared).
    """
    if not settings.LIVE_COOLDOWN_ENABLED:
        return False, "cooldown_disabled"
    if cooldown_row is None:
        return False, "no_cooldown"
    if now < cooldown_row.cooldown_until:
        return True, f"calendar_until_{cooldown_row.cooldown_until.isoformat()}"
    # Calendar expired — check the SL fresh-MTF override.
    if (
        cooldown_row.last_exit_reason == "stop_loss"
        and settings.LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF
    ):
        last_mtf = cooldown_row.last_mtf_agreement or 0
        new_mtf = new_mtf_agreement or 0
        if new_mtf <= last_mtf:
            return True, f"sl_stale_mtf_{new_mtf}<={last_mtf}"
    return False, "cleared"
