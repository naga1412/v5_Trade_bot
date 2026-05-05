"""Adapter registry + factory (SP-3 Phase F).

Lazy construction: adapters are instantiated on first ``get_adapter()`` call,
not at import. The shared httpx.AsyncClient is created on first use; call
``aclose_all()`` from app shutdown to clean up.
"""
from __future__ import annotations

from collections.abc import Callable

import httpx

from app.config import get_settings
from app.data.adapters._base import ExchangeAdapter

__all__ = [
    "AdapterNotRegistered",
    "ExchangeAdapter",
    "aclose_all",
    "get_adapter",
    "list_registered",
]


class AdapterNotRegistered(KeyError):
    """No adapter registered under ``name``."""


_HTTP: httpx.AsyncClient | None = None
_INSTANCES: dict[str, ExchangeAdapter] = {}


def _shared_http() -> httpx.AsyncClient:
    global _HTTP
    if _HTTP is None:
        _HTTP = httpx.AsyncClient(timeout=30.0)
    return _HTTP


def _make_binance() -> ExchangeAdapter:
    from app.data.adapters.binance import BinanceAdapter
    return BinanceAdapter(http=_shared_http())


def _make_bybit() -> ExchangeAdapter:
    from app.data.adapters.bybit import BybitAdapter
    return BybitAdapter(http=_shared_http())


def _make_yahoo() -> ExchangeAdapter:
    from app.data.adapters.yahoo import YahooAdapter
    return YahooAdapter(http=_shared_http())


def _make_twelvedata() -> ExchangeAdapter:
    from app.data.adapters.twelvedata import TwelveDataAdapter
    settings = get_settings()
    apikey = getattr(settings, "twelvedata_api_key", None) or "dev-noop-key"
    return TwelveDataAdapter(http=_shared_http(), apikey=apikey)


_FACTORIES: dict[str, Callable[[], ExchangeAdapter]] = {
    "binance": _make_binance,
    "bybit": _make_bybit,
    "yahoo": _make_yahoo,
    "twelvedata": _make_twelvedata,
}


def list_registered() -> list[str]:
    """Return the names of all registered adapters."""
    return list(_FACTORIES.keys())


def get_adapter(name: str) -> ExchangeAdapter:
    """Return the cached adapter instance for ``name`` (constructs on first use).

    Raises ``AdapterNotRegistered`` if no factory is registered for ``name``.
    """
    key = (name or "").lower().strip()
    if key not in _FACTORIES:
        raise AdapterNotRegistered(name)
    if key not in _INSTANCES:
        _INSTANCES[key] = _FACTORIES[key]()
    return _INSTANCES[key]


async def aclose_all() -> None:
    """Close the shared httpx client + clear cached adapter instances."""
    global _HTTP
    _INSTANCES.clear()
    if _HTTP is not None:
        await _HTTP.aclose()
        _HTTP = None
