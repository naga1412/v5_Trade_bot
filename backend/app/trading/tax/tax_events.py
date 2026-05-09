"""SP-8 Phase H — write a tax_events row for a closed live_trade.

Spec §8.1. Every closed live_trade triggers exactly one tax_events
row, hash-chained for audit integrity. The caller (Phase J wiring)
hooks this into the live engine's exit-handler.

Inputs needed: the live_trades row, the FIFO match result, and the
USD/INR rate at close time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import insert_with_chain
from app.trading.tax.fifo_matcher import FIFOMatch


_TDS_RATE = 0.01  # Section 194S: 1% of exit value (sale proceeds)


@dataclass(frozen=True)
class TaxEventInput:
    """The minimal data needed to compute + persist one tax event."""

    trade_id: int
    user_id: int
    symbol: str
    direction: Literal["LONG", "SHORT"]
    quantity: float
    entry_price_usd: float
    exit_price_usd: float
    leverage: int
    closed_at: datetime
    usd_inr_at_close: float
    exchange: str = "binance"


def fy_year_for(closed_at: datetime) -> str:
    """Indian financial year is 1 Apr → 31 Mar. Returns 'FYNNNN-MM' format."""
    if closed_at.month >= 4:
        start = closed_at.year
        end = closed_at.year + 1
    else:
        start = closed_at.year - 1
        end = closed_at.year
    return f"FY{start}-{str(end)[-2:]}"


def compute_tax_event_payload(
    inp: TaxEventInput,
    *,
    fifo_matches: list[FIFOMatch],
    fee_paid_usd: float = 0.0,
) -> dict:
    """Build the tax_events row payload (without the audit-chain fields).

    The caller passes this dict to insert_with_chain. The row schema
    matches migration 0016's tax_events.
    """
    # Convert all USD prices to INR at close time. (More precise: each
    # FIFO buy leg should use the rate at THAT buy's close; we'd need
    # entry_value_inr per leg. For phase H we use one rate at close —
    # acceptable approximation for INR-denominated tax compliance, with
    # the per-leg breakdown deferred to a future phase if RBI tightens.)
    entry_value_inr = (
        sum(m.cost_basis_total_inr for m in fifo_matches)
        if fifo_matches else inp.quantity * inp.entry_price_usd * inp.usd_inr_at_close
    )
    exit_value_inr = inp.quantity * inp.exit_price_usd * inp.usd_inr_at_close

    realized_pnl_inr = exit_value_inr - entry_value_inr
    tds_owed_inr = exit_value_inr * _TDS_RATE
    fee_paid_inr = fee_paid_usd * inp.usd_inr_at_close

    fifo_match_id = (
        fifo_matches[0].matched_against_trade_id if fifo_matches else None
    )

    return {
        "trade_id": inp.trade_id,
        "user_id": inp.user_id,
        "symbol": inp.symbol,
        "direction": inp.direction,
        "quantity": inp.quantity,
        "entry_price": inp.entry_price_usd,
        "exit_price": inp.exit_price_usd,
        "entry_value_inr": entry_value_inr,
        "exit_value_inr": exit_value_inr,
        "realized_pnl_inr": realized_pnl_inr,
        "tds_owed_inr": tds_owed_inr,
        "fee_paid_inr": fee_paid_inr,
        "leverage": inp.leverage,
        "exchange": inp.exchange,
        "fy_year": fy_year_for(inp.closed_at),
        "closed_at": inp.closed_at,
        "fifo_match_id": fifo_match_id,
    }


async def record_tax_event(
    session: AsyncSession,
    inp: TaxEventInput,
    *,
    fifo_matches: list[FIFOMatch],
    fee_paid_usd: float = 0.0,
) -> str:
    """Compute + insert the hash-chained tax_events row. Returns row_hash.

    Caller commits.
    """
    payload = compute_tax_event_payload(
        inp, fifo_matches=fifo_matches, fee_paid_usd=fee_paid_usd,
    )
    return await insert_with_chain(session, "tax_events", payload)


__all__ = [
    "TaxEventInput",
    "compute_tax_event_payload",
    "fy_year_for",
    "record_tax_event",
]
