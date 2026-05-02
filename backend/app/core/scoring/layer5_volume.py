import pandas as pd

from app.core.scoring.types import Direction, LayerScore


def score(bars: pd.DataFrame) -> LayerScore | None:
    if len(bars) < 21:  # need 20 history bars + 1 latest
        return None

    last = bars.iloc[-1]
    history = bars.iloc[-21:-1]
    mean_vol = float(history["volume"].mean())
    if mean_vol <= 0:
        return None

    ratio = float(last["volume"]) / mean_vol
    bull = last["close"] > last["open"]
    bear = last["close"] < last["open"]

    if ratio > 2.0 and bull:
        return LayerScore(Direction.LONG, min(1.0, ratio / 3.0), 0.7,
                          f"vol {ratio:.1f}x avg + bullish bar")
    if ratio > 2.0 and bear:
        return LayerScore(Direction.SHORT, min(1.0, ratio / 3.0), 0.7,
                          f"vol {ratio:.1f}x avg + bearish bar")
    if ratio < 0.5:
        return LayerScore(Direction.NEUTRAL, 0.0, 0.5, "low volume — no conviction")
    return LayerScore(Direction.NEUTRAL, 0.0, 0.6, "average volume — neutral")
