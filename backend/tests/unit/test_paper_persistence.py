import pytest
import sqlalchemy as sa
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.core.execution.paper_engine import PaperEngine
from app.core.execution.persistence import persist_trade, persist_prediction
from app.core.execution.types import Signal, ExitReason
from app.core.scoring.types import Direction


def make_signal() -> Signal:
    return Signal(
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
        direction=Direction.LONG,
        entry_price=100, stop_loss=95, take_profit=110,
        position_size=0.01, confidence=0.7, reasoning={"layer1": "long"},
    )


@pytest.mark.asyncio
async def test_persist_trade_writes_with_hash_chain() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE paper_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, direction TEXT, "
            "entry_price REAL, exit_price REAL, stop_loss REAL, take_profit REAL, "
            "position_size REAL, opened_at TEXT, closed_at TEXT, pnl_pct REAL, "
            "max_drawdown_during REAL, bars_held INTEGER, exit_reason TEXT, "
            "reasoning TEXT, model_version TEXT, prev_hash TEXT, row_hash TEXT UNIQUE)"
        ))

    pe = PaperEngine()
    pe.on_signal(make_signal())
    trade = pe.on_bar("BTC/USDT", datetime(2026,5,1,13, tzinfo=timezone.utc),
                      high=112, low=99, close=110)
    assert trade is not None

    async with AsyncSession(engine) as session:
        h = await persist_trade(session, trade)
        await session.commit()
        rows = (await session.execute(sa.text(
            "SELECT prev_hash, row_hash, exit_reason FROM paper_trades"
        ))).all()

    assert len(rows) == 1
    assert rows[0].prev_hash == "0" * 64
    assert rows[0].row_hash == h
    assert rows[0].exit_reason == "TAKE_PROFIT"


@pytest.mark.asyncio
async def test_persist_prediction_writes_with_hash_chain() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # SP-0.7 Phase E3: predictions has user_id NOT NULL.
        await conn.execute(sa.text(
            "CREATE TABLE predictions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "symbol TEXT, timeframe TEXT, "
            "ts TEXT, layer_scores TEXT, final_score REAL, direction TEXT, "
            "confidence REAL, inputs_hash TEXT, model_version TEXT, "
            "cold_start INTEGER, prev_hash TEXT, row_hash TEXT UNIQUE)"
        ))

    payload = dict(
        user_id=1,
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026,5,1, tzinfo=timezone.utc).isoformat(),
        layer_scores='{"1":"long"}',
        final_score=0.42, direction="LONG", confidence=0.7,
        inputs_hash="abc123", model_version="sp-0", cold_start=1,
    )

    async with AsyncSession(engine) as session:
        h = await persist_prediction(session, payload)
        await session.commit()
        row = (await session.execute(sa.text(
            "SELECT prev_hash, row_hash, user_id FROM predictions"
        ))).first()

    assert row.prev_hash == "0" * 64
    assert row.row_hash == h
    assert row.user_id == 1


@pytest.mark.asyncio
async def test_persist_prediction_rejects_missing_user_id() -> None:
    """Spec §7.3: persist_prediction is the per-user write path; user_id MUST be present."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "symbol TEXT, prev_hash TEXT, row_hash TEXT UNIQUE)"
        ))

    payload = dict(symbol="BTC/USDT")

    async with AsyncSession(engine) as session:
        with pytest.raises(ValueError, match="user_id"):
            await persist_prediction(session, payload)
