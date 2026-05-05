"""Ladder Bottom — bullish reversal (TA-Lib CDLLADDERBOTTOM)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

LadderBottomPattern = make_talib_wrapper(
    pattern_id="ladder_bottom",
    talib_func_name="CDLLADDERBOTTOM",
    confidence=0.7,
)
