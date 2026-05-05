"""Unit tests for app.data.adapters registry (SP-3 Phase F)."""
import pytest

from app.data.adapters import (
    AdapterNotRegistered,
    aclose_all,
    get_adapter,
    list_registered,
)


def test_list_registered_returns_four_known_exchanges() -> None:
    names = set(list_registered())
    assert names == {"binance", "bybit", "yahoo", "twelvedata"}


@pytest.mark.asyncio
async def test_get_adapter_returns_singleton_per_name() -> None:
    a1 = get_adapter("binance")
    a2 = get_adapter("binance")
    assert a1 is a2
    await aclose_all()


@pytest.mark.asyncio
async def test_get_adapter_unknown_raises() -> None:
    with pytest.raises(AdapterNotRegistered):
        get_adapter("kraken")
