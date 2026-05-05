"""Engulfing — bullish or bearish reversal (TA-Lib CDLENGULFING)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

EngulfingPattern = make_talib_wrapper(
    pattern_id="engulfing",
    talib_func_name="CDLENGULFING",
    confidence=0.7,
)
