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


def test_run_backtest_simulates_trades_on_signaling_data() -> None:
    """A clearly-trending market produces at least one trade."""
    bars = _synthetic_bars(300)
    bars_loader = lambda *_a, **_kw: bars  # noqa: E731

    result = run_backtest(
        symbol="BTC/USDT", timeframe="1h",
        start=bars.index[0].to_pydatetime(),
        end=bars.index[-1].to_pydatetime(),
        initial_balance_usdt=10000.0,
        _bars_loader=bars_loader,
    )
    assert result.n_trades > 0
    # Equity curve has at least one entry per closed trade (+ initial point)
    assert len(result.equity_curve) >= result.n_trades
    assert -1.0 <= result.win_rate <= 1.0
    assert result.max_drawdown >= 0.0
    # Final balance is initial +/- aggregate trade pnl
    expected_final = (
        result.initial_balance + sum(t.pnl_usdt for t in result.trade_log)
    )
    assert abs(result.final_balance - expected_final) < 1e-6


def test_run_backtest_respects_layer_weights() -> None:
    """Different weights produce different params_hash + potentially different metrics."""
    bars = _synthetic_bars(300)
    bars_loader = lambda *_a, **_kw: bars  # noqa: E731

    equal = run_backtest(
        symbol="BTC/USDT", timeframe="1h",
        start=bars.index[0].to_pydatetime(),
        end=bars.index[-1].to_pydatetime(),
        layer_weights={i: 1 / 9 for i in range(1, 10)},
        _bars_loader=bars_loader,
    )
    l3_heavy = run_backtest(
        symbol="BTC/USDT", timeframe="1h",
        start=bars.index[0].to_pydatetime(),
        end=bars.index[-1].to_pydatetime(),
        layer_weights={
            3: 1.0, 1: 0.0, 2: 0.0, 4: 0.0, 5: 0.0,
            6: 0.0, 7: 0.0, 8: 0.0, 9: 0.0,
        },
        _bars_loader=bars_loader,
    )
    assert equal.params_hash != l3_heavy.params_hash
    # At least one of the metrics should differ between the two runs
    assert (equal.n_trades, equal.sharpe, equal.final_balance) != (
        l3_heavy.n_trades, l3_heavy.sharpe, l3_heavy.final_balance,
    )


def test_run_backtest_short_trades_close_on_sl_or_tp() -> None:
    """A descending market should produce exits with SL/TP/TIMEOUT."""
    n = 300
    base_ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([base_ts + timedelta(hours=i) for i in range(n)])
    closes = np.array([100 - i * 0.1 for i in range(n)])  # monotonic down
    bars = pd.DataFrame({
        "open": closes - 0.05, "high": closes + 0.3, "low": closes - 0.3,
        "close": closes, "volume": np.full(n, 1000.0),
    }, index=idx)
    bars_loader = lambda *_a, **_kw: bars  # noqa: E731

    result = run_backtest(
        symbol="BTC/USDT", timeframe="1h",
        start=bars.index[0].to_pydatetime(),
        end=bars.index[-1].to_pydatetime(),
        _bars_loader=bars_loader,
    )
    # All exits should be SL/TP/TIMEOUT — never None
    assert all(t.exit_reason in {"SL", "TP", "TIMEOUT"} for t in result.trade_log)


def test_run_backtest_metrics_sane_bounds() -> None:
    """Sharpe finite, max_drawdown in [0,1], profit_factor >= 0 or +inf."""
    import math

    bars = _synthetic_bars(300)
    result = run_backtest(
        symbol="BTC/USDT", timeframe="1h",
        start=bars.index[0].to_pydatetime(),
        end=bars.index[-1].to_pydatetime(),
        _bars_loader=lambda *_a, **_kw: bars,
    )
    assert np.isfinite(result.sharpe)
    assert 0.0 <= result.max_drawdown <= 1.0
    assert result.profit_factor >= 0.0 or math.isinf(result.profit_factor)
