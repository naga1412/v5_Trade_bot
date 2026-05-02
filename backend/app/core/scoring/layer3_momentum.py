import math
import numpy as np
import pandas as pd

from app.core.indicators.macd import macd
from app.core.indicators.rsi import rsi
from app.core.scoring.types import Direction, LayerScore


def score(bars: pd.DataFrame) -> LayerScore | None:
    closes = bars["close"].to_numpy(dtype=float)
    if closes.shape[0] < 50:
        return None

    rsi14 = rsi(closes, 14)
    _, _, hist = macd(closes, 12, 26, 9)
    last_rsi, last_hist = rsi14[-1], hist[-1]
    if math.isnan(last_rsi) or math.isnan(last_hist):
        return None

    abs_hist = np.abs(hist[-50:])
    median_abs = float(np.nanmedian(abs_hist))
    if median_abs == 0 or math.isnan(median_abs):
        median_abs = 1e-9
    hist_strength = min(1.0, abs(last_hist) / median_abs)

    _EPS = 1e-10
    if last_rsi > 60 and last_hist > -_EPS:
        strength = min(1.0, (last_rsi / 100.0) * 1.4 * hist_strength)
        return LayerScore(Direction.LONG, strength, 0.75, "RSI>60 + MACD hist+")
    if last_rsi < 40 and last_hist < _EPS:
        strength = min(1.0, ((100 - last_rsi) / 100.0) * 1.4 * hist_strength)
        return LayerScore(Direction.SHORT, strength, 0.75, "RSI<40 + MACD hist-")
    return LayerScore(Direction.NEUTRAL, 0.0, 0.4, "RSI/MACD disagree or mid-zone")
