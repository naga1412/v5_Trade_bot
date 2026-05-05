"""Hanging Man — bearish reversal at uptrend top (TA-Lib CDLHANGINGMAN)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

HangingManPattern = make_talib_wrapper(
    pattern_id="hanging_man",
    talib_func_name="CDLHANGINGMAN",
    confidence=0.7,
)
