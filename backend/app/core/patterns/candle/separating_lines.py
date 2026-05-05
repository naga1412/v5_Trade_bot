"""Separating Lines — continuation (TA-Lib CDLSEPARATINGLINES)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

SeparatingLinesPattern = make_talib_wrapper(
    pattern_id="separating_lines",
    talib_func_name="CDLSEPARATINGLINES",
    confidence=0.7,
)
