"""PR10 dispatcher gate — pre-condition integration."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.trading.execution.symbol_allowlist_gate import (
    _apply_symbol_allowlist_gate,
)


_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_caches():
    from app.trading.execution import symbol_allowlist_gate as gate
    gate._CACHE.clear()
    gate._LOCKS.clear()
    yield
    gate._CACHE.clear()
    gate._LOCKS.clear()


def _proposal(symbol: str):
    return SimpleNamespace(symbol=symbol)


def _settings(enabled: bool = True):
    return SimpleNamespace(
        SYMBOL_ALLOWLIST_ENABLED=enabled,
        SHADOW_STABLECOIN_EXCLUDE_LIST=["USDC", "FDUSD", "USD1", "BUSD", "TUSD", "DAI"],
        SYMBOL_ALLOWLIST_GRACE_TRADES=50,
        SYMBOL_ALLOWLIST_CACHE_TTL_SECONDS=3600,
    )


@pytest.mark.asyncio
async def test_gate_disabled_returns_none() -> None:
    session = MagicMock()
    result = await _apply_symbol_allowlist_gate(
        proposal=_proposal("BTCUSDT"), user_id=1,
        session=session, settings=_settings(enabled=False),
        now_fn=lambda: _NOW,
    )
    assert result is None


@pytest.mark.asyncio
async def test_gate_stablecoin_returns_blocked_stablecoin() -> None:
    session = MagicMock()
    with patch(
        "app.trading.execution.symbol_allowlist_gate.load_latest_snapshots_per_symbol",
        new=AsyncMock(return_value={}),
    ):
        result = await _apply_symbol_allowlist_gate(
            proposal=_proposal("FDUSDUSDT"), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is not None
    assert result.outcome == "blocked_stablecoin"


@pytest.mark.asyncio
async def test_gate_no_snapshot_falls_open_allows() -> None:
    """When the symbol has NO snapshot row → allow (defensive default)."""
    session = MagicMock()
    with patch(
        "app.trading.execution.symbol_allowlist_gate.load_latest_snapshots_per_symbol",
        new=AsyncMock(return_value={}),
    ):
        result = await _apply_symbol_allowlist_gate(
            proposal=_proposal("BTCUSDT"), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is None


@pytest.mark.asyncio
async def test_gate_low_sharpe_returns_blocked_low_sharpe() -> None:
    snap = SimpleNamespace(trades_count=200, sharpe=-2.0)
    session = MagicMock()
    with patch(
        "app.trading.execution.symbol_allowlist_gate.load_latest_snapshots_per_symbol",
        new=AsyncMock(return_value={"BTCUSDT": snap}),
    ):
        result = await _apply_symbol_allowlist_gate(
            proposal=_proposal("BTCUSDT"), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is not None
    assert result.outcome == "blocked_low_sharpe"


@pytest.mark.asyncio
async def test_gate_positive_sharpe_passes() -> None:
    snap = SimpleNamespace(trades_count=200, sharpe=1.5)
    session = MagicMock()
    with patch(
        "app.trading.execution.symbol_allowlist_gate.load_latest_snapshots_per_symbol",
        new=AsyncMock(return_value={"BTCUSDT": snap}),
    ):
        result = await _apply_symbol_allowlist_gate(
            proposal=_proposal("BTCUSDT"), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is None


@pytest.mark.asyncio
async def test_gate_grace_window_passes_negative_sharpe() -> None:
    """trades_count < grace → allowed even with negative Sharpe."""
    snap = SimpleNamespace(trades_count=10, sharpe=-5.0)
    session = MagicMock()
    with patch(
        "app.trading.execution.symbol_allowlist_gate.load_latest_snapshots_per_symbol",
        new=AsyncMock(return_value={"BTCUSDT": snap}),
    ):
        result = await _apply_symbol_allowlist_gate(
            proposal=_proposal("BTCUSDT"), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is None


@pytest.mark.asyncio
async def test_gate_fails_open_on_db_error() -> None:
    """DB read failure → return None (let trade proceed). Critical."""
    session = MagicMock()
    with patch(
        "app.trading.execution.symbol_allowlist_gate.load_latest_snapshots_per_symbol",
        new=AsyncMock(side_effect=RuntimeError("db blip")),
    ):
        result = await _apply_symbol_allowlist_gate(
            proposal=_proposal("BTCUSDT"), user_id=1,
            session=session, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert result is None
