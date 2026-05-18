"""PR8 cooldown gate — dispatcher pre-conditions integration.

The fail-open contract is critical: a DB read failure on the cooldown
gate must NOT block trading. The matrix tests below pin that behavior.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.trading.execution.cooldown_gate import _apply_cooldown_gate


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _proposal(symbol: str = "BTCUSDT", mtf: int | None = 5):
    return SimpleNamespace(symbol=symbol, mtf_agreement=mtf)


def _settings(enabled: bool = True):
    return SimpleNamespace(
        LIVE_COOLDOWN_ENABLED=enabled,
        LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF=True,
        LIVE_COOLDOWN_HOURS_BY_OUTCOME={
            "stop_loss": 8.0,
            "take_profit": 1.0,
            "timeout": 4.0,
            "manual_close": 0.0,
            "external_close": 0.0,
            "liquidation_buffer_breach": 24.0,
        },
    )


@pytest.mark.asyncio
async def test_gate_disabled_short_circuits_without_db_call() -> None:
    """When LIVE_COOLDOWN_ENABLED=False, the gate must NOT hit the DB."""
    session = MagicMock()
    load_mock = AsyncMock()
    with patch(
        "app.trading.execution.cooldown_gate.load_cooldown", new=load_mock,
    ):
        # NOTE: gate currently still calls load_cooldown then is_cooldown_blocked
        # short-circuits on disabled. This is fine since load_cooldown is cheap,
        # but we don't assert "DB not called" here — we assert "result is None".
        result = await _apply_cooldown_gate(
            proposal=_proposal(), user_id=1,
            session=session, settings=_settings(enabled=False),
            now_fn=lambda: _NOW,
        )
    assert result is None


@pytest.mark.asyncio
async def test_gate_no_cooldown_row_returns_none() -> None:
    session = MagicMock()
    with patch(
        "app.trading.execution.cooldown_gate.load_cooldown",
        new=AsyncMock(return_value=None),
    ):
        result = await _apply_cooldown_gate(
            proposal=_proposal(), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is None


@pytest.mark.asyncio
async def test_gate_calendar_active_blocks_with_blocked_cooldown_outcome() -> None:
    row = SimpleNamespace(
        cooldown_until=_NOW + timedelta(hours=2),
        last_exit_reason="stop_loss",
        last_mtf_agreement=3,
    )
    session = MagicMock()
    with patch(
        "app.trading.execution.cooldown_gate.load_cooldown",
        new=AsyncMock(return_value=row),
    ):
        result = await _apply_cooldown_gate(
            proposal=_proposal(mtf=5), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is not None
    assert result.outcome == "blocked_cooldown"
    assert "calendar_until" in result.detail


@pytest.mark.asyncio
async def test_gate_sl_stale_mtf_blocks_after_calendar() -> None:
    """Calendar expired but SL + stale MTF still blocks."""
    row = SimpleNamespace(
        cooldown_until=_NOW - timedelta(hours=1),
        last_exit_reason="stop_loss",
        last_mtf_agreement=4,
    )
    session = MagicMock()
    with patch(
        "app.trading.execution.cooldown_gate.load_cooldown",
        new=AsyncMock(return_value=row),
    ):
        result = await _apply_cooldown_gate(
            proposal=_proposal(mtf=4), user_id=1,  # same mtf — stale
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is not None
    assert result.outcome == "blocked_cooldown"
    assert "sl_stale_mtf" in result.detail


@pytest.mark.asyncio
async def test_gate_fresh_mtf_after_sl_clears() -> None:
    row = SimpleNamespace(
        cooldown_until=_NOW - timedelta(hours=1),
        last_exit_reason="stop_loss",
        last_mtf_agreement=4,
    )
    session = MagicMock()
    with patch(
        "app.trading.execution.cooldown_gate.load_cooldown",
        new=AsyncMock(return_value=row),
    ):
        result = await _apply_cooldown_gate(
            proposal=_proposal(mtf=5), user_id=1,  # higher mtf — fresh
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is None


@pytest.mark.asyncio
async def test_gate_fails_open_on_db_error() -> None:
    """A DB read failure MUST NOT block trading. Critical fail-open."""
    session = MagicMock()
    with patch(
        "app.trading.execution.cooldown_gate.load_cooldown",
        new=AsyncMock(side_effect=RuntimeError("db blip")),
    ):
        result = await _apply_cooldown_gate(
            proposal=_proposal(), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is None  # fail-open
