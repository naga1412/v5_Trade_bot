"""Evening Doji Star — bearish reversal with doji middle (TA-Lib CDLEVENINGDOJISTAR)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

EveningDojiStarPattern = make_talib_wrapper(
    pattern_id="evening_doji_star",
    talib_func_name="CDLEVENINGDOJISTAR",
    confidence=0.7,
)
