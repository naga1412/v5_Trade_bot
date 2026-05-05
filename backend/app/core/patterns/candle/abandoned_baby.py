"""Abandoned Baby — strong reversal with gaps (TA-Lib CDLABANDONEDBABY)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

AbandonedBabyPattern = make_talib_wrapper(
    pattern_id="abandoned_baby",
    talib_func_name="CDLABANDONEDBABY",
    confidence=0.7,
)
