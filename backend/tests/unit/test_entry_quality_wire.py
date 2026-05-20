"""PR-strategy-1 wire-tests: verify the gate is invoked at both call sites.

Test 7 covers the shadow_worker open path: a deny decision short-circuits
before `persist_open_position` is reached.

Test 8 covers the dispatcher pre-conditions: a deny decision returns
`DispatchResult(outcome="blocked_entry_quality")` before any side effect
(no telegram_signals row, no live_trades row, no Binance call).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.gates.entry_quality import AllowDecision
from app.shadow.multi_stream import MultiStreamCandle
from app.shadow.worker import ShadowWorker


SYMBOL = "BTCUSDT"


def _seed_bars(n: int = 250) -> pd.DataFrame:
    closes = np.linspace(100.0, 200.0, n)
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


def _make_candle(ts: datetime) -> MultiStreamCandle:
    return MultiStreamCandle(
        symbol=SYMBOL, timeframe="1h", ts=ts,
        open=200.0, high=201.0, low=199.0, close=200.5, volume=1000.0,
    )


class _FakeReader:
    def __init__(self, candles: list[MultiStreamCandle]) -> None:
        self._candles = candles

    async def stream(self) -> AsyncIterator[MultiStreamCandle]:
        for c in self._candles:
            yield c


@dataclass
class _StubFinal:
    score: float = 0.85
    confidence: float = 0.80
    direction: str = "LONG"
    contributing_layers: tuple[int, ...] = (1, 3, 5)


@dataclass
class _StubPred:
    final: _StubFinal = field(default_factory=_StubFinal)
    layer_scores: dict[str, Any] = field(default_factory=dict)
    mtf_agreement: int | None = None
    mtf_dominant_tf: str | None = None
    mtf_directions_json: str | None = None
    p_win: float | None = None
    effective_score: float | None = None
    realized_vol_20d: float | None = None
    funding_directional_adj: float | None = None


async def _mk_shadow_engine() -> Any:
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
async def test_shadow_open_path_calls_gate_and_short_circuits_on_deny(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test 7: shadow worker open path invokes the gate; on deny, no
    persist_open_position call fires + no DB row is written."""
    engine = await _mk_shadow_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Stub build_prediction to drive a LONG signal.
    pred = _StubPred(final=_StubFinal(score=0.85, confidence=0.80, direction="LONG"))

    async def fake_build(*, symbol: str, timeframe: str, bars: pd.DataFrame, **_: Any) -> Any:  # noqa: ARG001
        return pred

    monkeypatch.setattr("app.shadow.worker.build_prediction", fake_build)

    # Patch the gate to deny.
    deny = AllowDecision(allow=False, reason="below_long_threshold")
    persist_spy = AsyncMock(return_value=None)
    with patch("app.shadow.worker.open_position_gate", return_value=deny), \
         patch("app.shadow.worker.persist_open_position", new=persist_spy):
        seed = _seed_bars()
        candle = _make_candle(datetime(2026, 5, 20, 11, tzinfo=UTC))
        reader = _FakeReader([candle])
        worker = ShadowWorker(
            symbols=[SYMBOL], session_factory=factory,
            reader=reader, seed_history={SYMBOL: seed},
        )
        await worker.run()

    persist_spy.assert_not_called()
    async with AsyncSession(engine) as session:
        rows = (await session.execute(
            sa.text("SELECT * FROM shadow_open_positions")
        )).all()
    assert len(rows) == 0


# ---- Test 8: dispatcher pre-condition --------------------------------------


_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


async def _mk_dispatcher_engine() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE users ("
            "id INTEGER PRIMARY KEY, "
            "trading_mode TEXT NOT NULL DEFAULT 'fully-auto')"
        ))
        await conn.execute(sa.text(
            "INSERT INTO users (id, trading_mode) VALUES (1, 'fully-auto')"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE telegram_signals ("
            "id TEXT PRIMARY KEY, user_id INTEGER, symbol TEXT, "
            "direction TEXT, sent_at TEXT, payload TEXT, "
            "response TEXT, response_at TEXT, response_leverage INTEGER)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE live_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, symbol TEXT, direction TEXT, "
            "margin_usdt REAL, leverage INTEGER, position_value_usdt REAL, "
            "entry_price REAL, stop_loss REAL, take_profit REAL, "
            "binance_order_id TEXT, opened_at TEXT, mode_at_open TEXT, "
            "approved_via TEXT, reasoning TEXT, inputs_hash TEXT, "
            "closed_at TEXT, pnl_usdt REAL, "
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
            "mtf_directions_json TEXT, "
            "prev_hash TEXT, row_hash TEXT)"
        ))
    return engine


def _user_ctx() -> Any:
    from app.trading.execution.dispatcher import UserContext
    return UserContext(
        user_id=1, mode="fully-auto",
        binance_api_key="k", binance_api_secret="s",
        use_testnet=True, portfolio_value_usdt=1000,
        successful_trades=0, sizing_mode="fixed",
        fixed_size_usdt=30.0, max_leverage_cap=10,
        max_concurrent_positions=5, open_positions_count=0,
    )


def _proposal(*, entry_score: float | None = 0.35, direction: str = "LONG") -> Any:
    from app.trading.execution.dispatcher import SignalProposal
    return SignalProposal(
        symbol="BTC/USDT", timeframe="1h",
        direction=direction,  # type: ignore[arg-type]
        entry_price=80_000.0, stop_loss_price=78_400.0,
        take_profit_price=83_280.0,
        confidence_pct=72, layer_summary={"L1": {"score": 0.85}},
        inputs_hash="abc", funding_rate_daily=-0.0002,
        chart_base_url="",
        entry_score=entry_score,
    )


@pytest.mark.asyncio
async def test_dispatcher_precondition_calls_gate_and_short_circuits_on_deny(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test 8: dispatcher gate returns blocked_entry_quality + writes no rows."""
    # Configure MIN_ENTRY_SCORE_LONG=0.50, propose score=0.35 → deny.
    monkeypatch.setenv("MIN_ENTRY_SCORE_LONG", "0.50")
    monkeypatch.setenv("DISABLE_SHORT_SIGNALS", "False")
    from app.config import get_settings
    get_settings.cache_clear()

    try:
        from app.trading.execution.dispatcher import dispatch

        engine = await _mk_dispatcher_engine()

        async with AsyncSession(engine) as s:
            result = await dispatch(
                s, proposal=_proposal(entry_score=0.35),
                user=_user_ctx(), now=_NOW,
            )
            await s.commit()

        assert result.outcome == "blocked_entry_quality"
        assert "below_long_threshold" in result.detail

        async with AsyncSession(engine) as s:
            n_live = (await s.execute(
                sa.text("SELECT count(*) FROM live_trades")
            )).scalar()
            n_tg = (await s.execute(
                sa.text("SELECT count(*) FROM telegram_signals")
            )).scalar()
        assert n_live == 0
        assert n_tg == 0
    finally:
        get_settings.cache_clear()
