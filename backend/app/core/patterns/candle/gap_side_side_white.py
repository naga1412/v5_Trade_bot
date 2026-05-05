"""Up/Down Gap Side-by-Side White — continuation (TA-Lib CDLGAPSIDESIDEWHITE)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

GapSideSideWhitePattern = make_talib_wrapper(
    pattern_id="gap_side_side_white",
    talib_func_name="CDLGAPSIDESIDEWHITE",
    confidence=0.7,
)
