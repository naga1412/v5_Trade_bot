from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.api.schemas import IntermarketSnapshotOut


def test_intermarket_snapshot_out_full_payload() -> None:
    out = IntermarketSnapshotOut(
        symbol="BTC/USDT",
        funding_rate=-0.0012,
        mark_price=78320.5,
        open_interest=1.23e9,
        open_interest_delta_24h_pct=0.085,
        dxy_correlation_30d=-0.42,
        gold_correlation_30d=0.18,
        captured_at=datetime(2026, 5, 6, 14, 55, tzinfo=timezone.utc),
    )
    assert out.symbol == "BTC/USDT"
    assert out.dxy_correlation_30d == pytest.approx(-0.42)


def test_intermarket_snapshot_out_allows_nulls() -> None:
    out = IntermarketSnapshotOut(
        symbol="BTC/USDT",
        funding_rate=None, mark_price=None, open_interest=None,
        open_interest_delta_24h_pct=None,
        dxy_correlation_30d=None, gold_correlation_30d=None,
        captured_at=datetime(2026, 5, 6, 14, 55, tzinfo=timezone.utc),
    )
    assert out.funding_rate is None
    assert out.dxy_correlation_30d is None


def test_intermarket_snapshot_out_correlation_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        IntermarketSnapshotOut(
            symbol="BTC/USDT",
            funding_rate=None, mark_price=None, open_interest=None,
            open_interest_delta_24h_pct=None,
            dxy_correlation_30d=1.5,  # > 1.0 → fails
            gold_correlation_30d=None,
            captured_at=datetime(2026, 5, 6, 14, 55, tzinfo=timezone.utc),
        )
