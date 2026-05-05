"""Homing Pigeon — bullish reversal (TA-Lib CDLHOMINGPIGEON)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

HomingPigeonPattern = make_talib_wrapper(
    pattern_id="homing_pigeon",
    talib_func_name="CDLHOMINGPIGEON",
    confidence=0.7,
)
