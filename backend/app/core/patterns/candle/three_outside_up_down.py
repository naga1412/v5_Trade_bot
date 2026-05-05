"""Three Outside Up/Down — reversal (TA-Lib CDL3OUTSIDE)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

ThreeOutsideUpDownPattern = make_talib_wrapper(
    pattern_id="three_outside_up_down",
    talib_func_name="CDL3OUTSIDE",
    confidence=0.7,
)
