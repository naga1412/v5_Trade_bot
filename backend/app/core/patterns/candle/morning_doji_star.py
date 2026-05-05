"""Morning Doji Star — bullish reversal with doji middle (TA-Lib CDLMORNINGDOJISTAR)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

MorningDojiStarPattern = make_talib_wrapper(
    pattern_id="morning_doji_star",
    talib_func_name="CDLMORNINGDOJISTAR",
    confidence=0.7,
)
