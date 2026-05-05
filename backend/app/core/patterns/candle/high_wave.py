"""High Wave — long wicks, small body (TA-Lib CDLHIGHWAVE)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

HighWavePattern = make_talib_wrapper(
    pattern_id="high_wave",
    talib_func_name="CDLHIGHWAVE",
    confidence=0.7,
)
