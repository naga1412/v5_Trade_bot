"""SP-8 Phase H — ITR-3 Schedule VDA CSV export.

Spec §8.5. User clicks "Download FY tax report" in settings; backend
emits a CSV that's compatible with ClearTax / Quicko / manual upload
to the Income Tax e-filing portal.

Format: one row per closed trade (one tax_events row), columns
matching what filing tools expect for VDA Schedule. We bias toward
ClearTax's format since that's the most common; minor deviations
should be acceptable for any of them.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from io import StringIO

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


# Column order for Schedule VDA CSV — matches ClearTax's import format.
_CSV_COLUMNS = [
    "Date of Sale",
    "Date of Acquisition",
    "Asset",
    "Direction",
    "Quantity",
    "Sale Consideration (INR)",
    "Cost of Acquisition (INR)",
    "TDS Deducted (INR)",
    "Income from VDA (INR)",
    "Exchange",
    "Trade ID",
]


@dataclass(frozen=True)
class FYSummary:
    """Header summary of the FY for the report."""

    fy_year: str
    total_trades: int
    total_realized_pnl_inr: float
    total_tds_inr: float
    total_fees_inr: float


async def fetch_tax_events_for_fy(
    session: AsyncSession, *, user_id: int, fy_year: str,
) -> list[dict]:
    """Return tax_events rows for the user + FY, oldest first."""
    rows = (await session.execute(
        sa.text(
            "SELECT id, trade_id, symbol, direction, quantity, "
            "       entry_price, exit_price, entry_value_inr, "
            "       exit_value_inr, realized_pnl_inr, tds_owed_inr, "
            "       fee_paid_inr, exchange, closed_at "
            "FROM tax_events "
            "WHERE user_id = :u AND fy_year = :fy "
            "ORDER BY closed_at ASC"
        ),
        {"u": user_id, "fy": fy_year},
    )).all()
    return [dict(r._mapping) for r in rows]


def compute_summary(events: list[dict], *, fy_year: str) -> FYSummary:
    return FYSummary(
        fy_year=fy_year,
        total_trades=len(events),
        total_realized_pnl_inr=sum(float(e["realized_pnl_inr"]) for e in events),
        total_tds_inr=sum(float(e["tds_owed_inr"]) for e in events),
        total_fees_inr=sum(float(e["fee_paid_inr"]) for e in events),
    )


def render_csv(events: list[dict]) -> str:
    """Render the rows as a Schedule-VDA CSV string.

    The acquisition date is the close timestamp of the matched FIFO
    buy-leg; until we wire the FIFO matcher's source-trade lookup, we
    leave it blank (caller fills from live_trades.opened_at if needed).
    Phase J wiring resolves this; for the standalone export, the
    sale-date column is canonical.
    """
    buf = StringIO()
    w = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS)
    w.writeheader()
    for e in events:
        closed_at = e["closed_at"]
        if isinstance(closed_at, str):
            closed_at_str = closed_at
        elif isinstance(closed_at, datetime):
            closed_at_str = closed_at.strftime("%Y-%m-%d")
        else:
            closed_at_str = str(closed_at)
        w.writerow({
            "Date of Sale": closed_at_str,
            "Date of Acquisition": "",  # filled by FIFO link in Phase J
            "Asset": e["symbol"],
            "Direction": e["direction"],
            "Quantity": f"{float(e['quantity']):.8f}",
            "Sale Consideration (INR)": f"{float(e['exit_value_inr']):.2f}",
            "Cost of Acquisition (INR)": f"{float(e['entry_value_inr']):.2f}",
            "TDS Deducted (INR)": f"{float(e['tds_owed_inr']):.2f}",
            "Income from VDA (INR)": f"{float(e['realized_pnl_inr']):.2f}",
            "Exchange": e["exchange"],
            "Trade ID": str(e["trade_id"]),
        })
    return buf.getvalue()


async def export_fy(
    session: AsyncSession, *, user_id: int, fy_year: str,
) -> tuple[FYSummary, str]:
    """Top-level: fetch events, compute summary, render CSV.

    Returns (summary, csv_text). Caller is the FastAPI route — it
    sends the CSV as a file download with a JSON summary header.
    """
    events = await fetch_tax_events_for_fy(
        session, user_id=user_id, fy_year=fy_year,
    )
    summary = compute_summary(events, fy_year=fy_year)
    csv_text = render_csv(events)
    return summary, csv_text


__all__ = [
    "FYSummary",
    "compute_summary",
    "export_fy",
    "fetch_tax_events_for_fy",
    "render_csv",
]
