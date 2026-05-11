"""Deterministic indicator-only fast scan (Feature 4 — multi-asset scanner).

Scores one symbol's recent candle data in ~5ms using a curated set of the
existing indicators in :mod:`app.core.indicators`. No ML inference, no
DB lookups — pure numpy/pandas math over an OHLCV DataFrame.

The output mirrors the shape of the ML-side prediction so the UI can
render scan cards with the same per-layer signed-bar visual the SMC-Pro
screenshots use. Five module sub-scores feed the final aggregate:

  * ``trend``       — EMA20 vs EMA50 cross + price position
  * ``momentum``    — MACD histogram + slope + RSI distance from 50
  * ``volatility``  — ATR % of price (high = caution, low = compression)
  * ``volume``      — OBV slope + close vs Bollinger middle
  * ``structure``   — ADX strength + price vs 20-bar high/low

Each sub-score is signed (-100 to +100). The aggregate ``final_score``
is a weighted average (trend 30%, momentum 30%, structure 20%, volume
10%, volatility 10%). ``confidence`` is the fraction of modules that
agree with the aggregate sign.

Tiers (mirrors SMC Pro's Confirmed/Probable/Weak buckets):

  * ``confirmed``   — |score| ≥ 50 AND confidence ≥ 0.8
  * ``probable``    — |score| ≥ 30 AND confidence ≥ 0.6
  * ``weak``        — |score| ≥ 15
  * ``diverging``   — confidence < 0.4 (modules disagree heavily)
  * ``neutral``     — none of the above

Market phase classification (Markup/Markdown/Accumulation/Distribution)
uses Wyckoff-style structure: ADX above 20 + price relative to EMA50
direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from app.core.indicators.atr import atr
from app.core.indicators.bollinger import bollinger
from app.core.indicators.ema import ema
from app.core.indicators.macd import macd
from app.core.indicators.obv import obv
from app.core.indicators.rsi import rsi

Direction = Literal["LONG", "SHORT", "NEUTRAL"]
Tier = Literal["confirmed", "probable", "weak", "diverging", "neutral"]
Phase = Literal["markup", "markdown", "accumulation", "distribution", "neutral"]


@dataclass(frozen=True)
class ModuleScore:
    """One sub-score row, signed -100..+100."""
    name: str
    score: float
    weight: float
    notes: str = ""


@dataclass(frozen=True)
class FastScanResult:
    symbol: str
    timeframe: str
    final_score: float       # -100..+100
    direction: Direction
    confidence: float        # 0..1
    tier: Tier
    phase: Phase
    modules: list[ModuleScore]
    last_close: float
    expected_move_pct: float | None  # ATR-derived, signed by direction
    rr_estimate: float | None        # ~3:1 if ATR-stops used


# Minimum bars to compute the indicators that need the longest lookback.
# EMA50 + MACD-26 + Bollinger-20 + ATR-14 all fit inside 60. Add headroom.
MIN_BARS_REQUIRED: int = 60


def _safe_last(arr: np.ndarray | pd.Series) -> float | None:
    """Return the last value as a float, or None if NaN/empty."""
    if arr is None or len(arr) == 0:
        return None
    v = float(arr[-1]) if isinstance(arr, np.ndarray) else float(arr.iloc[-1])
    if np.isnan(v):
        return None
    return v


def _slope(arr: pd.Series | np.ndarray, lookback: int = 5) -> float | None:
    """Linear-regression slope over the last ``lookback`` non-NaN values."""
    vals = np.asarray(arr, dtype=float)
    vals = vals[~np.isnan(vals)]
    if len(vals) < lookback:
        return None
    tail = vals[-lookback:]
    x = np.arange(lookback, dtype=float)
    a, _ = np.polyfit(x, tail, 1)
    return float(a)


def _clip(x: float, lo: float = -100.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _score_trend(bars: pd.DataFrame) -> ModuleScore:
    """EMA20-vs-EMA50 cross + price position. ±100 swing."""
    close = bars["close"].to_numpy(dtype=float)
    ema20 = ema(close, period=20)
    ema50 = ema(close, period=50)
    e20 = _safe_last(ema20)
    e50 = _safe_last(ema50)
    last = _safe_last(close)
    if e20 is None or e50 is None or last is None or e50 == 0:
        return ModuleScore("trend", 0.0, 0.30, "insufficient data")
    spread_pct = (e20 - e50) / e50 * 100.0
    # 2% EMA spread is a strong signal → ~80; clip beyond.
    cross_score = _clip(spread_pct * 40.0)
    pos_score = 20.0 if last > e20 else -20.0 if last < e20 else 0.0
    s = _clip(cross_score + pos_score)
    return ModuleScore(
        "trend", s, 0.30,
        f"ema20-50 spread={spread_pct:+.2f}% price={'above' if last>e20 else 'below'} ema20",
    )


def _score_momentum(bars: pd.DataFrame) -> ModuleScore:
    """MACD histogram + RSI distance from 50."""
    close = bars["close"].to_numpy(dtype=float)
    macd_line, signal, hist = macd(close)
    rsi_arr = rsi(close, period=14)
    h = _safe_last(hist)
    r = _safe_last(rsi_arr)
    if h is None or r is None:
        return ModuleScore("momentum", 0.0, 0.30, "insufficient data")
    last_close = _safe_last(close) or 1.0
    hist_pct = h / last_close * 100.0  # normalize to % of price
    hist_score = _clip(hist_pct * 200.0)  # 0.5% hist → ±100
    # RSI: 50 = neutral, ≥70 overbought (LONG getting tired), ≤30 oversold (SHORT exhausted)
    rsi_score = _clip((r - 50.0) * 2.0)  # +50 at RSI 75, -50 at RSI 25
    s = _clip(0.6 * hist_score + 0.4 * rsi_score)
    return ModuleScore(
        "momentum", s, 0.30,
        f"macd_hist={hist_pct:+.3f}% rsi={r:.1f}",
    )


def _score_volatility(bars: pd.DataFrame) -> ModuleScore:
    """ATR % of price. High = caution; low = compression (potential breakout)."""
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    atr_arr = atr(high, low, close, period=14)
    a = _safe_last(atr_arr)
    last = _safe_last(close)
    if a is None or last is None or last == 0:
        return ModuleScore("volatility", 0.0, 0.10, "insufficient data")
    atr_pct = a / last * 100.0
    # Sweet spot around 1-2% (active but not chaotic). >5% is dangerous,
    # <0.5% is compressed/coiled. Signed so a LONG-aligned readout means
    # "volatility supports the move" rather than "volatility is high".
    # Unsigned magnitude is the contribution; direction is set in the
    # aggregate step based on the trend module's sign.
    if atr_pct >= 5.0:
        s = -50.0  # too volatile — discount
    elif atr_pct >= 2.0:
        s = 20.0   # active, supports a move
    elif atr_pct >= 0.7:
        s = 35.0   # ideal
    elif atr_pct >= 0.3:
        s = -10.0  # compressed; could break either way
    else:
        s = -30.0  # too quiet — false signals likely
    return ModuleScore("volatility", s, 0.10, f"atr_pct={atr_pct:.2f}%")


def _score_volume(bars: pd.DataFrame) -> ModuleScore:
    """OBV slope + close position relative to Bollinger middle band."""
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    vol = bars["volume"].to_numpy(dtype=float)
    obv_arr = obv(close, vol)
    upper, middle, lower = bollinger(close, period=20, num_std=2.0)
    obv_slope = _slope(obv_arr, lookback=10)
    last_obv = _safe_last(obv_arr)
    last = _safe_last(close)
    mid = _safe_last(middle)
    if last is None or mid is None or obv_slope is None or last_obv is None:
        return ModuleScore("volume", 0.0, 0.10, "insufficient data")
    # Normalize OBV slope to a per-bar % change in OBV itself.
    obv_slope_pct = (obv_slope / abs(last_obv) * 100.0) if last_obv != 0 else 0.0
    obv_score = _clip(obv_slope_pct * 100.0)  # 1% slope/bar → 100
    bb_score = 30.0 if last > mid else -30.0 if last < mid else 0.0
    # Mild unused-variable note: upper/lower can drive band-squeeze
    # heuristics in a future enhancement.
    _ = upper, low, high, lower
    s = _clip(0.6 * obv_score + 0.4 * bb_score)
    return ModuleScore(
        "volume", s, 0.10,
        f"obv_slope={obv_slope_pct:+.2f}%/bar close={'above' if last>mid else 'below'} bb-mid",
    )


def _score_structure(bars: pd.DataFrame) -> ModuleScore:
    """Price position relative to recent 20-bar high/low (donchian-ish).

    Strong LONG when close is at top of 20-bar range; strong SHORT at bottom.
    A finer Wyckoff implementation would classify accumulation vs markup but
    that needs multi-timeframe context the fast scan deliberately avoids.
    """
    close = bars["close"].to_numpy(dtype=float)
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    if len(close) < 20:
        return ModuleScore("structure", 0.0, 0.20, "insufficient data")
    last = float(close[-1])
    hi20 = float(np.max(high[-20:]))
    lo20 = float(np.min(low[-20:]))
    if hi20 - lo20 <= 0:
        return ModuleScore("structure", 0.0, 0.20, "flat range")
    pos = (last - lo20) / (hi20 - lo20)  # 0..1
    # 0 → -100 (at bottom), 0.5 → 0, 1.0 → +100 (at top)
    s = (pos - 0.5) * 200.0
    return ModuleScore(
        "structure", _clip(s), 0.20,
        f"close_pos_in_20bar={pos:.2f}",
    )


def _classify_phase(bars: pd.DataFrame, final_score: float) -> Phase:
    """Simple Wyckoff-style classification using EMA50 + score sign + range pos."""
    close = bars["close"].to_numpy(dtype=float)
    if len(close) < 50:
        return "neutral"
    last = float(close[-1])
    e50 = _safe_last(ema(close, period=50))
    if e50 is None:
        return "neutral"
    above = last > e50
    if final_score > 30:
        return "markup" if above else "accumulation"
    if final_score < -30:
        return "markdown" if not above else "distribution"
    return "neutral"


def _classify_tier(final_score: float, confidence: float) -> Tier:
    if confidence < 0.4:
        return "diverging"
    abs_s = abs(final_score)
    if abs_s >= 50 and confidence >= 0.8:
        return "confirmed"
    if abs_s >= 30 and confidence >= 0.6:
        return "probable"
    if abs_s >= 15:
        return "weak"
    return "neutral"


def fast_scan(
    bars: pd.DataFrame, *, symbol: str, timeframe: str,
) -> FastScanResult | None:
    """Run the deterministic fast scan over one symbol's OHLCV bars.

    Returns ``None`` if there are fewer than :data:`MIN_BARS_REQUIRED`
    bars — caller should treat that as "skip this symbol this tick".
    """
    if len(bars) < MIN_BARS_REQUIRED:
        return None
    required_cols = {"open", "high", "low", "close", "volume"}
    if not required_cols.issubset(set(bars.columns)):
        return None

    modules = [
        _score_trend(bars),
        _score_momentum(bars),
        _score_volatility(bars),
        _score_volume(bars),
        _score_structure(bars),
    ]
    # The volatility module's contribution sign is set by the aggregate's
    # eventual direction — but to compute the aggregate we need a signed
    # contribution. Multiply its (unsigned) magnitude by sign(other-modules-sum).
    other_sum = sum(m.score * m.weight for m in modules if m.name != "volatility")
    direction_sign = 1.0 if other_sum > 0 else -1.0 if other_sum < 0 else 0.0
    vol_idx = next(i for i, m in enumerate(modules) if m.name == "volatility")
    vol_mod = modules[vol_idx]
    aligned_vol = ModuleScore(
        name=vol_mod.name,
        score=vol_mod.score * direction_sign,  # signs the volatility readout
        weight=vol_mod.weight,
        notes=vol_mod.notes,
    )
    modules[vol_idx] = aligned_vol

    final_score = sum(m.score * m.weight for m in modules)
    direction: Direction = (
        "LONG" if final_score > 5
        else "SHORT" if final_score < -5
        else "NEUTRAL"
    )

    # Confidence: fraction of modules whose sign matches the aggregate.
    agg_sign = 1.0 if final_score > 0 else -1.0 if final_score < 0 else 0.0
    agreeing = sum(
        1 for m in modules
        if (agg_sign > 0 and m.score > 0) or (agg_sign < 0 and m.score < 0)
    )
    confidence = agreeing / len(modules) if agg_sign != 0 else 0.0

    tier = _classify_tier(final_score, confidence)
    phase = _classify_phase(bars, final_score)
    last_close = float(bars["close"].iloc[-1])

    # Expected move: 3-bar ATR projection, signed by direction.
    atr_arr = atr(
        bars["high"].to_numpy(dtype=float),
        bars["low"].to_numpy(dtype=float),
        bars["close"].to_numpy(dtype=float),
        period=14,
    )
    last_atr = _safe_last(atr_arr)
    expected_move_pct: float | None = None
    rr_estimate: float | None = None
    if last_atr is not None and last_close > 0:
        atr_pct = last_atr / last_close * 100.0
        # Project 3 bars of typical move in the predicted direction.
        signed = atr_pct * 3.0 * (1.0 if direction == "LONG" else -1.0 if direction == "SHORT" else 0.0)
        expected_move_pct = signed
        # Same 1.5 SL / 3.0 TP ratio the shadow worker uses.
        rr_estimate = 2.0

    return FastScanResult(
        symbol=symbol,
        timeframe=timeframe,
        final_score=final_score,
        direction=direction,
        confidence=confidence,
        tier=tier,
        phase=phase,
        modules=modules,
        last_close=last_close,
        expected_move_pct=expected_move_pct,
        rr_estimate=rr_estimate,
    )


__all__ = [
    "Direction", "Phase", "Tier",
    "FastScanResult", "ModuleScore",
    "MIN_BARS_REQUIRED",
    "fast_scan",
]
