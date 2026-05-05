"""Hammer — bullish reversal at downtrend bottom (TA-Lib CDLHAMMER)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

HammerPattern = make_talib_wrapper(
    pattern_id="hammer",
    talib_func_name="CDLHAMMER",
    confidence=0.7,
)
