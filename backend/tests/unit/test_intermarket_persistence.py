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


from app.data.intermarket_persistence import (
    cleanup_old_intermarket,
    latest_snapshot_for,
    snapshot_at_or_before,
)


@pytest.mark.asyncio
async def test_latest_snapshot_for_returns_freshest_row(session: AsyncSession) -> None:
    t0 = datetime(2026, 5, 6, 11, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
    await persist_intermarket_snapshots(session, [
        IntermarketSnapshot(symbol="BTC/USDT", captured_at=t0,
            funding_rate=0.0, mark_price=1.0, open_interest=10.0,
            source="binance_futures"),
        IntermarketSnapshot(symbol="BTC/USDT", captured_at=t1,
            funding_rate=0.001, mark_price=1.0, open_interest=20.0,
            source="binance_futures"),
        IntermarketSnapshot(symbol="ETH/USDT", captured_at=t1,
            funding_rate=0.002, mark_price=1.0, open_interest=30.0,
            source="binance_futures"),
    ])
    snap = await latest_snapshot_for(session, "BTC/USDT")
    assert snap is not None
    assert snap.funding_rate == pytest.approx(0.001)
    assert snap.open_interest == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_latest_snapshot_for_returns_none_when_no_rows(session: AsyncSession) -> None:
    snap = await latest_snapshot_for(session, "BTC/USDT")
    assert snap is None


@pytest.mark.asyncio
async def test_snapshot_at_or_before_returns_pre_cutoff_row(session: AsyncSession) -> None:
    t0 = datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc)  # 24h ago
    t1 = datetime(2026, 5, 6, 11, 0, tzinfo=timezone.utc)  # 1h ago
    t2 = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)  # now
    await persist_intermarket_snapshots(session, [
        IntermarketSnapshot(symbol="BTC/USDT", captured_at=t0, funding_rate=0.0,
            mark_price=1.0, open_interest=10.0, source="binance_futures"),
        IntermarketSnapshot(symbol="BTC/USDT", captured_at=t1, funding_rate=0.001,
            mark_price=1.0, open_interest=20.0, source="binance_futures"),
        IntermarketSnapshot(symbol="BTC/USDT", captured_at=t2, funding_rate=0.001,
            mark_price=1.0, open_interest=22.0, source="binance_futures"),
    ])
    snap = await snapshot_at_or_before(session, "BTC/USDT",
                                       ts=datetime(2026, 5, 5, 13, 0, tzinfo=timezone.utc))
    assert snap is not None
    assert snap.open_interest == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_cleanup_old_intermarket_deletes_aged_rows(session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    await persist_intermarket_snapshots(session, [
        IntermarketSnapshot(symbol="BTC/USDT", captured_at=now - timedelta(days=20),
            funding_rate=0.0, mark_price=1.0, open_interest=1.0,
            source="binance_futures"),
        IntermarketSnapshot(symbol="BTC/USDT", captured_at=now - timedelta(days=5),
            funding_rate=0.0, mark_price=1.0, open_interest=1.0,
            source="binance_futures"),
    ])
    deleted = await cleanup_old_intermarket(session, older_than_days=14)
    assert deleted == 1
    rows = (await session.execute(sa.text("SELECT COUNT(*) AS n FROM intermarket_snapshots"))).first()
    assert rows.n == 1
