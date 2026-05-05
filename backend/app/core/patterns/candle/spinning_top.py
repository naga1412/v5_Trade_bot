"""Spinning Top — small body, indecision (TA-Lib CDLSPINNINGTOP)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

SpinningTopPattern = make_talib_wrapper(
    pattern_id="spinning_top",
    talib_func_name="CDLSPINNINGTOP",
    confidence=0.7,
)
