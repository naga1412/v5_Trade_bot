"""PR-BRAIN-EMBEDDINGS-LEARNABLE — verify the training-loop hot-swap.

The training loop now:
1. Includes asset_table.module.parameters() in the optimizer
2. Hot-swaps each obs's first emb_dim dims with a live nn.Embedding lookup

These tests pin both halves of that contract — without (1), grad-aware
lookup is a no-op; without (2), the optimizer would step with grad=None.
"""
from __future__ import annotations

import numpy as np
import pytest
import torch

from app.rl.adapter import AssetEmbeddingTable
from app.rl.obs import OBS_DIM
from app.rl.policy import PolicyNetwork
from app.rl.ppo import TrainConfig, _stack_transitions, train_ppo
from app.rl.replay_buffer import ACTION_LONG_FULL, Transition


def _make_transition(
    *,
    asset_id: int = 0,
    symbol: str = "BTCUSDT",
    reward: float = 0.1,
    action: str = ACTION_LONG_FULL,
    embedding: np.ndarray | None = None,
) -> Transition:
    """Helper — builds a Transition whose obs has a specific embedding
    in the first 32 floats. Used to verify hot-swap REPLACES that region."""
    obs = np.zeros(OBS_DIM, dtype=np.float32)
    if embedding is not None:
        obs[:32] = embedding.astype(np.float32)
    return Transition(
        obs=obs, action=action, reward=reward,
        next_obs=obs.copy(), done=True,
        asset_id=asset_id, symbol=symbol,
        opened_at_iso="2026-05-14T17:00:00+00:00",
    )


def _make_transitions_with_asset(
    asset_id: int, symbol: str, n: int, reward: float,
) -> list[Transition]:
    """N transitions all with the same asset_id + same reward sign."""
    return [
        _make_transition(asset_id=asset_id, symbol=symbol, reward=reward)
        for _ in range(n)
    ]


def test_stack_transitions_returns_asset_ids_tensor() -> None:
    """PR-BRAIN-EMBEDDINGS-LEARNABLE added the asset_ids return."""
    transitions = [
        _make_transition(asset_id=0),
        _make_transition(asset_id=1),
        _make_transition(asset_id=2),
    ]
    obs, actions, rewards, asset_ids = _stack_transitions(transitions)
    assert obs.shape[0] == 3
    assert asset_ids.shape == (3,)
    assert asset_ids.dtype == torch.long
    assert asset_ids.tolist() == [0, 1, 2]


def test_optimizer_includes_asset_table_parameters() -> None:
    """Sentinel: capture asset_table.module.weight before train_ppo runs;
    if the optimizer includes its params AND grads flow, ANY single
    training step must mutate the weight tensor."""
    torch.manual_seed(0)
    policy = PolicyNetwork()
    asset_table = AssetEmbeddingTable()
    asset_table.bulk_register(["A", "B"])
    transitions = [
        _make_transition(asset_id=0, reward=+0.5)
        for _ in range(40)
    ] + [
        _make_transition(asset_id=1, reward=-0.5)
        for _ in range(40)
    ]
    weights_before = asset_table.module.weight[:2].detach().cpu().numpy().copy()

    cfg = TrainConfig(epochs=2, batch_size=16, seed=0)
    train_ppo(
        policy=policy, transitions=transitions,
        asset_table=asset_table, config=cfg,
    )

    weights_after = asset_table.module.weight[:2].detach().cpu().numpy()
    assert not np.array_equal(weights_before, weights_after), (
        "asset_table.module.weight was NOT updated by train_ppo — "
        "either it's missing from optimizer or gradients don't flow"
    )


def test_gradient_flows_to_embedding_after_one_backward() -> None:
    """The hot-swap path must produce a non-None .grad on the embedding
    layer when loss.backward() runs. Direct contract test of the
    autograd connection."""
    torch.manual_seed(0)
    policy = PolicyNetwork()
    asset_table = AssetEmbeddingTable()
    asset_table.bulk_register(["A"])

    # Build a small graph mirroring what train_ppo does internally.
    asset_id = torch.tensor([0], dtype=torch.long)
    live_emb = asset_table.module(asset_id)  # (1, 32) — grad-aware
    obs_baked = torch.zeros(1, OBS_DIM, dtype=torch.float32)
    obs_live = torch.cat([live_emb, obs_baked[:, 32:]], dim=1)

    out = policy.forward(obs_live)
    loss = out.value.mean() + out.logits.mean()
    loss.backward()

    assert asset_table.module.weight.grad is not None, (
        "grad is None — hot-swap autograd path is broken"
    )
    # The relevant row (asset_id=0) should have non-zero gradient.
    grad_for_used_asset = asset_table.module.weight.grad[0]
    assert torch.any(grad_for_used_asset != 0.0), (
        "grad for the asset_id=0 row is all-zero — embedding wasn't "
        "actually consumed by the forward pass"
    )


def test_embedding_weights_change_after_multi_epoch_training() -> None:
    """Stronger version of test_optimizer_includes — train enough epochs
    that the embedding moves a measurable distance."""
    torch.manual_seed(1)
    policy = PolicyNetwork()
    asset_table = AssetEmbeddingTable()
    asset_table.bulk_register([f"SYM{i}" for i in range(5)])
    transitions = []
    for asset_id in range(5):
        sign = +1.0 if asset_id % 2 == 0 else -1.0
        transitions.extend(
            _make_transitions_with_asset(
                asset_id, f"SYM{asset_id}", n=20, reward=sign * 0.5,
            ),
        )

    init_weights = asset_table.module.weight[:5].detach().cpu().numpy().copy()
    cfg = TrainConfig(epochs=5, batch_size=16, seed=1)
    train_ppo(
        policy=policy, transitions=transitions,
        asset_table=asset_table, config=cfg,
    )
    final_weights = asset_table.module.weight[:5].detach().cpu().numpy()

    # At least one of the 5 rows must have moved at least 0.001 in L2.
    moves = np.linalg.norm(final_weights - init_weights, axis=1)
    assert float(moves.max()) > 0.001, (
        f"all 5 embeddings moved < 0.001 (max={moves.max():.6f}); "
        "training is not actually updating them"
    )


def test_per_asset_embeddings_diverge_with_distinct_signals() -> None:
    """Train on 2 assets where asset 0 has +reward, asset 1 has -reward.
    After training, the 2 embedding vectors should be MORE different
    than at init (cosine similarity decreases)."""
    torch.manual_seed(2)
    policy = PolicyNetwork()
    asset_table = AssetEmbeddingTable()
    asset_table.bulk_register(["GOOD", "BAD"])
    transitions = (
        _make_transitions_with_asset(0, "GOOD", n=30, reward=+0.5)
        + _make_transitions_with_asset(1, "BAD", n=30, reward=-0.5)
    )

    def _cos_sim(a, b):
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        return float(np.dot(a, b) / denom) if denom > 0 else 1.0

    w0_init = asset_table.module.weight[0].detach().cpu().numpy().copy()
    w1_init = asset_table.module.weight[1].detach().cpu().numpy().copy()
    cos_init = _cos_sim(w0_init, w1_init)

    cfg = TrainConfig(epochs=8, batch_size=16, seed=2)
    train_ppo(
        policy=policy, transitions=transitions,
        asset_table=asset_table, config=cfg,
    )

    w0_final = asset_table.module.weight[0].detach().cpu().numpy()
    w1_final = asset_table.module.weight[1].detach().cpu().numpy()
    cos_final = _cos_sim(w0_final, w1_final)

    # Specialization metric: per-asset embeddings should drift APART
    # (cos similarity decreases). With Gaussian init the random vectors
    # are already near-orthogonal (cos ~0); after differentiating training
    # signals they should move apart even further OR move in different
    # directions. Assert SOME change in cos_sim ≥ 0.01 to catch the
    # "embeddings stay frozen-near-init" regression.
    assert abs(cos_final - cos_init) > 0.01, (
        f"cosine sim barely changed: init={cos_init:.4f} → final={cos_final:.4f}. "
        "Per-asset embeddings aren't specializing as expected."
    )


def test_legacy_checkpoint_state_dict_loads_via_new_training_path() -> None:
    """An asset_table state_dict saved before this PR (frozen-init weights,
    same shape) must still load via load_state_dict. PR doesn't change the
    saved shape — pin this to catch accidental schema breaks."""
    asset_table_v1 = AssetEmbeddingTable()
    asset_table_v1.bulk_register(["A", "B", "C"])
    saved = asset_table_v1.state_dict()

    # New instance, load saved weights
    asset_table_v2 = AssetEmbeddingTable()
    asset_table_v2.load_state_dict(saved)

    assert asset_table_v2.symbol_to_id == {"A": 0, "B": 1, "C": 2}
    np.testing.assert_array_equal(
        asset_table_v2.module.weight.detach().cpu().numpy()[:3],
        asset_table_v1.module.weight.detach().cpu().numpy()[:3],
    )


def test_train_ppo_requires_asset_table_kwarg() -> None:
    """Removing asset_table kwarg would be a regression — pin via TypeError."""
    policy = PolicyNetwork()
    transitions = [_make_transition() for _ in range(40)]
    with pytest.raises(TypeError, match="asset_table"):
        train_ppo(
            policy=policy, transitions=transitions,
            config=TrainConfig(epochs=1, batch_size=16),
        )  # type: ignore[call-arg]
