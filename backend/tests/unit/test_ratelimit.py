import asyncio
import time
import pytest

from app.data.ratelimit import TokenBucket


# SP-9 follow-up: was skipped because the first test deterministically hung
# in selector.poll when reached after test_predictor.py. Two SP-3.5 changes
# might have fixed this: (a) test_predictor converted to native async, and
# (b) freezegun.configure(default_ignore_list=...) global. Unskipping to
# verify; if still hung, this needs proper local repro.


@pytest.mark.asyncio
async def test_initial_capacity_allows_immediate_calls() -> None:
    bucket = TokenBucket(capacity=5, refill_per_sec=1.0)
    for _ in range(5):
        await bucket.acquire(weight=1)
    # 6th call should block ~1 second waiting for refill
    start = time.monotonic()
    await bucket.acquire(weight=1)
    elapsed = time.monotonic() - start
    assert 0.9 <= elapsed <= 1.5


@pytest.mark.asyncio
async def test_weighted_acquire_drains_proportionally() -> None:
    bucket = TokenBucket(capacity=10, refill_per_sec=100.0)
    await bucket.acquire(weight=7)
    assert pytest.approx(bucket.tokens, abs=0.05) == 3


@pytest.mark.asyncio
async def test_refill_caps_at_capacity() -> None:
    bucket = TokenBucket(capacity=3, refill_per_sec=10.0)
    await asyncio.sleep(0.5)  # would refill 5; cap at 3
    assert bucket.tokens == 3
