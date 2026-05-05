"""Upside/Downside Gap Three Methods — continuation (TA-Lib CDLXSIDEGAP3METHODS)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

XsideGap3MethodsPattern = make_talib_wrapper(
    pattern_id="xside_gap_3_methods",
    talib_func_name="CDLXSIDEGAP3METHODS",
    confidence=0.7,
)
