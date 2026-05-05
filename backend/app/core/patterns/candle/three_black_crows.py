"""Three Black Crows — bearish reversal (TA-Lib CDL3BLACKCROWS)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

ThreeBlackCrowsPattern = make_talib_wrapper(
    pattern_id="three_black_crows",
    talib_func_name="CDL3BLACKCROWS",
    confidence=0.7,
)
