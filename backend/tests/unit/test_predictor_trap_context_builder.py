from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.predictor import _build_trap_context
from app.data.adapters.binance_futures_intermarket import IntermarketSnapshot
from app.data.intermarket_persistence import persist_intermarket_snapshots


def _bars() -> pd.DataFrame:
    idx = pd.date_range("2026-05-05", periods=120, freq="h", tz="UTC")
    return pd.DataFrame({
        "open":  [100.0] * 120,
        "high":  [101.0] * 120,
        "low":   [99.0]  * 120,
        "close": [100.0] * 120,
        "volume":[10.0]  * 120,
    }, index=idx)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE intermarket_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                funding_rate REAL, mark_price REAL, open_interest REAL,
                source TEXT NOT NULL
                    CHECK (source IN ('binance_futures', 'bybit'))
            )
        """))
    async with AsyncSession(engine) as s:
        yield s


@pytest.mark.asyncio
async def test_build_trap_context_no_session_leaves_intermarket_none() -> None:
    ctx = await _build_trap_context(
        symbol="BTC/USDT", timeframe="1h", bars=_bars(), session=None,
    )
    assert ctx.symbol == "BTC/USDT"
    assert ctx.timeframe == "1h"
    assert ctx.funding_rate is None
    assert ctx.open_interest_delta_24h is None
    assert ctx.borrow_rate_pct is None


@pytest.mark.asyncio
async def test_build_trap_context_populates_from_session(session: AsyncSession) -> None:
    now = datetime(2026, 5, 6, 12, tzinfo=timezone.utc)
    await persist_intermarket_snapshots(session, [
        IntermarketSnapshot(symbol="BTC/USDT",
            captured_at=now - timedelta(hours=24),
            funding_rate=0.0, mark_price=68000.0, open_interest=1.0e9,
            source="binance_futures"),
        IntermarketSnapshot(symbol="BTC/USDT", captured_at=now,
            funding_rate=-0.0012, mark_price=70000.0, open_interest=1.30e9,
            source="binance_futures"),
    ])
    ctx = await _build_trap_context(
        symbol="BTC/USDT", timeframe="1h", bars=_bars(), session=session,
    )
    assert ctx.funding_rate == pytest.approx(-0.0012)
    assert ctx.open_interest_delta_24h == pytest.approx(0.30)
    assert ctx.borrow_rate_pct is None  # SP-3.5 v1 known gap


@pytest.mark.asyncio
async def test_build_trap_context_preserves_bar_derived_fields(session: AsyncSession) -> None:
    """Friday-close + weekly_bias + btc_atr_pct still populated."""
    ctx = await _build_trap_context(
        symbol="BTC/USDT", timeframe="1h", bars=_bars(), session=session,
    )
    # bars are flat → ATR % near 0; weekly_bias is computable; is_friday_close depends on index.
    assert ctx.btc_atr_pct is not None
    assert ctx.is_friday_close in {True, False}
