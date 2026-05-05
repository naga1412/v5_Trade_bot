"""Concealing Baby Swallow — bullish reversal (TA-Lib CDLCONCEALBABYSWALL)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

ConcealingBabySwallowPattern = make_talib_wrapper(
    pattern_id="concealing_baby_swallow",
    talib_func_name="CDLCONCEALBABYSWALL",
    confidence=0.7,
)
