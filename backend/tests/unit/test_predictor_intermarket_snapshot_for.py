from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.predictor import _intermarket_snapshot_for
from app.data.adapters.binance_futures_intermarket import IntermarketSnapshot
from app.data.intermarket_persistence import persist_intermarket_snapshots


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE intermarket_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                funding_rate REAL,
                mark_price REAL,
                open_interest REAL,
                source TEXT NOT NULL
                    CHECK (source IN ('binance_futures', 'bybit'))
            )
        """))
    async with AsyncSession(engine) as s:
        yield s


@pytest.mark.asyncio
async def test_returns_none_when_no_rows(session: AsyncSession) -> None:
    funding, oi_delta = await _intermarket_snapshot_for("BTC/USDT", session)
    assert funding is None
    assert oi_delta is None


@pytest.mark.asyncio
async def test_returns_funding_and_none_when_no_24h_baseline(session: AsyncSession) -> None:
    """Latest exists but no 24h-ago row → funding only, oi_delta None."""
    now = datetime(2026, 5, 6, 12, tzinfo=timezone.utc)
    await persist_intermarket_snapshots(session, [
        IntermarketSnapshot(symbol="BTC/USDT", captured_at=now,
            funding_rate=-0.0012, mark_price=70000.0, open_interest=1.0e9,
            source="binance_futures"),
    ])
    funding, oi_delta = await _intermarket_snapshot_for("BTC/USDT", session)
    assert funding == pytest.approx(-0.0012)
    assert oi_delta is None


@pytest.mark.asyncio
async def test_returns_funding_and_oi_delta_when_baseline_exists(session: AsyncSession) -> None:
    """Latest + 24h-ago both present → oi_delta computed."""
    now = datetime(2026, 5, 6, 12, tzinfo=timezone.utc)
    yesterday = now - timedelta(hours=24)
    await persist_intermarket_snapshots(session, [
        IntermarketSnapshot(symbol="BTC/USDT", captured_at=yesterday,
            funding_rate=0.0, mark_price=68000.0, open_interest=1.0e9,
            source="binance_futures"),
        IntermarketSnapshot(symbol="BTC/USDT", captured_at=now,
            funding_rate=-0.0012, mark_price=70000.0, open_interest=1.25e9,
            source="binance_futures"),
    ])
    funding, oi_delta = await _intermarket_snapshot_for("BTC/USDT", session)
    assert funding == pytest.approx(-0.0012)
    assert oi_delta == pytest.approx(0.25)  # +25% in 24h


@pytest.mark.asyncio
async def test_returns_none_oi_delta_when_baseline_oi_zero(session: AsyncSession) -> None:
    now = datetime(2026, 5, 6, 12, tzinfo=timezone.utc)
    yesterday = now - timedelta(hours=24)
    await persist_intermarket_snapshots(session, [
        IntermarketSnapshot(symbol="BTC/USDT", captured_at=yesterday,
            funding_rate=0.0, mark_price=68000.0, open_interest=0.0,
            source="binance_futures"),
        IntermarketSnapshot(symbol="BTC/USDT", captured_at=now,
            funding_rate=-0.0012, mark_price=70000.0, open_interest=1.25e9,
            source="binance_futures"),
    ])
    funding, oi_delta = await _intermarket_snapshot_for("BTC/USDT", session)
    assert funding == pytest.approx(-0.0012)
    assert oi_delta is None
