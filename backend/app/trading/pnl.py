"""Realized PnL for a closed live_trades position.

Shared by live_exit_monitor (TP/SL/TIMEOUT/EXTERNAL_CLOSE) and
liquidation_monitor (LIQUIDATION_BUFFER_BREACH) so the formula can't drift
between the two close paths the way live's and shadow's SL geometry already
did (defect sweep 2026-08-06, TIER 1). Convention matches
db/payload_builders.py's shadow_trades formula: pnl_pct is a percentage
(10.0 == 10%), pnl_usdt = position_value_usdt * pnl_pct / 100.
"""
from __future__ import annotations


def compute_realized_pnl(
    *,
    direction: str,
    entry_price: float,
    exit_price: float,
    position_value_usdt: float,
) -> tuple[float, float]:
    """Returns ``(pnl_pct, pnl_usdt)`` for a closed LONG or SHORT position."""
    if entry_price <= 0:
        raise ValueError(f"entry_price must be positive, got {entry_price!r}")
    if direction == "LONG":
        pnl_pct = (exit_price - entry_price) / entry_price * 100.0
    else:
        pnl_pct = (entry_price - exit_price) / entry_price * 100.0
    pnl_usdt = position_value_usdt * pnl_pct / 100.0
    return pnl_pct, pnl_usdt
