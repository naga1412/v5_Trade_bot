"""Advance Block — bearish reversal of uptrend (TA-Lib CDLADVANCEBLOCK)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

AdvanceBlockPattern = make_talib_wrapper(
    pattern_id="advance_block",
    talib_func_name="CDLADVANCEBLOCK",
    confidence=0.7,
)
