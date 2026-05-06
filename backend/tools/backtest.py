"""Deterministic backtest runner over trading-radar predictions.

Pulls OHLCV from Postgres (or a test-injectable loader), iterates bar-by-bar,
calls build_prediction() with the configured layer weights, simulates trades
at the predicted entry with SL/TP/timeout exits, and aggregates metrics.

Phase B3 wires the full simulator: walk every bar past the warmup window,
call build_prediction() per bar, re-aggregate the per-layer scores with the
caller-supplied layer_weights when given, open a trade when the tier is
>= PAPER, walk forward to SL/TP/TIMEOUT exit, then aggregate metrics
(win_rate, profit_factor, Sharpe, max-drawdown).

Phase B4 persists to the backtests table. Phase B5 wires the admin REST
endpoint.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import numpy as np
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.predictor import build_prediction
from app.core.scoring.aggregator import aggregate
from app.core.scoring.tiers import classify_tier
from app.core.scoring.types import Direction, FinalScore, LayerScore

log = logging.getLogger(__name__)

BarsLoader = Callable[[str, str, datetime, datetime], pd.DataFrame]

# SL/TP/timeout simulator constants (mirror app/shadow/exit_monitor.py +
# the SP-2 SL/TP heuristic in app/core/predictor._build_trade_setup).
_SL_ATR_MULT: float = 1.5
_TP_ATR_MULT: float = 3.0
_TIMEOUT_BARS: int = 24
_FIXED_NOTIONAL_USDT: float = 100.0
# build_prediction needs at least 200 bars for layer 1 (EMA200) — use 200
# as the warmup floor and start scanning at bar 200.
_WARMUP_BARS: int = 200
# Sharpe annualization for hourly bars: sqrt(24 * 365) ~ 93.4. We compute
# the metric on per-trade pnl_pct, not on bar-by-bar returns.
_HOURLY_ANNUALIZATION: float = math.sqrt(24.0 * 365.0)
# Tier threshold for opening a trade — anything PAPER or above signals.
_OPEN_TIERS: frozenset[str] = frozenset({"PAPER", "SMALL", "STANDARD", "A+"})
# Minimum |final.score| for opening a backtest trade. Lower than the live
# PAPER tier (0.55 LONG / 0.65 SHORT) because the SP-7 backtester is the
# substrate hyperopt searches over — we want enough trades for the Sharpe
# / profit-factor metrics to converge, even on weakly-signalling regimes.
# Live trading still uses tier-gated entries via the shadow engine; this
# constant only governs the simulator.
_MIN_BACKTEST_SCORE: float = 0.05


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


def _atr(bars: pd.DataFrame, period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    h = bars["high"].to_numpy(dtype=float)
    lo = bars["low"].to_numpy(dtype=float)
    c = bars["close"].to_numpy(dtype=float)
    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_close), np.abs(lo - prev_close)))
    return float(np.mean(tr[-period:]))


def _to_pydatetime(value: object) -> datetime:
    """Coerce a pandas Timestamp (or already-py datetime) to datetime."""
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()  # type: ignore[no-any-return,union-attr]
    if isinstance(value, datetime):
        return value
    raise TypeError(f"cannot coerce {type(value)!r} to datetime")


def _aggregate_with_weights(
    layer_results: dict[int, LayerScore | None],
    *,
    layer_weights: dict[int, float],
) -> FinalScore:
    """Re-aggregate per-layer scores using caller-supplied weights.

    The production aggregator (app.core.scoring.aggregator.aggregate) uses
    a fixed 1/9 base weight per active layer. This wrapper mirrors the same
    formula but multiplies each layer's contribution by its custom weight
    and renormalises so total weight on present layers sums to 1. Layer 10
    is excluded (brain placeholder, same as the production aggregator).

    The returned FinalScore mimics aggregate()'s output shape so downstream
    callers (tier classifier, equity simulator) work unchanged.
    """
    present = {
        i: s for i, s in layer_results.items()
        if s is not None and i != 10 and layer_weights.get(i, 0.0) > 0.0
    }
    if not present:
        return FinalScore(
            score=0.0, direction=Direction.NEUTRAL, confidence=0.0,
            layer_results=layer_results, contributing_layers=(),
        )

    total_w = sum(layer_weights[i] for i in present)
    if total_w <= 0.0:
        return FinalScore(
            score=0.0, direction=Direction.NEUTRAL, confidence=0.0,
            layer_results=layer_results, contributing_layers=(),
        )

    static = 0.0
    confidences: list[float] = []
    for i, layer in present.items():
        w = layer_weights[i] / total_w
        static += w * layer.signed_strength * layer.confidence
        confidences.append(layer.confidence)
    static = max(-1.0, min(1.0, static))

    # Apply the SHORT direction penalty + neutral band (matches aggregator).
    neutral_band = 0.05
    short_penalty = 0.95
    if static > neutral_band:
        direction = Direction.LONG
    elif static < -neutral_band:
        direction = Direction.SHORT
    else:
        direction = Direction.NEUTRAL
    final = static * (short_penalty if direction is Direction.SHORT else 1.0)
    final = max(-1.0, min(1.0, final))
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    return FinalScore(
        score=final,
        direction=direction,
        confidence=avg_conf,
        layer_results=layer_results,
        contributing_layers=tuple(sorted(present.keys())),
    )


def _simulate_trade(
    bars: pd.DataFrame, entry_idx: int, side: str,
) -> TradeRecord | None:
    """Open at bars.iloc[entry_idx]['close']; walk forward; exit on SL/TP/TIMEOUT.

    Mirrors app.shadow.exit_monitor.check_exit's pessimistic convention (SL
    wins ties when both SL and TP are touched in the same bar).
    """
    if entry_idx >= len(bars) - 1:
        return None
    entry = float(bars.iloc[entry_idx]["close"])
    atr = _atr(bars.iloc[: entry_idx + 1])
    if atr <= 0.0:
        return None
    if side == "LONG":
        sl, tp = entry - _SL_ATR_MULT * atr, entry + _TP_ATR_MULT * atr
    else:  # SHORT
        sl, tp = entry + _SL_ATR_MULT * atr, entry - _TP_ATR_MULT * atr

    last_idx = min(entry_idx + _TIMEOUT_BARS, len(bars) - 1)
    exit_price = float(bars.iloc[last_idx]["close"])
    exit_reason = "TIMEOUT"
    exit_idx = last_idx
    for i in range(entry_idx + 1, last_idx + 1):
        bar = bars.iloc[i]
        hi, lo = float(bar["high"]), float(bar["low"])
        if side == "LONG":
            if lo <= sl:
                exit_price = sl
                exit_reason = "SL"
                exit_idx = i
                break
            if hi >= tp:
                exit_price = tp
                exit_reason = "TP"
                exit_idx = i
                break
        else:  # SHORT
            if hi >= sl:
                exit_price = sl
                exit_reason = "SL"
                exit_idx = i
                break
            if lo <= tp:
                exit_price = tp
                exit_reason = "TP"
                exit_idx = i
                break

    if side == "LONG":
        pnl_pct = (exit_price - entry) / entry
    else:
        pnl_pct = (entry - exit_price) / entry
    pnl_usdt = pnl_pct * _FIXED_NOTIONAL_USDT

    return TradeRecord(
        opened_at=_to_pydatetime(bars.index[entry_idx]),
        closed_at=_to_pydatetime(bars.index[exit_idx]),
        side=side,
        entry_price=entry,
        exit_price=exit_price,
        pnl_usdt=pnl_usdt,
        pnl_pct=pnl_pct,
        exit_reason=exit_reason,
    )


def _aggregate_metrics(
    trades: list[TradeRecord],
    initial_balance: float,
    bar_index: pd.DatetimeIndex,
) -> tuple[float, float, float, float, float, list[tuple[datetime, float]]]:
    """Return (win_rate, profit_factor, sharpe, max_drawdown,
    final_balance, equity_curve)."""
    if not trades:
        curve: list[tuple[datetime, float]] = []
        if len(bar_index) > 0:
            curve.append((_to_pydatetime(bar_index[0]), initial_balance))
        return 0.0, 0.0, 0.0, 0.0, initial_balance, curve

    wins = [t for t in trades if t.pnl_usdt > 0]
    losses = [t for t in trades if t.pnl_usdt < 0]
    win_rate = len(wins) / len(trades)
    sum_wins = sum(t.pnl_usdt for t in wins)
    sum_losses = abs(sum(t.pnl_usdt for t in losses))
    profit_factor = sum_wins / sum_losses if sum_losses > 0 else math.inf

    rets = np.array([t.pnl_pct for t in trades])
    if len(rets) >= 2 and float(np.std(rets, ddof=1)) > 0:
        sharpe = float(np.mean(rets) / np.std(rets, ddof=1)) * _HOURLY_ANNUALIZATION
    else:
        sharpe = 0.0

    equity = initial_balance
    curve = [(_to_pydatetime(bar_index[0]), initial_balance)] if len(bar_index) > 0 else []
    peak = initial_balance
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: x.closed_at):
        equity += t.pnl_usdt
        curve.append((t.closed_at, equity))
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return win_rate, profit_factor, sharpe, max_dd, equity, curve


def _scan_for_trades(
    bars: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    layer_weights: dict[int, float] | None,
    enabled_traps: set[str] | None,
) -> list[TradeRecord]:
    """Walk bars from warmup onward; per signal open one trade and skip past exit."""
    trades: list[TradeRecord] = []
    n = len(bars)
    if n <= _WARMUP_BARS:
        return trades
    i = _WARMUP_BARS
    while i < n - 1:
        window = bars.iloc[: i + 1]
        try:
            pred = build_prediction(
                symbol=symbol,
                timeframe=timeframe,
                bars=window,
                enabled_traps=enabled_traps,
            )
        except Exception as exc:  # noqa: BLE001 — backtest must keep going
            log.debug("build_prediction failed at bar %d: %s", i, exc)
            i += 1
            continue

        if layer_weights is not None:
            # Re-aggregate using caller-supplied weights; mirror the
            # build_prediction shape so we can reuse the tier classifier.
            layer_results = {
                int(k): None if v is None else LayerScore(
                    direction=Direction(v.direction),
                    strength=v.strength,
                    confidence=v.confidence,
                    notes=v.notes,
                )
                for k, v in pred.layer_scores.items()
            }
            final = _aggregate_with_weights(
                layer_results, layer_weights=layer_weights,
            )
            tier = classify_tier(final)
            direction = final.direction
            score = final.score
        else:
            tier = pred.prediction_extras.get("tier", "NO_SIGNAL") if pred.prediction_extras else "NO_SIGNAL"
            direction = Direction(pred.final.direction)
            score = pred.final.score

        # A trade fires when EITHER (a) tier-gated PAPER+ (matches live)
        # OR (b) the absolute score clears _MIN_BACKTEST_SCORE — the
        # backtester sees more trades than live so hyperopt has signal
        # to optimise over. Live shadow trading still uses tier gating only.
        passes_tier = tier in _OPEN_TIERS
        passes_score = abs(score) >= _MIN_BACKTEST_SCORE
        if (passes_tier or passes_score) and direction is not Direction.NEUTRAL:
            side = "LONG" if direction is Direction.LONG else "SHORT"
            trade = _simulate_trade(bars, entry_idx=i, side=side)
            if trade is not None:
                trades.append(trade)
                # Skip past the exit so we never open overlapping positions.
                exit_ts = trade.closed_at
                while i < n - 1 and _to_pydatetime(bars.index[i]) < exit_ts:
                    i += 1
                i += 1
                continue
        i += 1
    return trades


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

    trades = _scan_for_trades(
        bars,
        symbol=symbol, timeframe=timeframe,
        layer_weights=layer_weights, enabled_traps=enabled_traps,
    )

    win_rate, profit_factor, sharpe, max_dd, final_balance, equity_curve = (
        _aggregate_metrics(trades, initial_balance_usdt, bars.index)
    )

    # Ensure aggregate's invariant: if there are zero trades we must still
    # emit at least the initial-balance equity point (already handled in
    # _aggregate_metrics, but be explicit when bars is empty too).
    if not equity_curve and len(bars) > 0:
        equity_curve.append((_to_pydatetime(bars.index[0]), initial_balance_usdt))

    return BacktestResult(
        n_trades=len(trades),
        win_rate=win_rate,
        profit_factor=profit_factor,
        sharpe=sharpe,
        max_drawdown=max_dd,
        equity_curve=equity_curve,
        trade_log=trades,
        params_hash=params_hash,
        initial_balance=initial_balance_usdt,
        final_balance=final_balance,
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
    """Load OHLCV bars from the production `ohlcv` table.

    Phase B3 ships the test-injectable signature; the real Postgres-backed
    implementation is wired in Phase B4 when the admin endpoint lands and
    needs a session-aware loader. Until then, callers MUST pass
    ``_bars_loader=...`` explicitly (the unit tests do this; the admin
    endpoint will too once B4 wires it).
    """
    raise NotImplementedError(
        "_default_bars_loader: inject _bars_loader for tests; "
        "Postgres-backed loader is the Phase B4 deliverable."
    )


# A tiny helper kept package-level so the ``aggregate`` symbol is reachable
# from monkeypatch-based tests that want to swap the aggregator out without
# importing app.core.scoring.aggregator directly.
_aggregate = aggregate


async def persist_backtest_result(
    session: AsyncSession,
    *,
    result: BacktestResult,
    triggered_by_user_id: int | None = None,
) -> int:
    """Insert a BacktestResult into the ``backtests`` table; returns the new id.

    Caller commits the session — this lets the helper compose with larger
    admin transactions that may want to roll the whole thing back. The
    equity_curve / trade_log are NOT persisted inline here; spec §5.16
    pins them to ``equity_curve_uri`` / ``trade_log_uri`` (B2 upload).
    For Phase B4 those URIs stay NULL — the in-memory curve / trade log
    are still available on the returned ``BacktestResult``.

    ``profit_factor`` of ``math.inf`` (no losing trades) is coerced to NULL
    to keep the DOUBLE PRECISION column typed correctly across Postgres
    and SQLite.
    """
    layer_weights_json: str | None = (
        json.dumps({str(k): v for k, v in result.layer_weights.items()})
        if result.layer_weights else None
    )
    enabled_layers_json: str | None = (
        json.dumps(sorted(result.enabled_layers))
        if result.enabled_layers else None
    )
    enabled_traps_json: str | None = (
        json.dumps(sorted(result.enabled_traps))
        if result.enabled_traps else None
    )
    profit_factor_db: float | None = (
        None if (
            result.profit_factor == math.inf
            or not math.isfinite(result.profit_factor)
        )
        else result.profit_factor
    )

    insert_sql = sa.text(
        "INSERT INTO backtests "
        "(triggered_by, symbol, timeframe, start_ts, end_ts, "
        "layer_weights, enabled_layers, enabled_traps, initial_balance, "
        "n_trades, win_rate, profit_factor, sharpe, max_drawdown, "
        "params_hash, status) "
        "VALUES (:tb, :sym, :tf, :s, :e, :lw, :el, :et, :ib, "
        ":nt, :wr, :pf, :sh, :md, :ph, 'completed')"
    )
    await session.execute(insert_sql, {
        "tb": triggered_by_user_id, "sym": result.symbol,
        "tf": result.timeframe, "s": result.start_ts, "e": result.end_ts,
        "lw": layer_weights_json, "el": enabled_layers_json,
        "et": enabled_traps_json, "ib": result.initial_balance,
        "nt": result.n_trades, "wr": result.win_rate,
        "pf": profit_factor_db,
        "sh": result.sharpe, "md": result.max_drawdown,
        "ph": result.params_hash,
    })
    # Read the new id back via params_hash + symbol + timeframe + start_ts
    # so the helper avoids RETURNING (which is DB-dialect dependent and the
    # in-memory aiosqlite path used in tests doesn't always emit it cleanly).
    row = (await session.execute(sa.text(
        "SELECT id FROM backtests "
        "WHERE params_hash = :ph AND symbol = :sym AND timeframe = :tf "
        "ORDER BY id DESC LIMIT 1"
    ), {"ph": result.params_hash, "sym": result.symbol, "tf": result.timeframe})).first()
    if row is None:  # pragma: no cover — should never happen post-INSERT
        raise RuntimeError("persist_backtest_result: row not visible after insert")
    return int(row.id)
