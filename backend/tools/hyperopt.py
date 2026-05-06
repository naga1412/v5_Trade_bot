"""Optuna-driven hyperopt for layer weights. Phase C2 implementation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import optuna

from tools.backtest import BacktestResult


@dataclass
class HyperoptResult:
    """Result of an Optuna hyperopt run over layer weights L1-L9.

    ``best_weights`` is keyed by layer index (1..9) and is normalized so
    the values sum to 1.0. ``best_sharpe`` is the validation-window Sharpe
    achieved by ``best_weights``. ``study`` is the live Optuna study —
    not serializable; for in-process inspection only.
    """

    best_weights: dict[int, float]
    best_sharpe: float
    n_trials: int
    study: optuna.Study | None = None


BacktestRunner = Callable[..., BacktestResult]


def hyperopt_layer_weights(
    *,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    train_window: tuple[datetime, datetime],
    val_window: tuple[datetime, datetime],
    n_trials: int = 100,
    _bars_loader: Any = None,
    _backtest_runner: BacktestRunner | None = None,
    seed: int = 42,
) -> HyperoptResult:  # pragma: no cover — stub
    raise NotImplementedError("hyperopt_layer_weights: Phase C2 deliverable")
