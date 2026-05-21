"""PR-PLUMBING-1 Fix 1: wire `funding_rate_daily` from build_prediction
through proposal_from_prediction → SignalProposal so the dispatcher's
funding-rate guard evaluates on real values instead of the hard-coded 0.0.

Pre-fix `proposal_from_prediction` had no kwarg for the rate, so
`SignalProposal.funding_rate_daily` defaulted to 0.0 on every signal and
the kill-switch (`KILL_DEFAULTS["funding_rate_guard"]=0.01`) never tripped.

Tests in this module cover the 4-file thread:
    schemas.LivePredictionOut.funding_rate_daily
    predictor.build_prediction  (per-8h → per-day = ×3)
    glue.proposal_from_prediction  (`pred_funding_rate_daily` kwarg)
    dispatcher._check_funding_block  (consumes proposal.funding_rate_daily)
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest

from app.trading.execution.glue import proposal_from_prediction


# ---------------------------------------------------------------------------
# proposal_from_prediction — threading kwarg
# ---------------------------------------------------------------------------


def _baseline_kwargs() -> dict[str, Any]:
    return dict(
        symbol="BTC/USDT", timeframe="1h",
        pred_direction="LONG", pred_confidence=0.72,
        layer_summary={"L1": {"score": 0.85}},
        inputs_hash="abc",
        entry_price=80_000, stop_loss_price=78_400, take_profit_price=83_000,
    )


def test_proposal_from_prediction_threads_funding_rate() -> None:
    """When pred_funding_rate_daily=0.03 is passed, the resulting
    SignalProposal carries funding_rate_daily=0.03 (instead of 0.0)."""
    p = proposal_from_prediction(
        **_baseline_kwargs(),
        pred_funding_rate_daily=0.03,
    )
    assert p is not None
    assert p.funding_rate_daily == 0.03


def test_proposal_from_prediction_funding_rate_none_defaults_zero() -> None:
    """pred_funding_rate_daily=None → SignalProposal.funding_rate_daily == 0.0
    (backwards compat — existing SignalProposal default is 0.0).
    Guards against the lookup failing on a particular symbol — the
    dispatcher gate evaluates 0.0 → not blocked, which is fail-open."""
    p = proposal_from_prediction(
        **_baseline_kwargs(),
        pred_funding_rate_daily=None,
    )
    assert p is not None
    assert p.funding_rate_daily == 0.0


def test_proposal_from_prediction_omitted_funding_rate_defaults_zero() -> None:
    """When pred_funding_rate_daily kwarg is omitted entirely (admin_test_trade
    + manual operator callers that pre-date PR-PLUMBING-1), the proposal still
    constructs with funding_rate_daily=0.0 — bit-identical to pre-fix."""
    p = proposal_from_prediction(**_baseline_kwargs())
    assert p is not None
    assert p.funding_rate_daily == 0.0


# ---------------------------------------------------------------------------
# dispatcher funding gate — proves the thread reaches evaluate_funding_rate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_funding_block_evaluates_with_real_rate() -> None:
    """The dispatcher's _check_funding_block passes proposal.funding_rate_daily
    (not 0.0) into evaluate_funding_rate. With pred_funding_rate_daily=0.05
    threaded through, the kwarg must equal 0.05."""
    from app.trading.execution import dispatcher

    proposal = proposal_from_prediction(
        **_baseline_kwargs(),
        pred_funding_rate_daily=0.05,
    )
    assert proposal is not None
    assert proposal.funding_rate_daily == 0.05

    captured: dict[str, Any] = {}

    def _fake_evaluate(*, daily_funding_rate: float, position_direction: str, cfg: Any):
        captured["daily_funding_rate"] = daily_funding_rate
        captured["position_direction"] = position_direction
        from app.trading.kill_switches import TripDecision
        return TripDecision(name="funding_rate_guard", tripped=False, reason=None)

    with patch.object(dispatcher, "evaluate_funding_rate", new=_fake_evaluate):
        blocked, reason = await dispatcher._check_funding_block(proposal)

    assert captured["daily_funding_rate"] == 0.05
    assert captured["position_direction"] == "LONG"
    assert blocked is False
    assert reason is None


@pytest.mark.asyncio
async def test_existing_zero_funding_signal_passes_gate() -> None:
    """Regression: when funding_rate_daily defaults to 0.0 (no rate threaded —
    e.g. admin_test_trade), the funding guard does NOT trip (0.0 < 0.01)."""
    from app.trading.execution import dispatcher

    proposal = proposal_from_prediction(**_baseline_kwargs())
    assert proposal is not None
    assert proposal.funding_rate_daily == 0.0

    blocked, reason = await dispatcher._check_funding_block(proposal)
    assert blocked is False
    assert reason is None


# ---------------------------------------------------------------------------
# build_prediction — populates LivePredictionOut.funding_rate_daily
# ---------------------------------------------------------------------------


def _make_bars(n: int = 250) -> pd.DataFrame:
    closes = list(np.linspace(100.0, 200.0, n))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
        "open": closes, "high": [c * 1.01 for c in closes],
        "low":  [c * 0.99 for c in closes], "close": closes,
        "volume": [1000.0] * n,
    }).set_index("ts")


@pytest.mark.asyncio
async def test_build_prediction_sets_funding_rate_daily_3x_conversion() -> None:
    """build_prediction must populate LivePredictionOut.funding_rate_daily
    by multiplying the raw per-8h Binance funding rate by 3 (one funding
    event per 8h → 3 per day). Patch lookup_latest_funding_rate to return
    0.01 per-8h and assert the per-day field is ~0.03."""
    from app.core.predictor import build_prediction
    from unittest.mock import MagicMock

    bars = _make_bars(250)

    # Patch the lookup to return 0.01 (per-8h). Pass a non-None session
    # so the funding lookup branch is taken in _compute_aggregator_hook_fields.
    fake_session = MagicMock()

    with patch(
        "app.core.scoring.intermarket_lookup.lookup_latest_funding_rate",
        new=AsyncMock(return_value=0.01),
    ):
        pred = await build_prediction(
            symbol="BTC/USDT", timeframe="1h", bars=bars,
            session=fake_session,
        )

    assert pred.funding_rate_daily is not None
    assert pred.funding_rate_daily == pytest.approx(0.03, abs=1e-9)


@pytest.mark.asyncio
async def test_build_prediction_funding_rate_daily_none_when_lookup_returns_none() -> None:
    """When lookup_latest_funding_rate returns None (no rows for symbol,
    or session is None), LivePredictionOut.funding_rate_daily must be None."""
    from app.core.predictor import build_prediction

    bars = _make_bars(250)

    # session=None — the predictor's aggregator hook skips funding lookup.
    pred = await build_prediction(
        symbol="BTC/USDT", timeframe="1h", bars=bars,
        session=None,
    )

    assert pred.funding_rate_daily is None
