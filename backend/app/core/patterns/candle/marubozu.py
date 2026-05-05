"""Marubozu — full-body bar with no wicks (TA-Lib CDLMARUBOZU)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

MarubozuPattern = make_talib_wrapper(
    pattern_id="marubozu",
    talib_func_name="CDLMARUBOZU",
    confidence=0.7,
)
