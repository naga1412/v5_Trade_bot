"""Identical Three Crows — bearish reversal (TA-Lib CDLIDENTICAL3CROWS)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

IdenticalThreeCrowsPattern = make_talib_wrapper(
    pattern_id="identical_three_crows",
    talib_func_name="CDLIDENTICAL3CROWS",
    confidence=0.7,
)
