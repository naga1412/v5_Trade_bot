"""Dragonfly Doji — bullish hammer-like doji (TA-Lib CDLDRAGONFLYDOJI)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

DragonflyDojiPattern = make_talib_wrapper(
    pattern_id="dragonfly_doji",
    talib_func_name="CDLDRAGONFLYDOJI",
    confidence=0.7,
)
