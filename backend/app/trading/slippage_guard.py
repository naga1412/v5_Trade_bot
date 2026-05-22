"""FU-33 — symbol-level slippage circuit-breaker.

When realized SL slippage exceeds N× expected, halt new entries on
that symbol for a cooldown period. Prevents the FIDAUSDT 2026-05-18
class of incident (single trade lost -14.5%) from repeating.

Default-OFF — operator flips ``SLIPPAGE_GUARD_ENABLED=True`` per env
after observing the dormant-mode metric (this module records nothing
when the flag is False).

Two entry points:
  * ``check_slippage(...)`` — called on SL exit; computes whether the
    realized slippage triggers a halt, persists state to
    ``symbol_halt_state`` + sends a Telegram alert when it does. Returns
    the decision; the caller (shadow_worker / dispatcher) acts on it.
  * ``is_symbol_halted(...)`` — read-only check used by the entry path.
    Returns True only while ``halted_until > now()``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.ops.alert_routing import alert_admin


@dataclass(frozen=True)
class SlippageDecision:
    """Outcome of a slippage check on an SL exit."""

    halt: bool
    reason: str
    halt_until: datetime | None


async def check_slippage(
    session: AsyncSession,
    *,
    symbol: str,
    expected_sl_pct: float,
    actual_sl_pct: float,
    settings: Any,
) -> SlippageDecision:
    """Compute whether the realized slippage triggers a per-symbol halt.

    Returns ``halt=True`` when
    ``|actual_sl_pct / expected_sl_pct| > settings.SLIPPAGE_THRESHOLD_MULTIPLIER``.

    Side effects on halt:
      * UPSERTs a row into ``symbol_halt_state`` carrying
        ``halted_until = now() + SLIPPAGE_HALT_COOLDOWN_HOURS``.
      * Dispatches a critical-severity Telegram alert via
        ``app.ops.alert_routing.alert_admin``.

    Caller (shadow_worker / dispatcher) is responsible for committing the
    session AND for actually preventing new entries (via
    ``is_symbol_halted`` on the entry path).
    """
    if not getattr(settings, "SLIPPAGE_GUARD_ENABLED", False):
        return SlippageDecision(
            halt=False, reason="guard_disabled", halt_until=None,
        )
    if expected_sl_pct == 0:
        # Defensive — don't divide by zero on a degenerate position.
        return SlippageDecision(
            halt=False, reason="zero_expected_sl", halt_until=None,
        )

    ratio = abs(actual_sl_pct / expected_sl_pct)
    threshold = float(settings.SLIPPAGE_THRESHOLD_MULTIPLIER)
    if ratio <= threshold:
        return SlippageDecision(
            halt=False,
            reason=f"within_threshold (ratio={ratio:.2f})",
            halt_until=None,
        )

    halt_until = datetime.now(tz=timezone.utc) + timedelta(
        hours=int(settings.SLIPPAGE_HALT_COOLDOWN_HOURS),
    )
    reason = f"slippage_ratio={ratio:.2f} > {threshold:.2f}"

    # UPSERT — one row per symbol. Postgres ON CONFLICT and SQLite 3.24+
    # both speak this syntax, so the same statement runs on both backends.
    await session.execute(
        sa.text(
            "INSERT INTO symbol_halt_state (symbol, halted_until, reason, created_at) "
            "VALUES (:s, :u, :r, :c) "
            "ON CONFLICT (symbol) DO UPDATE SET "
            "halted_until = excluded.halted_until, "
            "reason = excluded.reason, "
            "created_at = excluded.created_at"
        ),
        {
            "s": symbol,
            # Use ISO strings so SQLite (TEXT) and Postgres (TIMESTAMPTZ)
            # both accept the value via SQLAlchemy's `sa.text` bind.
            "u": halt_until.isoformat(),
            "r": reason,
            "c": datetime.now(tz=timezone.utc).isoformat(),
        },
    )

    # Telegram alert (critical-severity → telegram-first per alert_routing).
    # Best-effort: a failed alert MUST NOT raise out of the halt path.
    try:
        await alert_admin(
            f"FU-33 slippage halt: {symbol} halted until "
            f"{halt_until.isoformat()} — ratio={ratio:.2f}× expected SL. "
            f"Investigate Binance liquidity / fill quality.",
            level="critical",
        )
    except Exception:  # noqa: BLE001 — alert is best-effort
        # Log line inside alert_admin is the last-resort signal.
        pass

    return SlippageDecision(halt=True, reason=reason, halt_until=halt_until)


async def is_symbol_halted(
    session: AsyncSession, *, symbol: str,
) -> bool:
    """Read-only check used by the entry path.

    Returns True only while ``halted_until > now()``. Expired halt rows
    are left in place (operator audit trail) but report False.
    """
    row = (
        await session.execute(
            sa.text(
                "SELECT halted_until FROM symbol_halt_state WHERE symbol = :s"
            ),
            {"s": symbol},
        )
    ).first()
    if row is None:
        return False
    halted_until_raw = row.halted_until
    # SQLite returns ISO strings; asyncpg returns native datetime.
    if isinstance(halted_until_raw, datetime):
        halted_until = halted_until_raw
    else:
        halted_until = datetime.fromisoformat(
            str(halted_until_raw).replace("Z", "+00:00"),
        )
    if halted_until.tzinfo is None:
        halted_until = halted_until.replace(tzinfo=timezone.utc)
    return halted_until > datetime.now(tz=timezone.utc)


__all__ = ["SlippageDecision", "check_slippage", "is_symbol_halted"]
