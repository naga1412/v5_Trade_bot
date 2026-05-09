"""SP-8 Phase H — INR converter cache + fallback tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.trading.tax.inr_converter import (
    FXRateUnavailable,
    get_usd_inr,
)


_NOW = datetime(2026, 5, 9, 12, 30, 45, tzinfo=timezone.utc)


async def _setup_db() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE fx_rates ("
            " pair TEXT NOT NULL, ts TEXT NOT NULL,"
            " rate REAL NOT NULL, source TEXT NOT NULL,"
            " PRIMARY KEY (pair, ts))"
        ))
    return engine


def _make_http(rate: float | None = None, *, error: Exception | None = None) -> httpx.AsyncClient:
    """Build a stub httpx client backed by an MockTransport."""
    def handler(request: httpx.Request) -> httpx.Response:
        if error is not None:
            raise error
        return httpx.Response(
            200, json={"rates": {"INR": rate}, "base": "USD"},
        )
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---- Happy path ----------------------------------------------------------


@pytest.mark.asyncio
async def test_first_call_fetches_and_caches() -> None:
    engine = await _setup_db()
    async with _make_http(rate=83.42) as http:
        async with AsyncSession(engine) as s:
            rate = await get_usd_inr(s, now=_NOW, http=http)
            await s.commit()
            assert rate == pytest.approx(83.42)
            # Cache row exists for the minute bucket
            row = (await s.execute(sa.text(
                "SELECT rate FROM fx_rates WHERE pair='USDINR'"
            ))).first()
            assert row is not None
            assert float(row.rate) == pytest.approx(83.42)


@pytest.mark.asyncio
async def test_second_call_in_same_minute_uses_cache() -> None:
    """Second call within the same minute bucket must NOT hit the API."""
    engine = await _setup_db()
    fetch_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fetch_count
        fetch_count += 1
        return httpx.Response(200, json={"rates": {"INR": 83.42}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with AsyncSession(engine) as s:
            r1 = await get_usd_inr(s, now=_NOW, http=http)
            await s.commit()
            # +5s stays in the same minute bucket (_NOW.second is 45 → 50)
            r2 = await get_usd_inr(
                s, now=_NOW + timedelta(seconds=5), http=http,
            )
            assert r1 == r2 == pytest.approx(83.42)
            assert fetch_count == 1  # second call hit cache, not API


@pytest.mark.asyncio
async def test_second_call_in_next_minute_refetches() -> None:
    engine = await _setup_db()
    fetch_count = 0
    rates_to_return = [83.42, 83.50]

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fetch_count
        rate = rates_to_return[fetch_count]
        fetch_count += 1
        return httpx.Response(200, json={"rates": {"INR": rate}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with AsyncSession(engine) as s:
            r1 = await get_usd_inr(s, now=_NOW, http=http)
            await s.commit()
            r2 = await get_usd_inr(
                s, now=_NOW + timedelta(minutes=2), http=http,
            )
            await s.commit()
            assert r1 == pytest.approx(83.42)
            assert r2 == pytest.approx(83.50)
            assert fetch_count == 2


# ---- Failure / fallback --------------------------------------------------


@pytest.mark.asyncio
async def test_api_failure_falls_back_to_most_recent_cache() -> None:
    """If the upstream throws, return the most recent cached rate."""
    engine = await _setup_db()
    # Pre-seed a cached row.
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "INSERT INTO fx_rates VALUES ('USDINR', :ts, 83.10, 'seed')"
        ), {"ts": _NOW - timedelta(hours=2)})
        await s.commit()

    async with _make_http(error=httpx.ConnectError("network down")) as http:
        async with AsyncSession(engine) as s:
            rate = await get_usd_inr(s, now=_NOW, http=http)
            assert rate == pytest.approx(83.10)


@pytest.mark.asyncio
async def test_api_failure_with_no_cache_raises() -> None:
    engine = await _setup_db()
    async with _make_http(error=httpx.ConnectError("network down")) as http:
        async with AsyncSession(engine) as s:
            with pytest.raises(FXRateUnavailable):
                await get_usd_inr(s, now=_NOW, http=http)


@pytest.mark.asyncio
async def test_api_returns_invalid_rate_falls_back() -> None:
    """If upstream returns rate=0 or missing, treat as failure."""
    engine = await _setup_db()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "INSERT INTO fx_rates VALUES ('USDINR', :ts, 83.05, 'seed')"
        ), {"ts": _NOW - timedelta(hours=1)})
        await s.commit()
    async with _make_http(rate=0.0) as http:
        async with AsyncSession(engine) as s:
            rate = await get_usd_inr(s, now=_NOW, http=http)
            assert rate == pytest.approx(83.05)
