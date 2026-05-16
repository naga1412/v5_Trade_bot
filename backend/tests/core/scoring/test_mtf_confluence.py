"""MTF confluence — full test suite (Tasks 4.4a + 4.4b + 4.4c).

Tests per-TF vote, cache TTL semantics, compute LONG/SHORT/NEUTRAL paths,
fail-open partial failures, prewarm, deadline enforcement, and refresh
entry detection.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import numpy as np
import pytest
import respx
from httpx import Response

from app.core.scoring.mtf_confluence import (
    CACHE_TTL_S,
    TIMEFRAMES,
    MtfConfluence,
    _KLINE_CACHE,
    _cache_get,
    _cache_set,
    _entries_due_for_refresh,
    _fetch_one_tf,
    _vote_for_tf,
    compute_mtf_confluence,
    prewarm_cache,
)
from app.core.scoring.types import Direction


# ---------------------------------------------------------------------------
# Kline fixtures
# ---------------------------------------------------------------------------


def _make_uptrend_klines(n: int = 200) -> list[list]:
    """Generate klines with a clear uptrend: EMA20 > EMA50, ADX high.

    Binance kline row: [open_ts, o, h, l, c, vol, close_ts, ...]
    """
    closes = np.linspace(80000.0, 90000.0, n)
    highs = closes * 1.005
    lows = closes * 0.995
    return [
        [
            i * 60_000,
            float(c * 0.999),
            float(h),
            float(l),
            float(c),
            100.0,
            i * 60_000 + 59999,
            0,
            0,
            0,
            0,
            0,
        ]
        for i, (c, h, l) in enumerate(zip(closes, highs, lows))
    ]


def _make_downtrend_klines(n: int = 200) -> list[list]:
    """Generate klines with a clear downtrend: EMA20 < EMA50, ADX high."""
    closes = np.linspace(90000.0, 80000.0, n)
    highs = closes * 1.005
    lows = closes * 0.995
    return [
        [
            i * 60_000,
            float(c * 1.001),
            float(h),
            float(l),
            float(c),
            100.0,
            i * 60_000 + 59999,
            0,
            0,
            0,
            0,
            0,
        ]
        for i, (c, h, l) in enumerate(zip(closes, highs, lows))
    ]


def _make_chop_klines(n: int = 200) -> list[list]:
    """Generate sideways klines: ADX < trend floor → 0 vote."""
    rng = np.random.default_rng(42)
    closes = 80000.0 + rng.normal(0, 50, n)
    highs = closes * 1.0001
    lows = closes * 0.9999
    return [
        [
            i * 60_000,
            float(c),
            float(h),
            float(l),
            float(c),
            100.0,
            i * 60_000 + 59999,
            0,
            0,
            0,
            0,
            0,
        ]
        for i, (c, h, l) in enumerate(zip(closes, highs, lows))
    ]


# ---------------------------------------------------------------------------
# Task 4.4a: per-TF vote tests
# ---------------------------------------------------------------------------


def test_vote_for_tf_uptrend_returns_long_with_adx() -> None:
    """Uptrend klines → vote=+1 and ADX ≥ trend floor (20.0)."""
    klines = _make_uptrend_klines()
    vote, adx = _vote_for_tf(klines)
    assert vote == +1
    assert adx >= 20.0  # strong uptrend → ADX well above floor


def test_vote_for_tf_downtrend_returns_short_with_adx() -> None:
    """Downtrend klines → vote=-1 and ADX ≥ trend floor."""
    klines = _make_downtrend_klines()
    vote, adx = _vote_for_tf(klines)
    assert vote == -1
    assert adx >= 20.0


def test_vote_for_tf_chop_returns_neutral_with_low_adx() -> None:
    """Sideways klines → ADX < floor → vote=0 regardless of EMA cross."""
    klines = _make_chop_klines()
    vote, adx = _vote_for_tf(klines)
    assert vote == 0
    # ADX may be any value but the vote must be 0 (clamped by trend floor)


# ---------------------------------------------------------------------------
# Task 4.4a: cache TTL tests
# ---------------------------------------------------------------------------


def test_cache_ttl_get_returns_none_when_expired() -> None:
    """Entry older than TTL returns None."""
    _KLINE_CACHE.clear()
    klines = _make_uptrend_klines()
    _cache_set("BTCUSDT", "1h", klines, fetched_at=0.0)
    # Advance time well past TTL
    result = _cache_get("BTCUSDT", "1h", now=1e9)
    assert result is None


def test_cache_ttl_get_returns_value_within_ttl() -> None:
    """Entry within TTL window returns the cached klines."""
    _KLINE_CACHE.clear()
    klines = _make_uptrend_klines()
    _cache_set("BTCUSDT", "1h", klines, fetched_at=100.0)
    # Access 1 second before expiry
    result = _cache_get("BTCUSDT", "1h", now=100.0 + CACHE_TTL_S["1h"] - 1)
    assert result == klines


# ---------------------------------------------------------------------------
# Task 4.4b: _fetch_one_tf tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_one_tf_caches_on_success() -> None:
    """Successful fetch stores to cache; second call is a cache hit (mock count=1)."""
    _KLINE_CACHE.clear()
    route = respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=_make_uptrend_klines())
    )
    async with httpx.AsyncClient() as http:
        klines = await _fetch_one_tf(http, "BTCUSDT", "1h")
    assert klines is not None
    assert len(klines) == 200

    # Second call — should hit cache, not make another HTTP request
    async with httpx.AsyncClient() as http:
        klines2 = await _fetch_one_tf(http, "BTCUSDT", "1h")
    assert klines2 == klines
    assert route.call_count == 1  # only one real request


@pytest.mark.asyncio
@respx.mock
async def test_fetch_one_tf_timeout_returns_none() -> None:
    """TimeoutException on fetch → None returned (fail-open)."""
    _KLINE_CACHE.clear()
    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        side_effect=httpx.TimeoutException("simulated timeout")
    )
    async with httpx.AsyncClient() as http:
        result = await _fetch_one_tf(http, "BTCUSDT", "5m")
    assert result is None


# ---------------------------------------------------------------------------
# Task 4.4b: compute_mtf_confluence tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_compute_mtf_confluence_uptrend_long() -> None:
    """All 6 TFs return uptrend klines → agreement=6 for LONG signal."""
    _KLINE_CACHE.clear()
    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=_make_uptrend_klines())
    )
    result = await compute_mtf_confluence("BTCUSDT", Direction.LONG)
    assert result is not None
    assert result.agreement == 6
    assert result.dominant_tf in TIMEFRAMES
    assert set(result.directions.values()) == {+1}


@pytest.mark.asyncio
@respx.mock
async def test_compute_mtf_confluence_neutral_signal_agreement_is_none() -> None:
    """NEUTRAL signal → agreement=None; directions still fully populated.

    Per Correction 4: NEUTRAL has no direction to compare against,
    so agreement is None. The dominant_tf is still the TF with highest
    |vote × ADX| (uptrend klines give non-zero votes).
    """
    _KLINE_CACHE.clear()
    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=_make_uptrend_klines())
    )
    result = await compute_mtf_confluence("BTCUSDT", Direction.NEUTRAL)
    assert result is not None
    assert result.agreement is None  # key invariant for NEUTRAL
    assert result.dominant_tf in TIMEFRAMES  # uptrend → at least one non-zero vote
    assert len(result.directions) == 6  # all 6 TFs recorded


@pytest.mark.asyncio
@respx.mock
async def test_compute_mtf_confluence_all_chop_dominant_is_none() -> None:
    """When every TF returns 0-vote (chop), dominant_tf=None and agreement=0."""
    _KLINE_CACHE.clear()
    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=_make_chop_klines())
    )
    result = await compute_mtf_confluence("BTCUSDT", Direction.LONG)
    assert result is not None
    assert result.dominant_tf is None  # all voted 0 → no dominant
    assert result.agreement == 0  # LONG signal, 0 TFs agreed


@pytest.mark.asyncio
@respx.mock
async def test_compute_mtf_confluence_partial_failure_fail_open() -> None:
    """One TF timeout doesn't kill result; failed TF recorded as 0 vote."""
    _KLINE_CACHE.clear()

    def _selective_route(request: httpx.Request) -> Response:
        if "interval=5m" in str(request.url):
            raise httpx.TimeoutException("5m simulated timeout")
        return Response(200, json=_make_uptrend_klines())

    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        side_effect=_selective_route
    )
    result = await compute_mtf_confluence("BTCUSDT", Direction.LONG)
    assert result is not None  # partial result, not None
    assert result.directions["5m"] == 0  # failed TF → 0 vote
    assert result.agreement is not None
    assert result.agreement >= 4  # at least 4 remaining TFs are uptrend


# ---------------------------------------------------------------------------
# Task 4.4c: prewarm_cache tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_prewarm_cache_populates_all_tfs() -> None:
    """prewarm_cache fetches all symbols × TFs and counts cached entries."""
    _KLINE_CACHE.clear()
    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=_make_uptrend_klines())
    )
    n = await prewarm_cache(["BTCUSDT", "ETHUSDT"], deadline_seconds=30.0)
    assert n == 2 * len(TIMEFRAMES)  # 12 entries (2 symbols × 6 TFs)


@pytest.mark.asyncio
@respx.mock
async def test_prewarm_cache_respects_deadline() -> None:
    """Prewarm stops early when deadline elapsed; returns < full count."""
    _KLINE_CACHE.clear()

    async def _slow_route(request: httpx.Request) -> Response:
        await asyncio.sleep(2.0)  # each fetch takes 2s
        return Response(200, json=_make_uptrend_klines())

    respx.get(url__startswith="https://api.binance.com/api/v3/klines").mock(
        side_effect=_slow_route
    )
    # With a 1s deadline and 2s-per-fetch, we get 0 or very few entries
    n = await prewarm_cache(["BTCUSDT"], deadline_seconds=1.0)
    # Deadline fires before all 6 TFs finish
    assert n < 6


@pytest.mark.asyncio
async def test_prewarm_cache_enforces_hard_deadline_mid_batch(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Hard-deadline enforcement MID-BATCH within a single symbol's gather.

    This documents the prewarm_cache deviation from the original plan
    (which checked deadline only BETWEEN symbols). The implementation
    wraps the per-symbol asyncio.gather in asyncio.wait_for(remaining)
    so a slow batch cannot exceed the deadline.

    Synthetic conditions:
      - Mock _fetch_one_tf to sleep 5s per call.
      - deadline_seconds=3.0
      - 3-symbol universe — first symbol's gather alone would take 5s.

    Expected: prewarm_cache returns within ~3s + slack; cache is
    incomplete; the "deadline reached" log fires; no leaked tasks.
    """
    import logging
    from app.core.scoring import mtf_confluence as mod

    _KLINE_CACHE.clear()

    # Slow fetch: each call sleeps 5s before returning klines.
    # Per-symbol gather of 6 TFs runs in PARALLEL → wall-clock ~5s,
    # which exceeds the 3s deadline → asyncio.wait_for cancels mid-batch.
    async def _slow_fetch(
        http: httpx.AsyncClient, symbol: str, tf: str, **_kwargs: object,
    ) -> list[list] | None:
        await asyncio.sleep(5.0)
        return _make_uptrend_klines()

    monkeypatch.setattr(mod, "_fetch_one_tf", _slow_fetch)

    # Snapshot tasks BEFORE so we can detect leaks AFTER.
    tasks_before = {t for t in asyncio.all_tasks() if not t.done()}

    caplog.set_level(logging.INFO, logger="app.core.scoring.mtf_confluence")

    start = time.monotonic()
    n_cached = await prewarm_cache(
        ["BTCUSDT", "ETHUSDT", "BNBUSDT"], deadline_seconds=3.0,
    )
    elapsed = time.monotonic() - start

    # Returned within deadline + small slack (asyncio.wait_for + cancellation
    # propagation overhead). 1s slack is generous.
    assert elapsed < 3.0 + 1.0, (
        f"prewarm exceeded hard deadline: {elapsed:.2f}s "
        f"(expected < {3.0 + 1.0}s)"
    )

    # At least one symbol's TFs must be ABSENT from the cache (deadline
    # interrupted mid-batch). With 5s-per-fetch and 3s deadline, all
    # entries should actually be missing — but we assert the looser
    # "at least one missing" to tolerate any future timing shift.
    cached_keys = set(_KLINE_CACHE.keys())
    all_expected_keys = {
        (sym, tf)
        for sym in ("BTCUSDT", "ETHUSDT", "BNBUSDT")
        for tf in TIMEFRAMES
    }
    missing = all_expected_keys - cached_keys
    assert missing, (
        f"expected at least one (symbol, tf) to be uncached due to deadline; "
        f"all 18 keys are cached: {cached_keys}"
    )
    # n_cached should reflect missing entries
    assert n_cached < len(all_expected_keys)

    # Log assertion: at least one "mtf_prewarm: deadline reached" message.
    deadline_msgs = [
        r for r in caplog.records
        if "deadline reached" in r.getMessage()
    ]
    assert deadline_msgs, (
        "expected 'mtf_prewarm: deadline reached at sym=...' log; "
        f"got {[r.getMessage() for r in caplog.records]}"
    )

    # Let the event loop settle so any pending cancellations propagate.
    await asyncio.sleep(0.1)

    # No leaked tasks: anything spawned during prewarm_cache must be
    # cancelled cleanly by the asyncio.wait_for + finally block.
    tasks_after = {t for t in asyncio.all_tasks() if not t.done()}
    new_tasks = tasks_after - tasks_before
    new_tasks.discard(asyncio.current_task())
    assert not new_tasks, (
        f"prewarm_cache leaked tasks after returning: "
        f"{[t.get_name() for t in new_tasks]}"
    )


# ---------------------------------------------------------------------------
# Task 4.4c: _entries_due_for_refresh test
# ---------------------------------------------------------------------------


def test_entries_due_for_refresh_identifies_near_expiry() -> None:
    """Entry within 20% of TTL expiry is returned as due for refresh."""
    _KLINE_CACHE.clear()
    klines = _make_uptrend_klines()
    fake_now = 1000.0
    # Seed entry that is 85% of TTL old (within the 20% threshold window)
    # For "1h" (TTL=300s): 85% of 300 = 255s old → 45s remaining → 15% of TTL → within threshold
    _cache_set("BTCUSDT", "1h", klines, fetched_at=fake_now - CACHE_TTL_S["1h"] * 0.85)
    due = _entries_due_for_refresh(now=fake_now, expiry_threshold_pct=0.20)
    assert ("BTCUSDT", "1h") in due
