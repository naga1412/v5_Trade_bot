"""TIER 1 (defect sweep 2026-08-06): live_trades.pnl_usdt/pnl_pct were never
written by any close path. compute_realized_pnl is the single formula shared
by live_exit_monitor and liquidation_monitor so the two paths can't drift
from each other the way live's and shadow's SL geometry already did.
"""
from __future__ import annotations

import pytest

from app.trading.pnl import compute_realized_pnl


def test_long_profit() -> None:
    pct, usdt = compute_realized_pnl(
        direction="LONG", entry_price=100.0, exit_price=110.0,
        position_value_usdt=200.0,
    )
    assert pct == pytest.approx(10.0)
    assert usdt == pytest.approx(20.0)


def test_long_loss() -> None:
    pct, usdt = compute_realized_pnl(
        direction="LONG", entry_price=100.0, exit_price=95.0,
        position_value_usdt=200.0,
    )
    assert pct == pytest.approx(-5.0)
    assert usdt == pytest.approx(-10.0)


def test_short_profit() -> None:
    pct, usdt = compute_realized_pnl(
        direction="SHORT", entry_price=100.0, exit_price=90.0,
        position_value_usdt=200.0,
    )
    assert pct == pytest.approx(10.0)
    assert usdt == pytest.approx(20.0)


def test_short_loss() -> None:
    pct, usdt = compute_realized_pnl(
        direction="SHORT", entry_price=100.0, exit_price=105.0,
        position_value_usdt=200.0,
    )
    assert pct == pytest.approx(-5.0)
    assert usdt == pytest.approx(-10.0)


def test_rejects_non_positive_entry_price() -> None:
    """A financial calculation must not silently divide by zero / negative."""
    with pytest.raises(ValueError):
        compute_realized_pnl(
            direction="LONG", entry_price=0.0, exit_price=10.0,
            position_value_usdt=200.0,
        )
