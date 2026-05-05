"""Counterattack — reversal with matching closes (TA-Lib CDLCOUNTERATTACK)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

CounterattackPattern = make_talib_wrapper(
    pattern_id="counterattack",
    talib_func_name="CDLCOUNTERATTACK",
    confidence=0.7,
)
