"""Modified Hikkake — refined false-breakout reversal (TA-Lib CDLHIKKAKEMOD)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

HikkakeModifiedPattern = make_talib_wrapper(
    pattern_id="hikkake_modified",
    talib_func_name="CDLHIKKAKEMOD",
    confidence=0.7,
)
