"""SP-8 Phase B — promotion gate computation.

Spec §4. Compute rolling-window stats over the closed shadow_trades
(later live_trades) and compare against the gate thresholds.

Gates (per spec §4.1, §4.2):

    Telegram-approve (30-day window, all required):
        days       >= 30
        trades     >= 100
        sharpe     >= 1.0  (annualized)
        max_dd     <= 12%
        win_rate   >= 40%
        profit_fac >= 1.5

    Fully-auto (90-day window, all required):
        days       >= 90
        trades     >= 300
        sharpe     >= 1.5
        max_dd     <= 10%
        win_rate   >= 45%
        profit_fac >= 2.0

This module is pure stats over a list of (pnl_pct, opened_at, closed_at)
tuples — no DB dependency in the math itself. The reading-from-DB
helper `compute_gates_from_db` wraps it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

GateName = Literal[
    "days", "trades", "sharpe", "max_drawdown_pct",
    "win_rate", "profit_factor",
]
TargetMode = Literal["telegram-approve", "fully-auto"]


# (window_days, min_trades, min_sharpe, max_dd_pct, min_win_rate, min_pf)
_TELEGRAM_THRESHOLDS = (30, 100, 1.0, 12.0, 0.40, 1.5)
_FULLY_AUTO_THRESHOLDS = (90, 300, 1.5, 10.0, 0.45, 2.0)


# Trading-day approximation. Crypto is 24/7 but we compute Sharpe with
# a 252-day annualization to match the conventional definition (so
# numbers can be compared to TradFi benchmarks). For pure crypto compare
# 365 would be more apples-to-apples — explicit choice documented here.
_TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class GateSnapshot:
    """Computed gate values vs the per-mode thresholds."""

    window_days: int
    n_trades: int
    sharpe: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    computed_at: datetime
    failures: dict[TargetMode, list[str]] = field(default_factory=dict)

    def passes(self, target: TargetMode) -> bool:
        return not self.failures.get(target)

    def failures_for(self, target: TargetMode) -> list[str]:
        return list(self.failures.get(target, []))

    def to_dict(self) -> dict:
        return {
            "window_days": self.window_days,
            "n_trades": self.n_trades,
            "sharpe": self.sharpe,
            "max_drawdown_pct": self.max_drawdown_pct,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "computed_at": self.computed_at.isoformat(),
            "failures": {k: list(v) for k, v in self.failures.items()},
        }


def _check_thresholds(
    *,
    target: TargetMode,
    window_days: int,
    n_trades: int,
    sharpe: float,
    max_dd: float,
    win_rate: float,
    pf: float,
) -> list[str]:
    if target == "telegram-approve":
        win_days, min_trades, min_sharpe, max_dd_lim, min_wr, min_pf = (
            _TELEGRAM_THRESHOLDS
        )
    else:
        win_days, min_trades, min_sharpe, max_dd_lim, min_wr, min_pf = (
            _FULLY_AUTO_THRESHOLDS
        )

    fails: list[str] = []
    if window_days < win_days:
        fails.append(f"days={window_days} < {win_days}")
    if n_trades < min_trades:
        fails.append(f"trades={n_trades} < {min_trades}")
    if sharpe < min_sharpe:
        fails.append(f"sharpe={sharpe:.2f} < {min_sharpe}")
    if max_dd > max_dd_lim:
        fails.append(f"max_drawdown={max_dd:.2f}% > {max_dd_lim}%")
    if win_rate < min_wr:
        fails.append(f"win_rate={win_rate:.2%} < {min_wr:.0%}")
    if pf < min_pf:
        fails.append(f"profit_factor={pf:.2f} < {min_pf}")
    return fails


def compute_stats(
    pnl_pcts: list[float],
    *,
    span_days: int,
    now: datetime | None = None,
) -> GateSnapshot:
    """Compute the gate snapshot from a list of closed-trade pnl_pct values.

    Args:
        pnl_pcts: list of percentage P&L per closed trade (e.g. [2.5, -1.0, ...])
        span_days: actual window span in days (capped at the requested
            window_days; <= window_days when there isn't enough history).
        now: snapshot timestamp (defaults to UTC now).

    Returns a snapshot with both target-mode failure lists computed.
    """
    n = now or datetime.now(timezone.utc)
    n_trades = len(pnl_pcts)

    if n_trades == 0:
        snap = GateSnapshot(
            window_days=span_days, n_trades=0,
            sharpe=0.0, max_drawdown_pct=0.0, win_rate=0.0, profit_factor=0.0,
            computed_at=n,
            failures={
                "telegram-approve": _check_thresholds(
                    target="telegram-approve", window_days=span_days,
                    n_trades=0, sharpe=0, max_dd=0, win_rate=0, pf=0,
                ),
                "fully-auto": _check_thresholds(
                    target="fully-auto", window_days=span_days,
                    n_trades=0, sharpe=0, max_dd=0, win_rate=0, pf=0,
                ),
            },
        )
        return snap

    # Sharpe (per trade returns, annualized to the conventional 252-day TradFi).
    mean = sum(pnl_pcts) / n_trades
    variance = sum((p - mean) ** 2 for p in pnl_pcts) / n_trades
    std = math.sqrt(variance)
    if std == 0:
        sharpe = 0.0
    else:
        # Annualize: trades_per_year = n_trades / (span_days / 365)
        trades_per_year = n_trades / max(span_days / 365.0, 1.0 / 365.0)
        sharpe = (mean / std) * math.sqrt(trades_per_year)

    # MaxDD: max peak-to-trough drop in cumulative pnl.
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnl_pcts:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    # Win rate.
    wins = sum(1 for p in pnl_pcts if p > 0)
    win_rate = wins / n_trades

    # Profit factor.
    gross_win = sum(p for p in pnl_pcts if p > 0)
    gross_loss = -sum(p for p in pnl_pcts if p < 0)
    if gross_loss == 0:
        pf = float("inf") if gross_win > 0 else 0.0
    else:
        pf = gross_win / gross_loss

    snap = GateSnapshot(
        window_days=span_days, n_trades=n_trades,
        sharpe=round(sharpe, 4),
        max_drawdown_pct=round(max_dd, 4),
        win_rate=round(win_rate, 4),
        profit_factor=(
            round(pf, 4) if pf != float("inf") else float("inf")
        ),
        computed_at=n,
        failures={
            "telegram-approve": _check_thresholds(
                target="telegram-approve", window_days=span_days,
                n_trades=n_trades, sharpe=sharpe,
                max_dd=max_dd, win_rate=win_rate, pf=pf,
            ),
            "fully-auto": _check_thresholds(
                target="fully-auto", window_days=span_days,
                n_trades=n_trades, sharpe=sharpe,
                max_dd=max_dd, win_rate=win_rate, pf=pf,
            ),
        },
    )
    return snap


async def compute_gates_from_db(
    session: AsyncSession,
    *,
    window_days: int = 30,
    now: datetime | None = None,
    direction_filter: str | None = None,
    exclude_timeframes: list[str] | None = None,
) -> GateSnapshot:
    """Read closed shadow_trades from the rolling window and compute gates.

    Reads pnl_pct from shadow_trades where closed_at IS NOT NULL AND
    closed_at >= (now - window_days). When live_trades start landing
    in production, this gets extended to UNION with live_trades.

    direction_filter: when set (e.g. "LONG"), count only trades in that
    direction. Callers derive this from DISABLE_SHORT_SIGNALS so the gate
    only scores directions the live path is actually trading.

    exclude_timeframes: when set (e.g. ["15m"]), drop trades on those
    timeframes from the gate calculation. Callers derive this from
    SHADOW_15M_ELIGIBLE_FOR_PROMOTION (defect sweep 2026-08-06, TIER 3) —
    this parameter previously didn't exist here at all, so the flag only
    ever affected the /promotion-gate DASHBOARD DISPLAY
    (app/api/routes/bot_status.py), never the real auto-promotion decision
    in app/trading/auto_promote.py.
    """
    n = now or datetime.now(timezone.utc)
    cutoff = n - timedelta(days=window_days)

    sql = (
        "SELECT pnl_pct FROM shadow_trades "
        "WHERE closed_at IS NOT NULL "
        "  AND closed_at >= :cutoff "
        "  AND pnl_pct IS NOT NULL "
    )
    params: dict = {"cutoff": cutoff}
    if direction_filter is not None:
        sql += "  AND direction = :direction_filter "
        params["direction_filter"] = direction_filter
    if exclude_timeframes:
        # Parametrize each excluded TF (mirrors bot_status.py's
        # _select_trades_since) so any value flows through asyncpg
        # parameter-binding cleanly rather than string-interpolated.
        placeholders = []
        for i, tf in enumerate(exclude_timeframes):
            key = f"excl_tf_{i}"
            placeholders.append(f":{key}")
            params[key] = tf
        sql += f"  AND timeframe NOT IN ({', '.join(placeholders)}) "
    sql += "ORDER BY closed_at"

    rows = (await session.execute(sa.text(sql), params)).all()

    pnl_pcts = [float(r.pnl_pct) for r in rows]
    return compute_stats(pnl_pcts, span_days=window_days, now=n)


__all__ = [
    "GateName",
    "GateSnapshot",
    "TargetMode",
    "compute_gates_from_db",
    "compute_stats",
]
