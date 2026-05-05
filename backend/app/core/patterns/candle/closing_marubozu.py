"""Closing Marubozu — close at extreme (TA-Lib CDLCLOSINGMARUBOZU)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

ClosingMarubozuPattern = make_talib_wrapper(
    pattern_id="closing_marubozu",
    talib_func_name="CDLCLOSINGMARUBOZU",
    confidence=0.7,
)
