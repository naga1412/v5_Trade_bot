import math
import pandas as pd

from app.core.indicators.ema import ema
from app.core.scoring.types import Direction, LayerScore


def score(bars: pd.DataFrame) -> LayerScore | None:
    closes = bars["close"].to_numpy(dtype=float)
    if closes.shape[0] < 200:
        return None

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)
    last_close, last_e20, last_e50, last_e200 = closes[-1], e20[-1], e50[-1], e200[-1]

    if any(math.isnan(v) for v in (last_e20, last_e50, last_e200)):
        return None

    asc = last_close > last_e20 > last_e50 > last_e200
    desc = last_close < last_e20 < last_e50 < last_e200
    above_200 = last_close > last_e200
    below_200 = last_close < last_e200

    if asc:
        return LayerScore(Direction.LONG, 0.9, 0.85, "EMAs aligned ascending")
    if desc:
        return LayerScore(Direction.SHORT, 0.9, 0.85, "EMAs aligned descending")
    if above_200:
        return LayerScore(Direction.LONG, 0.5, 0.6, "Close above EMA200, EMAs mixed")
    if below_200:
        return LayerScore(Direction.SHORT, 0.5, 0.6, "Close below EMA200, EMAs mixed")
    return LayerScore(Direction.NEUTRAL, 0.0, 0.4, "Price ≈ EMA200")
