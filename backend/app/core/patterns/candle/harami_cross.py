"""Harami Cross — reversal with doji inside (TA-Lib CDLHARAMICROSS)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

HaramiCrossPattern = make_talib_wrapper(
    pattern_id="harami_cross",
    talib_func_name="CDLHARAMICROSS",
    confidence=0.7,
)
