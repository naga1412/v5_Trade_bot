"""Kicking — strong reversal with marubozu gap (TA-Lib CDLKICKING)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

KickingPattern = make_talib_wrapper(
    pattern_id="kicking",
    talib_func_name="CDLKICKING",
    confidence=0.7,
)
