"""Upside Gap Two Crows — bearish reversal (TA-Lib CDLUPSIDEGAP2CROWS)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

UpsideGapTwoCrowsPattern = make_talib_wrapper(
    pattern_id="upside_gap_two_crows",
    talib_func_name="CDLUPSIDEGAP2CROWS",
    confidence=0.7,
)
