"""PR8: live_exit_monitor worker registry entry.

Mirrors test_shadow_worker_pr3_registry.py — pins the registry contract
for live_exit_monitor so a future PR can't silently drift the staleness
budget or required_env.
"""
from __future__ import annotations

from app.ops.worker_registry import by_name


def test_live_exit_monitor_is_registered() -> None:
    spec = by_name("live_exit_monitor")
    assert spec is not None


def test_live_exit_monitor_max_staleness_matches_30s_poll_cadence() -> None:
    """30s poll + 1.5x slack = 5min budget, matching liquidation_monitor."""
    spec = by_name("live_exit_monitor")
    assert spec.max_staleness_seconds == 5 * 60


def test_live_exit_monitor_requires_autonomous_trading_env() -> None:
    """Worker only runs when autonomous trading is enabled."""
    spec = by_name("live_exit_monitor")
    assert "AUTONOMOUS_TRADING_ENABLED" in spec.required_env


def test_live_exit_monitor_is_stateful() -> None:
    """Touches the exchange (Binance get_position) — never auto-restart."""
    spec = by_name("live_exit_monitor")
    assert spec.stateful is True
