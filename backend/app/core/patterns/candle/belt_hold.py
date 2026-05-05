"""Belt Hold — bullish or bearish single-bar (TA-Lib CDLBELTHOLD)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

BeltHoldPattern = make_talib_wrapper(
    pattern_id="belt_hold",
    talib_func_name="CDLBELTHOLD",
    confidence=0.7,
)
