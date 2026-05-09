"""SP-8 Phase H — USD/INR spot rate fetcher with per-minute cache.

Spec §8.3. We need INR-denominated trade values for Indian tax
computation. Source: ``api.exchangerate.host`` (free, no key). Cached
in the ``fx_rates`` table per minute — sufficient granularity for tax,
and avoids hammering the upstream API.

Failure mode: if the upstream returns 5xx or times out, fall back to
the most recent cached rate AND log a data_quality_alert (caller's
responsibility — this module just returns the stale rate).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


_PAIR = "USDINR"
_DEFAULT_API_URL = "https://api.exchangerate.host/latest"
_CACHE_GRANULARITY_SECONDS = 60
_FETCH_TIMEOUT_SECONDS = 10.0


class FXRateUnavailable(Exception):
    """No fresh rate from the API AND no cached fallback either."""


def _cache_bucket(now: datetime) -> datetime:
    """Round down to the minute — every rate within the same minute
    shares one cache row."""
    return now.replace(second=0, microsecond=0)


async def _fetch_remote(http: httpx.AsyncClient, *, url: str) -> float:
    """Hit exchangerate.host and return USDINR spot."""
    r = await http.get(
        url, params={"base": "USD", "symbols": "INR"},
        timeout=_FETCH_TIMEOUT_SECONDS,
    )
    r.raise_for_status()
    data = r.json()
    rate = float(data["rates"]["INR"])
    if rate <= 0:
        raise ValueError(f"exchangerate.host returned non-positive rate {rate}")
    return rate


async def _read_cached(
    session: AsyncSession, *, ts: datetime,
) -> float | None:
    """Return cached rate exactly at ``ts`` (minute bucket), or None."""
    row = (await session.execute(
        sa.text(
            "SELECT rate FROM fx_rates WHERE pair = :p AND ts = :ts"
        ),
        {"p": _PAIR, "ts": ts},
    )).first()
    return float(row.rate) if row else None


async def _read_most_recent(
    session: AsyncSession,
) -> tuple[datetime, float] | None:
    """Return the most recent cached (ts, rate). For fallback when the
    upstream API is down."""
    row = (await session.execute(
        sa.text(
            "SELECT ts, rate FROM fx_rates "
            "WHERE pair = :p ORDER BY ts DESC LIMIT 1"
        ),
        {"p": _PAIR},
    )).first()
    if row is None:
        return None
    return (row.ts, float(row.rate))


async def _write_cache(
    session: AsyncSession, *,
    ts: datetime, rate: float, source: str,
) -> None:
    """INSERT rate for the minute bucket. UPSERT on conflict (same minute)."""
    await session.execute(
        sa.text(
            "INSERT INTO fx_rates (pair, ts, rate, source) "
            "VALUES (:p, :ts, :r, :s) "
            "ON CONFLICT (pair, ts) DO UPDATE SET rate = :r, source = :s"
        ),
        {"p": _PAIR, "ts": ts, "r": rate, "s": source},
    )


async def get_usd_inr(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    http: httpx.AsyncClient | None = None,
    api_url: str = _DEFAULT_API_URL,
) -> float:
    """Return the USD/INR spot rate, using minute-cache + API + fallback.

    Resolution order:
      1. Cached rate for the current minute bucket → return it.
      2. Fetch from upstream API → cache + return.
      3. Upstream failed → return most recent cached rate (any age) +
         log warning. Caller's responsibility to also write a
         data_quality_alert if the staleness matters for the use case.
      4. No cached rate at all → raise FXRateUnavailable.

    Caller is responsible for ``session.commit()`` after this returns
    on the first call of a new minute (the cache write is staged).
    """
    n = now or datetime.now(timezone.utc)
    bucket = _cache_bucket(n)

    cached = await _read_cached(session, ts=bucket)
    if cached is not None:
        return cached

    client = http if http is not None else httpx.AsyncClient()
    own_http = http is None
    try:
        try:
            rate = await _fetch_remote(client, url=api_url)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("USD/INR fetch failed: %s — falling back to cache", e)
            most_recent = await _read_most_recent(session)
            if most_recent is None:
                raise FXRateUnavailable(
                    f"upstream {api_url} failed and no cached rate available"
                ) from e
            stale_ts, stale_rate = most_recent
            log.warning(
                "USD/INR using stale rate from %s (%.4f) at %s",
                stale_ts, stale_rate, n,
            )
            return stale_rate
        await _write_cache(session, ts=bucket, rate=rate, source="exchangerate.host")
        return rate
    finally:
        if own_http:
            await client.aclose()


__all__ = [
    "FXRateUnavailable",
    "get_usd_inr",
]
