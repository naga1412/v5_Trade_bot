"""Three Stars in the South — bullish reversal (TA-Lib CDL3STARSINSOUTH)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

ThreeStarsInSouthPattern = make_talib_wrapper(
    pattern_id="three_stars_in_south",
    talib_func_name="CDL3STARSINSOUTH",
    confidence=0.7,
)
