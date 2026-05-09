"""SP-8 Phase D — kill switches + auto-demote orchestrator.

Spec §11. Six configurable safety triggers; each can be disabled
individually but disabling requires hardware-confirm (Phase E + I).

Each switch is a pure function (state) → (tripped, reason). The
orchestrator polls them every 30s when any user is in a non-Manual
mode, persists trips to ``kill_switch_state``, and either freezes
trading or auto-demotes per spec §4.4.

This module deliberately avoids the running engine — it's a library
of switch functions + the orchestrator coordinator. Wiring into
``app/main.py`` lifespan + the persistence layer happens in Phase J.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

log = logging.getLogger(__name__)


SwitchName = Literal[
    "daily_loss",
    "consecutive_losses",
    "network_outage",
    "slippage",
    "liquidation_near",
    "funding_rate_guard",
]


@dataclass(frozen=True)
class SwitchConfig:
    """Per-switch user setting from kill_switch_state row."""

    name: SwitchName
    enabled: bool
    threshold_value: float | None  # interpretation per-switch


@dataclass(frozen=True)
class TripDecision:
    """Outcome of evaluating a single switch."""

    name: SwitchName
    tripped: bool
    reason: str | None = None


# Spec §11.1 defaults — used when kill_switch_state has no row for a switch.
DEFAULTS: dict[SwitchName, float] = {
    "daily_loss": 0.02,           # 2 % portfolio (or $200 absolute, whichever first)
    "consecutive_losses": 5,      # # of losses in a row
    "network_outage": 60.0,       # seconds without exchange reachability
    "slippage": 0.005,            # 0.5 % beyond expected
    "liquidation_near": 0.50,     # < 50 % buffer remaining
    "funding_rate_guard": 0.01,   # 1 % per day
}


# ---- 1. Daily loss --------------------------------------------------------

_DAILY_LOSS_ABS_USDT = 200.0  # spec §11.1 — absolute floor


def evaluate_daily_loss(
    *,
    portfolio_value_usdt: float,
    realized_pnl_today_usdt: float,
    cfg: SwitchConfig,
    now: datetime | None = None,  # noqa: ARG001 — kept for symmetry
) -> TripDecision:
    """Trip when realized P&L today < min(2 % portfolio, $200)."""
    if not cfg.enabled or portfolio_value_usdt <= 0:
        return TripDecision(name="daily_loss", tripped=False)
    pct_threshold = (cfg.threshold_value or DEFAULTS["daily_loss"])
    pct_loss_limit = portfolio_value_usdt * pct_threshold
    abs_limit = min(pct_loss_limit, _DAILY_LOSS_ABS_USDT)
    if realized_pnl_today_usdt < -abs_limit:
        return TripDecision(
            name="daily_loss", tripped=True,
            reason=(
                f"realized_pnl_today=${realized_pnl_today_usdt:.2f} "
                f"< -${abs_limit:.2f}"
            ),
        )
    return TripDecision(name="daily_loss", tripped=False)


# ---- 2. Consecutive losses -----------------------------------------------


def evaluate_consecutive_losses(
    *,
    recent_trade_pnls: list[float],
    cfg: SwitchConfig,
) -> TripDecision:
    """Trip when the last N trades were all losses (N from cfg)."""
    if not cfg.enabled or not recent_trade_pnls:
        return TripDecision(name="consecutive_losses", tripped=False)
    n_required = int(cfg.threshold_value or DEFAULTS["consecutive_losses"])
    tail = recent_trade_pnls[-n_required:]
    if len(tail) < n_required:
        return TripDecision(name="consecutive_losses", tripped=False)
    if all(p < 0 for p in tail):
        return TripDecision(
            name="consecutive_losses", tripped=True,
            reason=f"last {n_required} trades all losses (worst {min(tail):.2f}%)",
        )
    return TripDecision(name="consecutive_losses", tripped=False)


# ---- 3. Network outage ---------------------------------------------------


def evaluate_network_outage(
    *,
    last_heartbeat_at: datetime,
    cfg: SwitchConfig,
    now: datetime | None = None,
) -> TripDecision:
    """Trip when ≥ N seconds since last successful exchange heartbeat."""
    if not cfg.enabled:
        return TripDecision(name="network_outage", tripped=False)
    n = now or datetime.now(timezone.utc)
    threshold_s = float(cfg.threshold_value or DEFAULTS["network_outage"])
    elapsed = (n - last_heartbeat_at).total_seconds()
    if elapsed > threshold_s:
        return TripDecision(
            name="network_outage", tripped=True,
            reason=f"no heartbeat for {elapsed:.0f}s (limit {threshold_s:.0f}s)",
        )
    return TripDecision(name="network_outage", tripped=False)


# ---- 4. Slippage ---------------------------------------------------------


def evaluate_slippage(
    *,
    expected_price: float, actual_fill_price: float,
    cfg: SwitchConfig,
) -> TripDecision:
    """Trip when fill price deviates more than threshold % from expected."""
    if not cfg.enabled or expected_price <= 0:
        return TripDecision(name="slippage", tripped=False)
    threshold_pct = float(cfg.threshold_value or DEFAULTS["slippage"])
    deviation = abs(actual_fill_price - expected_price) / expected_price
    if deviation > threshold_pct:
        return TripDecision(
            name="slippage", tripped=True,
            reason=(
                f"fill ${actual_fill_price:.2f} vs expected "
                f"${expected_price:.2f} ({deviation:.3%} > {threshold_pct:.3%})"
            ),
        )
    return TripDecision(name="slippage", tripped=False)


# ---- 5. Liquidation-near (Telegram alert only — no trade freeze) -------


def evaluate_liquidation_near(
    *,
    current_price: float, liquidation_price: float, entry_price: float,
    cfg: SwitchConfig,
) -> TripDecision:
    """Trip when buffer to liquidation drops below the configured fraction.

    buffer = |current - liquidation| / |entry - liquidation|. When buffer
    is < threshold, the position is dangerously close to liquidation.
    Spec §11.1 — this fires a Telegram alert; does NOT auto-freeze
    (operator decides).
    """
    if not cfg.enabled:
        return TripDecision(name="liquidation_near", tripped=False)
    if liquidation_price <= 0 or entry_price <= 0:
        return TripDecision(name="liquidation_near", tripped=False)
    initial_distance = abs(entry_price - liquidation_price)
    if initial_distance == 0:
        return TripDecision(name="liquidation_near", tripped=False)
    current_distance = abs(current_price - liquidation_price)
    buffer = current_distance / initial_distance
    threshold = float(cfg.threshold_value or DEFAULTS["liquidation_near"])
    if buffer < threshold:
        return TripDecision(
            name="liquidation_near", tripped=True,
            reason=(
                f"buffer {buffer:.1%} < {threshold:.0%} "
                f"(current ${current_price:.2f} vs liq ${liquidation_price:.2f})"
            ),
        )
    return TripDecision(name="liquidation_near", tripped=False)


# ---- 6. Funding rate guard ----------------------------------------------


def evaluate_funding_rate(
    *,
    daily_funding_rate: float,
    position_direction: Literal["LONG", "SHORT"],
    cfg: SwitchConfig,
) -> TripDecision:
    """Trip new-position-open in the SAME direction when funding > threshold.

    Funding > 1 %/day on LONG positions means longs pay 1 % to shorts every
    24 h — opening more LONGs is a bad idea. (Existing position stays open;
    this only blocks new-position-open in the same direction.)
    """
    if not cfg.enabled:
        return TripDecision(name="funding_rate_guard", tripped=False)
    threshold = float(cfg.threshold_value or DEFAULTS["funding_rate_guard"])
    if position_direction == "LONG" and daily_funding_rate > threshold:
        return TripDecision(
            name="funding_rate_guard", tripped=True,
            reason=(
                f"funding {daily_funding_rate:.3%}/day on LONG > "
                f"{threshold:.3%} threshold"
            ),
        )
    if position_direction == "SHORT" and daily_funding_rate < -threshold:
        return TripDecision(
            name="funding_rate_guard", tripped=True,
            reason=(
                f"funding {daily_funding_rate:.3%}/day on SHORT < "
                f"-{threshold:.3%} threshold"
            ),
        )
    return TripDecision(name="funding_rate_guard", tripped=False)


# ---- Auto-demote logic (spec §4.4) ---------------------------------------


@dataclass(frozen=True)
class AutoDemoteSignal:
    """Spec §4.4 mode-demotion triggers (separate from the switches above)."""

    fully_to_telegram: bool
    telegram_to_manual: bool
    reasons: list[str]


def evaluate_auto_demote(
    *,
    daily_loss_pct: float,
    consecutive_losses: int,
    days_below_gates: int,
    audit_chain_broken: bool,
) -> AutoDemoteSignal:
    """Map the §4.4 inputs to mode-demote decisions.

    Returns the strongest demotion signaled, plus reasons.

    Spec §4.4:
    - daily loss > 5 % portfolio: Fully-auto -> Telegram-approve
    - 10 consecutive losses: Fully-auto -> Telegram-approve
    - gates failed for 7 consecutive days: Telegram-approve -> Manual
    - audit chain integrity broken: any -> Manual + freeze
    """
    fully_to_tel = False
    tel_to_man = False
    reasons: list[str] = []

    if audit_chain_broken:
        # Worst case: demote to Manual + freeze regardless of mode.
        tel_to_man = True
        fully_to_tel = True
        reasons.append("audit chain integrity violation")

    if daily_loss_pct < -0.05:
        fully_to_tel = True
        reasons.append(f"daily loss {daily_loss_pct:.2%} > 5%")

    if consecutive_losses >= 10:
        fully_to_tel = True
        reasons.append(f"{consecutive_losses} consecutive losses")

    if days_below_gates >= 7:
        tel_to_man = True
        reasons.append(f"gates failed {days_below_gates} consecutive days")

    return AutoDemoteSignal(
        fully_to_telegram=fully_to_tel,
        telegram_to_manual=tel_to_man,
        reasons=reasons,
    )


__all__ = [
    "DEFAULTS",
    "AutoDemoteSignal",
    "SwitchConfig",
    "SwitchName",
    "TripDecision",
    "evaluate_auto_demote",
    "evaluate_consecutive_losses",
    "evaluate_daily_loss",
    "evaluate_funding_rate",
    "evaluate_liquidation_near",
    "evaluate_network_outage",
    "evaluate_slippage",
]
