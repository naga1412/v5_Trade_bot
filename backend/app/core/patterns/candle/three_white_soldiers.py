"""Three White Soldiers — bullish reversal (TA-Lib CDL3WHITESOLDIERS)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

ThreeWhiteSoldiersPattern = make_talib_wrapper(
    pattern_id="three_white_soldiers",
    talib_func_name="CDL3WHITESOLDIERS",
    confidence=0.7,
)
