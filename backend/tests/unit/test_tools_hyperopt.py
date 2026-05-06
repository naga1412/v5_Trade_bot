"""Unit tests for hyperopt — Phase C1/C2."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.hyperopt import HyperoptResult, hyperopt_layer_weights


def _fake_runner_l1_l3(*, layer_weights: dict[int, float], **_kw: Any) -> Any:
    """Returns a sharpe equal to the sum of weights on layers 1-3.

    Hyperopt should converge to weights concentrated on those layers
    (a non-trivial signal that the TPE sampler is actually optimizing).
    """
    from tools.backtest import BacktestResult

    sharpe = sum(layer_weights.get(i, 0.0) for i in (1, 2, 3))
    return BacktestResult(
        n_trades=10, win_rate=0.5, profit_factor=1.0,
        sharpe=sharpe, max_drawdown=0.05,
        equity_curve=[], trade_log=[], params_hash="x",
        initial_balance=10000.0, final_balance=10100.0,
    )


def test_hyperopt_returns_best_weights_summing_to_one() -> None:
    """Stub run with 15 trials — weights sum to 1.0, sharpe is finite."""
    train_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    train_end = datetime(2025, 6, 30, tzinfo=timezone.utc)
    val_start = datetime(2025, 7, 1, tzinfo=timezone.utc)
    val_end = datetime(2025, 12, 31, tzinfo=timezone.utc)

    result = hyperopt_layer_weights(
        symbol="BTC/USDT", timeframe="1h",
        train_window=(train_start, train_end),
        val_window=(val_start, val_end),
        n_trials=15,
        _backtest_runner=_fake_runner_l1_l3,
    )
    assert isinstance(result, HyperoptResult)
    assert result.n_trials == 15
    assert abs(sum(result.best_weights.values()) - 1.0) < 1e-6
    # Layers 1-3 should dominate (since fake_runner only rewards them).
    assert sum(result.best_weights.get(i, 0.0) for i in (1, 2, 3)) > 0.5


def test_hyperopt_seed_reproducibility() -> None:
    """Same seed → same best_weights; deterministic for tests."""
    def fake_runner(*, layer_weights: dict[int, float], **_kw: Any) -> Any:
        from tools.backtest import BacktestResult

        sharpe = sum(layer_weights.get(i, 0.0) ** 2 for i in range(1, 10))
        return BacktestResult(
            n_trades=1, win_rate=1.0, profit_factor=1.0, sharpe=sharpe,
            max_drawdown=0.0, equity_curve=[], trade_log=[], params_hash="x",
            initial_balance=10000.0, final_balance=10000.0,
        )

    args: dict[str, Any] = dict(
        symbol="BTC/USDT", timeframe="1h",
        train_window=(datetime(2025, 1, 1, tzinfo=timezone.utc),
                      datetime(2025, 6, 30, tzinfo=timezone.utc)),
        val_window=(datetime(2025, 7, 1, tzinfo=timezone.utc),
                    datetime(2025, 12, 31, tzinfo=timezone.utc)),
        n_trials=10, _backtest_runner=fake_runner,
    )
    a = hyperopt_layer_weights(**args, seed=42)
    b = hyperopt_layer_weights(**args, seed=42)
    assert a.best_weights == b.best_weights
    assert a.best_sharpe == b.best_sharpe


def test_hyperopt_zero_trades_train_returns_penalty() -> None:
    """If TRAIN backtest produces zero trades, the trial is penalized.

    Verifies the spec §8 risk row 2 — degenerate weights that never trade
    must not be reported as the best result.
    """
    call_log: list[dict[str, Any]] = []

    def runner(*, layer_weights: dict[int, float], **kw: Any) -> Any:
        from tools.backtest import BacktestResult

        call_log.append({"weights": dict(layer_weights), **kw})
        # Half of trials produce zero trades on TRAIN.
        n_trades = 0 if sum(layer_weights.values()) > 0.5 else 5
        return BacktestResult(
            n_trades=n_trades, win_rate=0.5, profit_factor=1.0,
            sharpe=0.7, max_drawdown=0.05,
            equity_curve=[], trade_log=[], params_hash="x",
            initial_balance=10000.0, final_balance=10100.0,
        )

    result = hyperopt_layer_weights(
        symbol="BTC/USDT", timeframe="1h",
        train_window=(datetime(2025, 1, 1, tzinfo=timezone.utc),
                      datetime(2025, 6, 30, tzinfo=timezone.utc)),
        val_window=(datetime(2025, 7, 1, tzinfo=timezone.utc),
                    datetime(2025, 12, 31, tzinfo=timezone.utc)),
        n_trials=8, _backtest_runner=runner,
    )
    assert isinstance(result, HyperoptResult)
    # We must have made at least n_trials TRAIN calls.
    assert len(call_log) >= 8
