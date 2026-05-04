import math
from dataclasses import dataclass
from datetime import datetime

_MIN_TRADES_FOR_SHARPE = 2


@dataclass(frozen=True)
class Trade:
    pnl_pct: float
    pnl_usdt: float
    risk_reward: float
    closed_at: datetime


def compute_win_rate(trades: list[Trade]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    return wins / len(trades)


def compute_profit_factor(trades: list[Trade]) -> float:
    if not trades:
        return 0.0
    gross_profit = sum(t.pnl_usdt for t in trades if t.pnl_usdt > 0)
    gross_loss = abs(sum(t.pnl_usdt for t in trades if t.pnl_usdt < 0))
    if gross_loss == 0:
        return float("inf")
    return gross_profit / gross_loss


def compute_avg_rr(trades: list[Trade]) -> float:
    if not trades:
        return 0.0
    return sum(t.risk_reward for t in trades) / len(trades)


def compute_sharpe_annualized(trades: list[Trade], window_days: int) -> float | None:
    """Sharpe ratio (annualized) on per-trade returns. Returns None if undefined."""
    if len(trades) < _MIN_TRADES_FOR_SHARPE or window_days <= 0:
        return None
    returns = [t.pnl_pct / 100.0 for t in trades]
    mean_return = sum(returns) / len(returns)
    var = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std == 0:
        return None
    trades_per_year = len(trades) * (365.0 / window_days)
    return (mean_return / std) * math.sqrt(trades_per_year)


def compute_max_drawdown(trades: list[Trade]) -> float:
    """Returns max drawdown as positive % (e.g., 0.08 for -8% peak-to-trough)."""
    if not trades:
        return 0.0
    sorted_trades = sorted(trades, key=lambda t: t.closed_at)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in sorted_trades:
        equity += t.pnl_usdt
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak
            max_dd = max(max_dd, dd)
    return max_dd
