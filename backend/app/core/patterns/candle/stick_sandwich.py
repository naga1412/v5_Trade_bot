"""Stick Sandwich — bullish reversal (TA-Lib CDLSTICKSANDWICH)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

StickSandwichPattern = make_talib_wrapper(
    pattern_id="stick_sandwich",
    talib_func_name="CDLSTICKSANDWICH",
    confidence=0.7,
)
