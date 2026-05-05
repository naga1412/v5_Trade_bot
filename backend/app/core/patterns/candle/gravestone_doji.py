"""Gravestone Doji — bearish shooting-star-like doji (TA-Lib CDLGRAVESTONEDOJI)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

GravestoneDojiPattern = make_talib_wrapper(
    pattern_id="gravestone_doji",
    talib_func_name="CDLGRAVESTONEDOJI",
    confidence=0.7,
)
