"""SP-4 Phase A end-to-end integration smoke.

Wires the five Phase A modules together against an in-memory SQLite DB:

* migration 0015 — rl_checkpoints + brain_decisions tables (file-scan
  test only; this integration uses manual table DDL like the rest of
  the SQLite test suite)
* shadow_trades fixtures
* app.rl.replay_buffer.load_from_shadow_trades  → list[Transition]
* app.rl.obs.build_observation                  → 53-float vectors
* app.rl.reward.compute_reward                  → in [-3, +3]
* app.rl.adapter.AssetEmbeddingTable            → per-asset embeddings
* app.rl.checkpoints.set_active / clear_active  → module-state pattern

This is the Phase A acceptance gate per the SP-4 plan: "Replay buffer
materializes 365d of past trades; obs/reward unit tests green; modules
compose without crashing."
"""
from __future__ import annotations

import json

import numpy as np
import pytest
import pytest_asyncio
import sqlalchemy as sa
import torch
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from torch import nn

from app.rl.adapter import AssetEmbeddingTable
from app.rl.checkpoints import (
    ActiveRlCheckpoint,
    clear_active,
    get_active_policy_and_checkpoint,
    set_active,
)
from app.rl.obs import OBS_DIM
from app.rl.replay_buffer import (
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

# Added by migration 0019 — replay_buffer.load_from_shadow_trades LEFT-JOINs
# against this table, so the fixture must create it even if the test doesn't
# populate it (LEFT JOIN with empty table is fine; missing table is not).
CREATE_SHADOW_OBSERVATIONS_TABLE = (
    "CREATE TABLE shadow_observations ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "signal_id TEXT NOT NULL UNIQUE, "
    "user_id INTEGER NOT NULL, "
    "symbol TEXT NOT NULL, "
    "opened_at TEXT NOT NULL, "
    "components TEXT NOT NULL, "
    "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(CREATE_SHADOW_TRADES_TABLE))
        await conn.execute(sa.text(CREATE_INTERMARKET_TABLE))
        await conn.execute(sa.text(CREATE_SHADOW_OBSERVATIONS_TABLE))
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_module_state() -> None:
    clear_active()


async def _seed_trades(session: AsyncSession, n: int = 5) -> None:
    """Insert N closed shadow_trades alternating LONG/SHORT across two assets."""
    for i in range(n):
        symbol = "BTC/USDT" if i % 2 == 0 else "ETH/USDT"
        direction = "LONG" if i % 2 == 0 else "SHORT"
        entry = 50_000.0 if symbol == "BTC/USDT" else 3000.0
        sl = entry * (0.99 if direction == "LONG" else 1.01)
        await session.execute(sa.text("""
            INSERT INTO shadow_trades
            (symbol, direction, entry_price, stop_loss, take_profit,
             position_size_usdt, entry_score, entry_confidence,
             layer_scores, entry_atr, exit_price, exit_reason, pnl_pct, pnl_usdt,
             bars_held, opened_at, closed_at, inputs_hash, signal_id,
             prev_hash, row_hash)
            VALUES (:s, :d, :ep, :sl, :tp, 1000.0, 0.7, 0.8,
                    :ls, :atr, :xp, 'TAKE_PROFIT', 1.0, :pnl, 6,
                    :oa, :ca, 'h', :sid, '00', :rh)
        """), {
            "s": symbol, "d": direction, "ep": entry, "sl": sl,
            "tp": entry * (1.02 if direction == "LONG" else 0.98),
            "ls": json.dumps({f"L{j}": 0.1 * j for j in range(1, 10)}),
            "atr": entry * 0.01, "xp": entry * (1.005 if direction == "LONG" else 0.995),
            "pnl": 50.0 if i % 3 != 0 else -25.0,
            "oa": f"2024-04-0{i+1}T12:00:00+00:00",
            "ca": f"2024-04-0{i+1}T18:00:00+00:00",
            "sid": f"sig-{i}", "rh": f"rh-{i}",
        })
    await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_phase_a_end_to_end(session: AsyncSession) -> None:
    """Five trades go in → five Transitions come out, all valid shape + range."""
    await _seed_trades(session, n=5)

    # Build an embedding table mapping the seeded symbols to ids.
    table = AssetEmbeddingTable()
    table.register_asset("BTC/USDT")
    table.register_asset("ETH/USDT")

    transitions = await load_from_shadow_trades(
        session,
        window_days=365,
        asset_id_for_symbol=table.symbol_to_id,
    )

    assert len(transitions) == 5
    for t in transitions:
        assert isinstance(t, Transition)
        assert t.obs.shape == (OBS_DIM,)
        assert t.obs.dtype == np.float32
        assert t.next_obs.shape == (OBS_DIM,)
        assert t.action in ALL_ACTIONS
        assert -3.0 <= t.reward <= 3.0
        assert t.done is True
        assert t.symbol in {"BTC/USDT", "ETH/USDT"}
        assert t.asset_id in {0, 1}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_phase_a_module_state_pattern_round_trip() -> None:
    """set_active → get → clear_active leaves no residual state."""
    class _Dummy(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.fc = nn.Linear(OBS_DIM, 5)

    model = _Dummy()
    ck = ActiveRlCheckpoint(
        id=1, model_name="ppo_policy_v1", version="phase-a-smoke",
        sha256="0" * 64, checkpoint_uri="file:///tmp/x.pt",
    )
    set_active(model, ck)
    out = get_active_policy_and_checkpoint()
    assert out is not None
    assert out[0] is model
    assert out[1].version == "phase-a-smoke"
    clear_active()
    assert get_active_policy_and_checkpoint() is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_phase_a_replay_buffer_with_zero_embeddings_works(
    session: AsyncSession,
) -> None:
    """Without an embedding table, the buffer still produces transitions
    (the trainer rebinds embeddings before each PPO epoch in Phase B)."""
    await _seed_trades(session, n=3)
    transitions = await load_from_shadow_trades(session, window_days=365)
    assert len(transitions) == 3
    # Default embedding (zeros) → first 32 floats of obs are all zero
    for t in transitions:
        assert np.allclose(t.obs[:32], 0.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_phase_a_torch_save_load_round_trip(tmp_path) -> None:
    """End-to-end: save an embedding table state_dict to disk, restore it,
    verify weights match. Sanity-check that the Phase A pieces compose
    cleanly through torch.save / torch.load."""
    a = AssetEmbeddingTable()
    a.register_asset("BTC/USDT")
    a.register_asset("ETH/USDT")
    with torch.no_grad():
        a.module.weight[0].fill_(0.42)
        a.module.weight[1].fill_(-0.7)

    pt = tmp_path / "embed.pt"
    torch.save(a.state_dict(), pt)

    state = torch.load(pt, weights_only=False)
    b = AssetEmbeddingTable()
    b.load_state_dict(state)

    assert b.symbol_to_id == {"BTC/USDT": 0, "ETH/USDT": 1}
    assert torch.allclose(a.module.weight[0:2], b.module.weight[0:2], atol=1e-7)
