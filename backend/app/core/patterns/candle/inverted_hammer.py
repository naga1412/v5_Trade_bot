"""Inverted Hammer — bullish reversal (TA-Lib CDLINVERTEDHAMMER)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

InvertedHammerPattern = make_talib_wrapper(
    pattern_id="inverted_hammer",
    talib_func_name="CDLINVERTEDHAMMER",
    confidence=0.7,
)
