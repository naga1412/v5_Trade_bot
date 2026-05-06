from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

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
async def test_persist_inserts_each_snapshot(session: AsyncSession) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
    snaps = [
        IntermarketSnapshot(symbol="BTC/USDT", captured_at=now,
                            funding_rate=-0.0001, mark_price=70000.0,
                            open_interest=1.0e9, source="binance_futures"),
        IntermarketSnapshot(symbol="ETH/USDT", captured_at=now,
                            funding_rate=0.0002, mark_price=3000.0,
                            open_interest=2.0e8, source="binance_futures"),
    ]
    n = await persist_intermarket_snapshots(session, snaps)
    assert n == 2
    rows = (await session.execute(sa.text(
        "SELECT symbol, funding_rate, source FROM intermarket_snapshots "
        "ORDER BY symbol"
    ))).all()
    assert [r.symbol for r in rows] == ["BTC/USDT", "ETH/USDT"]
    assert rows[0].source == "binance_futures"


@pytest.mark.asyncio
async def test_persist_empty_returns_zero(session: AsyncSession) -> None:
    n = await persist_intermarket_snapshots(session, [])
    assert n == 0


@pytest.mark.asyncio
async def test_persist_allows_null_open_interest(session: AsyncSession) -> None:
    """Partial snapshot (OI endpoint failed) must still persist."""
    now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
    snap = IntermarketSnapshot(
        symbol="BTC/USDT", captured_at=now,
        funding_rate=-0.0001, mark_price=70000.0,
        open_interest=None, source="binance_futures",
    )
    n = await persist_intermarket_snapshots(session, [snap])
    assert n == 1
