"""Unit tests for app.core.regime.market_regime.

Covers the pure `compute_market_regime` classifier on synthetic OHLCV that
satisfies bull / bear / neutral / insufficient-data shapes, and the
24h-TTL cache short-circuit behavior of `get_cached_market_regime`.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd

from app.core.regime import market_regime as mr


def _make_ohlcv(closes: list[float]) -> pd.DataFrame:
    """Build a synthetic OHLCV frame from a closes series.

    high = close × 1.005, low = close × 0.995, open = previous close (or
    first close). Volume omitted — compute_market_regime ignores it.
    """
    n = len(closes)
    arr = np.asarray(closes, dtype=np.float64)
    highs = arr * 1.005
    lows = arr * 0.995
    opens = np.concatenate([[arr[0]], arr[:-1]])
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": arr},
        index=pd.RangeIndex(n),
    )


def test_regime_classifier_bull_when_emas_aligned_and_adx_strong() -> None:
    """Monotonic uptrend → price > EMA21 > EMA55 > EMA200, +DI > -DI, RSI > 50,
    ADX > 25 → bull."""
    # Tail-end accelerating uptrend so ADX is firmly above 25 and EMAs
    # stack cleanly. 300 bars rising from 100 → 350 with a small noise
    # term; closes monotonically increasing keeps RSI well above 50.
    closes = [100.0 + i + 0.05 * (i ** 1.4) for i in range(300)]
    df = _make_ohlcv(closes)
    assert mr.compute_market_regime(df) == "bull"


def test_regime_classifier_bear_when_emas_inverted() -> None:
    """Monotonic downtrend → inverted EMA stack, -DI > +DI, RSI < 50,
    ADX > 25 → bear."""
    # Mirror of the bull case — starts high, decays.
    closes = [350.0 - i - 0.05 * (i ** 1.4) for i in range(300)]
    # Floor to keep prices positive (the late-tail formula would dip below
    # zero around i=290 without clamping).
    closes = [max(c, 1.0) for c in closes]
    df = _make_ohlcv(closes)
    assert mr.compute_market_regime(df) == "bear"


def test_regime_classifier_neutral_when_adx_low() -> None:
    """Tight chop → ADX stays below 25 → neutral regardless of EMA stack."""
    # Sine-wave around a fixed midpoint: never trends, ADX stays low.
    closes = [100.0 + 0.5 * np.sin(i / 6.0) for i in range(300)]
    df = _make_ohlcv(closes)
    assert mr.compute_market_regime(df) == "neutral"


def test_regime_classifier_neutral_when_insufficient_data() -> None:
    """Shorter than EMA200 warm-up → neutral (fail-open)."""
    closes = [100.0 + i for i in range(50)]
    df = _make_ohlcv(closes)
    assert mr.compute_market_regime(df) == "neutral"


def test_regime_cache_hit_within_ttl() -> None:
    """Two calls inside the 24h TTL window → second returns cached value
    without triggering a fetch.

    Stubs out `_fetch_btc_daily_ohlcv` to return a known bull-shaped frame,
    then a sentinel that would crash if called again — verifying the cache
    short-circuits the second invocation.
    """
    mr.reset_cache_for_tests()

    call_count = {"n": 0}
    bull_df = _make_ohlcv([100.0 + i + 0.05 * (i ** 1.4) for i in range(300)])

    async def fake_fetch(http=None):  # noqa: ARG001 — match real signature
        call_count["n"] += 1
        return bull_df

    original = mr._fetch_btc_daily_ohlcv
    mr._fetch_btc_daily_ohlcv = fake_fetch  # type: ignore[assignment]
    try:
        # First call at t=0 — populates cache.
        regime_1 = asyncio.run(mr.get_cached_market_regime(now=0.0))
        # Second call 1 second later — must hit cache, no fetch.
        regime_2 = asyncio.run(mr.get_cached_market_regime(now=1.0))
    finally:
        mr._fetch_btc_daily_ohlcv = original  # type: ignore[assignment]

    assert regime_1 == "bull"
    assert regime_2 == "bull"
    assert call_count["n"] == 1, "second call must hit cache, not re-fetch"


def test_regime_cache_refetches_after_ttl() -> None:
    """After CACHE_TTL_SECONDS, the next call re-fetches."""
    mr.reset_cache_for_tests()

    call_count = {"n": 0}
    bull_df = _make_ohlcv([100.0 + i + 0.05 * (i ** 1.4) for i in range(300)])

    async def fake_fetch(http=None):  # noqa: ARG001
        call_count["n"] += 1
        return bull_df

    original = mr._fetch_btc_daily_ohlcv
    mr._fetch_btc_daily_ohlcv = fake_fetch  # type: ignore[assignment]
    try:
        asyncio.run(mr.get_cached_market_regime(now=0.0))
        # Jump past the TTL — a fresh fetch must fire.
        asyncio.run(mr.get_cached_market_regime(now=float(mr.CACHE_TTL_SECONDS + 1)))
    finally:
        mr._fetch_btc_daily_ohlcv = original  # type: ignore[assignment]

    assert call_count["n"] == 2, "second call past TTL must re-fetch"


def test_regime_fetch_failure_returns_stale_or_none() -> None:
    """When the network fetch returns None (failure path), the cached value
    is returned (or None when the cache is also cold). The gate caller
    treats None as 'no opinion, do not block'."""
    mr.reset_cache_for_tests()

    async def fake_fail(http=None):  # noqa: ARG001
        return None

    original = mr._fetch_btc_daily_ohlcv
    mr._fetch_btc_daily_ohlcv = fake_fail  # type: ignore[assignment]
    try:
        result = asyncio.run(mr.get_cached_market_regime(now=0.0))
    finally:
        mr._fetch_btc_daily_ohlcv = original  # type: ignore[assignment]

    assert result is None, "cold cache + fetch failure → None"
