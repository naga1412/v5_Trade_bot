"""SP-8 Phase H — ITR-3 Schedule VDA CSV export tests."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import StringIO

import pytest

from app.trading.tax.itr_export import compute_summary, render_csv


def _ev(**overrides) -> dict:
    base = dict(
        id=1, trade_id=42, symbol="BTC/USDT", direction="LONG",
        quantity=0.1, entry_price=80_000, exit_price=82_000,
        entry_value_inr=80_000 * 0.1 * 83.42,
        exit_value_inr=82_000 * 0.1 * 83.42,
        realized_pnl_inr=2_000 * 0.1 * 83.42,
        tds_owed_inr=82_000 * 0.1 * 83.42 * 0.01,
        fee_paid_inr=2.50 * 83.42,
        exchange="binance",
        closed_at=datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return base


# ---- compute_summary -----------------------------------------------------


def test_compute_summary_sums_all_columns() -> None:
    events = [
        _ev(realized_pnl_inr=1000, tds_owed_inr=100, fee_paid_inr=10),
        _ev(realized_pnl_inr=-500, tds_owed_inr=80, fee_paid_inr=8),
        _ev(realized_pnl_inr=200, tds_owed_inr=20, fee_paid_inr=2),
    ]
    s = compute_summary(events, fy_year="FY2026-27")
    assert s.fy_year == "FY2026-27"
    assert s.total_trades == 3
    assert s.total_realized_pnl_inr == pytest.approx(700)
    assert s.total_tds_inr == pytest.approx(200)
    assert s.total_fees_inr == pytest.approx(20)


def test_empty_summary() -> None:
    s = compute_summary([], fy_year="FY2026-27")
    assert s.total_trades == 0
    assert s.total_realized_pnl_inr == 0.0
    assert s.total_tds_inr == 0.0
    assert s.total_fees_inr == 0.0


# ---- render_csv: shape + header + rows ----------------------------------


def test_render_csv_has_correct_header_columns() -> None:
    out = render_csv([_ev()])
    reader = csv.reader(StringIO(out))
    header = next(reader)
    assert header == [
        "Date of Sale", "Date of Acquisition", "Asset", "Direction",
        "Quantity", "Sale Consideration (INR)", "Cost of Acquisition (INR)",
        "TDS Deducted (INR)", "Income from VDA (INR)", "Exchange", "Trade ID",
    ]


def test_render_csv_one_row_per_event() -> None:
    out = render_csv([_ev(), _ev(trade_id=43), _ev(trade_id=44)])
    rows = list(csv.reader(StringIO(out)))
    assert len(rows) == 4  # 1 header + 3 data


def test_render_csv_formats_quantities_with_8_decimals() -> None:
    """Crypto exchanges report quantities to 8 decimal places — match that."""
    out = render_csv([_ev(quantity=0.12345678)])
    rows = list(csv.reader(StringIO(out)))
    # Find the Quantity column
    header = rows[0]
    qty_col = header.index("Quantity")
    assert rows[1][qty_col] == "0.12345678"


def test_render_csv_formats_currency_with_2_decimals() -> None:
    out = render_csv([_ev(exit_value_inr=683998.4567)])
    rows = list(csv.reader(StringIO(out)))
    sale_col = rows[0].index("Sale Consideration (INR)")
    assert rows[1][sale_col] == "683998.46"


def test_render_csv_propagates_direction() -> None:
    out = render_csv([_ev(direction="SHORT")])
    rows = list(csv.reader(StringIO(out)))
    dir_col = rows[0].index("Direction")
    assert rows[1][dir_col] == "SHORT"


def test_render_csv_round_trips_through_pandas() -> None:
    """Acceptance criterion §19: ITR-3 export produces a CSV that
    round-trips through pandas with no schema warning."""
    pd = pytest.importorskip("pandas")
    out = render_csv([_ev(), _ev(trade_id=43, direction="SHORT")])
    df = pd.read_csv(StringIO(out))
    assert len(df) == 2
    assert list(df.columns) == [
        "Date of Sale", "Date of Acquisition", "Asset", "Direction",
        "Quantity", "Sale Consideration (INR)", "Cost of Acquisition (INR)",
        "TDS Deducted (INR)", "Income from VDA (INR)", "Exchange", "Trade ID",
    ]


def test_render_csv_handles_string_closed_at() -> None:
    """When closed_at comes back as ISO string from psql, format it as date."""
    out = render_csv([_ev(closed_at="2026-05-09 12:00:00+00:00")])
    rows = list(csv.reader(StringIO(out)))
    assert rows[1][0]  # non-empty
