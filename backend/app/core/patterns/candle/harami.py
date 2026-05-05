"""Harami — bullish or bearish reversal (TA-Lib CDLHARAMI)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

HaramiPattern = make_talib_wrapper(
    pattern_id="harami",
    talib_func_name="CDLHARAMI",
    confidence=0.7,
)
