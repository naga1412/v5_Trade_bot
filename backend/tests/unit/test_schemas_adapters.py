"""Unit tests for SP-3 admin / universe Pydantic schemas (Phase F)."""
from datetime import datetime, timezone

from app.api.schemas import (
    AdapterHealthOut,
    SyncResultOut,
    UniverseEntryOut,
)


def test_adapter_health_out_basic() -> None:
    h = AdapterHealthOut(
        exchange="binance",
        checked_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
        is_healthy=True, latency_ms=42,
        error_message=None, quota_used_pct=0.12,
    )
    assert h.is_healthy is True


def test_universe_entry_out_active_when_delisted_at_is_none() -> None:
    u = UniverseEntryOut(
        exchange="binance", symbol="BTC/USDT", asset_class="crypto",
        listed_at=datetime(2017, 8, 17, tzinfo=timezone.utc),
        delisted_at=None,
        last_synced_at=datetime.now(timezone.utc),
    )
    assert u.is_active is True


def test_universe_entry_out_inactive_when_delisted_at_is_set() -> None:
    u = UniverseEntryOut(
        exchange="binance", symbol="LUNA/USDT", asset_class="crypto",
        listed_at=datetime(2020, 8, 1, tzinfo=timezone.utc),
        delisted_at=datetime(2022, 5, 12, tzinfo=timezone.utc),
        last_synced_at=datetime.now(timezone.utc),
    )
    assert u.is_active is False


def test_sync_result_out() -> None:
    r = SyncResultOut(
        exchange="binance",
        added=12, still_active=240, newly_delisted=3,
    )
    assert r.added == 12
    assert r.still_active == 240
    assert r.newly_delisted == 3
