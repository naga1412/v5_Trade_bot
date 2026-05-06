"""Unit tests for the backtest framework — Phase B1.

Synthetic OHLCV: 200 hourly bars of close = 100 + sin(i * 0.1) * 5 + i * 0.05.
That's a trending-up market with mild oscillation — enough to produce a
non-zero number of trades for the L1+L3+L5 path.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from tools.backtest import BacktestResult, run_backtest


def _synthetic_bars(n: int = 200) -> pd.DataFrame:
    base_ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex(
        [base_ts + timedelta(hours=i) for i in range(n)],
        name="ts",
    )
    closes = np.array([100 + np.sin(i * 0.1) * 5 + i * 0.05 for i in range(n)])
    return pd.DataFrame(
        {
            "open": closes - 0.1,
            "high": closes + 0.5,
            "low": closes - 0.5,
            "close": closes,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def test_backtest_result_dataclass_shape() -> None:
    """The result dataclass exposes all metrics the spec promises."""
    fields = {
        "n_trades", "win_rate", "profit_factor", "sharpe", "max_drawdown",
        "equity_curve", "trade_log", "params_hash", "initial_balance",
        "final_balance",
    }
    assert fields.issubset(BacktestResult.__dataclass_fields__.keys())


def test_run_backtest_returns_result_with_zero_trades_on_flat_data() -> None:
    """A flat-line market (no signals) returns 0 trades + initial balance."""
    flat = _synthetic_bars(50).assign(close=100.0, open=100.0, high=100.0, low=100.0)
    bars_loader = lambda *_a, **_kw: flat  # noqa: E731 — test stub
    result = run_backtest(
        symbol="BTC/USDT",
        timeframe="1h",
        start=flat.index[0].to_pydatetime(),
        end=flat.index[-1].to_pydatetime(),
        initial_balance_usdt=10000.0,
        _bars_loader=bars_loader,
    )
    assert isinstance(result, BacktestResult)
    assert result.n_trades == 0
    assert result.final_balance == 10000.0
    assert result.equity_curve[0] == (flat.index[0].to_pydatetime(), 10000.0)


def test_run_backtest_params_hash_is_deterministic() -> None:
    """Same inputs → same params_hash. Different inputs → different hash."""
    flat = _synthetic_bars(50)
    bars_loader = lambda *_a, **_kw: flat  # noqa: E731

    a = run_backtest(
        symbol="BTC/USDT", timeframe="1h",
        start=flat.index[0].to_pydatetime(),
        end=flat.index[-1].to_pydatetime(),
        initial_balance_usdt=10000.0,
        _bars_loader=bars_loader,
    )
    b = run_backtest(
        symbol="BTC/USDT", timeframe="1h",
        start=flat.index[0].to_pydatetime(),
        end=flat.index[-1].to_pydatetime(),
        initial_balance_usdt=10000.0,
        _bars_loader=bars_loader,
    )
    c = run_backtest(
        symbol="ETH/USDT", timeframe="1h",
        start=flat.index[0].to_pydatetime(),
        end=flat.index[-1].to_pydatetime(),
        initial_balance_usdt=10000.0,
        _bars_loader=bars_loader,
    )
    assert a.params_hash == b.params_hash
    assert a.params_hash != c.params_hash
