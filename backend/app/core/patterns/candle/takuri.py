"""Takuri — dragonfly doji with very long lower shadow (TA-Lib CDLTAKURI)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

TakuriPattern = make_talib_wrapper(
    pattern_id="takuri",
    talib_func_name="CDLTAKURI",
    confidence=0.7,
)
