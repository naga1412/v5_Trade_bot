"""SP-9 Phase A — minimal per-user in-memory rate limiter.

Built to defend the TOTP-verify path from brute-force. A 6-digit TOTP
code has 1M possible values; without throttling, a 100 req/sec attacker
who somehow held a valid CF Access JWT for the target user would
exhaust the search space in ~3h. The configured 5/min cap pushes that
to ~280 days — well past the 30s rotation window.

In-memory only — single-process / single-replica deployment (current
prod is one Hetzner box, one uvicorn worker). Promote to Redis if we
ever scale horizontally; this module isolates the contract.
"""
from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Final

# Public defaults.
TOTP_VERIFY_MAX_PER_WINDOW: Final[int] = 5
TOTP_VERIFY_WINDOW_SECONDS: Final[float] = 60.0


class RateLimitExceeded(Exception):
    """Raised by check_and_record on cap."""

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"rate limit exceeded; retry in {retry_after_seconds:.1f}s",
        )


class _Bucket:
    """One bucket = one (key, action) pair."""

    __slots__ = ("hits",)

    def __init__(self) -> None:
        self.hits: deque[float] = deque()


_buckets: dict[tuple[str, int], _Bucket] = {}
_lock = Lock()


def check_and_record(
    *,
    bucket_key: str,
    user_id: int,
    max_hits: int = TOTP_VERIFY_MAX_PER_WINDOW,
    window_seconds: float = TOTP_VERIFY_WINDOW_SECONDS,
    now: float | None = None,
) -> None:
    """Record a hit + raise RateLimitExceeded if over the cap.

    bucket_key namespaces the limit (e.g. 'totp_verify' vs 'login') so
    the same user can have separate budgets per action. user_id pins
    the bucket per identity — even with a global token a bad actor
    can't move budget between accounts.
    """
    n = now if now is not None else time.monotonic()
    cutoff = n - window_seconds
    key = (bucket_key, user_id)
    with _lock:
        b = _buckets.get(key)
        if b is None:
            b = _Bucket()
            _buckets[key] = b
        # Drop expired hits.
        while b.hits and b.hits[0] < cutoff:
            b.hits.popleft()
        if len(b.hits) >= max_hits:
            oldest = b.hits[0]
            retry_after = (oldest + window_seconds) - n
            raise RateLimitExceeded(max(retry_after, 0.0))
        b.hits.append(n)


def reset_for_tests() -> None:
    """Test helper — production never calls this."""
    with _lock:
        _buckets.clear()


__all__ = [
    "RateLimitExceeded",
    "TOTP_VERIFY_MAX_PER_WINDOW",
    "TOTP_VERIFY_WINDOW_SECONDS",
    "check_and_record",
    "reset_for_tests",
]
