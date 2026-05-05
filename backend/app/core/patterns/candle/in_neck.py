"""In Neck — bearish continuation (TA-Lib CDLINNECK)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

InNeckPattern = make_talib_wrapper(
    pattern_id="in_neck",
    talib_func_name="CDLINNECK",
    confidence=0.7,
)
