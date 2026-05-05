"""Doji — indecision (open == close) (TA-Lib CDLDOJI)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

DojiPattern = make_talib_wrapper(
    pattern_id="doji",
    talib_func_name="CDLDOJI",
    confidence=0.7,
)
