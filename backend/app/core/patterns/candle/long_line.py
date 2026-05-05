"""Long Line — long body candle (TA-Lib CDLLONGLINE)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

LongLinePattern = make_talib_wrapper(
    pattern_id="long_line",
    talib_func_name="CDLLONGLINE",
    confidence=0.7,
)
