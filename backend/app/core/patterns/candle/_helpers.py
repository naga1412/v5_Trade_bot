"""Shared utilities for hand-rolled candle pattern detectors (patterns 62-82).

Geometric helpers (``body_size``, ``upper_wick``, ``lower_wick``) operate on
a single bar (``pd.Series`` with ``open/high/low/close``). Trend helpers
(``recent_swing_high``, ``recent_swing_low``) summarise the trailing window.
Indicator wrappers (``compute_atr``, ``compute_rsi_at``) provide scalar
readings at ``current_idx`` so detectors can context-gate fires (e.g. RSI < 30).

All functions are pure and never raise on degenerate input — they return 0.0
or NaN when there is insufficient history, mirroring the protocol's
"return None / no-fire" convention.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.indicators.rsi import rsi as _rsi


def body_size(bar: pd.Series) -> float:
    """Absolute body size: ``|close - open|``."""
    return float(abs(bar["close"] - bar["open"]))


def upper_wick(bar: pd.Series) -> float:
    """Upper wick length: ``high - max(open, close)`` (≥ 0)."""
    return float(bar["high"] - max(bar["open"], bar["close"]))


def lower_wick(bar: pd.Series) -> float:
    """Lower wick length: ``min(open, close) - low`` (≥ 0)."""
    return float(min(bar["open"], bar["close"]) - bar["low"])


def is_bullish(bar: pd.Series) -> bool:
    """True iff ``close > open`` (strict)."""
    return bool(bar["close"] > bar["open"])


def is_bearish(bar: pd.Series) -> bool:
    """True iff ``close < open`` (strict)."""
    return bool(bar["close"] < bar["open"])


def recent_swing_high(bars: pd.DataFrame, lookback: int = 20) -> float:
    """Maximum ``high`` over the trailing ``lookback`` bars (inclusive of last).

    Falls back to the max of available history if fewer than ``lookback``
    bars exist. Always returns a finite float (assumes non-empty input).
    """
    if len(bars) == 0:
        return 0.0
    window = bars["high"].iloc[-lookback:] if len(bars) > lookback else bars["high"]
    return float(window.max())


def recent_swing_low(bars: pd.DataFrame, lookback: int = 20) -> float:
    """Minimum ``low`` over the trailing ``lookback`` bars (inclusive of last)."""
    if len(bars) == 0:
        return 0.0
    window = bars["low"].iloc[-lookback:] if len(bars) > lookback else bars["low"]
    return float(window.min())


def compute_atr(bars: pd.DataFrame, current_idx: int, period: int = 14) -> float:
    """Simple ATR at ``current_idx`` — mean of last ``period`` true ranges.

    Mirrors :func:`app.core.predictor._atr` (no Wilder smoothing) so detectors
    have a cheap, stable threshold for body-size gating. Returns 0.0 when
    history is shorter than ``period + 1`` bars.
    """
    if current_idx < period:
        return 0.0
    sub = bars.iloc[: current_idx + 1]
    if len(sub) < period + 1:
        return 0.0
    h = sub["high"].to_numpy(dtype=float)
    lo = sub["low"].to_numpy(dtype=float)
    c = sub["close"].to_numpy(dtype=float)
    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(
        h - lo, np.maximum(np.abs(h - prev_close), np.abs(lo - prev_close))
    )
    return float(np.mean(tr[-period:]))


def compute_rsi_at(bars: pd.DataFrame, current_idx: int, period: int = 14) -> float:
    """RSI value at ``current_idx`` using the project's Wilder RSI.

    Returns ``NaN`` while the RSI series is still warming up (first
    ``period`` bars). Detectors should treat NaN as "no signal".
    """
    if current_idx < 0 or current_idx >= len(bars):
        return float("nan")
    closes = bars["close"].to_numpy(dtype=float)[: current_idx + 1]
    series = _rsi(closes, period=period)
    return float(series[-1])
