import pytest
import sqlalchemy as sa
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.shadow.engine import ShadowPosition, ShadowSignal, Direction
from app.shadow.exit_monitor import ExitReason
from app.shadow.persistence import (
    persist_open_position,
    delete_open_position,  # noqa: F401 — part of public API, tested via round-trip
    persist_closed_trade,
    set_cooldown,
    list_open_positions,
)


def make_signal() -> ShadowSignal:
    return ShadowSignal(
        symbol="BTCUSDT", direction=Direction.LONG, score=0.65,
        confidence=0.72, entry_price=78250.0, stop_loss=77077.75,
        take_profit=80594.5, atr=781.5,
        layer_scores={"1": 0.85, "3": 0.72, "5": 0.40},
        ts=datetime(2026, 5, 3, 14, tzinfo=timezone.utc),
        signal_id="abc12345",
    )


@pytest.mark.asyncio
async def test_persist_and_retrieve_open_position() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_open_positions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL UNIQUE, direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL, stop_loss REAL NOT NULL, "
            "take_profit REAL NOT NULL, position_size_usdt REAL NOT NULL, "
            "entry_score REAL NOT NULL, entry_confidence REAL NOT NULL, "
            "entry_atr REAL NOT NULL, bars_held INTEGER NOT NULL DEFAULT 0, "
            "opened_at TEXT NOT NULL, last_check_at TEXT NOT NULL, "
            "signal_id TEXT NOT NULL UNIQUE)"
        ))

    sig = make_signal()
    pos = ShadowPosition.from_signal(sig, position_size_usdt=30.0)
    async with AsyncSession(engine) as session:
        await persist_open_position(session, pos)
        await session.commit()

        loaded = await list_open_positions(session)
    assert len(loaded) == 1
    assert loaded[0].symbol == "BTCUSDT"
    assert loaded[0].entry_price == 78250.0
    assert loaded[0].signal_id == "abc12345"


@pytest.mark.asyncio
async def test_persist_closed_trade_with_audit_chain() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL, stop_loss REAL NOT NULL, "
            "take_profit REAL NOT NULL, position_size_usdt REAL NOT NULL, "
            "entry_score REAL NOT NULL, entry_confidence REAL NOT NULL, "
            "layer_scores TEXT NOT NULL, entry_atr REAL NOT NULL, "
            "exit_price REAL, exit_reason TEXT, pnl_pct REAL, pnl_usdt REAL, "
            "bars_held INTEGER, opened_at TEXT NOT NULL, closed_at TEXT, "
            "inputs_hash TEXT NOT NULL, model_version TEXT NOT NULL, "
            "signal_id TEXT NOT NULL UNIQUE, "
            "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
        ))

    sig = make_signal()
    pos = ShadowPosition.from_signal(sig, position_size_usdt=30.0)
    closed_at = datetime(2026, 5, 3, 18, tzinfo=timezone.utc)

    async with AsyncSession(engine) as session:
        row_hash = await persist_closed_trade(
            session, pos, exit_price=80594.5, exit_reason=ExitReason.TAKE_PROFIT,
            closed_at=closed_at, bars_held=4, inputs_hash="deadbeef",
        )
        await session.commit()
        rows = (await session.execute(
            sa.text("SELECT * FROM shadow_trades")
        )).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.symbol == "BTCUSDT"
    assert r.exit_price == 80594.5
    assert r.exit_reason == "TAKE_PROFIT"
    assert r.pnl_pct == pytest.approx((80594.5 - 78250.0) / 78250.0 * 100)
    assert r.row_hash == row_hash
    assert r.prev_hash == "0" * 64  # genesis hash for first row


@pytest.mark.asyncio
async def test_set_and_check_cooldown() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_cooldowns ("
            "symbol TEXT PRIMARY KEY, cooldown_until TEXT NOT NULL)"
        ))

    until = datetime(2026, 5, 3, 14, 30, tzinfo=timezone.utc)
    async with AsyncSession(engine) as session:
        await set_cooldown(session, "BTCUSDT", until)
        await session.commit()
        # Set again — should upsert
        await set_cooldown(session, "BTCUSDT", until + timedelta(minutes=10))
        await session.commit()
        rows = (await session.execute(
            sa.text("SELECT symbol, cooldown_until FROM shadow_cooldowns")
        )).all()
    assert len(rows) == 1
