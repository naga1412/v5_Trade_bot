"""Unit tests for YahooAdapter (SP-3 Phase D — >=10 tests)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.data.adapters._base import ExchangeAdapter
from app.data.adapters.yahoo import YahooAdapter


def _fake_df(rows: list[tuple]) -> pd.DataFrame:
    """Build a yfinance-shaped DataFrame.

    Each row is `(day, open, high, low, close, adj_close, volume)` where
    `day` becomes the row's DatetimeIndex entry (May 2026) and the
    remaining six values populate the OHLC + Adj Close + Volume columns.
    """
    return pd.DataFrame(
        [r[1:] for r in rows],
        columns=["Open", "High", "Low", "Close", "Adj Close", "Volume"],
        index=pd.DatetimeIndex(
            [datetime(2026, 5, r[0], tzinfo=timezone.utc) for r in rows],
            name="Datetime",
        ),
    )


@pytest.mark.asyncio
async def test_yahoo_adapter_satisfies_protocol() -> None:
    adapter = YahooAdapter(_download=MagicMock())
    assert isinstance(adapter, ExchangeAdapter)
    assert adapter.name == "yahoo"


@pytest.mark.asyncio
async def test_fetch_klines_crypto_translates_to_btc_usd() -> None:
    fake_dl = MagicMock(return_value=_fake_df(
        [(1, 65000, 65500, 64800, 65300, 65300, 1234)],
    ))
    adapter = YahooAdapter(_download=fake_dl)
    bars = await adapter.fetch_klines(symbol="BTC/USDT", timeframe="1d", limit=1)
    assert fake_dl.call_args.kwargs.get("tickers") == "BTC-USD"
    assert len(bars) == 1
    assert bars[0].close == 65300


@pytest.mark.asyncio
async def test_fetch_klines_stock_passthrough() -> None:
    fake_dl = MagicMock(return_value=_fake_df(
        [(1, 175.0, 178.0, 174.5, 177.5, 177.5, 5_000_000)],
    ))
    adapter = YahooAdapter(_download=fake_dl)
    bars = await adapter.fetch_klines(symbol="AAPL", timeframe="1d", limit=1)
    assert fake_dl.call_args.kwargs.get("tickers") == "AAPL"
    assert bars[0].close == 177.5


@pytest.mark.asyncio
async def test_fetch_klines_fx_appends_x_suffix() -> None:
    fake_dl = MagicMock(return_value=_fake_df(
        [(1, 1.10, 1.11, 1.09, 1.105, 1.105, 0)],
    ))
    adapter = YahooAdapter(_download=fake_dl)
    bars = await adapter.fetch_klines(symbol="EUR/USD", timeframe="1d", limit=1)
    assert fake_dl.call_args.kwargs.get("tickers") == "EURUSD=X"
    assert len(bars) == 1


@pytest.mark.asyncio
async def test_fetch_klines_empty_dataframe_returns_empty() -> None:
    fake_dl = MagicMock(return_value=pd.DataFrame())
    adapter = YahooAdapter(_download=fake_dl)
    bars = await adapter.fetch_klines(symbol="AAPL", timeframe="1d")
    assert bars == []


@pytest.mark.asyncio
async def test_fetch_klines_network_error_returns_empty() -> None:
    fake_dl = MagicMock(side_effect=ConnectionError("yahoo timeout"))
    adapter = YahooAdapter(_download=fake_dl)
    bars = await adapter.fetch_klines(symbol="AAPL", timeframe="1d")
    assert bars == []


@pytest.mark.asyncio
async def test_list_symbols_returns_empty_per_design() -> None:
    """Spec section 3.4: Yahoo has no list-all endpoint."""
    adapter = YahooAdapter(_download=MagicMock())
    symbols = await adapter.list_symbols()
    assert symbols == []


@pytest.mark.asyncio
async def test_unsupported_4h_timeframe_raises() -> None:
    fake_dl = MagicMock()
    adapter = YahooAdapter(_download=fake_dl)
    with pytest.raises(ValueError, match="4h"):
        await adapter.fetch_klines(symbol="AAPL", timeframe="4h")
    fake_dl.assert_not_called()


@pytest.mark.asyncio
async def test_self_throttle_drains_bucket() -> None:
    fake_dl = MagicMock(return_value=pd.DataFrame())
    adapter = YahooAdapter(_download=fake_dl)
    assert adapter.rate_client is not None
    bucket = adapter.rate_client.buckets["default"]
    before = bucket.tokens
    await adapter.fetch_klines(symbol="AAPL", timeframe="1d")
    assert bucket.tokens < before


@pytest.mark.asyncio
async def test_dxy_index_uses_explicit_override() -> None:
    fake_dl = MagicMock(return_value=_fake_df(
        [(1, 100.0, 101.0, 99.5, 100.5, 100.5, 0)],
    ))
    adapter = YahooAdapter(_download=fake_dl)
    bars = await adapter.fetch_klines(symbol="DXY", timeframe="1d")
    assert fake_dl.call_args.kwargs.get("tickers") == "DX-Y.NYB"
    assert len(bars) == 1


@pytest.mark.asyncio
async def test_unknown_symbol_raises_unknown_symbol_error() -> None:
    """No mapping for an arbitrary string -> caller sees a clean error."""
    from app.data.symbols import UnknownSymbolError
    fake_dl = MagicMock()
    adapter = YahooAdapter(_download=fake_dl)
    with pytest.raises(UnknownSymbolError):
        await adapter.fetch_klines(symbol="UNKNOWN-NOT-IN-MAP", timeframe="1d")
    fake_dl.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_klines_uses_asyncio_to_thread(monkeypatch) -> None:
    """yfinance is sync — adapter must dispatch via asyncio.to_thread."""
    import asyncio

    calls: list[tuple] = []
    real_to_thread = asyncio.to_thread

    async def spy(func, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((func, args, kwargs))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr("app.data.adapters.yahoo.asyncio.to_thread", spy)

    fake_dl = MagicMock(return_value=_fake_df(
        [(1, 1.0, 2.0, 0.5, 1.5, 1.5, 100)],
    ))
    adapter = YahooAdapter(_download=fake_dl)
    bars = await adapter.fetch_klines(symbol="AAPL", timeframe="1d", limit=1)
    assert len(calls) == 1
    assert calls[0][0] is fake_dl
    assert len(bars) == 1


@pytest.mark.asyncio
async def test_fetch_klines_passes_interval_for_hourly() -> None:
    """1h timeframe maps to yfinance interval 60m."""
    fake_dl = MagicMock(return_value=_fake_df(
        [(1, 1.0, 2.0, 0.5, 1.5, 1.5, 100)],
    ))
    adapter = YahooAdapter(_download=fake_dl)
    await adapter.fetch_klines(symbol="AAPL", timeframe="1h", limit=24)
    assert fake_dl.call_args.kwargs.get("interval") == "60m"


@pytest.mark.asyncio
async def test_fetch_klines_with_start_end_uses_date_range() -> None:
    """When start is supplied, kwargs use start/end (not period)."""
    fake_dl = MagicMock(return_value=_fake_df(
        [(1, 1.0, 2.0, 0.5, 1.5, 1.5, 100)],
    ))
    adapter = YahooAdapter(_download=fake_dl)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 1, tzinfo=timezone.utc)
    await adapter.fetch_klines(
        symbol="AAPL", timeframe="1d", start=start, end=end,
    )
    kwargs = fake_dl.call_args.kwargs
    assert kwargs.get("start") == start
    assert kwargs.get("end") == end
    assert "period" not in kwargs
