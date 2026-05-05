"""Unique Three River Bottom — bullish reversal (TA-Lib CDLUNIQUE3RIVER)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

Unique3RiverPattern = make_talib_wrapper(
    pattern_id="unique_3_river",
    talib_func_name="CDLUNIQUE3RIVER",
    confidence=0.7,
)
