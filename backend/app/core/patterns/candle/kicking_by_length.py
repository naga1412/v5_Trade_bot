"""Kicking by Length — kicking with longer marubozu (TA-Lib CDLKICKINGBYLENGTH)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

KickingByLengthPattern = make_talib_wrapper(
    pattern_id="kicking_by_length",
    talib_func_name="CDLKICKINGBYLENGTH",
    confidence=0.7,
)
