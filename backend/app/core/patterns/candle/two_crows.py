"""Two Crows — bearish reversal (TA-Lib CDL2CROWS)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

TwoCrowsPattern = make_talib_wrapper(
    pattern_id="two_crows",
    talib_func_name="CDL2CROWS",
    confidence=0.7,
)
