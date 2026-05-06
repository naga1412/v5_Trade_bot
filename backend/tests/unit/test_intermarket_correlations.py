from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.data.intermarket_correlations import compute_30d_correlations


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
    asc = [100 + i for i in range(30)]
    desc = list(reversed(asc))
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
