from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.data import intermarket_correlations as ic
from app.data.intermarket_correlations import compute_30d_correlations


@pytest.fixture(autouse=True)
def _clear_correlation_cache() -> None:
    """The 1h dict cache is module-level; reset between tests."""
    ic._cache.clear()


def _candles(closes: list[float]) -> list:
    """Build a list of objects with .ts and .close attrs (Candle-shaped)."""
    base = datetime(2026, 4, 6, tzinfo=timezone.utc)
    out = []
    for i, c in enumerate(closes):
        out.append(MagicMock(ts=base + timedelta(days=i), close=c))
    return out


@pytest.mark.asyncio
async def test_correlations_positive_when_series_move_together() -> None:
    closes = [100 + i for i in range(30)]
    binance = MagicMock()
    binance.fetch_klines = AsyncMock(return_value=_candles(closes))
    yahoo = MagicMock()
    yahoo.fetch_klines = AsyncMock(return_value=_candles(closes))
    dxy, gold = await compute_30d_correlations(
        "BTC/USDT", binance_adapter=binance, yahoo_adapter=yahoo,
    )
    assert dxy is not None
    assert dxy > 0.95   # near-perfect positive
    assert gold is not None
    assert gold > 0.95


@pytest.mark.asyncio
async def test_correlations_inverse_returns_negative() -> None:
    # Pearson correlation is computed on PCT-CHANGE series, not on the closes
    # themselves — `[100..129]` and `reversed([100..129])` have the same
    # divisor pattern and end up positively correlated. Construct asc and
    # desc so their pct-change series are exact negatives of each other:
    # apply the same return sequence in opposite signs.
    rets = [0.01, -0.005, 0.012, -0.008, 0.015, -0.01, 0.007, -0.004,
            0.02, -0.012, 0.006, 0.018, -0.009, 0.011, -0.006,
            0.013, -0.007, 0.009, 0.016, -0.011, 0.005, 0.014,
            -0.003, 0.017, -0.013, 0.008, 0.019, -0.015, 0.022]  # 29 returns
    asc = [100.0]
    desc = [100.0]
    for r in rets:
        asc.append(asc[-1] * (1 + r))
        desc.append(desc[-1] * (1 - r))

    binance = MagicMock()
    binance.fetch_klines = AsyncMock(return_value=_candles(asc))
    yahoo = MagicMock()
    yahoo.fetch_klines = AsyncMock(side_effect=[_candles(desc), _candles(asc)])
    dxy, gold = await compute_30d_correlations(
        "BTC/USDT", binance_adapter=binance, yahoo_adapter=yahoo,
    )
    assert dxy is not None and dxy < -0.95
    assert gold is not None and gold > 0.95


@pytest.mark.asyncio
async def test_correlations_returns_none_on_yahoo_failure() -> None:
    binance = MagicMock()
    binance.fetch_klines = AsyncMock(return_value=_candles([100 + i for i in range(30)]))
    yahoo = MagicMock()
    yahoo.fetch_klines = AsyncMock(side_effect=RuntimeError("yahoo down"))
    dxy, gold = await compute_30d_correlations(
        "BTC/USDT", binance_adapter=binance, yahoo_adapter=yahoo,
    )
    assert dxy is None
    assert gold is None


@pytest.mark.asyncio
async def test_correlations_returns_none_when_too_few_samples() -> None:
    binance = MagicMock()
    binance.fetch_klines = AsyncMock(return_value=_candles([100, 101, 102]))
    yahoo = MagicMock()
    yahoo.fetch_klines = AsyncMock(return_value=_candles([10, 11, 12]))
    dxy, gold = await compute_30d_correlations(
        "BTC/USDT", binance_adapter=binance, yahoo_adapter=yahoo,
    )
    assert dxy is None
    assert gold is None
