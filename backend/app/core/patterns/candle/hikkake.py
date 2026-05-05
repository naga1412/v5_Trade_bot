"""Hikkake — false-breakout reversal (TA-Lib CDLHIKKAKE)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

HikkakePattern = make_talib_wrapper(
    pattern_id="hikkake",
    talib_func_name="CDLHIKKAKE",
    confidence=0.7,
)
