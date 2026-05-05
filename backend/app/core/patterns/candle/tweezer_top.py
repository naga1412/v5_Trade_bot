"""Tweezer Top (closest TA-Lib analogue: Matching Low) (TA-Lib CDLMATCHINGLOW)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

TweezerTopPattern = make_talib_wrapper(
    pattern_id="tweezer_top",
    talib_func_name="CDLMATCHINGLOW",
    confidence=0.7,
)
