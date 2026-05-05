"""Rising/Falling Three Methods — continuation (TA-Lib CDLRISEFALL3METHODS)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

RiseFall3MethodsPattern = make_talib_wrapper(
    pattern_id="rise_fall_3_methods",
    talib_func_name="CDLRISEFALL3METHODS",
    confidence=0.7,
)
