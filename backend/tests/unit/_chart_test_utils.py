"""Test fixtures for chart pattern tests — synthetic OHLCV builders."""
from __future__ import annotations

import numpy as np
import pandas as pd


def from_closes(
    closes: list[float] | np.ndarray, *, vol: float = 1_000.0
) -> pd.DataFrame:
    """Build OHLCV from a closes-only series (open=prev close, h/l = close ± 0.2%)."""
    closes_arr = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes_arr[0]], closes_arr[:-1]])
    highs = np.maximum(opens, closes_arr) * 1.002
    lows = np.minimum(opens, closes_arr) * 0.998
    idx = pd.date_range("2025-01-01", periods=len(closes_arr), freq="1h")
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes_arr,
            "volume": [vol] * len(closes_arr),
        },
        index=idx,
    )


def from_ohlcv(
    rows: list[tuple[float, float, float, float, float]],
) -> pd.DataFrame:
    """Build OHLCV from explicit (open, high, low, close, volume) tuples."""
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def flat_bars(n: int = 100, *, price: float = 100.0) -> pd.DataFrame:
    """N flat constant bars at `price` — should never trigger a chart pattern."""
    rows = [(price, price + 0.1, price - 0.1, price, 1_000.0)] * n
    return from_ohlcv(rows)


def random_walk(n: int = 100, *, seed: int = 42, vol: float = 0.5) -> pd.DataFrame:
    """Random walk closes with small wicks — used as 'no pattern' input."""
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.normal(0, vol, n))
    return from_closes(closes)
