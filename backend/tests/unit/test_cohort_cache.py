"""Unit tests for app.shadow.cohort_cache -- item 0 (2026-08-30).

Covers the three operator-mandated constraints directly:
  1. Baseline loads once, never re-reads (mocked session.execute call
     count stays at 1 across repeated load_baseline_cache_once calls).
  2. futures_only refresh failure leaves the PRIOR cache in place,
     never wipes it to None.
  3. Sync getters return None (never a guessed value) before anything
     has ever loaded successfully.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from app.shadow import cohort_cache


@pytest.fixture(autouse=True)
def _reset() -> Any:
    cohort_cache._reset_for_tests()
    yield
    cohort_cache._reset_for_tests()


@pytest.mark.asyncio
async def test_get_baseline_cache_none_before_first_load() -> None:
    assert cohort_cache.get_baseline_cache() is None


@pytest.mark.asyncio
async def test_get_futures_only_cache_none_before_first_load() -> None:
    assert cohort_cache.get_futures_only_cache() is None
    assert cohort_cache.get_futures_only_cache_age_seconds() is None


@pytest.mark.asyncio
async def test_load_baseline_cache_once_loads_exactly_once() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=[
        type("Row", (), {"symbol": "BTCUSDT"})(),
        type("Row", (), {"symbol": "ETHUSDT"})(),
    ])

    first = await cohort_cache.load_baseline_cache_once(session)
    assert first == {"BTCUSDT", "ETHUSDT"}
    assert session.execute.call_count == 1

    # Second call: same session or a different one -- must NOT re-query.
    second_session = AsyncMock()
    second = await cohort_cache.load_baseline_cache_once(second_session)
    assert second == {"BTCUSDT", "ETHUSDT"}
    second_session.execute.assert_not_called()
    assert cohort_cache.get_baseline_cache() == {"BTCUSDT", "ETHUSDT"}


@pytest.mark.asyncio
async def test_load_baseline_cache_once_raises_on_failure_and_stays_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_loader(_session: Any) -> set[str]:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(cohort_cache, "load_baseline_symbols", failing_loader)

    with pytest.raises(RuntimeError):
        await cohort_cache.load_baseline_cache_once(AsyncMock())

    # Never partially populated -- still None, so callers correctly see
    # "cannot classify" rather than an empty-but-truthy cache.
    assert cohort_cache.get_baseline_cache() is None


@pytest.mark.asyncio
async def test_refresh_futures_only_cache_success_updates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(_http: httpx.AsyncClient) -> tuple[set[str], set[str]]:
        return {"BTCUSDT", "XPLUSDT"}, {"XPLUSDT"}

    monkeypatch.setattr(
        cohort_cache, "fetch_futures_and_futures_only_symbols", fake_fetch,
    )

    result = await cohort_cache.refresh_futures_only_cache(AsyncMock())
    assert result == {"XPLUSDT"}
    assert cohort_cache.get_futures_only_cache() == {"XPLUSDT"}
    assert cohort_cache.get_futures_only_cache_age_seconds() is not None
    assert cohort_cache.get_futures_only_cache_age_seconds() < 5.0


@pytest.mark.asyncio
async def test_refresh_futures_only_cache_failure_preserves_prior_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact behavior the operator asked for: a transient failure
    must NOT wipe a good cache down to None -- that would turn every
    position open for the next 24h into a forced NULL until the next
    successful cycle, over one blip."""
    async def fake_fetch_ok(_http: httpx.AsyncClient) -> tuple[set[str], set[str]]:
        return {"BTCUSDT", "XPLUSDT"}, {"XPLUSDT"}

    monkeypatch.setattr(
        cohort_cache, "fetch_futures_and_futures_only_symbols", fake_fetch_ok,
    )
    await cohort_cache.refresh_futures_only_cache(AsyncMock())
    assert cohort_cache.get_futures_only_cache() == {"XPLUSDT"}

    async def fake_fetch_fails(_http: httpx.AsyncClient) -> tuple[set[str], set[str]]:
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(
        cohort_cache, "fetch_futures_and_futures_only_symbols", fake_fetch_fails,
    )
    result = await cohort_cache.refresh_futures_only_cache(AsyncMock())
    assert result is None
    # Prior value untouched.
    assert cohort_cache.get_futures_only_cache() == {"XPLUSDT"}


@pytest.mark.asyncio
async def test_refresh_futures_only_cache_failure_before_any_success_stays_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_fails(_http: httpx.AsyncClient) -> tuple[set[str], set[str]]:
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(
        cohort_cache, "fetch_futures_and_futures_only_symbols", fake_fetch_fails,
    )
    result = await cohort_cache.refresh_futures_only_cache(AsyncMock())
    assert result is None
    assert cohort_cache.get_futures_only_cache() is None
