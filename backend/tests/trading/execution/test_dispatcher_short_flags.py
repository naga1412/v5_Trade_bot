"""PR2 SHORT-side safety gates — `_apply_short_safety_gates`.

Spec §4.2 + R7:
  - SHORT_VETO_HIGH_BORROW fires only when (a) flag ON,
    (b) direction == "SHORT", (c) borrow APR > threshold.
  - Fail-open when borrow data is missing or stale (R7).
  - LONG signals NEVER enter this code path (early return).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.config import Settings
from app.trading.execution.dispatcher import (
    SignalProposal,
    _apply_short_safety_gates,
)


def _make_proposal(direction: str = "SHORT") -> SignalProposal:
    return SignalProposal(
        symbol="DOGE/USDT", timeframe="1h",
        direction=direction,  # type: ignore[arg-type]
        entry_price=0.10, stop_loss_price=0.11, take_profit_price=0.08,
        confidence_pct=70.0,
        layer_summary={"L1": {}, "L2": {}, "L3": {}},
        inputs_hash="b" * 64,
    )


def _settings(veto: bool = False, threshold: float = 10.0) -> Settings:
    return Settings(
        database_url="postgresql://x", redis_url="redis://x",
        SHORT_VETO_HIGH_BORROW=veto,
        SHORT_VETO_BORROW_APR_PCT=threshold,
    )


def test_all_short_safety_flags_default_off() -> None:
    """Spec §6.1 hard bound: ALL three SHORT safety flags default False.

    Co-located here (rather than only in test_pr2_settings_defaults.py)
    so the SHORT-side test module is self-contained — a future commit
    that flips one of these defaults can't be merged without breaking
    a test in the same file as the gate it controls.
    """
    s = Settings(database_url="postgresql://x", redis_url="redis://x")
    assert s.SHORT_VETO_HIGH_BORROW is False
    assert s.SHORT_FUNDING_HALVE_HOLD is False
    assert s.SHORT_TIGHTEN_SL_LOW_MTF is False


@pytest.mark.asyncio
async def test_veto_does_not_fire_for_long() -> None:
    """LONG never enters the SHORT branch — borrow APR is not even checked."""
    p = _make_proposal(direction="LONG")
    session = AsyncMock()
    with patch(
        "app.trading.execution.dispatcher._lookup_borrow_apr",
        new_callable=AsyncMock,
        return_value=99.0,
    ) as mock_lookup:
        result = await _apply_short_safety_gates(
            p, _settings(veto=True), session=session,
        )
    assert result is None
    mock_lookup.assert_not_called()


@pytest.mark.asyncio
async def test_veto_does_not_fire_when_flag_off() -> None:
    """Flag OFF → borrow lookup is not performed, gate passes."""
    p = _make_proposal()
    session = AsyncMock()
    with patch(
        "app.trading.execution.dispatcher._lookup_borrow_apr",
        new_callable=AsyncMock,
        return_value=99.0,
    ) as mock_lookup:
        result = await _apply_short_safety_gates(
            p, _settings(veto=False), session=session,
        )
    assert result is None
    mock_lookup.assert_not_called()


@pytest.mark.asyncio
async def test_veto_fires_when_borrow_above_threshold() -> None:
    p = _make_proposal()
    session = AsyncMock()
    with patch(
        "app.trading.execution.dispatcher._lookup_borrow_apr",
        new_callable=AsyncMock,
        return_value=12.0,  # > 10% default threshold
    ):
        result = await _apply_short_safety_gates(
            p, _settings(veto=True, threshold=10.0), session=session,
        )
    assert result is not None
    assert result.outcome == "blocked_short_high_borrow"


@pytest.mark.asyncio
async def test_veto_does_not_fire_when_borrow_below_threshold() -> None:
    p = _make_proposal()
    session = AsyncMock()
    with patch(
        "app.trading.execution.dispatcher._lookup_borrow_apr",
        new_callable=AsyncMock,
        return_value=5.0,
    ):
        result = await _apply_short_safety_gates(
            p, _settings(veto=True, threshold=10.0), session=session,
        )
    assert result is None


@pytest.mark.asyncio
async def test_veto_does_not_fire_at_exact_threshold() -> None:
    """Threshold is `>`, not `>=` — borrow == threshold passes."""
    p = _make_proposal()
    session = AsyncMock()
    with patch(
        "app.trading.execution.dispatcher._lookup_borrow_apr",
        new_callable=AsyncMock,
        return_value=10.0,
    ):
        result = await _apply_short_safety_gates(
            p, _settings(veto=True, threshold=10.0), session=session,
        )
    assert result is None


@pytest.mark.asyncio
async def test_veto_fails_open_on_missing_borrow_data() -> None:
    """Per spec §R7: borrow data unavailable (None) → fail-open (no veto)."""
    p = _make_proposal()
    session = AsyncMock()
    with patch(
        "app.trading.execution.dispatcher._lookup_borrow_apr",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await _apply_short_safety_gates(
            p, _settings(veto=True), session=session,
        )
    assert result is None
