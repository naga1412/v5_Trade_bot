"""Smoke tests for the SP-3 ExchangeAdapter Protocol surface."""
from datetime import datetime, timezone
from typing import get_type_hints

import pytest


def test_candle_dataclass_is_frozen_and_typed() -> None:
    from app.data.adapters._base import Candle

    c = Candle(
        ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=100.0, high=110.0, low=99.0, close=105.0, volume=1234.5,
    )
    assert c.open == 100.0
    with pytest.raises(Exception):  # frozen → assignment raises
        c.open = 999.0  # type: ignore[misc]


def test_symbol_info_carries_canonical_and_native() -> None:
    from app.data.adapters._base import SymbolInfo

    s = SymbolInfo(
        canonical="BTC/USDT",
        native="BTCUSDT",
        base="BTC",
        quote="USDT",
        listed_at=datetime(2017, 8, 17, tzinfo=timezone.utc),
        delisted_at=None,
        asset_class="crypto",
    )
    assert s.canonical == "BTC/USDT"
    assert s.delisted_at is None


def test_symbol_info_asset_class_literal_accepts_all_five() -> None:
    from app.data.adapters._base import SymbolInfo

    for cls in ("crypto", "stock", "fx", "commodity", "index"):
        s = SymbolInfo(
            canonical="X", native="X", base="X", quote="",
            listed_at=None, delisted_at=None, asset_class=cls,  # type: ignore[arg-type]
        )
        assert s.asset_class == cls


def test_exchange_adapter_protocol_has_required_methods() -> None:
    from app.data.adapters._base import ExchangeAdapter

    # Verify the Protocol declares fetch_klines + list_symbols + name attribute.
    hints = get_type_hints(ExchangeAdapter)
    assert "name" in hints
    # Methods are declared on the Protocol body (not in __annotations__);
    # check via hasattr on the class itself.
    assert hasattr(ExchangeAdapter, "fetch_klines")
    assert hasattr(ExchangeAdapter, "list_symbols")


@pytest.mark.asyncio
async def test_protocol_runtime_check_with_minimal_implementation() -> None:
    """A class implementing fetch_klines + list_symbols + name should satisfy the Protocol."""
    from app.data.adapters._base import Candle, ExchangeAdapter, SymbolInfo

    class FakeAdapter:
        name = "fake"

        async def fetch_klines(
            self, *, symbol: str, timeframe: str,
            limit: int = 500,
            start: datetime | None = None, end: datetime | None = None,
        ) -> list[Candle]:
            return []

        async def list_symbols(self) -> list[SymbolInfo]:
            return []

    adapter: ExchangeAdapter = FakeAdapter()
    assert (await adapter.fetch_klines(symbol="X", timeframe="1h")) == []
    assert (await adapter.list_symbols()) == []
