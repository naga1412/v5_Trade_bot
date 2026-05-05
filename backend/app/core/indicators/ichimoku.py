import numpy as np
from numpy.typing import NDArray


def _rolling_mid(
    highs: NDArray[np.float64], lows: NDArray[np.float64], period: int
) -> NDArray[np.float64]:
    """Midpoint of rolling high/low: (max(highs[-n:]) + min(lows[-n:])) / 2."""
    n = highs.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(period - 1, n):
        h = highs[i - period + 1 : i + 1].max()
        low = lows[i - period + 1 : i + 1].min()
        out[i] = (h + low) / 2.0
    return out


def ichimoku(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
    displacement: int = 26,
) -> tuple[
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
]:
    """Ichimoku Cloud → (tenkan, kijun, senkou_a, senkou_b, chikou).

    Tenkan-sen (Conversion line):  rolling 9-period midpoint
    Kijun-sen (Base line):         rolling 26-period midpoint
    Senkou A (Leading span A):     (tenkan + kijun)/2 shifted +26 bars
    Senkou B (Leading span B):     rolling 52-period midpoint shifted +26 bars
    Chikou (Lagging span):         close shifted -26 bars

    Forward-shifted values write past the end of the array land out-of-bounds
    (i.e., are not visible at any current bar). Leading shifts produce NaN at
    the head. No look-ahead at index i (each output uses only bars <= i).
    """
    if min(tenkan_period, kijun_period, senkou_b_period, displacement) <= 0:
        raise ValueError("all period/displacement values must be positive")
    if highs.shape != lows.shape or highs.shape != closes.shape:
        raise ValueError("highs/lows/closes must have identical shape")

    n = highs.shape[0]
    tenkan = _rolling_mid(highs, lows, tenkan_period)
    kijun = _rolling_mid(highs, lows, kijun_period)

    senkou_a_raw = (tenkan + kijun) / 2.0
    senkou_a = np.full(n, np.nan, dtype=np.float64)
    if displacement < n:
        senkou_a[displacement:] = senkou_a_raw[: n - displacement]

    senkou_b_raw = _rolling_mid(highs, lows, senkou_b_period)
    senkou_b = np.full(n, np.nan, dtype=np.float64)
    if displacement < n:
        senkou_b[displacement:] = senkou_b_raw[: n - displacement]

    chikou = np.full(n, np.nan, dtype=np.float64)
    if displacement < n:
        chikou[: n - displacement] = closes[displacement:]

    return tenkan, kijun, senkou_a, senkou_b, chikou
