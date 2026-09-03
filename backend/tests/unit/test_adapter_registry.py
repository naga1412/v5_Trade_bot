"""Unit tests for app.data.adapters registry (SP-3 Phase F)."""
import pytest

import app.data.adapters as adapters
from app.data.adapters import (
    AdapterNotRegistered,
    aclose_all,
    get_adapter,
    list_registered,
)


@pytest.fixture(autouse=True)
def _reset_adapter_registry():
    """Full-suite flake fix: _HTTP/_INSTANCES are module-level globals, but
    pytest-asyncio gives each async test its own event loop by default. A
    prior test elsewhere in the suite that calls get_adapter() without also
    calling aclose_all() leaves _HTTP bound to ITS (now-closed) loop; this
    test's own aclose_all() then fails with "RuntimeError: Event loop is
    closed" trying to close a client from a dead loop. Reset before AND
    after so this module is isolated regardless of what ran before or
    after it in full-suite collection order (same pattern as conftest.py's
    _pause_state_clean for app.ops.pause_state's own module-level state)."""
    adapters._HTTP = None
    adapters._INSTANCES = {}
    adapters._INTERMARKET_INSTANCE = None
    yield
    adapters._HTTP = None
    adapters._INSTANCES = {}
    adapters._INTERMARKET_INSTANCE = None


def test_list_registered_returns_known_exchanges() -> None:
    names = set(list_registered())
    # binance-futures added in SP-9 alongside the spot binance adapter so
    # the symbol-search dropdown surfaces USDT-margined perpetuals too.
    assert names == {
        "binance", "binance-futures", "bybit", "yahoo", "twelvedata",
    }


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
