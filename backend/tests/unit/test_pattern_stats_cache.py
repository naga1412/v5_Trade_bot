"""SP-2 Phase E task E4 — process-memory cache for PatternStatsLookup.

The live + shadow workers want to load ``pattern_stats`` for their
``(symbol, timeframe)`` ONCE per process and reuse the result on every closed
candle. This test exercises the cache hit path (no second DB call), the
miss path (single DB call), and the invalidation hooks.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.core.scoring import _pattern_stats_cache as cache_mod
from app.core.scoring._pattern_stats_cache import (
    PatternStatsCache,
    get_or_load,
    invalidate,
)
from app.core.scoring.layer2_patterns import PatternStatsLookup


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    invalidate()
    yield
    invalidate()


class _FakeSession:
    """Minimal stand-in we can hand to ``load_pattern_stats``."""

    def __init__(self) -> None:
        self.execute_calls = 0

    async def execute(self, _stmt: Any, _params: Any = None) -> "_FakeResult":
        self.execute_calls += 1
        return _FakeResult([])


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


@pytest.mark.asyncio
async def test_get_or_load_caches_after_first_call() -> None:
    session = _FakeSession()
    a = await get_or_load(session, symbol="BTC/USDT", timeframe="1h")  # type: ignore[arg-type]
    b = await get_or_load(session, symbol="BTC/USDT", timeframe="1h")  # type: ignore[arg-type]
    assert a is b
    assert session.execute_calls == 1


@pytest.mark.asyncio
async def test_different_keys_load_independently() -> None:
    session = _FakeSession()
    await get_or_load(session, symbol="BTC/USDT", timeframe="1h")  # type: ignore[arg-type]
    await get_or_load(session, symbol="ETH/USDT", timeframe="1h")  # type: ignore[arg-type]
    await get_or_load(session, symbol="BTC/USDT", timeframe="4h")  # type: ignore[arg-type]
    assert session.execute_calls == 3


@pytest.mark.asyncio
async def test_invalidate_specific_key_forces_reload() -> None:
    session = _FakeSession()
    await get_or_load(session, symbol="BTC/USDT", timeframe="1h")  # type: ignore[arg-type]
    invalidate(symbol="BTC/USDT", timeframe="1h")
    await get_or_load(session, symbol="BTC/USDT", timeframe="1h")  # type: ignore[arg-type]
    assert session.execute_calls == 2


@pytest.mark.asyncio
async def test_invalidate_by_symbol_only_drops_all_timeframes_for_symbol() -> None:
    session = _FakeSession()
    await get_or_load(session, symbol="BTC/USDT", timeframe="1h")  # type: ignore[arg-type]
    await get_or_load(session, symbol="BTC/USDT", timeframe="4h")  # type: ignore[arg-type]
    await get_or_load(session, symbol="ETH/USDT", timeframe="1h")  # type: ignore[arg-type]
    invalidate(symbol="BTC/USDT")
    await get_or_load(session, symbol="BTC/USDT", timeframe="1h")  # type: ignore[arg-type]
    await get_or_load(session, symbol="ETH/USDT", timeframe="1h")  # type: ignore[arg-type]
    # 3 first round + 1 (BTC reload) — ETH still cached.
    assert session.execute_calls == 4


@pytest.mark.asyncio
async def test_invalidate_all_when_no_args_drops_everything() -> None:
    session = _FakeSession()
    await get_or_load(session, symbol="BTC/USDT", timeframe="1h")  # type: ignore[arg-type]
    await get_or_load(session, symbol="ETH/USDT", timeframe="1h")  # type: ignore[arg-type]
    invalidate()
    await get_or_load(session, symbol="BTC/USDT", timeframe="1h")  # type: ignore[arg-type]
    await get_or_load(session, symbol="ETH/USDT", timeframe="1h")  # type: ignore[arg-type]
    assert session.execute_calls == 4


@pytest.mark.asyncio
async def test_get_or_load_returns_pattern_stats_lookup_instance() -> None:
    session = _FakeSession()
    out = await get_or_load(session, symbol="BTC/USDT", timeframe="1h")  # type: ignore[arg-type]
    assert isinstance(out, PatternStatsLookup)


def test_pattern_stats_cache_module_exports_helpers() -> None:
    """Public surface check — workers import these names."""
    assert hasattr(cache_mod, "get_or_load")
    assert hasattr(cache_mod, "invalidate")
    assert hasattr(cache_mod, "PatternStatsCache")
    assert isinstance(PatternStatsCache, type)
