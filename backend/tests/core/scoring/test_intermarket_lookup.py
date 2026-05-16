"""Tests for intermarket_lookup.lookup_latest_funding_rate (PR1 Task 3.4).

Verifies read path against an in-memory SQLite table whose schema mirrors
the real intermarket_snapshots schema (alembic 0014_intermarket), including
the real column name: ``captured_at`` (NOT ``ts``).
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.scoring.intermarket_lookup import lookup_latest_funding_rate

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def session_with_intermarket():
    """SQLite in-memory session with intermarket_snapshots table."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE intermarket_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                captured_at TEXT NOT NULL DEFAULT (datetime('now')),
                funding_rate REAL,
                mark_price REAL,
                open_interest REAL,
                source TEXT NOT NULL
            )
        """))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_returns_most_recent_funding_rate(session_with_intermarket):
    """INSERT 2 rows; the function must return the later one's funding_rate."""
    await session_with_intermarket.execute(
        sa.text(
            "INSERT INTO intermarket_snapshots (symbol, captured_at, funding_rate, source) "
            "VALUES (:sym, :ts, :fr, 'binance_futures')"
        ),
        {"sym": "BTCUSDT", "ts": "2026-05-01 10:00:00", "fr": 0.0003},
    )
    await session_with_intermarket.execute(
        sa.text(
            "INSERT INTO intermarket_snapshots (symbol, captured_at, funding_rate, source) "
            "VALUES (:sym, :ts, :fr, 'binance_futures')"
        ),
        {"sym": "BTCUSDT", "ts": "2026-05-01 18:00:00", "fr": 0.0009},
    )
    await session_with_intermarket.commit()

    result = await lookup_latest_funding_rate(session_with_intermarket, "BTCUSDT")
    assert result == pytest.approx(0.0009)


async def test_returns_none_when_no_rows_for_symbol(session_with_intermarket):
    """Empty table → None returned, no exception."""
    result = await lookup_latest_funding_rate(session_with_intermarket, "ETHUSDT")
    assert result is None


async def test_returns_none_when_funding_rate_column_is_null(session_with_intermarket):
    """Row present but funding_rate IS NULL → None returned."""
    await session_with_intermarket.execute(
        sa.text(
            "INSERT INTO intermarket_snapshots (symbol, captured_at, funding_rate, source) "
            "VALUES (:sym, :ts, NULL, 'binance_futures')"
        ),
        {"sym": "SOLUSDT", "ts": "2026-05-01 10:00:00"},
    )
    await session_with_intermarket.commit()

    result = await lookup_latest_funding_rate(session_with_intermarket, "SOLUSDT")
    assert result is None


async def test_returns_float_not_raw_decimal(session_with_intermarket):
    """Stored value 0.00075 → returned as Python float (not Decimal)."""
    await session_with_intermarket.execute(
        sa.text(
            "INSERT INTO intermarket_snapshots (symbol, captured_at, funding_rate, source) "
            "VALUES (:sym, :ts, :fr, 'binance_futures')"
        ),
        {"sym": "BNBUSDT", "ts": "2026-05-01 12:00:00", "fr": 0.00075},
    )
    await session_with_intermarket.commit()

    result = await lookup_latest_funding_rate(session_with_intermarket, "BNBUSDT")
    assert isinstance(result, float)
    assert result == pytest.approx(0.00075)


async def test_returns_none_on_db_error(session_with_intermarket):
    """Drop the table → OperationalError; function must return None (fail-open)."""
    # Drop the table to force OperationalError on next query
    await session_with_intermarket.execute(sa.text("DROP TABLE intermarket_snapshots"))
    await session_with_intermarket.commit()

    result = await lookup_latest_funding_rate(session_with_intermarket, "BTCUSDT")
    assert result is None


async def test_symbol_isolation(session_with_intermarket):
    """Funding rate for symbol A must not bleed into symbol B."""
    await session_with_intermarket.execute(
        sa.text(
            "INSERT INTO intermarket_snapshots (symbol, captured_at, funding_rate, source) "
            "VALUES (:sym, :ts, :fr, 'binance_futures')"
        ),
        {"sym": "BTCUSDT", "ts": "2026-05-01 10:00:00", "fr": 0.001},
    )
    await session_with_intermarket.commit()

    result = await lookup_latest_funding_rate(session_with_intermarket, "ETHUSDT")
    assert result is None
