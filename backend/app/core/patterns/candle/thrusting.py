"""Thrusting Line — bearish continuation (TA-Lib CDLTHRUSTING)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

ThrustingPattern = make_talib_wrapper(
    pattern_id="thrusting",
    talib_func_name="CDLTHRUSTING",
    confidence=0.7,
)
