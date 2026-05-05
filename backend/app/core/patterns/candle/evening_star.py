"""Evening Star — bearish reversal (TA-Lib CDLEVENINGSTAR)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

EveningStarPattern = make_talib_wrapper(
    pattern_id="evening_star",
    talib_func_name="CDLEVENINGSTAR",
    confidence=0.7,
)
