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


# PR-BRAIN-WARMSTART-FIX — bulk_register preserves Gaussian init


def test_bulk_register_preserves_gaussian_init() -> None:
    """bulk_register must NOT median-seed; each slot keeps its independent
    Gaussian-init weight so the trainer sees diverse per-asset signal."""
    torch.manual_seed(42)  # determinism for the randomness-based assertion
    t = AssetEmbeddingTable()
    symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT", "SOLUSDT", "DOTUSDT"]
    t.bulk_register(symbols)

    assert t.symbol_to_id == {s: i for i, s in enumerate(symbols)}

    weights = t.module.weight.detach().cpu().numpy()
    # The 5 registered slots should NOT be identical — that would be the
    # symptom if bulk_register accidentally called register_asset.
    pairwise_max_diff = 0.0
    for i in range(5):
        for j in range(i + 1, 5):
            pairwise_max_diff = max(
                pairwise_max_diff,
                float(np.abs(weights[i] - weights[j]).max()),
            )
    # With Gaussian(0, 0.05) init on 32-dim vectors, the max-abs pairwise
    # difference between any 2 slots is overwhelmingly likely to exceed
    # 0.01 (well below the std of 0.05).
    assert pairwise_max_diff > 0.01, (
        f"slots are too similar (max pairwise diff {pairwise_max_diff:.4f}) — "
        "bulk_register likely median-seeded instead of preserving init"
    )


def test_bulk_register_is_idempotent() -> None:
    """Calling bulk_register twice with overlapping symbols doesn't double-
    allocate or overwrite."""
    t = AssetEmbeddingTable()
    t.bulk_register(["BTCUSDT", "ETHUSDT"])
    snapshot = t.module.weight[:2].detach().cpu().numpy().copy()

    t.bulk_register(["BTCUSDT", "ETHUSDT", "ADAUSDT"])

    assert t.symbol_to_id == {"BTCUSDT": 0, "ETHUSDT": 1, "ADAUSDT": 2}
    # The first 2 slots' weights must be byte-identical to the snapshot.
    after = t.module.weight[:2].detach().cpu().numpy()
    np.testing.assert_array_equal(snapshot, after)


def test_bulk_register_respects_n_slots_cap() -> None:
    """Exceeding n_slots raises RuntimeError (matches register_asset)."""
    t = AssetEmbeddingTable(n_slots=3)
    t.bulk_register(["A", "B", "C"])
    with pytest.raises(RuntimeError, match="AssetEmbeddingTable full"):
        t.bulk_register(["D"])


def test_bulk_register_vs_register_asset_diversity() -> None:
    """Direct comparison: bulk_register gives independent per-slot vectors,
    register_asset in a loop collapses all slots to the first asset's
    Gaussian sample (median-of-{same}={same}, propagated). Documents the
    failure mode the PR avoids."""
    t_bulk = AssetEmbeddingTable()
    t_loop = AssetEmbeddingTable()
    symbols = [f"SYM{i:02d}" for i in range(10)]

    t_bulk.bulk_register(symbols)
    for s in symbols:
        t_loop.register_asset(s)

    bulk_w = t_bulk.module.weight[:10].detach().cpu().numpy()
    loop_w = t_loop.module.weight[:10].detach().cpu().numpy()

    # In the loop case, slot 1+ are all median-seeded from prior slots.
    # With a single starting Gaussian sample (slot 0), every subsequent
    # slot collapses to slot 0's value. Assert that holds:
    for i in range(1, 10):
        np.testing.assert_array_equal(loop_w[i], loop_w[0])

    # In the bulk case, all 10 slots are independent Gaussian samples —
    # adjacent slots should differ.
    distinct_pairs = sum(
        1 for i in range(10) for j in range(i + 1, 10)
        if not np.array_equal(bulk_w[i], bulk_w[j])
    )
    assert distinct_pairs == 45, (
        f"expected all 45 slot pairs distinct, got {distinct_pairs}"
    )
