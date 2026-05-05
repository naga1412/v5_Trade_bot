"""Short Line — short body candle (TA-Lib CDLSHORTLINE)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

ShortLinePattern = make_talib_wrapper(
    pattern_id="short_line",
    talib_func_name="CDLSHORTLINE",
    confidence=0.7,
)
