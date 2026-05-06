"""Deterministic backtest runner over trading-radar predictions.

Pulls OHLCV from Postgres (or a test-injectable loader), iterates bar-by-bar,
calls build_prediction() with the configured layer weights, simulates trades
at the predicted entry with SL/TP/timeout exits, and aggregates metrics.

Phase B2 ships the dataclass + a skeleton that returns 0 trades. Phase B3
fills in build_prediction integration + SL/TP simulator. Phase B4 persists
to the backtests table. Phase B5 wires the admin REST endpoint.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pandas as pd

log = logging.getLogger(__name__)

BarsLoader = Callable[[str, str, datetime, datetime], pd.DataFrame]


@dataclass(frozen=True)
class TradeRecord:
    opened_at: datetime
    closed_at: datetime
    side: str  # "LONG" | "SHORT"
    entry_price: float
    exit_price: float
    pnl_usdt: float
    pnl_pct: float
    exit_reason: str  # "SL" | "TP" | "TIMEOUT"


@dataclass
class BacktestResult:
    n_trades: int
    win_rate: float
    profit_factor: float
    sharpe: float
    max_drawdown: float
    equity_curve: list[tuple[datetime, float]]
    trade_log: list[TradeRecord]
    params_hash: str
    initial_balance: float
    final_balance: float
    # Provenance:
    symbol: str = ""
    timeframe: str = ""
    start_ts: datetime | None = None
    end_ts: datetime | None = None
    layer_weights: dict[int, float] | None = None
    enabled_layers: set[int] | None = None
    enabled_traps: set[str] | None = None


def _compute_params_hash(
    *,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    layer_weights: dict[int, float] | None,
    enabled_layers: set[int] | None,
    enabled_traps: set[str] | None,
    initial_balance_usdt: float,
) -> str:
    payload = {
        "symbol": symbol,
        "timeframe": timeframe,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "layer_weights": (
            sorted(layer_weights.items()) if layer_weights else None
        ),
        "enabled_layers": (
            sorted(enabled_layers) if enabled_layers else None
        ),
        "enabled_traps": (
            sorted(enabled_traps) if enabled_traps else None
        ),
        "initial_balance_usdt": initial_balance_usdt,
    }
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


def run_backtest(
    *,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    start: datetime,
    end: datetime,
    layer_weights: dict[int, float] | None = None,
    enabled_layers: set[int] | None = None,
    enabled_traps: set[str] | None = None,
    initial_balance_usdt: float = 10000.0,
    _bars_loader: BarsLoader | None = None,
) -> BacktestResult:
    """Run a deterministic backtest. See module docstring."""
    params_hash = _compute_params_hash(
        symbol=symbol, timeframe=timeframe, start=start, end=end,
        layer_weights=layer_weights, enabled_layers=enabled_layers,
        enabled_traps=enabled_traps,
        initial_balance_usdt=initial_balance_usdt,
    )

    loader: BarsLoader = _bars_loader if _bars_loader is not None else _default_bars_loader

    bars = loader(symbol, timeframe, start, end)

    # Phase B2 skeleton: no trade simulation yet. Returns initial-balance
    # equity curve + zero trades. Phase B3 fills this in.
    equity_curve: list[tuple[datetime, float]] = []
    if len(bars) > 0:
        first_idx = bars.index[0]
        ts = first_idx.to_pydatetime() if hasattr(first_idx, "to_pydatetime") else first_idx
        equity_curve.append((ts, initial_balance_usdt))

    return BacktestResult(
        n_trades=0,
        win_rate=0.0,
        profit_factor=0.0,
        sharpe=0.0,
        max_drawdown=0.0,
        equity_curve=equity_curve,
        trade_log=[],
        params_hash=params_hash,
        initial_balance=initial_balance_usdt,
        final_balance=initial_balance_usdt,
        symbol=symbol,
        timeframe=timeframe,
        start_ts=start,
        end_ts=end,
        layer_weights=layer_weights,
        enabled_layers=enabled_layers,
        enabled_traps=enabled_traps,
    )


def _default_bars_loader(
    symbol: str, timeframe: str, start: datetime, end: datetime,
) -> pd.DataFrame:
    """Phase B3 deliverable — pull from `ohlcv` table via session_factory."""
    raise NotImplementedError(
        "_default_bars_loader: Phase B3 deliverable — inject _bars_loader for tests"
    )
