"""Dark Cloud Cover — bearish reversal (TA-Lib CDLDARKCLOUDCOVER)."""
from app.core.patterns.candle._talib_helpers import make_talib_wrapper

DarkCloudCoverPattern = make_talib_wrapper(
    pattern_id="dark_cloud_cover",
    talib_func_name="CDLDARKCLOUDCOVER",
    confidence=0.7,
)
