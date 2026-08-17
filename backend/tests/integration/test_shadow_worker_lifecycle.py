"""End-to-end multi-asset scenario for the shadow worker (Phase P1).

This complements ``test_shadow_worker.py`` (which covers the per-mechanism
unit-style entry / TP / SL / TIMEOUT / cooldown tests in isolation) by exercising
the full pipeline over a hand-scripted candle stream across multiple assets:

    reader -> _handle_candle -> [exit_monitor | predictor+gate+evaluator]
           -> persistence (hash-chained) -> in-memory cache update.

We assert:

* Multiple positions are opened and closed across BTCUSDT, ETHUSDT, SOLUSDT.
* ``shadow_trades`` row count matches the expected number of closed positions.
* PnL distribution is plausible (at least one win, at least one loss).
* All three exit reasons (TAKE_PROFIT, STOP_LOSS, TIMEOUT) are represented.
* The cooldown gate blocks an immediate re-entry on a symbol whose previous
  position just closed.
* The audit chain on ``shadow_trades`` verifies cleanly via
  :func:`app.db.audit_verify.verify_chain`.

Approach: monkeypatch ``app.shadow.worker.build_prediction`` with a stateful
stub that returns a high-score LONG prediction only at chosen ``(symbol, ts)``
trigger points. All other invocations return a NEUTRAL prediction so no
spurious entries occur. Candle prices are scripted so ATR (computed over the
seed history of ~250 bars at base price ~100 with +/-1% bands) stays around 4
USDT per bar — yielding SL ~= entry - 6 and TP ~= entry + 12 at an entry close
of 200.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from app.db.audit_verify import verify_chain
from app.shadow.exit_monitor import TIMEOUT_BARS
from app.shadow.multi_stream import MultiStreamCandle
from app.shadow.persistence import set_cooldown
from app.shadow.worker import COOLDOWN_MINUTES, ShadowWorker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture(autouse=True)
def _stub_flow_features_fire_and_forget(monkeypatch: pytest.MonkeyPatch) -> None:
    """_handle_candle fires app.core.features.flow_features as an un-awaited
    background task on every 1h candle (W4). It's irrelevant to what this
    file tests and, left real, touches the process-global Binance Futures
    RateLimitedClient singleton (app.data.adapters) across this test's own
    event loop — which then breaks unrelated tests (e.g.
    test_adapter_registry.py) that reuse the same singleton in a later,
    different event loop. Stub it out entirely; scenario assertions here
    never depend on flow-feature data.
    """
    monkeypatch.setattr(
        "app.core.features.flow_features.update_flow_cache_and_persist",
        AsyncMock(return_value=None),
    )

# ---------------------------------------------------------------------------
# Test fixtures (mirror test_shadow_worker.py — kept local to avoid coupling).
# ---------------------------------------------------------------------------


SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
T0 = datetime(2026, 5, 11, 0, tzinfo=UTC)


# Columns of shadow_trades that participate in the row hash (i.e. everything
# inserted via persist_closed_trade — see persistence.persist_closed_trade).
# SP-0.7 §13: user_id is part of the canonical payload so cross-user tampering
# surfaces during chain verification.
SHADOW_TRADE_HASH_COLS: list[str] = [
    "user_id",
    "symbol",
    "timeframe",
    "direction",
    "entry_price",
    "stop_loss",
    "take_profit",
    "position_size_usdt",
    "entry_score",
    "entry_confidence",
    "layer_scores",
    "entry_atr",
    "exit_price",
    "exit_reason",
    "pnl_pct",
    "pnl_usdt",
    "bars_held",
    "opened_at",
    "closed_at",
    "inputs_hash",
    "model_version",
    "signal_id",
]


def _seed_bars(n: int = 250, start_price: float = 100.0) -> pd.DataFrame:
    """Deterministic uptrend bar history (matches test_shadow_worker._seed_bars)."""
    closes = np.linspace(start_price, start_price * 2.0, n)
    idx = pd.date_range(start=T0 - timedelta(hours=n), periods=n, freq="1h")
    df = pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )
    df.index.name = "ts"
    return df


def _candle(
    *,
    symbol: str,
    bar_idx: int,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> MultiStreamCandle:
    return MultiStreamCandle(
        symbol=symbol,
        timeframe="1h",
        ts=T0 + timedelta(hours=bar_idx),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
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


def _scripted_predictor(triggers: set[tuple[str, datetime]]) -> Any:
    """Return a fake build_prediction that fires LONG only on listed triggers."""
    long_pred = _StubPred(final=_StubFinal(score=0.85, confidence=0.80, direction="LONG"))
    neutral_pred = _StubPred(final=_StubFinal(score=0.05, confidence=0.80, direction="NEUTRAL"))

    async def fake(  # noqa: ARG001
        *, symbol: str, timeframe: str, bars: pd.DataFrame, **_: Any,
    ) -> Any:
        # SP-9 Phase E2: build_prediction is async; fake must match.
        last_ts = bars.index[-1].to_pydatetime()
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=UTC)
        if (symbol, last_ts) in triggers:
            return long_pred
        return neutral_pred

    return fake


async def _create_shadow_tables(engine: Any) -> None:
    async with engine.begin() as conn:
        # SP-0.7 Phase E: per-user tables include user_id NOT NULL DEFAULT 1.
        await conn.execute(sa.text(
            "CREATE TABLE shadow_open_positions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 1, "
            "symbol TEXT NOT NULL, "
            # PR3 alembic 0021: timeframe + (symbol, timeframe) UNIQUE
            "timeframe TEXT NOT NULL DEFAULT '1h', "
            "direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL, stop_loss REAL NOT NULL, "
            "take_profit REAL NOT NULL, position_size_usdt REAL NOT NULL, "
            "entry_score REAL NOT NULL, entry_confidence REAL NOT NULL, "
            "entry_atr REAL NOT NULL, bars_held INTEGER NOT NULL DEFAULT 0, "
            "opened_at TEXT NOT NULL, last_check_at TEXT NOT NULL, "
            "signal_id TEXT NOT NULL UNIQUE, "
            # PR-PLUMBING-1 (alembic 0025): 7 PR1 analytics columns.
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
            "mtf_directions_json TEXT, p_win REAL, effective_score REAL, "
            "realized_vol_20d REAL, funding_directional_adj REAL, "
            "UNIQUE (symbol, timeframe))"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 1, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL, stop_loss REAL NOT NULL, "
            "take_profit REAL NOT NULL, position_size_usdt REAL NOT NULL, "
            "entry_score REAL NOT NULL, entry_confidence REAL NOT NULL, "
            "layer_scores TEXT NOT NULL, entry_atr REAL NOT NULL, "
            "exit_price REAL, exit_reason TEXT, pnl_pct REAL, pnl_usdt REAL, "
            "bars_held INTEGER, opened_at TEXT NOT NULL, closed_at TEXT, "
            "inputs_hash TEXT NOT NULL, model_version TEXT NOT NULL, "
            "signal_id TEXT NOT NULL UNIQUE, "
            "hold_scaling_factor REAL, hold_timeout_bars INTEGER, "  # PR3 G1
            # PR-strategy-1: PR1 analytics columns now populated.
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
            "mtf_directions_json TEXT, p_win REAL, effective_score REAL, "
            "realized_vol_20d REAL, funding_directional_adj REAL, "
            # Item 4 (alembic 0034): per-TF ADX map, same NULL-when-absent treatment.
            "mtf_adx_by_tf_json TEXT, "
            "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE shadow_cooldowns ("
            "user_id INTEGER NOT NULL DEFAULT 1, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL DEFAULT '1h', "  # PR3 alembic 0021
            "cooldown_until TEXT NOT NULL, "
            "PRIMARY KEY (user_id, symbol, timeframe))"
        ))


# ---------------------------------------------------------------------------
# Scenario builder
# ---------------------------------------------------------------------------


def _build_scenario() -> tuple[list[MultiStreamCandle], set[tuple[str, datetime]]]:
    """Hand-scripted multi-asset candle stream + entry-trigger set.

    Layout (entries forced via the scripted predictor; exits driven by the
    candle OHLC):

    * BTCUSDT: bar 5 entry (close=200) -> bar 8 TAKE_PROFIT (high pierces TP).
      Bar 10 forces another entry trigger but cooldown should block it.
    * ETHUSDT: bar 3 entry (close=200) -> bar 7 STOP_LOSS (low pierces SL).
    * SOLUSDT: bar 1 entry (close=200) -> drifts sideways for 24 bars then
      TIMEOUT exits at bar 25.
    """
    candles: list[MultiStreamCandle] = []
    triggers: set[tuple[str, datetime]] = set()

    flat = lambda sym, idx, c: _candle(  # noqa: E731
        symbol=sym, bar_idx=idx, open_=c, high=c * 1.001, low=c * 0.999, close=c,
    )

    # ---- SOLUSDT timeline ------------------------------------------------
    # Entry at bar 1 (close=200). Then 24 sideways bars (close=205, range
    # within SL/TP) so TIMEOUT fires on the 25th post-entry bar.
    candles.append(flat("SOLUSDT", 1, 200.0))
    triggers.add(("SOLUSDT", T0 + timedelta(hours=1)))
    for k in range(2, 27):  # bars 2..26  (25 candles after entry)
        # bars_held >= TIMEOUT_BARS (24) triggers TIMEOUT; we keep prices
        # neatly inside the SL/TP corridor (~194..212) so neither fires first.
        candles.append(flat("SOLUSDT", k, 205.0))

    # ---- ETHUSDT timeline ------------------------------------------------
    # Entry at bar 3 (close=200). Bars 4..6 stay inside the corridor.
    # Bar 7's low pierces SL (~194) — close at 192 (below SL).
    candles.append(flat("ETHUSDT", 3, 200.0))
    triggers.add(("ETHUSDT", T0 + timedelta(hours=3)))
    candles.append(flat("ETHUSDT", 4, 201.0))
    candles.append(flat("ETHUSDT", 5, 200.5))
    candles.append(flat("ETHUSDT", 6, 199.0))
    candles.append(_candle(
        symbol="ETHUSDT", bar_idx=7,
        open_=198.0, high=199.0, low=190.0, close=192.0,
    ))

    # ---- BTCUSDT timeline ------------------------------------------------
    # Entry at bar 5 (close=200). Bars 6..7 inside the corridor.
    # Bar 8 high pierces TP (~212) — close at 213.
    candles.append(flat("BTCUSDT", 5, 200.0))
    triggers.add(("BTCUSDT", T0 + timedelta(hours=5)))
    candles.append(flat("BTCUSDT", 6, 202.0))
    candles.append(flat("BTCUSDT", 7, 205.0))
    candles.append(_candle(
        symbol="BTCUSDT", bar_idx=8,
        open_=210.0, high=215.0, low=209.0, close=213.0,
    ))
    # Bar 10: trigger another entry — the cooldown set on close at bar 8 lasts
    # 30 minutes, so a candle 2h later is OUTSIDE the cooldown window. To prove
    # the cooldown gate works inside the worker pipeline we add a candle at
    # bar 8.5 (30 min after the close) that should be blocked even though
    # triggered.
    candles.append(_candle(
        symbol="BTCUSDT",
        bar_idx=0,  # placeholder; we override ts below
        open_=213.0, high=214.0, low=212.0, close=213.5,
    ))
    # Override ts to be 15 minutes after the bar-8 close (within 30-min cooldown).
    candles[-1] = MultiStreamCandle(
        symbol="BTCUSDT", timeframe="1h",
        ts=T0 + timedelta(hours=8, minutes=15),
        open=213.0, high=214.0, low=212.0, close=213.5, volume=1000.0,
    )
    triggers.add(("BTCUSDT", T0 + timedelta(hours=8, minutes=15)))

    # The reader presents candles in chronological order. Sort by ts so the
    # interleaving matches the live multi-stream behaviour.
    candles.sort(key=lambda c: c.ts)
    return candles, triggers


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_multi_asset_scenario(monkeypatch: pytest.MonkeyPatch) -> None:
    """One worker run, three symbols, three closed trades, cooldown blocks re-entry."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _create_shadow_tables(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    candles, triggers = _build_scenario()
    monkeypatch.setattr(
        "app.shadow.worker.build_prediction", _scripted_predictor(triggers)
    )

    seed = {sym: _seed_bars(n=250, start_price=100.0) for sym in SYMBOLS}
    worker = ShadowWorker(
        symbols=list(SYMBOLS),
        session_factory=factory,
        reader=_FakeReader(candles),
        seed_history=seed,
    )
    await worker.run()

    async with AsyncSession(engine) as session:
        opens = (await session.execute(
            sa.text("SELECT * FROM shadow_open_positions ORDER BY symbol")
        )).all()
        trades = (await session.execute(
            sa.text("SELECT * FROM shadow_trades ORDER BY id")
        )).all()
        cooldowns = (await session.execute(
            sa.text("SELECT * FROM shadow_cooldowns ORDER BY symbol")
        )).all()

    # 1. All three positions closed (no leftovers in shadow_open_positions).
    assert len(opens) == 0, f"unexpected open positions: {[o.symbol for o in opens]}"

    # 2. Exactly three closed trades — one per symbol.
    assert len(trades) == len(SYMBOLS), f"expected {len(SYMBOLS)} closed trades, got {len(trades)}"
    closed_symbols = {t.symbol for t in trades}
    assert closed_symbols == set(SYMBOLS), closed_symbols

    # 3. All three exit reasons represented.
    reasons_by_symbol = {t.symbol: t.exit_reason for t in trades}
    assert reasons_by_symbol["BTCUSDT"] == "TAKE_PROFIT"
    assert reasons_by_symbol["ETHUSDT"] == "STOP_LOSS"
    assert reasons_by_symbol["SOLUSDT"] == "TIMEOUT"

    # 4. PnL distribution: at least one win, at least one loss.
    pnls = [t.pnl_usdt for t in trades]
    assert any(p > 0 for p in pnls), f"no winners in {pnls}"
    assert any(p < 0 for p in pnls), f"no losers in {pnls}"

    # 5. Specific PnL signs.
    assert reasons_by_symbol["BTCUSDT"] == "TAKE_PROFIT"
    btc = next(t for t in trades if t.symbol == "BTCUSDT")
    assert btc.pnl_pct > 0 and btc.pnl_usdt > 0

    eth = next(t for t in trades if t.symbol == "ETHUSDT")
    assert eth.pnl_pct < 0 and eth.pnl_usdt < 0

    sol = next(t for t in trades if t.symbol == "SOLUSDT")
    assert sol.bars_held >= TIMEOUT_BARS

    # 6. Cooldowns set for every closed symbol.
    cool_symbols = {c.symbol for c in cooldowns}
    assert cool_symbols == set(SYMBOLS)
    for c in cooldowns:
        cd = datetime.fromisoformat(c.cooldown_until)
        # Cooldown is exactly COOLDOWN_MINUTES past the close candle's ts.
        # We don't pin the exact value because each symbol closes at a
        # different ts; a minimum sanity check is enough.
        assert cd > T0

    # 7. Audit chain on shadow_trades verifies cleanly.
    async with AsyncSession(engine) as session:
        result = await verify_chain(
            session, "shadow_trades", columns=SHADOW_TRADE_HASH_COLS,
        )
    assert result.ok, f"audit chain broken: {result.violations}"
    assert result.rows_checked == len(SYMBOLS)

    # 8. prev_hash linkage spot-check: row N's prev_hash == row N-1's row_hash;
    #    row 1's prev_hash == GENESIS_HASH (all zeros).
    from itertools import pairwise

    from app.db.audit import GENESIS_HASH
    assert trades[0].prev_hash == GENESIS_HASH
    for prev, curr in pairwise(trades):
        assert curr.prev_hash == prev.row_hash, (
            f"chain break between row {prev.id} -> {curr.id}"
        )
        assert curr.prev_hash  # non-null


@pytest.mark.asyncio
async def test_cooldown_blocks_reentry_in_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-seed an active cooldown then send a high-score candle on the same
    symbol. The full worker pipeline (gate -> predictor -> evaluator) must
    refuse to open a new position even though the predictor returns a strong
    LONG signal.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _create_shadow_tables(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    sym = "BTCUSDT"
    candle_ts = T0 + timedelta(hours=1)
    cooldown_until = candle_ts + timedelta(minutes=15)  # still active
    async with factory() as s:
        await set_cooldown(s, user_id=1, symbol=sym, until=cooldown_until)
        await s.commit()

    triggers = {(sym, candle_ts)}
    monkeypatch.setattr(
        "app.shadow.worker.build_prediction", _scripted_predictor(triggers),
    )

    candle = _candle(
        symbol=sym, bar_idx=1,
        open_=200.0, high=201.0, low=199.0, close=200.5,
    )
    seed = {sym: _seed_bars(n=250, start_price=100.0)}
    worker = ShadowWorker(
        symbols=[sym],
        session_factory=factory,
        reader=_FakeReader([candle]),
        seed_history=seed,
    )
    await worker.run()

    async with AsyncSession(engine) as session:
        opens = (await session.execute(
            sa.text("SELECT * FROM shadow_open_positions")
        )).all()
        trades = (await session.execute(
            sa.text("SELECT * FROM shadow_trades")
        )).all()

    assert len(opens) == 0
    assert len(trades) == 0


EXPECTED_COOLDOWN_MINUTES: int = 240
EXPECTED_TIMEOUT_BARS: int = 24


@pytest.mark.asyncio
async def test_cooldown_window_constants() -> None:
    """Pin the cooldown window so a regression in COOLDOWN_MINUTES surfaces."""
    assert COOLDOWN_MINUTES == EXPECTED_COOLDOWN_MINUTES
    assert TIMEOUT_BARS == EXPECTED_TIMEOUT_BARS
