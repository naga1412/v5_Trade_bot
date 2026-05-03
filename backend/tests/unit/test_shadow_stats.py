from datetime import UTC, datetime

import pytest
from app.shadow.stats import (
    Trade,
    compute_avg_rr,
    compute_max_drawdown,
    compute_profit_factor,
    compute_sharpe_annualized,
    compute_win_rate,
)


def make_trade(pnl_pct: float, pnl_usdt: float | None = None,
               rr: float = 2.0, ts: datetime | None = None) -> Trade:
    if pnl_usdt is None:
        pnl_usdt = 30.0 * pnl_pct / 100.0
    if ts is None:
        ts = datetime(2026, 5, 1, tzinfo=UTC)
    return Trade(pnl_pct=pnl_pct, pnl_usdt=pnl_usdt, risk_reward=rr, closed_at=ts)


def test_win_rate_basic() -> None:
    trades = [make_trade(2.0), make_trade(-1.5), make_trade(3.0), make_trade(-1.0)]
    assert compute_win_rate(trades) == pytest.approx(0.5)


def test_win_rate_empty_returns_zero() -> None:
    assert compute_win_rate([]) == 0.0


def test_profit_factor_basic() -> None:
    trades = [make_trade(2.0, 0.6), make_trade(3.0, 0.9), make_trade(-1.0, -0.3)]
    # gross profit = 0.6 + 0.9 = 1.5; gross loss = 0.3
    assert compute_profit_factor(trades) == pytest.approx(5.0)


def test_profit_factor_no_losses_returns_inf() -> None:
    trades = [make_trade(2.0, 0.6)]
    assert compute_profit_factor(trades) == float("inf")


def test_profit_factor_empty_returns_zero() -> None:
    assert compute_profit_factor([]) == 0.0


def test_avg_rr_basic() -> None:
    trades = [make_trade(0, rr=2.0), make_trade(0, rr=1.5), make_trade(0, rr=2.5)]
    assert compute_avg_rr(trades) == pytest.approx(2.0)


def test_sharpe_undefined_when_one_trade() -> None:
    assert compute_sharpe_annualized([make_trade(2.0)], window_days=30) is None


def test_sharpe_undefined_when_all_returns_identical() -> None:
    trades = [make_trade(1.0) for _ in range(5)]
    assert compute_sharpe_annualized(trades, window_days=30) is None


def test_sharpe_basic_positive() -> None:
    # 4 trades over 30 days, mean +1.5%, some variance — should give positive Sharpe
    trades = [
        make_trade(2.0), make_trade(1.0), make_trade(2.5), make_trade(0.5),
    ]
    sharpe = compute_sharpe_annualized(trades, window_days=30)
    assert sharpe is not None
    assert sharpe > 0


def test_max_drawdown_basic() -> None:
    trades = [
        make_trade(2.0, pnl_usdt=10, ts=datetime(2026, 5, 1, tzinfo=UTC)),
        make_trade(3.0, pnl_usdt=15, ts=datetime(2026, 5, 2, tzinfo=UTC)),
        make_trade(-4.0, pnl_usdt=-12, ts=datetime(2026, 5, 3, tzinfo=UTC)),
        make_trade(-1.0, pnl_usdt=-3, ts=datetime(2026, 5, 4, tzinfo=UTC)),
    ]
    # equity: 10, 25, 13, 10. peak = 25. trough after = 10. dd = (25-10)/25 = 0.6
    dd = compute_max_drawdown(trades)
    assert dd == pytest.approx(0.6)


def test_max_drawdown_empty() -> None:
    assert compute_max_drawdown([]) == 0.0
