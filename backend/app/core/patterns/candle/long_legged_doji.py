"""Long Legged Doji — high-volatility indecision (TA-Lib CDLLONGLEGGEDDOJI)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

LongLeggedDojiPattern = make_talib_wrapper(
    pattern_id="long_legged_doji",
    talib_func_name="CDLLONGLEGGEDDOJI",
    confidence=0.7,
)
