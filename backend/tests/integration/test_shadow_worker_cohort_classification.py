"""Item 0 (2026-08-30): symbol_source classified synchronously at
position-open time, with NULL-on-failure per operator ruling.

Three things this file must prove, per the operator's explicit
instruction ("Unit-test this explicitly"):
  1. The happy path: a warm cache produces a real, correct cohort.
  2. Cache unavailable (never warmed / cleared) at open time -> the
     position's symbol_source lands as NULL in the DB, and an
     alert_admin call fires. NEVER a guessed/default cohort.
  3. The classifier itself raising -> the same NULL + alert contract.

Mirrors test_shadow_worker.py's fixture shape (fake reader, in-memory
SQLite, forced LONG evaluation) but with symbol_source TEXT (nullable,
no DEFAULT) in the DDL -- matching migration 0042's real schema,
unlike test_shadow_worker.py's DDL which predates it and is
deliberately left NOT NULL DEFAULT there (its tests never exercise the
failure path, so the stricter DDL is still valid coverage for the
happy-path contract).
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
from app.shadow import cohort_cache
from app.shadow.multi_stream import MultiStreamCandle
from app.shadow.worker import ShadowWorker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SYMBOL = "BTCUSDT"


def _seed_bars(n: int = 250, start_price: float = 100.0) -> pd.DataFrame:
    closes = np.linspace(start_price, start_price * 2.0, n)
    idx = pd.date_range(start=datetime(2026, 5, 1, tzinfo=UTC), periods=n, freq="1h")
    df = pd.DataFrame({
        "open": closes, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": np.full(n, 1000.0),
    }, index=idx)
    df.index.name = "ts"
    return df


def _make_candle(*, ts: datetime, close: float) -> MultiStreamCandle:
    return MultiStreamCandle(
        symbol=SYMBOL, timeframe="1h", ts=ts,
        open=close - 0.5, high=close + 0.5, low=close - 1.0,
        close=close, volume=1000.0,
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


def _force_long_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    pred = _StubPred(final=_StubFinal(score=0.85, confidence=0.80, direction="LONG"))

    async def fake_build(*, symbol: str, timeframe: str, bars: pd.DataFrame, **_: Any) -> Any:  # noqa: ARG001
        return pred

    monkeypatch.setattr("app.shadow.worker.build_prediction", fake_build)


async def _create_shadow_tables(engine: Any) -> None:
    """Same shape as test_shadow_worker.py's fixture, EXCEPT
    symbol_source is nullable with no DEFAULT -- migration 0042's real
    post-item-0 schema, needed here because these tests deliberately
    exercise the NULL-write path."""
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
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
            "mtf_directions_json TEXT, p_win REAL, effective_score REAL, "
            "realized_vol_20d REAL, funding_directional_adj REAL, "
            "layer_scores TEXT, mtf_adx_by_tf_json TEXT, "
            "symbol_source TEXT, "  # migration 0042: nullable, no DEFAULT
            "hold_scaling_factor REAL, hold_timeout_bars INTEGER, "
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
        await conn.execute(sa.text(
            "CREATE TABLE asset_universe ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "quote_volume_usd_24h REAL NOT NULL, "
            "rank INTEGER NOT NULL, "
            "snapshot_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "UNIQUE (symbol, snapshot_at))"
        ))


async def _open_one_position(
    monkeypatch: pytest.MonkeyPatch, engine: Any,
) -> Any:
    """Shared drive: force a LONG signal, run one candle through a
    fresh worker, return the resulting shadow_open_positions row."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    _force_long_eval(monkeypatch)
    seed = _seed_bars(n=250, start_price=100.0)
    candle = _make_candle(ts=datetime(2026, 5, 11, 11, tzinfo=UTC), close=200.5)
    worker = ShadowWorker(
        symbols=[SYMBOL], session_factory=factory,
        reader=_FakeReader([candle]), seed_history={SYMBOL: seed},
    )
    await worker.run()
    async with AsyncSession(engine) as session:
        rows = (await session.execute(
            sa.text("SELECT * FROM shadow_open_positions")
        )).all()
    assert len(rows) == 1
    return rows[0]


@pytest.mark.asyncio
async def test_position_open_classifies_real_cohort(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: warm caches -> a real, correct cohort lands on the row."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _create_shadow_tables(engine)

    monkeypatch.setattr(cohort_cache, "_baseline_cache", {"BTCUSDT", "ETHUSDT"})
    monkeypatch.setattr(cohort_cache, "_futures_only_cache", set())

    row = await _open_one_position(monkeypatch, engine)
    assert row.symbol_source == "established_top20"


@pytest.mark.asyncio
async def test_position_open_writes_null_when_cache_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NO DEFAULT ON FAILURE (operator ruling, 2026-08-30): caches never
    warmed -> symbol_source is NULL, never a guessed cohort, and
    alert_admin fires exactly once."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _create_shadow_tables(engine)

    # Simulate "never warmed" -- both caches back to their unwarmed None
    # state (the conftest autouse fixture pre-seeds empty sets; undo
    # that here deliberately).
    monkeypatch.setattr(cohort_cache, "_baseline_cache", None)
    monkeypatch.setattr(cohort_cache, "_futures_only_cache", None)

    alert_calls: list[tuple[str, str]] = []

    async def fake_alert_admin(message: str, *, level: str = "warning") -> None:
        alert_calls.append((message, level))

    monkeypatch.setattr("app.shadow.worker.alert_admin", fake_alert_admin)

    row = await _open_one_position(monkeypatch, engine)
    assert row.symbol_source is None
    assert len(alert_calls) == 1
    message, level = alert_calls[0]
    assert level == "critical"
    assert SYMBOL in message


@pytest.mark.asyncio
async def test_position_open_writes_null_when_classifier_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same NULL + alert contract when _classify_cohort itself raises,
    not just when the caches are unwarmed -- 'unexpected state' from
    the operator's ruling, covered separately from the cache-empty
    case above."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _create_shadow_tables(engine)

    monkeypatch.setattr(cohort_cache, "_baseline_cache", {"BTCUSDT"})
    monkeypatch.setattr(cohort_cache, "_futures_only_cache", set())

    def raising_classify(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("synthetic classification failure")

    monkeypatch.setattr("app.shadow.worker._classify_cohort", raising_classify)

    alert_calls: list[tuple[str, str]] = []

    async def fake_alert_admin(message: str, *, level: str = "warning") -> None:
        alert_calls.append((message, level))

    monkeypatch.setattr("app.shadow.worker.alert_admin", fake_alert_admin)

    row = await _open_one_position(monkeypatch, engine)
    assert row.symbol_source is None
    assert len(alert_calls) == 1
    message, level = alert_calls[0]
    assert level == "critical"
    assert SYMBOL in message
