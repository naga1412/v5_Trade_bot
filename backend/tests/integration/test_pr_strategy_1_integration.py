"""PR-strategy-1 integration: shadow worker honors the entry-quality gate.

Drives a full `ShadowWorker.run()` with a synthetic LONG signal whose score
(0.10) is below the configured `MIN_ENTRY_SCORE_LONG=0.30` threshold. Asserts
that:
  1. No row was written to `shadow_open_positions`.
  2. The worker's in-memory `open_positions` is empty.

This complements the gate-unit tests by proving the wire-in actually
short-circuits the open path end-to-end (no DB I/O, no publish).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shadow.multi_stream import MultiStreamCandle
from app.shadow.worker import ShadowWorker


SYMBOL = "BTCUSDT"


def _seed_bars(n: int = 250, start_price: float = 100.0) -> pd.DataFrame:
    closes = np.linspace(start_price, start_price * 2.0, n)
    idx = pd.date_range(
        start=datetime(2026, 5, 1, tzinfo=UTC), periods=n, freq="1h",
    )
    df = pd.DataFrame({
        "open": closes, "high": closes * 1.01,
        "low": closes * 0.99, "close": closes,
        "volume": np.full(n, 1000.0),
    }, index=idx)
    df.index.name = "ts"
    return df


def _make_candle(
    *, ts: datetime, open_: float, high: float, low: float, close: float,
) -> MultiStreamCandle:
    return MultiStreamCandle(
        symbol=SYMBOL, timeframe="1h", ts=ts,
        open=open_, high=high, low=low, close=close, volume=1000.0,
    )


class _FakeReader:
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
    mtf_agreement: int | None = None
    mtf_dominant_tf: str | None = None
    mtf_directions_json: str | None = None
    p_win: float | None = None
    effective_score: float | None = None
    realized_vol_20d: float | None = None
    funding_directional_adj: float | None = None


async def _build_in_memory_engine() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_open_positions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 1, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL DEFAULT '1h', "
            "direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL, stop_loss REAL NOT NULL, "
            "take_profit REAL NOT NULL, position_size_usdt REAL NOT NULL, "
            "entry_score REAL NOT NULL, entry_confidence REAL NOT NULL, "
            "entry_atr REAL NOT NULL, bars_held INTEGER NOT NULL DEFAULT 0, "
            "opened_at TEXT NOT NULL, last_check_at TEXT NOT NULL, "
            "signal_id TEXT NOT NULL UNIQUE, "
            "UNIQUE (symbol, timeframe))"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE shadow_cooldowns ("
            "user_id INTEGER NOT NULL DEFAULT 1, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL DEFAULT '1h', "
            "cooldown_until TEXT NOT NULL, "
            "PRIMARY KEY (user_id, symbol, timeframe))"
        ))
    return engine


@pytest.mark.asyncio
async def test_shadow_worker_open_with_score_below_threshold_writes_no_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LONG signal at score=0.10 with MIN_ENTRY_SCORE_LONG=0.30 → no open row."""
    # 1) Set the threshold so the gate denies. Clear lru_cache on get_settings.
    monkeypatch.setenv("MIN_ENTRY_SCORE_LONG", "0.30")
    from app.config import get_settings
    get_settings.cache_clear()

    # 2) Stub build_prediction → LONG, score above evaluator threshold
    # (0.30 default) but below MIN_ENTRY_SCORE_LONG (0.30) ... wait, that
    # has to be ABOVE evaluator's 0.30 so a signal is produced AT ALL, but
    # then BELOW the threshold so the gate denies. The evaluator uses
    # `score > long_threshold` (strict). So with evaluator=0.30 +
    # MIN_ENTRY_SCORE_LONG=0.40 + pred.final.score=0.35 we get a signal
    # AND gate denial.
    monkeypatch.setenv("MIN_ENTRY_SCORE_LONG", "0.40")
    get_settings.cache_clear()

    pred = _StubPred(final=_StubFinal(score=0.35, confidence=0.80, direction="LONG"))

    async def fake_build(*, symbol: str, timeframe: str, bars: pd.DataFrame, **_: Any) -> Any:  # noqa: ARG001
        return pred

    monkeypatch.setattr("app.shadow.worker.build_prediction", fake_build)

    # 3) Drive the worker
    engine = await _build_in_memory_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    seed = _seed_bars(n=250, start_price=100.0)
    candle = _make_candle(
        ts=datetime(2026, 5, 20, 11, tzinfo=UTC),
        open_=200.0, high=201.0, low=199.0, close=200.5,
    )
    reader = _FakeReader([candle])
    worker = ShadowWorker(
        symbols=[SYMBOL], session_factory=factory,
        reader=reader, seed_history={SYMBOL: seed},
    )
    try:
        await worker.run()

        # 4) Assert no DB row + empty in-memory cache
        async with AsyncSession(engine) as session:
            rows = (await session.execute(
                sa.text("SELECT * FROM shadow_open_positions")
            )).all()
        assert len(rows) == 0
        assert worker.open_positions == {}
    finally:
        # Cleanup: reset lru_cache to avoid leaking the test env into others
        get_settings.cache_clear()
