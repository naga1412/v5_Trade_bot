"""W3 BTC-spread feature computer.

Pure function (except for the module-level BTC close cache).
Returns a dict with float values (or None when data is insufficient).

Called at two sites:
  - predictor.py: stashes result in prediction_extras["features"]
  - shadow/observation.py: stores result in obs_components["features"]

The shadow worker's _handle_candle updates the BTC close cache whenever
a BTCUSDT/1h candle closes. Staleness bound: up to one 1h candle —
immaterial for a 30-day z-score.

Spec: docs/superpowers/specs/2026-07-18-brain-supervisor-expansion.md §3.4
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_MIN_BARS = 720  # 30 days × 24 h per candle

# Module-level BTC close cache. Updated by shadow/worker.py when a
# BTCUSDT/1h candle closes and by predictor.py when scoring BTCUSDT.
# None until the first BTCUSDT candle is seen after startup.
_BTC_CLOSE: float | None = None


def update_btc_close(close: float) -> None:
    """Record the most recent BTCUSDT 1h close. Thread-safe via the GIL."""
    global _BTC_CLOSE
    _BTC_CLOSE = float(close)


def get_cached_btc_close() -> float | None:
    """Return the most recent cached BTC close, or None if unavailable."""
    return _BTC_CLOSE


def compute(bars: pd.DataFrame) -> dict[str, float | None]:
    """Return W3 BTC-spread features for the latest bar.

    Keys:
        alt_btc_log_zscore: z-score of log(alt_close / btc_close) over
            trailing 720 bars. Positive = alt running above its 30-day
            log-ratio average; negative = below. None when < 720 bars or
            BTC close is unavailable.
    """
    NULL: dict[str, float | None] = {"alt_btc_log_zscore": None}
    btc = _BTC_CLOSE
    if btc is None or btc <= 0:
        return NULL
    if len(bars) < _MIN_BARS:
        return NULL

    closes = bars["close"].to_numpy(dtype=float)[-_MIN_BARS:]
    ratios = np.log(np.maximum(closes, 1e-12) / btc)
    mean = float(np.mean(ratios))
    std = float(np.std(ratios, ddof=1))
    if not np.isfinite(std):
        return NULL
    if std == 0.0:
        return {"alt_btc_log_zscore": 0.0}
    z = float((ratios[-1] - mean) / std)
    if not np.isfinite(z):
        return NULL
    return {"alt_btc_log_zscore": z}
