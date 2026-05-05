"""Shooting Star — bearish reversal (TA-Lib CDLSHOOTINGSTAR)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

ShootingStarPattern = make_talib_wrapper(
    pattern_id="shooting_star",
    talib_func_name="CDLSHOOTINGSTAR",
    confidence=0.7,
)
