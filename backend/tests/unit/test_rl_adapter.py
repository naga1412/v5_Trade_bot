"""Tests for app.rl.adapter — asset embedding + cold-start blending (Phase A5)."""
from __future__ import annotations

import numpy as np
import pytest
import torch

from app.rl.adapter import (
    COLD_START_TRADES,
    INITIAL_TABLE_SIZE,
    AssetEmbeddingTable,
    cold_start_blend,
)
from app.rl.obs import EMB_DIM


def test_constants_match_spec_section_Q5() -> None:
    assert COLD_START_TRADES == 100
    assert INITIAL_TABLE_SIZE >= 1024  # plenty of headroom for 60-asset universe


def test_table_initialises_with_normal_weights() -> None:
    t = AssetEmbeddingTable()
    w = t.module.weight.detach().cpu().numpy()
    assert w.shape == (INITIAL_TABLE_SIZE, EMB_DIM)
    # Std should be roughly the requested 0.05 — sanity check, not a tight bound.
    assert 0.01 < float(w.std()) < 0.2


def test_register_asset_assigns_sequential_ids() -> None:
    t = AssetEmbeddingTable()
    assert t.register_asset("BTC/USDT") == 0
    assert t.register_asset("ETH/USDT") == 1
    assert t.register_asset("SOL/USDT") == 2
    # Re-registering an existing symbol returns the existing id.
    assert t.register_asset("BTC/USDT") == 0
    assert t.symbol_to_id == {"BTC/USDT": 0, "ETH/USDT": 1, "SOL/USDT": 2}


def test_new_asset_seeded_from_median_of_existing() -> None:
    """The Nth registration should land at median(weights[:N-1])."""
    t = AssetEmbeddingTable()
    # Force known embeddings to specific values
    with torch.no_grad():
        t.module.weight[0] = torch.full((EMB_DIM,), -1.0)
        t.module.weight[1] = torch.full((EMB_DIM,), +1.0)
        t.module.weight[2] = torch.full((EMB_DIM,), 0.5)
    t.symbol_to_id = {"A": 0, "B": 1, "C": 2}

    new_id = t.register_asset("D")
    assert new_id == 3
    new_emb = t.module.weight[3].detach().cpu().numpy()
    # Median of [-1, +1, 0.5] = 0.5
    assert np.allclose(new_emb, 0.5)


def test_table_overflow_raises() -> None:
    t = AssetEmbeddingTable(n_slots=2)
    t.register_asset("A")
    t.register_asset("B")
    with pytest.raises(RuntimeError, match="full"):
        t.register_asset("C")


def test_get_embedding_with_full_history_returns_learned() -> None:
    """With n_trades >= COLD_START_TRADES, no blending."""
    t = AssetEmbeddingTable()
    t.register_asset("BTC/USDT")
    with torch.no_grad():
        t.module.weight[0] = torch.full((EMB_DIM,), 0.7)
    # Force known set so median != learned
    with torch.no_grad():
        t.module.weight[1] = torch.zeros(EMB_DIM)
    t.symbol_to_id["other"] = 1

    emb = t.get_embedding("BTC/USDT", n_trades_for_asset=COLD_START_TRADES)
    assert np.allclose(emb, 0.7)


def test_get_embedding_with_zero_trades_returns_median() -> None:
    """With n_trades=0, blend yields median (α=0)."""
    t = AssetEmbeddingTable()
    with torch.no_grad():
        t.module.weight[0] = torch.full((EMB_DIM,), -1.0)
        t.module.weight[1] = torch.full((EMB_DIM,), +1.0)
    t.symbol_to_id = {"A": 0, "B": 1, "C": 2}
    # Set C to a distinct value so we can detect blending
    with torch.no_grad():
        t.module.weight[2] = torch.full((EMB_DIM,), 5.0)

    emb = t.get_embedding("C", n_trades_for_asset=0)
    # Median of [-1, +1, 5] = 1.0
    assert np.allclose(emb, 1.0)


def test_get_embedding_at_half_history_blends_50_50() -> None:
    t = AssetEmbeddingTable()
    with torch.no_grad():
        t.module.weight[0] = torch.full((EMB_DIM,), 0.0)
    t.symbol_to_id = {"M": 0, "X": 1}
    with torch.no_grad():
        t.module.weight[1] = torch.full((EMB_DIM,), 1.0)

    emb = t.get_embedding("X", n_trades_for_asset=COLD_START_TRADES // 2)
    # α=0.5; median([0, 1]) = 0.5; blend = 0.5*0.5 + 0.5*1.0 = 0.75
    assert np.allclose(emb, 0.75, atol=1e-5)


def test_get_embedding_returns_float32() -> None:
    t = AssetEmbeddingTable()
    t.register_asset("BTC/USDT")
    emb = t.get_embedding("BTC/USDT", n_trades_for_asset=200)
    assert emb.dtype == np.float32
    assert emb.shape == (EMB_DIM,)


def test_state_dict_round_trip_preserves_weights_and_ids() -> None:
    a = AssetEmbeddingTable()
    a.register_asset("BTC/USDT")
    a.register_asset("ETH/USDT")
    with torch.no_grad():
        a.module.weight[0] = torch.full((EMB_DIM,), 0.42)

    state = a.state_dict()
    b = AssetEmbeddingTable()
    b.load_state_dict(state)

    assert b.symbol_to_id == {"BTC/USDT": 0, "ETH/USDT": 1}
    assert torch.allclose(
        a.module.weight[0:2], b.module.weight[0:2], atol=1e-7,
    )


def test_cold_start_blend_alpha_zero() -> None:
    median = np.full(32, 0.5, dtype=np.float32)
    learned = np.full(32, 1.0, dtype=np.float32)
    out = cold_start_blend(median, learned, n_trades=0)
    assert np.allclose(out, 0.5)


def test_cold_start_blend_alpha_one() -> None:
    median = np.full(32, 0.5, dtype=np.float32)
    learned = np.full(32, 1.0, dtype=np.float32)
    out = cold_start_blend(median, learned, n_trades=COLD_START_TRADES)
    assert np.allclose(out, 1.0)


def test_cold_start_blend_alpha_clamped_above_one() -> None:
    """n_trades >> COLD_START_TRADES should still α=1."""
    median = np.full(32, 0.0, dtype=np.float32)
    learned = np.full(32, 7.0, dtype=np.float32)
    out = cold_start_blend(median, learned, n_trades=10_000)
    assert np.allclose(out, 7.0)


def test_cold_start_blend_shape_mismatch_raises() -> None:
    median = np.zeros(16, dtype=np.float32)
    learned = np.zeros(32, dtype=np.float32)
    with pytest.raises(ValueError, match="shape mismatch"):
        cold_start_blend(median, learned, n_trades=50)


def test_blend_returns_float32_regardless_of_input_dtype() -> None:
    median = np.zeros(32, dtype=np.float64)
    learned = np.ones(32, dtype=np.float64)
    out = cold_start_blend(median, learned, n_trades=50)
    assert out.dtype == np.float32
