"""PR8: Live trade exit-reason enum.

Persisted as TEXT to `live_trades.exit_reason` (column exists since
schema 0016 but was NULL-by-default — PR8 wires the write path).

Any value outside this enum landing on the column is an invariant
violation; tests catch the boundary.
"""
from __future__ import annotations

from enum import StrEnum


class LiveExitReason(StrEnum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TIMEOUT = "timeout"
    MANUAL_CLOSE = "manual_close"
    EXTERNAL_CLOSE = "external_close"
    LIQUIDATION_BUFFER_BREACH = "liquidation_buffer_breach"
