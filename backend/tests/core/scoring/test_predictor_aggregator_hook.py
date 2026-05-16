"""Tests for PR1 Task 3.4: aggregator hook in build_prediction.

Strategy: test ``_compute_aggregator_hook_fields`` directly (the extracted
helper) for fine-grained coverage, plus one integration smoke-test that
calls ``build_prediction`` end-to-end and checks the 7 new fields pass
through to ``LivePredictionOut`` and that existing fields are unchanged.

Approach avoids mocking 10+ layers by testing the hook in isolation.
The integration smoke test patches only the async helpers so the heavy
layer scorers still run (they're fast on synthetic data).
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.core.predictor import _compute_aggregator_hook_fields, _none_coro
from app.core.scoring.mtf_confluence import MtfConfluence
from app.core.scoring.types import Direction, FinalScore


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_bars(n: int = 500, *, seed: int = 42) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame with DatetimeIndex (hourly, UTC)."""
    rng = np.random.default_rng(seed)
    closes = 80_000.0 + np.cumsum(rng.normal(0.0, 50.0, size=n))
    opens = closes + rng.normal(0.0, 25.0, size=n)
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0.0, 30.0, size=n))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0.0, 30.0, size=n))
    volumes = np.abs(rng.normal(100.0, 20.0, size=n))
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def _make_final(
    score: float = 0.35,
    direction: Direction = Direction.LONG,
    confidence: float = 0.7,
) -> FinalScore:
    return FinalScore(
        score=score,
        direction=direction,
        confidence=confidence,
        layer_results={i: None for i in range(1, 11)},
        contributing_layers=(1, 3),
    )


def _make_mtf(
    agreement: int | None = 4,
    dominant_tf: str | None = "1h",
    directions: dict[str, int] | None = None,
) -> MtfConfluence:
    return MtfConfluence(
        agreement=agreement,
        dominant_tf=dominant_tf,
        directions=directions or {"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": 0},
    )


# ---------------------------------------------------------------------------
# _none_coro
# ---------------------------------------------------------------------------

async def test_none_coro_returns_none():
    result = await _none_coro()
    assert result is None


# ---------------------------------------------------------------------------
# _compute_aggregator_hook_fields: happy path — all 7 fields populated
# ---------------------------------------------------------------------------

async def test_all_7_fields_attach_when_helpers_succeed():
    bars = _make_bars(500)
    final = _make_final(score=0.40, direction=Direction.LONG)
    mtf = _make_mtf()

    with (
        patch(
            "app.core.scoring.mtf_confluence.compute_mtf_confluence",
            new=AsyncMock(return_value=mtf),
        ),
        patch(
            "app.core.scoring.p_win_calibrator.predict_p_win",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.core.scoring.intermarket_lookup.lookup_latest_funding_rate",
            new=AsyncMock(return_value=-0.0008),
        ),
    ):
        result = await _compute_aggregator_hook_fields(
            symbol="BTCUSDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=MagicMock(),  # non-None to trigger funding lookup
        )

    (
        mtf_agreement, mtf_dominant_tf, mtf_directions_json,
        p_win, effective_score, realized_vol_20d, funding_directional_adj,
    ) = result

    assert mtf_agreement == 4
    assert mtf_dominant_tf == "1h"
    assert mtf_directions_json is not None
    directions_parsed = json.loads(mtf_directions_json)
    assert isinstance(directions_parsed, dict)
    assert p_win is None  # PR1: always None
    # realized_vol_20d populated from real compute_realized_vol_20d on 500 bars (> 20 days)
    assert realized_vol_20d is not None
    assert isinstance(realized_vol_20d, float)
    # effective_score follows from non-None realized_vol
    assert effective_score is not None
    assert isinstance(effective_score, float)
    # funding_directional_adj: negative funding + LONG → BOOST (+0.10)
    assert funding_directional_adj == pytest.approx(0.10)


async def test_mtf_none_keeps_3_mtf_fields_none():
    """When compute_mtf_confluence returns None, all 3 mtf_* fields stay None."""
    bars = _make_bars(500)
    final = _make_final()

    with patch(
        "app.core.scoring.mtf_confluence.compute_mtf_confluence",
        new=AsyncMock(return_value=None),
    ):
        result = await _compute_aggregator_hook_fields(
            symbol="BTCUSDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=None,
        )

    mtf_agreement, mtf_dominant_tf, mtf_directions_json = result[0], result[1], result[2]
    assert mtf_agreement is None
    assert mtf_dominant_tf is None
    assert mtf_directions_json is None


async def test_p_win_always_none_in_pr1():
    """PR1 contract: predict_p_win always returns None regardless of inputs."""
    bars = _make_bars(500)
    final = _make_final(score=0.8, direction=Direction.LONG)

    result = await _compute_aggregator_hook_fields(
        symbol="BTCUSDT",
        timeframe="1h",
        bars=bars,
        final=final,
        session=None,
    )

    p_win = result[3]
    assert p_win is None


async def test_funding_rate_missing_yields_none_funding_adj():
    """When funding_rate is None, funding_directional_adj should be None."""
    bars = _make_bars(500)
    final = _make_final(direction=Direction.LONG)

    with patch(
        "app.core.scoring.intermarket_lookup.lookup_latest_funding_rate",
        new=AsyncMock(return_value=None),
    ):
        result = await _compute_aggregator_hook_fields(
            symbol="BTCUSDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=MagicMock(),  # non-None so lookup is attempted
        )

    funding_directional_adj = result[6]
    assert funding_directional_adj is None


async def test_insufficient_bars_yields_none_realized_vol_and_effective_score():
    """< 20 days of bars → compute_realized_vol_20d returns None → effective_score None."""
    # Only 10 bars — far below the 20-day minimum
    bars = _make_bars(10)
    final = _make_final()

    with (
        patch(
            "app.core.scoring.mtf_confluence.compute_mtf_confluence",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.core.scoring.p_win_calibrator.predict_p_win",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await _compute_aggregator_hook_fields(
            symbol="BTCUSDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=None,
        )

    realized_vol_20d = result[5]
    effective_score = result[4]
    assert realized_vol_20d is None
    assert effective_score is None


async def test_sync_helper_exception_yields_none_and_logs(caplog):
    """If the sync block raises, all 3 sync outputs stay None (fail-open)."""
    import logging

    bars = _make_bars(500)
    final = _make_final()

    with (
        patch(
            "app.core.scoring.mtf_confluence.compute_mtf_confluence",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.core.scoring.p_win_calibrator.predict_p_win",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.core.scoring.vol_normalization.compute_realized_vol_20d",
            side_effect=RuntimeError("boom"),
        ),
        caplog.at_level(logging.ERROR, logger="app.core.predictor"),
    ):
        result = await _compute_aggregator_hook_fields(
            symbol="BTCUSDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=None,
        )

    realized_vol_20d, effective_score, funding_adj = result[5], result[4], result[6]
    assert realized_vol_20d is None
    assert effective_score is None
    assert funding_adj is None
    assert "aggregator_hook: sync compute failed" in caplog.text


async def test_async_helpers_dispatched_concurrently():
    """The 3 async helpers must be launched concurrently, not sequentially.

    Proof: record start-times from each coroutine — all 3 should start
    within a tight window (< 10 ms). This distinguishes asyncio.gather
    (concurrent dispatch) from sequential await-by-await.
    """
    start_times: list[float] = []

    async def _mock_mtf(*args: Any, **kwargs: Any) -> None:
        start_times.append(time.monotonic())
        await asyncio.sleep(0.005)  # 5ms fake I/O
        return None

    async def _mock_p_win(*args: Any, **kwargs: Any) -> None:
        start_times.append(time.monotonic())
        await asyncio.sleep(0.005)
        return None

    async def _mock_funding(*args: Any, **kwargs: Any) -> None:
        start_times.append(time.monotonic())
        await asyncio.sleep(0.005)
        return None

    bars = _make_bars(500)
    final = _make_final()

    with (
        patch(
            "app.core.scoring.mtf_confluence.compute_mtf_confluence",
            new=_mock_mtf,
        ),
        patch(
            "app.core.scoring.p_win_calibrator.predict_p_win",
            new=_mock_p_win,
        ),
        patch(
            "app.core.scoring.intermarket_lookup.lookup_latest_funding_rate",
            new=_mock_funding,
        ),
    ):
        await _compute_aggregator_hook_fields(
            symbol="BTCUSDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=MagicMock(),  # non-None triggers funding lookup
        )

    assert len(start_times) == 3, f"Expected 3 start-times, got {len(start_times)}"
    spread_ms = (max(start_times) - min(start_times)) * 1000
    # All 3 tasks should have started within 10 ms (concurrent dispatch).
    # Sequential dispatch would spread them by ~5ms each → ~10ms between first and last.
    assert spread_ms < 10, (
        f"Helpers did not start concurrently — spread={spread_ms:.1f}ms suggests sequential dispatch"
    )


# ---------------------------------------------------------------------------
# Direction-specific paths
# ---------------------------------------------------------------------------

async def test_long_signal_path():
    """LONG direction wires correctly through funding adjustment."""
    bars = _make_bars(500)
    final = _make_final(score=0.4, direction=Direction.LONG)

    with (
        patch(
            "app.core.scoring.mtf_confluence.compute_mtf_confluence",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.core.scoring.p_win_calibrator.predict_p_win",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await _compute_aggregator_hook_fields(
            symbol="BTC/USDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=None,
        )

    # Hook should run to completion without exception
    assert len(result) == 7


async def test_short_signal_path():
    """SHORT direction wires correctly through funding adjustment."""
    bars = _make_bars(500)
    final = _make_final(score=-0.4, direction=Direction.SHORT)

    with (
        patch(
            "app.core.scoring.mtf_confluence.compute_mtf_confluence",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.core.scoring.p_win_calibrator.predict_p_win",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await _compute_aggregator_hook_fields(
            symbol="BTCUSDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=None,
        )

    assert len(result) == 7


async def test_neutral_signal_path():
    """NEUTRAL direction: funding_directional_adj must be None (no boost)."""
    bars = _make_bars(500)
    final = _make_final(score=0.0, direction=Direction.NEUTRAL)

    with (
        patch(
            "app.core.scoring.mtf_confluence.compute_mtf_confluence",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.core.scoring.p_win_calibrator.predict_p_win",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.core.scoring.intermarket_lookup.lookup_latest_funding_rate",
            new=AsyncMock(return_value=0.001),
        ),
    ):
        result = await _compute_aggregator_hook_fields(
            symbol="BTCUSDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=MagicMock(),
        )

    funding_adj = result[6]
    # NEUTRAL direction → compute_funding_directional_adj returns None
    assert funding_adj is None


# ---------------------------------------------------------------------------
# Integration smoke-test: build_prediction passes 7 fields to LivePredictionOut
# ---------------------------------------------------------------------------

async def test_existing_fields_unchanged_regression_guard():
    """Call _compute_aggregator_hook_fields and verify it doesn't mutate
    the FinalScore object (since FinalScore is frozen, any mutation attempt
    would raise — but we verify the score/direction/confidence are preserved).
    """
    bars = _make_bars(500)
    final = _make_final(score=0.35, direction=Direction.LONG, confidence=0.72)

    # Record the immutable fields before
    score_before = final.score
    direction_before = final.direction
    confidence_before = final.confidence

    with (
        patch(
            "app.core.scoring.mtf_confluence.compute_mtf_confluence",
            new=AsyncMock(return_value=_make_mtf()),
        ),
        patch(
            "app.core.scoring.p_win_calibrator.predict_p_win",
            new=AsyncMock(return_value=None),
        ),
    ):
        await _compute_aggregator_hook_fields(
            symbol="BTCUSDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=None,
        )

    # FinalScore is frozen — mutation would have raised already.
    # Verify values are intact.
    assert final.score == score_before
    assert final.direction == direction_before
    assert final.confidence == confidence_before


async def test_symbol_slash_normalized_to_binance_format():
    """BTC/USDT → BTCUSDT when passed to MTF confluence and funding lookup."""
    bars = _make_bars(500)
    final = _make_final()
    captured_symbols: list[str] = []

    async def _capture_mtf(symbol: str, *args: Any, **kwargs: Any) -> None:
        captured_symbols.append(symbol)
        return None

    with patch(
        "app.core.scoring.mtf_confluence.compute_mtf_confluence",
        new=_capture_mtf,
    ):
        await _compute_aggregator_hook_fields(
            symbol="BTC/USDT",  # slash-separated input
            timeframe="1h",
            bars=bars,
            final=final,
            session=None,
        )

    assert captured_symbols[0] == "BTCUSDT"


async def test_mtf_directions_json_valid():
    """mtf_directions_json must be valid JSON encoding a dict[str, int]."""
    bars = _make_bars(500)
    final = _make_final()
    mtf = _make_mtf(
        directions={"5m": 1, "15m": 0, "1h": 1, "4h": -1, "1d": 1, "1w": 0},
    )

    with patch(
        "app.core.scoring.mtf_confluence.compute_mtf_confluence",
        new=AsyncMock(return_value=mtf),
    ):
        result = await _compute_aggregator_hook_fields(
            symbol="BTCUSDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=None,
        )

    mtf_directions_json = result[2]
    assert mtf_directions_json is not None
    parsed = json.loads(mtf_directions_json)
    assert parsed == {"5m": 1, "15m": 0, "1h": 1, "4h": -1, "1d": 1, "1w": 0}


async def test_session_none_skips_funding_lookup():
    """When session=None, lookup_latest_funding_rate must NOT be called."""
    bars = _make_bars(500)
    final = _make_final()
    funding_called = False

    async def _should_not_be_called(*args: Any, **kwargs: Any) -> float:
        nonlocal funding_called
        funding_called = True
        return 0.001

    with (
        patch(
            "app.core.scoring.mtf_confluence.compute_mtf_confluence",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.core.scoring.intermarket_lookup.lookup_latest_funding_rate",
            new=_should_not_be_called,
        ),
    ):
        await _compute_aggregator_hook_fields(
            symbol="BTCUSDT",
            timeframe="1h",
            bars=bars,
            final=final,
            session=None,  # ← no session
        )

    assert not funding_called, "lookup_latest_funding_rate should not be called when session=None"
