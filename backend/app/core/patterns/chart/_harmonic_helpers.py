"""Shared helpers for harmonic-pattern detectors (Gartley, Butterfly, Bat, Crab).

A canonical XABCD harmonic pattern has five pivots:
    X (start) → A (extreme) → B (retrace) → C (countertrend) → D (completion).

Detectors find the most recent five-pivot sequence in a window and check
specific Fibonacci ratios. We tolerate ±5% on each ratio.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.chart._helpers import find_swing_highs, find_swing_lows


def find_xabcd_pivots(
    bars: pd.DataFrame, *, lookback: int, current_idx: int
) -> list[tuple[int, str, float]] | None:
    """Return the most recent 5 alternating pivots [(idx, kind, price), ...].

    `kind` is 'high' or 'low'. Returns None if fewer than 5 pivots are found.
    """
    if current_idx < lookback:
        return None
    win = bars.iloc[current_idx - lookback : current_idx + 1]
    highs = win["high"].to_numpy(dtype=float)
    lows = win["low"].to_numpy(dtype=float)
    prom_h = max(float(highs.std()) * 0.15, 0.5)
    prom_l = max(float(lows.std()) * 0.15, 0.5)
    peaks = find_swing_highs(highs, prominence=prom_h, distance=3)
    troughs = find_swing_lows(lows, prominence=prom_l, distance=3)
    pivots: list[tuple[int, str, float]] = []
    pivots.extend((p, "high", float(highs[p])) for p in peaks)
    pivots.extend((t, "low", float(lows[t])) for t in troughs)
    pivots.sort(key=lambda x: x[0])
    # Filter to alternating sequence (drop runs of same kind, keep extreme)
    cleaned: list[tuple[int, str, float]] = []
    for pv in pivots:
        if cleaned and cleaned[-1][1] == pv[1]:
            # Same type — keep the more extreme one
            prev = cleaned[-1]
            if pv[1] == "high" and pv[2] > prev[2]:
                cleaned[-1] = pv
            elif pv[1] == "low" and pv[2] < prev[2]:
                cleaned[-1] = pv
            continue
        cleaned.append(pv)
    if len(cleaned) < 5:
        return None
    return cleaned[-5:]


def ratio_in_band(value: float, target: float, tol: float = 0.1) -> bool:
    """True if |value - target| / target ≤ tol."""
    if target == 0:
        return False
    return abs(value - target) / target <= tol


def safe_ratio(num: float, den: float) -> float:
    """num/den, returning np.nan when |den| ≈ 0."""
    if abs(den) < 1e-9:
        return float("nan")
    return num / den
