"""Up/Down-Gap Tasuki — continuation (TA-Lib CDLTASUKIGAP)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

TasukiGapPattern = make_talib_wrapper(
    pattern_id="tasuki_gap",
    talib_func_name="CDLTASUKIGAP",
    confidence=0.7,
)
