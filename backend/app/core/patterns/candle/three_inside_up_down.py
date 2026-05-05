"""Three Inside Up/Down — reversal (TA-Lib CDL3INSIDE)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

ThreeInsideUpDownPattern = make_talib_wrapper(
    pattern_id="three_inside_up_down",
    talib_func_name="CDL3INSIDE",
    confidence=0.7,
)
