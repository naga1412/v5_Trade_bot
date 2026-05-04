"""Integration tests for the shadow worker orchestrator.

The worker mirrors the live_prediction.py pattern:
- Seeds REST history per symbol
- Reads closed candles from a multi-stream
- Either checks exits on open positions OR evaluates new signals
- Persists before publishing

Tests use a fake reader and an in-memory SQLite session with the same DDL
the production migration creates.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from app.shadow.engine import Direction
from app.shadow.multi_stream import MultiStreamCandle
from app.shadow.persistence import persist_open_position
from app.shadow.worker import COOLDOWN_MINUTES, SHADOW_POSITION_SIZE_USDT, ShadowWorker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SYMBOL = "BTCUSDT"


def _seed_bars(n: int = 250, start_price: float = 100.0) -> pd.DataFrame:
    """Build a deterministic uptrend bar history."""
    closes = np.linspace(start_price, start_price * 2.0, n)
    idx = pd.date_range(
        start=datetime(2026, 5, 1, tzinfo=UTC),
        periods=n,
        freq="1h",
    )
    df = pd.DataFrame({
        "open": closes,
        "high": closes * 1.01,
        "low": closes * 0.99,
        "close": closes,
        "volume": np.full(n, 1000.0),
    }, index=idx)
    df.index.name = "ts"
    return df


def _make_candle(
    *, symbol: str = SYMBOL, ts: datetime, open_: float, high: float,
    low: float, close: float, volume: float = 1000.0,
) -> MultiStreamCandle:
    return MultiStreamCandle(
        symbol=symbol, timeframe="1h", ts=ts,
        open=open_, high=high, low=low, close=close, volume=volume,
    )


class _FakeReader:
    """Stand-in for MultiStreamReader.stream()."""

    def __init__(self, candles: list[MultiStreamCandle]) -> None:
        self._candles = candles

    async def stream(self) -> AsyncIterator[MultiStreamCandle]:
        for c in self._candles:
            yield c


@dataclass
class _StubFinal:
    score: float
    confidence: float
    direction: str = "LONG"
    contributing_layers: tuple[int, ...] = (1, 3, 5)


@dataclass
class _StubPred:
    final: _StubFinal
    layer_scores: dict[str, Any] = field(default_factory=dict)


def _force_eval(monkeypatch: pytest.MonkeyPatch, *, score: float,
                confidence: float = 0.80, direction: str = "LONG") -> None:
    """Patch build_prediction inside the worker module with a fixed-score stub."""
    pred = _StubPred(final=_StubFinal(score=score, confidence=confidence,
                                       direction=direction))

    def fake_build(*, symbol: str, timeframe: str, bars: pd.DataFrame) -> Any:  # noqa: ARG001
        return pred

    monkeypatch.setattr("app.shadow.worker.build_prediction", fake_build)


def _force_long_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_eval(monkeypatch, score=0.85, confidence=0.80, direction="LONG")


def _force_neutral_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_eval(monkeypatch, score=0.05, confidence=0.80, direction="NEUTRAL")


def _build_in_memory_engine() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    return engine


async def _create_shadow_tables(engine: Any) -> None:
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
        await conn.execute(sa.text(
            "CREATE TABLE shadow_cooldowns ("
            "symbol TEXT PRIMARY KEY, cooldown_until TEXT NOT NULL)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE asset_universe ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "quote_volume_usd_24h REAL NOT NULL, "
            "rank INTEGER NOT NULL, "
            "snapshot_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "UNIQUE (symbol, snapshot_at))"
        ))


@pytest.mark.asyncio
async def test_long_entry_creates_open_position(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _build_in_memory_engine()
    await _create_shadow_tables(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    _force_long_eval(monkeypatch)

    seed = _seed_bars(n=250, start_price=100.0)
    candle = _make_candle(
        ts=datetime(2026, 5, 11, 11, tzinfo=UTC),
        open_=200.0, high=201.0, low=199.0, close=200.5,
    )
    reader = _FakeReader([candle])

    worker = ShadowWorker(
        symbols=[SYMBOL],
        session_factory=factory,
        reader=reader,
        seed_history={SYMBOL: seed},
    )
    await worker.run()

    async with AsyncSession(engine) as session:
        rows = (await session.execute(
            sa.text("SELECT * FROM shadow_open_positions")
        )).all()

    assert len(rows) == 1
    r = rows[0]
    assert r.symbol == SYMBOL
    assert r.direction == Direction.LONG.value
    assert r.entry_price == pytest.approx(200.5)
    assert r.position_size_usdt == SHADOW_POSITION_SIZE_USDT
    assert r.bars_held == 0
    assert r.signal_id  # populated


@pytest.mark.asyncio
async def test_neutral_score_does_not_open(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _build_in_memory_engine()
    await _create_shadow_tables(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    _force_neutral_eval(monkeypatch)

    seed = _seed_bars(n=250, start_price=100.0)
    candle = _make_candle(
        ts=datetime(2026, 5, 11, 11, tzinfo=UTC),
        open_=200.0, high=201.0, low=199.0, close=200.5,
    )
    reader = _FakeReader([candle])

    worker = ShadowWorker(
        symbols=[SYMBOL],
        session_factory=factory,
        reader=reader,
        seed_history={SYMBOL: seed},
    )
    await worker.run()

    async with AsyncSession(engine) as session:
        rows = (await session.execute(
            sa.text("SELECT * FROM shadow_open_positions")
        )).all()

    assert len(rows) == 0


@pytest.mark.asyncio
async def test_take_profit_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _build_in_memory_engine()
    await _create_shadow_tables(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Pre-seed an open LONG position with TP at 210.
    from app.shadow.engine import ShadowPosition

    opened_at = datetime(2026, 5, 11, 11, tzinfo=UTC)
    pos = ShadowPosition(
        symbol=SYMBOL, direction=Direction.LONG,
        entry_price=200.0, stop_loss=195.0, take_profit=210.0,
        position_size_usdt=30.0, entry_score=0.7, entry_confidence=0.8,
        entry_atr=2.0, layer_scores={}, bars_held=0,
        opened_at=opened_at, last_check_at=opened_at,
        signal_id="seedsig0",
    )
    async with factory() as s:
        await persist_open_position(s, pos)
        await s.commit()

    # Make build_prediction never fire a new signal (won't be reached anyway since
    # the symbol now has an open position, but be defensive).
    _force_neutral_eval(monkeypatch)

    # Candle whose high crosses the TP.
    candle = _make_candle(
        ts=opened_at + timedelta(hours=1),
        open_=205.0, high=212.0, low=204.0, close=211.0,
    )
    seed = _seed_bars(n=250, start_price=100.0)
    reader = _FakeReader([candle])

    worker = ShadowWorker(
        symbols=[SYMBOL],
        session_factory=factory,
        reader=reader,
        seed_history={SYMBOL: seed},
    )
    await worker.run()

    async with AsyncSession(engine) as session:
        opens = (await session.execute(
            sa.text("SELECT * FROM shadow_open_positions")
        )).all()
        trades = (await session.execute(
            sa.text("SELECT * FROM shadow_trades")
        )).all()
        cools = (await session.execute(
            sa.text("SELECT * FROM shadow_cooldowns")
        )).all()

    assert len(opens) == 0  # closed
    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason == "TAKE_PROFIT"
    assert t.exit_price == pytest.approx(210.0)
    assert t.pnl_pct > 0
    assert t.pnl_usdt > 0
    assert t.signal_id == "seedsig0"
    assert t.bars_held == 1
    # Cooldown set to candle.ts + 30min.
    assert len(cools) == 1
    assert cools[0].symbol == SYMBOL
    expected_cd = (candle.ts + timedelta(minutes=COOLDOWN_MINUTES)).isoformat()
    assert cools[0].cooldown_until == expected_cd


@pytest.mark.asyncio
async def test_stop_loss_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _build_in_memory_engine()
    await _create_shadow_tables(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    from app.shadow.engine import ShadowPosition

    opened_at = datetime(2026, 5, 11, 11, tzinfo=UTC)
    pos = ShadowPosition(
        symbol=SYMBOL, direction=Direction.LONG,
        entry_price=200.0, stop_loss=195.0, take_profit=210.0,
        position_size_usdt=30.0, entry_score=0.7, entry_confidence=0.8,
        entry_atr=2.0, layer_scores={}, bars_held=0,
        opened_at=opened_at, last_check_at=opened_at,
        signal_id="seedsig1",
    )
    async with factory() as s:
        await persist_open_position(s, pos)
        await s.commit()

    _force_neutral_eval(monkeypatch)

    # Candle whose low pierces the stop.
    candle = _make_candle(
        ts=opened_at + timedelta(hours=1),
        open_=199.0, high=200.0, low=190.0, close=193.0,
    )
    seed = _seed_bars(n=250, start_price=100.0)
    reader = _FakeReader([candle])

    worker = ShadowWorker(
        symbols=[SYMBOL],
        session_factory=factory,
        reader=reader,
        seed_history={SYMBOL: seed},
    )
    await worker.run()

    async with AsyncSession(engine) as session:
        trades = (await session.execute(
            sa.text("SELECT * FROM shadow_trades")
        )).all()

    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason == "STOP_LOSS"
    assert t.exit_price == pytest.approx(195.0)
    assert t.pnl_pct < 0
    assert t.pnl_usdt < 0


@pytest.mark.asyncio
async def test_timeout_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _build_in_memory_engine()
    await _create_shadow_tables(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    from app.shadow.engine import ShadowPosition
    from app.shadow.exit_monitor import TIMEOUT_BARS

    opened_at = datetime(2026, 5, 11, 11, tzinfo=UTC)
    # Position already at TIMEOUT_BARS - 1 -> after ++ it triggers timeout.
    pos = ShadowPosition(
        symbol=SYMBOL, direction=Direction.LONG,
        entry_price=200.0, stop_loss=190.0, take_profit=220.0,
        position_size_usdt=30.0, entry_score=0.7, entry_confidence=0.8,
        entry_atr=2.0, layer_scores={}, bars_held=TIMEOUT_BARS - 1,
        opened_at=opened_at, last_check_at=opened_at,
        signal_id="seedsig2",
    )
    async with factory() as s:
        await persist_open_position(s, pos)
        await s.commit()

    _force_neutral_eval(monkeypatch)

    # Candle that does NOT hit SL or TP — only timeout fires.
    candle = _make_candle(
        ts=opened_at + timedelta(hours=24),
        open_=205.0, high=206.0, low=204.0, close=205.5,
    )
    seed = _seed_bars(n=250, start_price=100.0)
    reader = _FakeReader([candle])

    worker = ShadowWorker(
        symbols=[SYMBOL],
        session_factory=factory,
        reader=reader,
        seed_history={SYMBOL: seed},
    )
    await worker.run()

    async with AsyncSession(engine) as session:
        trades = (await session.execute(
            sa.text("SELECT * FROM shadow_trades")
        )).all()

    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason == "TIMEOUT"
    assert t.exit_price == pytest.approx(205.5)
    assert t.bars_held == TIMEOUT_BARS


@pytest.mark.asyncio
async def test_cooldown_blocks_new_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _build_in_memory_engine()
    await _create_shadow_tables(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    from app.shadow.persistence import set_cooldown

    # Place an active cooldown on BTCUSDT.
    cooldown_until = datetime(2026, 5, 11, 12, tzinfo=UTC)
    async with factory() as s:
        await set_cooldown(s, SYMBOL, cooldown_until)
        await s.commit()

    _force_long_eval(monkeypatch)

    seed = _seed_bars(n=250, start_price=100.0)
    candle = _make_candle(
        # Before cooldown expires.
        ts=datetime(2026, 5, 11, 11, 30, tzinfo=UTC),
        open_=200.0, high=201.0, low=199.0, close=200.5,
    )
    reader = _FakeReader([candle])

    worker = ShadowWorker(
        symbols=[SYMBOL],
        session_factory=factory,
        reader=reader,
        seed_history={SYMBOL: seed},
    )
    await worker.run()

    async with AsyncSession(engine) as session:
        opens = (await session.execute(
            sa.text("SELECT * FROM shadow_open_positions")
        )).all()
    assert len(opens) == 0
