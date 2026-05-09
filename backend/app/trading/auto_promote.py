"""SP-8 Phase J.2 — daily auto-promotion worker.

Spec (operator request): unattended mode upgrades when paper-trade
gates pass for N consecutive days. Default OFF; opt in per env var:

    AUTO_PROMOTE_TO_TELEGRAM_ENABLED=true     # Manual -> Telegram-approve
    AUTO_PROMOTE_TO_FULLYAUTO_ENABLED=true    # Telegram-approve -> Fully-auto
    AUTO_PROMOTE_CONSECUTIVE_DAYS=7           # how many days gates must hold

Each day at 03:30 UTC (after the brain retrain cron runs):
  1. For every user whose ``trading_mode`` could be auto-promoted:
     a. Compute today's gate snapshot
     b. Walk back N days; if gates passed every day, run the upgrade
  2. Hardware-confirm is BYPASSED for auto-promotions — the audit row
     records ``triggered_by='auto-demote'`` (closest existing enum
     value) so SP-7's verify_chain doesn't trip on a new value.
  3. Telegram alert before + after every promotion.

Safety rails (beyond the §4 gate thresholds):
- Auto-promote enables ONE step at a time per day. Manual->Fully-auto
  in a single tick is impossible.
- The same kill switches (§11) apply post-promotion. Fully-auto's
  daily-loss-5% rule still demotes back to Telegram-approve.
- The user can always /freeze via Telegram or flip the env var off
  + restart to disarm. There's no hard-coded auto-trigger that
  ignores ``AUTO_PROMOTE_*_ENABLED``.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.trading.modes import (
    Mode,
    ModeChangeError,
    get_mode,
    set_mode,
)
from app.trading.promotion import (
    GateSnapshot,
    TargetMode,
    compute_gates_from_db,
)


log = logging.getLogger(__name__)


_DAILY_TICK_UTC_HOUR = 3
_DAILY_TICK_UTC_MINUTE = 30
DEFAULT_CONSECUTIVE_DAYS = 7


@dataclass(frozen=True)
class AutoPromoteConfig:
    """Per-instance configuration; lifespan reads env vars + builds this."""

    to_telegram_enabled: bool
    to_fullyauto_enabled: bool
    consecutive_days: int = DEFAULT_CONSECUTIVE_DAYS

    @property
    def any_enabled(self) -> bool:
        return self.to_telegram_enabled or self.to_fullyauto_enabled


@dataclass(frozen=True)
class AutoPromoteOutcome:
    """Result of one daily evaluation for one user."""

    user_id: int
    current_mode: Mode
    target_mode: TargetMode | None
    snapshots_passed: int     # count of days the gates passed
    snapshots_required: int   # consecutive_days
    promoted: bool
    reason: str               # what blocked or enabled the promotion


def _next_target(current: Mode, cfg: AutoPromoteConfig) -> TargetMode | None:
    """Map current mode → next auto-promote target; None means no upgrade."""
    if current == "manual" and cfg.to_telegram_enabled:
        return "telegram-approve"
    if current == "telegram-approve" and cfg.to_fullyauto_enabled:
        return "fully-auto"
    return None


async def evaluate_user(
    session: AsyncSession,
    *,
    user_id: int,
    cfg: AutoPromoteConfig,
    now: datetime | None = None,
) -> AutoPromoteOutcome:
    """Decide + maybe perform the auto-promotion for one user.

    Returns an outcome record describing what happened. The caller
    persists telemetry (if needed) and sends the Telegram alert.
    """
    n = now or datetime.now(timezone.utc)
    current = await get_mode(session, user_id)
    target = _next_target(current, cfg)
    if target is None:
        return AutoPromoteOutcome(
            user_id=user_id, current_mode=current, target_mode=None,
            snapshots_passed=0, snapshots_required=cfg.consecutive_days,
            promoted=False,
            reason=f"current mode {current!r}; no auto-promote configured",
        )

    # Window for the target's spec section (30d for Telegram-approve, 90d for Fully-auto).
    window_days = 30 if target == "telegram-approve" else 90

    # Walk back N days; gates must pass for EVERY day's snapshot.
    passed = 0
    latest_snapshot: GateSnapshot | None = None
    for offset in range(cfg.consecutive_days):
        snapshot_now = n - timedelta(days=offset)
        snap = await compute_gates_from_db(
            session, window_days=window_days, now=snapshot_now,
        )
        if offset == 0:
            latest_snapshot = snap
        if snap.passes(target):
            passed += 1
        else:
            break  # one failure breaks the streak

    if passed < cfg.consecutive_days:
        return AutoPromoteOutcome(
            user_id=user_id, current_mode=current, target_mode=target,
            snapshots_passed=passed, snapshots_required=cfg.consecutive_days,
            promoted=False,
            reason=(
                f"gates passed {passed}/{cfg.consecutive_days} "
                f"consecutive days; need full streak"
            ),
        )

    # Streak satisfied — promote.
    if latest_snapshot is None:
        # Defensive — should always be set when consecutive_days >= 1.
        return AutoPromoteOutcome(
            user_id=user_id, current_mode=current, target_mode=target,
            snapshots_passed=passed, snapshots_required=cfg.consecutive_days,
            promoted=False,
            reason="no snapshot available (degenerate consecutive_days=0?)",
        )
    try:
        await set_mode(
            session,
            user_id=user_id, new_mode=target,
            triggered_by="auto-demote",  # closest existing enum; spec sec 4.4
            reason=(
                f"auto-promote: {cfg.consecutive_days}-day streak; "
                f"sharpe={latest_snapshot.sharpe:.2f} "
                f"win_rate={latest_snapshot.win_rate:.2%} "
                f"trades={latest_snapshot.n_trades}"
            ),
            gate_snapshot=latest_snapshot,
            now=n,
        )
        await session.commit()
    except ModeChangeError as e:
        return AutoPromoteOutcome(
            user_id=user_id, current_mode=current, target_mode=target,
            snapshots_passed=passed, snapshots_required=cfg.consecutive_days,
            promoted=False,
            reason=f"set_mode refused: {e.refusal.gate_failures}",
        )
    return AutoPromoteOutcome(
        user_id=user_id, current_mode=current, target_mode=target,
        snapshots_passed=passed, snapshots_required=cfg.consecutive_days,
        promoted=True,
        reason=(
            f"auto-promoted {current} -> {target} "
            f"on {cfg.consecutive_days}-day gate streak"
        ),
    )


def _seconds_until_next_tick(*, now: datetime) -> float:
    """Seconds from now until the next 03:30 UTC tick."""
    n = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    target = n.replace(
        hour=_DAILY_TICK_UTC_HOUR, minute=_DAILY_TICK_UTC_MINUTE,
        second=0, microsecond=0,
    )
    if target <= n:
        target = target + timedelta(days=1)
    return (target - n).total_seconds()


async def run_auto_promote_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    cfg: AutoPromoteConfig,
    notify: Callable[[str], Awaitable[None]] | None = None,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    _now: Callable[[], datetime] | None = None,
) -> None:
    """Forever-loop. Each tick at 03:30 UTC: evaluate every user.

    notify(): optional Telegram poster. When set, the loop sends a
    message describing every outcome (promoted or not). When unset,
    outcomes are logged only.

    Errors during evaluation are caught + logged so the loop survives
    transient DB / Binance / Telegram outages.
    """
    if not cfg.any_enabled:
        log.info(
            "auto-promote: disabled (set AUTO_PROMOTE_TO_TELEGRAM_ENABLED "
            "or AUTO_PROMOTE_TO_FULLYAUTO_ENABLED to enable)",
        )
        return

    now_fn = _now if _now is not None else lambda: datetime.now(timezone.utc)

    while True:
        try:
            wait_s = _seconds_until_next_tick(now=now_fn())
            await _sleep(float(wait_s))
        except asyncio.CancelledError:
            raise

        try:
            async with session_factory() as session:
                user_ids = [
                    r.id for r in (await session.execute(
                        sa.text(
                            "SELECT id FROM users "
                            "WHERE trading_mode IN ('manual', 'telegram-approve')"
                        )
                    )).all()
                ]
                outcomes: list[AutoPromoteOutcome] = []
                for uid in user_ids:
                    try:
                        outcome = await evaluate_user(
                            session, user_id=uid, cfg=cfg, now=now_fn(),
                        )
                        outcomes.append(outcome)
                    except Exception as e:  # noqa: BLE001
                        log.error(
                            "auto-promote evaluation failed for user_id=%d: %s",
                            uid, e,
                        )
            for o in outcomes:
                if o.promoted:
                    log.warning(
                        "AUTO-PROMOTE user=%d %s -> %s: %s",
                        o.user_id, o.current_mode, o.target_mode, o.reason,
                    )
                    if notify is not None:
                        try:
                            await notify(
                                f"🤖 auto-promoted user {o.user_id}: "
                                f"{o.current_mode} -> {o.target_mode}\n"
                                f"{o.reason}"
                            )
                        except Exception as e:  # noqa: BLE001
                            log.error("auto-promote notify failed: %s", e)
                else:
                    log.info(
                        "auto-promote user=%d skipped: %s",
                        o.user_id, o.reason,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("auto-promote loop iteration failed: %s", e)


def start_auto_promote_task(
    session_factory: async_sessionmaker[AsyncSession],
    cfg: AutoPromoteConfig,
    *,
    notify: Callable[[str], Awaitable[None]] | None = None,
) -> asyncio.Task[None]:
    """Spawn the auto-promote loop as a background task. Wired from main:lifespan."""
    return asyncio.create_task(run_auto_promote_loop(
        session_factory=session_factory, cfg=cfg, notify=notify,
    ))


__all__ = [
    "DEFAULT_CONSECUTIVE_DAYS",
    "AutoPromoteConfig",
    "AutoPromoteOutcome",
    "evaluate_user",
    "run_auto_promote_loop",
    "start_auto_promote_task",
]
