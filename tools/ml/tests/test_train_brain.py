"""Smoke tests for tools/ml/train_brain.py (SP-4 Phase B5).

Covers the helpers that are testable without spinning up a real backend
DB or running a full PPO loop:
  - device picker
  - checkpoint save / sha256
  - warm-start cold-vs-warm decision
  - eval doc shape

End-to-end "train a small brain on synthetic data" is already covered
by the app.rl.ppo unit tests — this file focuses on the CLI plumbing.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import torch

# Add backend to path so app.rl.* imports resolve.
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.rl.adapter import AssetEmbeddingTable  # noqa: E402
from app.rl.policy import PolicyNetwork  # noqa: E402


def _load_train_brain():
    """Load the repo-root tools/ml/train_brain.py by absolute file path.

    There's a name collision with backend/tools/ml/ (legacy SP-1 location)
    so we side-step the package import system.
    """
    path = _REPO_ROOT / "tools" / "ml" / "train_brain.py"
    spec = importlib.util.spec_from_file_location("_sp4_train_brain", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TB = _load_train_brain()


def test_pick_device_cpu_explicit() -> None:
    assert TB.pick_device("cpu") == torch.device("cpu")


def test_pick_device_auto_falls_back_to_cpu_when_no_cuda() -> None:
    with patch.object(torch.cuda, "is_available", return_value=False):
        assert TB.pick_device("auto") == torch.device("cpu")


def test_pick_device_auto_picks_cuda_when_available() -> None:
    with patch.object(torch.cuda, "is_available", return_value=True):
        assert TB.pick_device("auto") == torch.device("cuda")


def test_save_checkpoint_writes_file_and_returns_sha(tmp_path: Path) -> None:
    policy = PolicyNetwork()
    table = AssetEmbeddingTable()
    table.register_asset("BTC/USDT")

    ckpt = tmp_path / "test.pt"
    sha = TB.save_checkpoint(policy=policy, asset_table=table, ckpt_path=ckpt)

    assert ckpt.exists()
    assert ckpt.stat().st_size > 0
    assert len(sha) == 64
    # sha matches the file we wrote
    import hashlib
    assert hashlib.sha256(ckpt.read_bytes()).hexdigest() == sha


def test_save_checkpoint_round_trips_through_warm_start(tmp_path: Path) -> None:
    """Save → load reproduces both the policy weights and the symbol map."""
    p1 = PolicyNetwork()
    t1 = AssetEmbeddingTable()
    t1.register_asset("BTC/USDT")
    t1.register_asset("ETH/USDT")
    with torch.no_grad():
        p1.policy_head.bias[2] = 0.7

    ckpt = tmp_path / "warm.pt"
    TB.save_checkpoint(policy=p1, asset_table=t1, ckpt_path=ckpt)

    p2 = PolicyNetwork()
    t2 = AssetEmbeddingTable()
    used_warm = TB.maybe_warm_start(p2, t2, ckpt)

    assert used_warm is True
    assert torch.allclose(p1.policy_head.bias, p2.policy_head.bias)
    assert t2.symbol_to_id == {"BTC/USDT": 0, "ETH/USDT": 1}


def test_maybe_warm_start_returns_false_when_path_missing(tmp_path: Path) -> None:
    p = PolicyNetwork()
    t = AssetEmbeddingTable()
    used = TB.maybe_warm_start(p, t, tmp_path / "does_not_exist.pt")
    assert used is False


def test_maybe_warm_start_returns_false_when_path_none() -> None:
    p = PolicyNetwork()
    t = AssetEmbeddingTable()
    used = TB.maybe_warm_start(p, t, None)
    assert used is False


def test_build_eval_doc_has_expected_keys() -> None:
    """Returned doc has the keys register_brain.py will read."""
    from app.rl.ppo import EpochStats
    from app.rl.replay_buffer import Transition
    import numpy as np

    transitions = [
        Transition(
            obs=np.zeros(58, dtype=np.float32),
            action="LONG_FULL",
            reward=0.5,
            next_obs=np.zeros(58, dtype=np.float32),
            done=True,
            asset_id=0,
            symbol="BTC/USDT",
            opened_at_iso="2024-04-01T12:00:00+00:00",
        ),
    ]
    history = [
        EpochStats(epoch=1, policy_loss=0.1, value_loss=0.2, entropy=1.5, total_loss=0.0),
    ]
    doc = TB.build_eval_doc(
        version="v1-test",
        transitions=transitions,
        train_history=history,
        all_pass=False,
    )
    assert doc["model_name"] == "ppo_policy_v1"
    assert doc["version"] == "v1-test"
    assert doc["train_data_window"]["n_transitions"] == 1
    assert doc["training"]["epochs_completed"] == 1
    assert doc["training"]["final_entropy"] == 1.5
    # The doc must be JSON-serializable for register_brain.py.
    assert json.dumps(doc, default=str)
