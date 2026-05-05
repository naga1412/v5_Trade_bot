"""SP-0 token-bucket primitive + SP-3 RateLimitedClient extensions.

The original `TokenBucket` is kept verbatim - it's the primitive. SP-3 adds:

- `RateLimitedClient`: wraps `httpx.AsyncClient` with one or more buckets,
  routes each request to a bucket via `endpoint_key`, and (for Binance)
  rewinds the bucket from a response header.
- `DailyCounterBucket`: a hard-counter bucket that resets at 00:00 UTC,
  used by TwelveData (800 calls/day).
- `RateLimitExceeded`: raised when `raise_on_exhaust=True` and the bucket
  cannot accept the request.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol

import httpx


@dataclass
class TokenBucket:
    capacity: float
    refill_per_sec: float
    _tokens: float = 0.0
    _last_refill: float = 0.0
    _lock: asyncio.Lock | None = None

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def tokens(self) -> float:
        self._refill_locked()
        return self._tokens

    def _refill_locked(self) -> None:
        now = time.monotonic()
        delta = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + delta * self.refill_per_sec)
        self._last_refill = now

    async def acquire(self, weight: float = 1.0) -> None:
        assert self._lock is not None
        while True:
            async with self._lock:
                self._refill_locked()
                if self._tokens >= weight:
                    self._tokens -= weight
                    return
                deficit = weight - self._tokens
                wait_for = deficit / self.refill_per_sec
            await asyncio.sleep(wait_for)


# --- SP-3 extensions ---------------------------------------------------------


class RateLimitExceeded(Exception):
    """Raised by RateLimitedClient when bucket is empty and raise_on_exhaust=True."""


class _BucketLike(Protocol):
    async def acquire(self, weight: float = 1.0) -> None: ...
    @property
    def tokens(self) -> float: ...


_ONE_DAY = timedelta(days=1)


class DailyCounterBucket:
    """Simple counter that resets at 00:00 UTC. Used for TwelveData free tier.

    Not a true token bucket - there's no per-second refill, just a hard
    daily cap. `used_today` is exposed for telemetry.
    """

    def __init__(
        self, *, daily_limit: int,
        _now: Callable[[], datetime] | None = None,
    ) -> None:
        self.daily_limit = daily_limit
        self._now = _now or (lambda: datetime.now(timezone.utc))
        self._used = 0.0
        self._date = self._now().date()
        self._lock = asyncio.Lock()

    @property
    def tokens(self) -> float:
        self._maybe_reset()
        return max(0.0, self.daily_limit - self._used)

    @property
    def used_today(self) -> int:
        self._maybe_reset()
        return int(self._used)

    def _maybe_reset(self) -> None:
        today = self._now().date()
        if today != self._date:
            self._date = today
            self._used = 0.0

    async def acquire(self, weight: float = 1.0) -> None:
        async with self._lock:
            self._maybe_reset()
            if self._used + weight <= self.daily_limit:
                self._used += weight
                return
            # Wait until 00:00 UTC. In tests, the injected clock will already
            # have advanced; in prod, we sleep the actual delta.
            now = self._now()
            tomorrow = datetime.combine(
                self._date, datetime.min.time(), tzinfo=timezone.utc,
            ) + _ONE_DAY
            wait_s = max(0.0, (tomorrow - now).total_seconds())
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        async with self._lock:
            self._maybe_reset()
            self._used += weight


class RateLimitedClient:
    """httpx.AsyncClient wrapper with per-exchange/per-endpoint rate buckets."""

    def __init__(
        self, *,
        exchange: str,
        http: httpx.AsyncClient,
        buckets: Mapping[str, _BucketLike],
        endpoint_weights: Mapping[str, int] | None = None,
        sync_header: str | None = None,
        sync_capacity: float | None = None,
        raise_on_exhaust: bool = False,
    ) -> None:
        if "default" not in buckets:
            raise ValueError("buckets must contain a 'default' bucket")
        self.exchange = exchange
        self.http = http
        self.buckets: dict[str, _BucketLike] = dict(buckets)
        self.endpoint_weights = dict(endpoint_weights or {})
        self.sync_header = sync_header
        self.sync_capacity = sync_capacity
        self.raise_on_exhaust = raise_on_exhaust

    def _resolve_weight(
        self, *, endpoint_key: str, weight: int | None,
    ) -> float:
        if weight is not None:
            return float(weight)
        return float(self.endpoint_weights.get(endpoint_key, 1))

    async def request(
        self, method: str, url: str, *,
        endpoint_key: str = "default",
        weight: int | None = None,
        **httpx_kwargs: Any,
    ) -> httpx.Response:
        bucket = self.buckets.get(endpoint_key, self.buckets["default"])
        w = self._resolve_weight(endpoint_key=endpoint_key, weight=weight)

        if self.raise_on_exhaust and bucket.tokens < w:
            raise RateLimitExceeded(
                f"{self.exchange}/{endpoint_key}: bucket empty "
                f"({bucket.tokens:.1f} < {w})"
            )

        await bucket.acquire(weight=w)
        response = await self.http.request(method, url, **httpx_kwargs)

        # Header sync (Binance): authoritative used-weight overrides our local count.
        if self.sync_header and self.sync_capacity is not None:
            header_val = response.headers.get(self.sync_header)
            if header_val is not None:
                try:
                    used = float(header_val)
                except ValueError:
                    used = 0.0
                # Rewind by setting tokens = capacity - used.
                if hasattr(bucket, "_tokens"):
                    bucket._tokens = max(  # type: ignore[attr-defined]
                        0.0, self.sync_capacity - used,
                    )

        return response

    async def aclose(self) -> None:
        await self.http.aclose()
