import asyncio
import time
from dataclasses import dataclass


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
