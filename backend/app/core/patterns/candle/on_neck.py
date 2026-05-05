"""On Neck — bearish continuation (TA-Lib CDLONNECK)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

OnNeckPattern = make_talib_wrapper(
    pattern_id="on_neck",
    talib_func_name="CDLONNECK",
    confidence=0.7,
)
