"""SP-8 Phase H — tax_events row construction tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.trading.tax.fifo_matcher import FIFOMatch
from app.trading.tax.tax_events import (
    TaxEventInput,
    compute_tax_event_payload,
    fy_year_for,
)


_INR_RATE = 83.42  # ~1 USD


def _input(**overrides) -> TaxEventInput:
    base = dict(
        trade_id=42,
        user_id=1,
        symbol="BTC/USDT",
        direction="LONG",
        quantity=0.1,
        entry_price_usd=80_000.0,
        exit_price_usd=82_000.0,
        leverage=5,
        closed_at=datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc),
        usd_inr_at_close=_INR_RATE,
    )
    base.update(overrides)
    return TaxEventInput(**base)


# ---- fy_year_for ---------------------------------------------------------


def test_fy_year_april_to_march_boundary() -> None:
    # April 1 of any year starts a new FY
    assert fy_year_for(datetime(2026, 4, 1, tzinfo=timezone.utc)) == "FY2026-27"
    # March 31 closes the previous FY
    assert fy_year_for(datetime(2026, 3, 31, tzinfo=timezone.utc)) == "FY2025-26"
    # Mid-FY date
    assert fy_year_for(datetime(2026, 5, 9, tzinfo=timezone.utc)) == "FY2026-27"
    assert fy_year_for(datetime(2026, 1, 15, tzinfo=timezone.utc)) == "FY2025-26"


# ---- compute_tax_event_payload (no FIFO matches — fallback path) --------


def test_payload_uses_entry_value_when_no_fifo_matches() -> None:
    """Without FIFO matches we use entry_price * qty * rate as cost basis.
    (The Phase J wiring always provides matches; this is the fallback.)"""
    p = compute_tax_event_payload(_input(), fifo_matches=[])
    expected_entry_inr = 0.1 * 80_000 * _INR_RATE
    expected_exit_inr = 0.1 * 82_000 * _INR_RATE
    assert p["entry_value_inr"] == pytest.approx(expected_entry_inr)
    assert p["exit_value_inr"] == pytest.approx(expected_exit_inr)
    assert p["realized_pnl_inr"] == pytest.approx(
        expected_exit_inr - expected_entry_inr,
    )


def test_payload_tds_is_one_pct_of_exit_value() -> None:
    p = compute_tax_event_payload(_input(), fifo_matches=[])
    assert p["tds_owed_inr"] == pytest.approx(p["exit_value_inr"] * 0.01)


# ---- compute_tax_event_payload (FIFO matches — primary path) ------------


def test_payload_uses_fifo_matches_for_cost_basis() -> None:
    """When FIFO provides matches, sum cost_basis_total_inr across them."""
    matches = [
        FIFOMatch(
            sell_qty=0.05,
            cost_basis_per_unit_inr=80_000 * _INR_RATE,
            cost_basis_total_inr=0.05 * 80_000 * _INR_RATE,
            matched_against_trade_id=10,
        ),
        FIFOMatch(
            sell_qty=0.05,
            cost_basis_per_unit_inr=82_000 * _INR_RATE,
            cost_basis_total_inr=0.05 * 82_000 * _INR_RATE,
            matched_against_trade_id=11,
        ),
    ]
    p = compute_tax_event_payload(_input(), fifo_matches=matches)
    expected_entry = (0.05 * 80_000 + 0.05 * 82_000) * _INR_RATE
    assert p["entry_value_inr"] == pytest.approx(expected_entry)
    # First match's source is recorded for audit
    assert p["fifo_match_id"] == 10


def test_payload_includes_fees() -> None:
    p = compute_tax_event_payload(
        _input(), fifo_matches=[], fee_paid_usd=2.50,
    )
    assert p["fee_paid_inr"] == pytest.approx(2.50 * _INR_RATE)


# ---- Direction + leverage propagated -------------------------------------


def test_payload_carries_direction_and_leverage() -> None:
    p = compute_tax_event_payload(
        _input(direction="SHORT", leverage=10),
        fifo_matches=[],
    )
    assert p["direction"] == "SHORT"
    assert p["leverage"] == 10


def test_payload_fy_year_set_from_closed_at() -> None:
    p = compute_tax_event_payload(_input(), fifo_matches=[])
    assert p["fy_year"] == "FY2026-27"


def test_payload_loss_produces_negative_pnl() -> None:
    p = compute_tax_event_payload(
        _input(entry_price_usd=82_000, exit_price_usd=80_000),
        fifo_matches=[],
    )
    assert p["realized_pnl_inr"] < 0
