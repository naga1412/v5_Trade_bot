"""Rickshaw Man — long-legged doji centered (TA-Lib CDLRICKSHAWMAN)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

RickshawManPattern = make_talib_wrapper(
    pattern_id="rickshaw_man",
    talib_func_name="CDLRICKSHAWMAN",
    confidence=0.7,
)
