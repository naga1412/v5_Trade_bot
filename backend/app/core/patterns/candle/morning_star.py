"""Morning Star — bullish reversal (TA-Lib CDLMORNINGSTAR)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

MorningStarPattern = make_talib_wrapper(
    pattern_id="morning_star",
    talib_func_name="CDLMORNINGSTAR",
    confidence=0.7,
)
