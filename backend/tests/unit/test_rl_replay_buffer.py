"""Tests for app.rl.replay_buffer.load_from_shadow_trades (SP-4 Phase A4)."""
from __future__ import annotations

import json

import numpy as np
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.rl.obs import OBS_DIM
from app.rl.replay_buffer import (
    ACTION_LONG_FULL,
    ACTION_SHORT_FULL,
    ALL_ACTIONS,
    Transition,
    load_from_shadow_trades,
)


CREATE_SHADOW_TRADES_TABLE = (
    "CREATE TABLE shadow_trades ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "symbol TEXT NOT NULL, timeframe TEXT NOT NULL DEFAULT '1h', "
    "direction TEXT NOT NULL, entry_price REAL NOT NULL, "
    "stop_loss REAL NOT NULL, take_profit REAL NOT NULL, "
    "position_size_usdt REAL NOT NULL, entry_score REAL NOT NULL, "
    "entry_confidence REAL NOT NULL, layer_scores TEXT NOT NULL, "
    "entry_atr REAL NOT NULL, exit_price REAL, exit_reason TEXT, "
    "pnl_pct REAL, pnl_usdt REAL, bars_held INTEGER, "
    "opened_at TEXT NOT NULL, closed_at TEXT, inputs_hash TEXT NOT NULL, "
    "model_version TEXT NOT NULL DEFAULT 'sp-0.5', signal_id TEXT NOT NULL, "
    "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL)"
)

CREATE_INTERMARKET_TABLE = (
    "CREATE TABLE intermarket_snapshots ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, "
    "captured_at TEXT NOT NULL DEFAULT (datetime('now')), "
    "funding_rate REAL, mark_price REAL, open_interest REAL, source TEXT NOT NULL)"
)

# Added by migration 0019_shadow_observations — needed because the loader
# now LEFT-JOINs against it. SQLite mirror of the Postgres DDL.
CREATE_SHADOW_OBSERVATIONS_TABLE = (
    "CREATE TABLE shadow_observations ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "signal_id TEXT NOT NULL UNIQUE, "
    "user_id INTEGER NOT NULL, "
    "symbol TEXT NOT NULL, "
    "opened_at TEXT NOT NULL, "
    "components TEXT NOT NULL, "
    "created_at TEXT NOT NULL DEFAULT (datetime('now'))"
    ")"
)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(CREATE_SHADOW_TRADES_TABLE))
        await conn.execute(sa.text(CREATE_INTERMARKET_TABLE))
        await conn.execute(sa.text(CREATE_SHADOW_OBSERVATIONS_TABLE))
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as s:
        yield s
    await engine.dispose()


def _layer_scores_json(values: dict[str, float] | None = None) -> str:
    base = {f"L{i}": 0.1 * i for i in range(1, 10)}
    if values:
        base.update(values)
    return json.dumps(base)


async def _insert_trade(
    session: AsyncSession, *,
    sid: str = "sig-1", symbol: str = "BTC/USDT", direction: str = "LONG",
    pnl: float | None = 100.0,
    entry_price: float = 50_000.0, stop_loss: float = 49_500.0,
    position_size_usdt: float = 1000.0, entry_atr: float = 500.0,
    opened_at: str = "2024-04-01T12:00:00+00:00",
    closed_at: str | None = "2024-04-01T18:00:00+00:00",
) -> None:
    await session.execute(sa.text("""
        INSERT INTO shadow_trades
        (symbol, direction, entry_price, stop_loss, take_profit,
         position_size_usdt, entry_score, entry_confidence,
         layer_scores, entry_atr, exit_price, exit_reason, pnl_pct, pnl_usdt,
         bars_held, opened_at, closed_at, inputs_hash, signal_id,
         prev_hash, row_hash)
        VALUES (:s, :d, :ep, :sl, :tp, :sz, 0.7, 0.8, :ls, :atr,
                :xp, 'TAKE_PROFIT', :ppct, :pq, 6, :oa, :ca,
                'h', :sid, '00', :rh)
    """), {
        "s": symbol, "d": direction, "ep": entry_price, "sl": stop_loss,
        "tp": entry_price * 1.02, "sz": position_size_usdt,
        "ls": _layer_scores_json(), "atr": entry_atr,
        "xp": entry_price * 1.01, "ppct": 1.0, "pq": pnl,
        "oa": opened_at, "ca": closed_at, "sid": sid, "rh": "rh-" + sid,
    })
    await session.commit()


@pytest.mark.asyncio
async def test_empty_db_returns_empty_buffer(session: AsyncSession) -> None:
    out = await load_from_shadow_trades(session, window_days=365)
    assert out == []


@pytest.mark.asyncio
async def test_one_closed_trade_yields_one_transition(session: AsyncSession) -> None:
    await _insert_trade(session)
    out = await load_from_shadow_trades(session, window_days=365)
    assert len(out) == 1
    t = out[0]
    assert isinstance(t, Transition)
    assert t.action == ACTION_LONG_FULL
    assert t.symbol == "BTC/USDT"
    assert t.done is True


@pytest.mark.asyncio
async def test_open_trade_excluded(session: AsyncSession) -> None:
    """Trades with NULL closed_at OR NULL pnl_usdt aren't training data yet."""
    await _insert_trade(session, sid="open-1", closed_at=None, pnl=None)
    await _insert_trade(session, sid="closed-1")
    out = await load_from_shadow_trades(session, window_days=365)
    assert len(out) == 1
    assert out[0].symbol == "BTC/USDT"


@pytest.mark.asyncio
async def test_short_direction_maps_to_short_full(session: AsyncSession) -> None:
    await _insert_trade(session, direction="SHORT", stop_loss=50_500.0)
    out = await load_from_shadow_trades(session, window_days=365)
    assert out[0].action == ACTION_SHORT_FULL


@pytest.mark.asyncio
async def test_observation_shape_and_dtype(session: AsyncSession) -> None:
    await _insert_trade(session)
    out = await load_from_shadow_trades(session, window_days=365)
    obs = out[0].obs
    assert obs.shape == (OBS_DIM,)
    assert obs.dtype == np.float32


@pytest.mark.asyncio
async def test_action_is_one_of_five_known_strings(session: AsyncSession) -> None:
    await _insert_trade(session)
    out = await load_from_shadow_trades(session, window_days=365)
    assert out[0].action in ALL_ACTIONS


@pytest.mark.asyncio
async def test_reward_in_clip_range(session: AsyncSession) -> None:
    """Even an extreme +20R trade should clip into [-3, +3]."""
    await _insert_trade(
        session,
        entry_price=100.0, stop_loss=99.0,  # 1% risk
        position_size_usdt=1000.0,
        pnl=10_000.0,  # absurd win
    )
    out = await load_from_shadow_trades(session, window_days=365)
    assert -3.0 <= out[0].reward <= 3.0


@pytest.mark.asyncio
async def test_layer_scores_extracted_from_jsonb(session: AsyncSession) -> None:
    """L1..L9 from layer_scores JSONB land in obs[32:41]."""
    # Custom layer scores so we can spot them in the obs vector.
    await session.execute(sa.text("""
        INSERT INTO shadow_trades
        (symbol, direction, entry_price, stop_loss, take_profit,
         position_size_usdt, entry_score, entry_confidence,
         layer_scores, entry_atr, exit_price, exit_reason, pnl_pct, pnl_usdt,
         bars_held, opened_at, closed_at, inputs_hash, signal_id,
         prev_hash, row_hash)
        VALUES ('ETH/USDT', 'LONG', 3000, 2970, 3060, 1000, 0.7, 0.8,
                :ls, 30, 3030, 'TAKE_PROFIT', 1.0, 100, 6,
                '2024-04-01T12:00:00+00:00', '2024-04-01T18:00:00+00:00',
                'h', 'sig-x', '00', 'rh-x')
    """), {"ls": json.dumps({f"L{i}": 0.5 for i in range(1, 10)})})
    await session.commit()
    out = await load_from_shadow_trades(session, window_days=365)
    assert len(out) == 1
    # Embedding=zeros (32) → layer scores start at obs[32]
    layer_slice = out[0].obs[32:41]
    assert np.allclose(layer_slice, 0.5)


@pytest.mark.asyncio
async def test_trades_grouped_by_symbol_for_sigma(session: AsyncSession) -> None:
    """Different symbols don't pollute each other's σ history."""
    for i in range(6):
        await _insert_trade(session, sid=f"btc-{i}", symbol="BTC/USDT", pnl=100.0)
    for i in range(6):
        await _insert_trade(session, sid=f"eth-{i}", symbol="ETH/USDT", pnl=-100.0)
    out = await load_from_shadow_trades(session, window_days=365)
    assert len(out) == 12
    btc_rewards = [t.reward for t in out if t.symbol == "BTC/USDT"]
    eth_rewards = [t.reward for t in out if t.symbol == "ETH/USDT"]
    assert all(r > 0 for r in btc_rewards)
    assert all(r < 0 for r in eth_rewards)


@pytest.mark.asyncio
async def test_invalid_risk_trade_is_skipped(session: AsyncSession) -> None:
    """If entry == stop_loss, risk = 0, trade should be skipped (logged warn)."""
    await _insert_trade(
        session, entry_price=50_000.0, stop_loss=50_000.0,  # 0 risk
    )
    out = await load_from_shadow_trades(session, window_days=365)
    assert out == []


@pytest.mark.asyncio
async def test_asset_id_map_assigns_per_symbol(session: AsyncSession) -> None:
    await _insert_trade(session, sid="btc-1", symbol="BTC/USDT")
    await _insert_trade(session, sid="eth-1", symbol="ETH/USDT")
    sym_to_id = {"BTC/USDT": 0, "ETH/USDT": 1}
    out = await load_from_shadow_trades(
        session, window_days=365, asset_id_for_symbol=sym_to_id,
    )
    by_symbol = {t.symbol: t.asset_id for t in out}
    assert by_symbol == {"BTC/USDT": 0, "ETH/USDT": 1}


@pytest.mark.asyncio
async def test_load_from_shadow_trades_uses_provided_embeddings(
    session: AsyncSession,
) -> None:
    """PR-BRAIN-WARMSTART-FIX: when asset_embeddings is provided, each
    transition's obs contains the supplied embedding bytes for that asset_id
    (not the zero fallback). Pins the bug that produced rl_checkpoints.id=1's
    all-zero asset-id-zero observations.
    """
    await _insert_trade(session, sid="a-1", symbol="ASYMUSDT")
    await _insert_trade(session, sid="b-1", symbol="BSYMUSDT")
    await _insert_trade(session, sid="c-1", symbol="CSYMUSDT")
    sym_to_id = {"ASYMUSDT": 0, "BSYMUSDT": 1, "CSYMUSDT": 2}
    asset_embeddings = {
        0: np.full(32, 1.0, dtype=np.float32),
        1: np.full(32, 2.0, dtype=np.float32),
        2: np.full(32, 3.0, dtype=np.float32),
    }
    out = await load_from_shadow_trades(
        session,
        window_days=365,
        asset_id_for_symbol=sym_to_id,
        asset_embeddings=asset_embeddings,
    )
    by_symbol = {t.symbol: t for t in out}
    # The first 32 floats of every obs are the asset embedding (per obs.py
    # build_observation: parts[0] = asset.embedding).
    np.testing.assert_array_equal(
        by_symbol["ASYMUSDT"].obs[:32], asset_embeddings[0],
    )
    np.testing.assert_array_equal(
        by_symbol["BSYMUSDT"].obs[:32], asset_embeddings[1],
    )
    np.testing.assert_array_equal(
        by_symbol["CSYMUSDT"].obs[:32], asset_embeddings[2],
    )


# ---------------------------------------------------------------------------
# Migration 0019 — shadow_observations table. The loader LEFT-JOINs against
# this table to prefer exact obs components captured at trade-open time over
# lossy time-based reconstruction. Tests below cover BOTH paths.


async def _insert_observation(
    session: AsyncSession,
    *,
    signal_id: str,
    user_id: int = 1,
    symbol: str = "BTC/USDT",
    opened_at: str = "2024-04-01T12:00:00+00:00",
    layer_scores_array: list[float] | None = None,
    funding_rate: float = 0.0002,
    open_interest: float = 1.5e9,
    regime: str = "bull_breakout",
) -> None:
    """Stub helper — writes a row to shadow_observations mirroring what
    the shadow worker would persist via app.shadow.observation."""
    ls = layer_scores_array if layer_scores_array is not None else [
        0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.4, 0.0,
    ]
    components = {
        "schema_version": 1,
        "captured_at": opened_at,
        "symbol": symbol,
        "atr": 500.0,
        "last_close": 50_000.0,
        "layer_scores": ls,
        "market": {
            "atr_pct": 0.01,
            "funding_rate": funding_rate,
            "open_interest": open_interest,
            "oi_delta_24h": 0.0,
            "dxy_corr_30d": -0.4,
            "gold_corr_30d": 0.5,
            "regime": regime,
        },
        "position": {
            "cur_position": 0,
            "unrealized_pnl_R": 0.0,
            "bars_in_position": 0,
        },
        "macro": {
            "hours_to_next_high_impact": 24.0,
            "fomc_window": False,
            "weekend": False,
            "asia_open": True,
        },
    }
    await session.execute(sa.text("""
        INSERT INTO shadow_observations
        (signal_id, user_id, symbol, opened_at, components)
        VALUES (:sid, :uid, :sym, :oa, :comp)
    """), {
        "sid": signal_id, "uid": user_id, "sym": symbol,
        "oa": opened_at, "comp": json.dumps(components),
    })
    await session.commit()


@pytest.mark.asyncio
async def test_obs_components_path_uses_stored_market_features(
    session: AsyncSession,
) -> None:
    """When a shadow_observations row exists for a trade's signal_id, the
    loader must use ITS market features (not re-query intermarket_snapshots)."""
    # Insert a trade + matching observation row.
    await _insert_trade(session, sid="sig-stored-1")
    await _insert_observation(
        session, signal_id="sig-stored-1",
        funding_rate=0.0008,  # distinctive value to confirm it gets read
        regime="bear_crash",
    )
    out = await load_from_shadow_trades(session, window_days=365)
    assert len(out) == 1
    # Obs is a numpy float32 array — we can't directly inspect "regime"
    # but we can confirm the observation came from build_observation
    # using the EXACT funding_rate the JSON stored.
    # OBS layout: emb(32) + ls(9) + market(5: atr_pct,funding,oi_delta,dxy,gold)
    #           + regime(5) + position(3) + macro(4)
    obs = out[0].obs
    funding_idx = 32 + 9 + 1  # second slot of market block
    assert obs[funding_idx] == pytest.approx(0.0008, abs=1e-6)
    # Regime one-hot at indices 32+9+5 .. 32+9+5+4. bear_crash is index 1.
    regime_base = 32 + 9 + 5
    assert obs[regime_base + 1] == pytest.approx(1.0)
    assert obs[regime_base + 0] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_legacy_trade_without_observation_falls_back_to_reconstruction(
    session: AsyncSession,
) -> None:
    """Pre-0019 trades have NO shadow_observations row. The LEFT JOIN
    yields NULL on obs_components and the loader must fall back cleanly
    to the time-based market reconstruction."""
    await _insert_trade(session, sid="sig-legacy-1")
    # NO matching observation row inserted.
    out = await load_from_shadow_trades(session, window_days=365)
    # Must still produce one transition — backward compat is the invariant.
    assert len(out) == 1
    # Funding defaults to 0 in the legacy path because no intermarket row exists.
    obs = out[0].obs
    funding_idx = 32 + 9 + 1
    assert obs[funding_idx] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_obs_components_layer_scores_take_precedence(
    session: AsyncSession,
) -> None:
    """The stored layer_scores array must override whatever's in
    shadow_trades.layer_scores (JSONB) — the snapshot is the source of
    truth when present."""
    await _insert_trade(session, sid="sig-ls-1")  # layer_scores all keyed L1..L9
    distinctive = [0.91, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    await _insert_observation(
        session, signal_id="sig-ls-1", layer_scores_array=distinctive,
    )
    out = await load_from_shadow_trades(session, window_days=365)
    assert len(out) == 1
    obs = out[0].obs
    # L1 is at index 32 (after the 32-float asset embedding).
    assert obs[32] == pytest.approx(0.91, abs=1e-6)


@pytest.mark.asyncio
async def test_partial_components_handled_safely(
    session: AsyncSession,
) -> None:
    """If the stored components dict is missing fields (e.g. older schema),
    the loader must not crash and should zero-fill the missing pieces."""
    await _insert_trade(session, sid="sig-partial-1")
    # Manually insert a minimal components blob.
    await session.execute(sa.text("""
        INSERT INTO shadow_observations
        (signal_id, user_id, symbol, opened_at, components)
        VALUES (:sid, 1, 'BTC/USDT', :oa, :comp)
    """), {
        "sid": "sig-partial-1",
        "oa": "2024-04-01T12:00:00+00:00",
        "comp": json.dumps({"schema_version": 1, "layer_scores": [0.5] * 9}),
    })
    await session.commit()
    out = await load_from_shadow_trades(session, window_days=365)
    assert len(out) == 1
    # Should not crash. Obs is correct shape.
    assert out[0].obs.shape == (OBS_DIM,)
