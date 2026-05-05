"""Mat Hold — bullish continuation (TA-Lib CDLMATHOLD)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

MatHoldPattern = make_talib_wrapper(
    pattern_id="mat_hold",
    talib_func_name="CDLMATHOLD",
    confidence=0.7,
)
