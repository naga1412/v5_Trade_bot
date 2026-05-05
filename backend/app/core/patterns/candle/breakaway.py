"""Breakaway — five-candle reversal (TA-Lib CDLBREAKAWAY)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

BreakawayPattern = make_talib_wrapper(
    pattern_id="breakaway",
    talib_func_name="CDLBREAKAWAY",
    confidence=0.7,
)
