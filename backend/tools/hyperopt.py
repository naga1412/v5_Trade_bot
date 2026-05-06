"""Optuna-driven hyperopt for layer weights (SP-7 Phase C).

Searches for the L1-L9 weight vector that maximizes the validation-window
Sharpe of the SP-7 backtester. L10 is the brain placeholder and is never
tuned by hyperopt (per the production aggregator contract).

Search space: nine independent ``trial.suggest_float(f"w{i}", 0.0, 0.3)``
calls. After sampling, the weights are normalized to sum=1.0 so the
aggregator semantics stay stable across trials. Each trial runs TWO
``run_backtest`` calls — one on TRAIN (to discard degenerate strategies
that never trade), one on VAL (the optimization objective).

Sampler: ``TPESampler(seed=...)``. Reproducible across runs given the
same seed and the same backtest runner.

CLI entry point::

    python -m tools.hyperopt --symbol BTC/USDT --tf 1h \\
        --train-start 2025-01-01T00:00:00Z --train-end 2025-06-30T00:00:00Z \\
        --val-start   2025-07-01T00:00:00Z --val-end   2025-12-31T00:00:00Z \\
        --n-trials 100
"""
from __future__ import annotations

import argparse
import json as _json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np
import optuna
import sqlalchemy as sa
from optuna.samplers import TPESampler
from sqlalchemy.ext.asyncio import AsyncSession

from tools.backtest import BacktestResult, run_backtest as _real_run_backtest

log = logging.getLogger(__name__)

# Suppress Optuna's default per-trial INFO log spam — the test suite asserts
# clean stderr and the CLI surfaces its own summary on completion.
optuna.logging.set_verbosity(optuna.logging.WARNING)


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
) -> HyperoptResult:
    """Search for layer weights maximizing val-set Sharpe.

    Args:
        symbol: ticker (e.g. "BTC/USDT").
        timeframe: OHLCV timeframe (e.g. "1h").
        train_window: (start, end) — used to filter degenerate strategies
            that never trade. The objective itself is the val Sharpe.
        val_window: (start, end) — Sharpe on this window IS the objective.
        n_trials: number of Optuna trials. CLI default 100; test default 3-15.
        _bars_loader: forwarded to the backtest runner; tests inject a fake.
        _backtest_runner: forwarded; tests inject a fake to bypass real
            ``run_backtest`` (which needs ~200 warmup bars + a real
            indicator pipeline).
        seed: fixes the TPE RNG for reproducibility in tests.

    Returns:
        HyperoptResult with best_weights (dict[int, float] summing to 1.0),
        best_sharpe, n_trials, and the live study.
    """
    runner: BacktestRunner = (
        _backtest_runner if _backtest_runner is not None else _real_run_backtest
    )

    def _objective(trial: optuna.Trial) -> float:
        raw = {i: trial.suggest_float(f"w{i}", 0.0, 0.3) for i in range(1, 10)}
        total = sum(raw.values())
        if total <= 0:
            return -1e9  # degenerate sample — discourage hard.
        weights = {k: v / total for k, v in raw.items()}

        # Sanity-check pass on TRAIN — abort if the strategy never trades.
        train_result = runner(
            symbol=symbol, timeframe=timeframe,
            start=train_window[0], end=train_window[1],
            layer_weights=weights, _bars_loader=_bars_loader,
        )
        if train_result.n_trades == 0:
            return -1e6

        val_result = runner(
            symbol=symbol, timeframe=timeframe,
            start=val_window[0], end=val_window[1],
            layer_weights=weights, _bars_loader=_bars_loader,
        )
        # Penalize tiny weight stddev (avoid all-on-one-layer per spec §8).
        stddev = float(np.std(list(weights.values())))
        regularization = 0.0 if stddev > 0.01 else -0.5
        return float(val_result.sharpe + regularization)

    study = optuna.create_study(
        direction="maximize", sampler=TPESampler(seed=seed),
    )
    study.optimize(_objective, n_trials=n_trials, show_progress_bar=False)

    best_raw = study.best_params  # dict like {"w1": 0.12, ...}
    best_dict = {int(k.removeprefix("w")): v for k, v in best_raw.items()}
    total = sum(best_dict.values())
    if total > 0:
        best_normalized = {k: v / total for k, v in best_dict.items()}
    else:
        best_normalized = {i: 1 / 9 for i in range(1, 10)}

    log.info(
        "hyperopt complete: best_sharpe=%.4f best_weights=%s",
        study.best_value, best_normalized,
    )
    return HyperoptResult(
        best_weights=best_normalized,
        best_sharpe=float(study.best_value),
        n_trials=n_trials,
        study=study,
    )


async def persist_hyperopt_result(
    session: AsyncSession,
    *,
    result: HyperoptResult,
    symbol: str,
    timeframe: str,
    train_window: tuple[datetime, datetime],
    val_window: tuple[datetime, datetime],
    triggered_by_user_id: int | None = None,
    mlflow_run_id: str | None = None,
) -> int:
    """Insert HyperoptResult row into ``hyperopt_studies``; returns new id.

    Postgres ``TSTZRANGE`` columns accept the canonical literal
    ``[2025-01-01T00:00:00+00:00,2025-06-30T00:00:00+00:00)``. SQLite
    tests accept the same string as TEXT (no schema enforcement). Caller
    commits — this lets the helper compose with larger transactions.
    """
    train_range = (
        f"[{train_window[0].isoformat()},{train_window[1].isoformat()})"
    )
    val_range = (
        f"[{val_window[0].isoformat()},{val_window[1].isoformat()})"
    )
    weights_json = _json.dumps(
        {str(k): v for k, v in result.best_weights.items()}
    )
    insert_sql = sa.text(
        "INSERT INTO hyperopt_studies "
        "(triggered_by, n_trials, train_window, val_window, "
        "symbol, timeframe, best_weights, best_sharpe, mlflow_run_id, "
        "status, completed_at) "
        "VALUES (:tb, :n, :tw, :vw, :sym, :tf, :bw, :bs, :ml, "
        "'completed', :now)"
    )
    await session.execute(insert_sql, {
        "tb": triggered_by_user_id,
        "n": result.n_trials,
        "tw": train_range, "vw": val_range,
        "sym": symbol, "tf": timeframe,
        "bw": weights_json,
        "bs": result.best_sharpe,
        "ml": mlflow_run_id,
        "now": datetime.now(timezone.utc),
    })
    # Read the new id back without RETURNING — the in-memory aiosqlite
    # path used by tests doesn't always emit RETURNING cleanly across
    # SQLAlchemy versions. Match on the recently-inserted columns.
    row = (await session.execute(sa.text(
        "SELECT id FROM hyperopt_studies "
        "WHERE symbol = :sym AND timeframe = :tf AND best_sharpe = :bs "
        "ORDER BY id DESC LIMIT 1"
    ), {"sym": symbol, "tf": timeframe, "bs": result.best_sharpe})).first()
    if row is None:  # pragma: no cover — should never happen post-INSERT
        raise RuntimeError(
            "persist_hyperopt_result: row not visible after insert"
        )
    return int(row.id)


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 string with optional ``Z`` suffix into a UTC datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _cli_main(argv: list[str] | None = None) -> int:  # pragma: no cover — CLI
    """Command-line entry point: ``python -m tools.hyperopt --help``."""
    parser = argparse.ArgumentParser(
        description="Run Optuna hyperopt over SP-7 layer weights.",
    )
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--tf", default="1h")
    parser.add_argument("--train-start", required=True, type=_parse_iso)
    parser.add_argument("--train-end", required=True, type=_parse_iso)
    parser.add_argument("--val-start", required=True, type=_parse_iso)
    parser.add_argument("--val-end", required=True, type=_parse_iso)
    parser.add_argument("--n-trials", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    result = hyperopt_layer_weights(
        symbol=args.symbol, timeframe=args.tf,
        train_window=(args.train_start, args.train_end),
        val_window=(args.val_start, args.val_end),
        n_trials=args.n_trials, seed=args.seed,
    )
    log.info("best_sharpe=%.4f", result.best_sharpe)
    log.info("best_weights=%s", _json.dumps(
        {str(k): round(v, 4) for k, v in result.best_weights.items()},
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI
    raise SystemExit(_cli_main())
