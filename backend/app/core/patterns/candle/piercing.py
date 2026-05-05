"""Piercing Line — bullish reversal (TA-Lib CDLPIERCING)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

PiercingPattern = make_talib_wrapper(
    pattern_id="piercing",
    talib_func_name="CDLPIERCING",
    confidence=0.7,
)
