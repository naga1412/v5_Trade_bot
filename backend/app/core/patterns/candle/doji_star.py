"""Doji Star — gap doji indicating reversal (TA-Lib CDLDOJISTAR)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

DojiStarPattern = make_talib_wrapper(
    pattern_id="doji_star",
    talib_func_name="CDLDOJISTAR",
    confidence=0.7,
)
