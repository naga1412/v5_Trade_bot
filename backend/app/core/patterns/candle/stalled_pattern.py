"""Stalled Pattern — bearish reversal of uptrend (TA-Lib CDLSTALLEDPATTERN)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

StalledPatternPattern = make_talib_wrapper(
    pattern_id="stalled_pattern",
    talib_func_name="CDLSTALLEDPATTERN",
    confidence=0.7,
)
