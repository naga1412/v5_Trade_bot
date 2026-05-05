"""Three Line Strike — continuation/reversal (TA-Lib CDL3LINESTRIKE)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

ThreeLineStrikePattern = make_talib_wrapper(
    pattern_id="three_line_strike",
    talib_func_name="CDL3LINESTRIKE",
    confidence=0.7,
)
