"""SP-9 Phase A — per-user rate limiter unit tests."""
from __future__ import annotations

import pytest

from app.auth import rate_limit


@pytest.fixture(autouse=True)
def _reset() -> None:
    rate_limit.reset_for_tests()
    yield
    rate_limit.reset_for_tests()


def test_under_cap_no_raise() -> None:
    for i in range(5):
        rate_limit.check_and_record(
            bucket_key="totp_verify", user_id=1, now=100.0 + i,
        )


def test_over_cap_raises_with_retry_after() -> None:
    for i in range(5):
        rate_limit.check_and_record(
            bucket_key="totp_verify", user_id=1, now=100.0 + i,
        )
    with pytest.raises(rate_limit.RateLimitExceeded) as exc:
        rate_limit.check_and_record(
            bucket_key="totp_verify", user_id=1, now=104.5,
        )
    # Oldest hit was at 100, window 60s → retry_after ≈ 100 + 60 - 104.5
    assert 55.0 < exc.value.retry_after_seconds < 56.0


def test_window_slides_so_old_hits_drop_off() -> None:
    for i in range(5):
        rate_limit.check_and_record(
            bucket_key="totp_verify", user_id=1, now=100.0 + i,
        )
    # 61s after the first hit → 5 attempts ago has expired, allow more.
    rate_limit.check_and_record(
        bucket_key="totp_verify", user_id=1, now=161.0,
    )


def test_buckets_are_per_user() -> None:
    for _ in range(5):
        rate_limit.check_and_record(
            bucket_key="totp_verify", user_id=1, now=100.0,
        )
    # User 2 still has full budget.
    rate_limit.check_and_record(
        bucket_key="totp_verify", user_id=2, now=100.0,
    )


def test_buckets_are_per_action() -> None:
    for _ in range(5):
        rate_limit.check_and_record(
            bucket_key="totp_verify", user_id=1, now=100.0,
        )
    # Different bucket key gets its own budget.
    rate_limit.check_and_record(
        bucket_key="login", user_id=1, now=100.0,
    )


def test_custom_max_hits_and_window() -> None:
    rate_limit.check_and_record(
        bucket_key="x", user_id=1, max_hits=2, window_seconds=10, now=0,
    )
    rate_limit.check_and_record(
        bucket_key="x", user_id=1, max_hits=2, window_seconds=10, now=1,
    )
    with pytest.raises(rate_limit.RateLimitExceeded):
        rate_limit.check_and_record(
            bucket_key="x", user_id=1, max_hits=2, window_seconds=10, now=2,
        )
