"""Tests for the brain RL training driver at host-tools/ml/train_brain.py.

The driver is a thin orchestration layer over already-tested components
(load_from_shadow_trades, PolicyNetwork, train_ppo). The tests here cover:

  1. CLI argument parsing — the cron passes specific flags we must accept.
  2. The insufficient-data soft-exit path — when the transition buffer is
     empty or below MIN_TRANSITIONS_TO_TRAIN, the driver must exit 0
     (not 1) and leave no partial output files behind.
  3. The output filename convention — cron expects
     ppo_policy_<version>.pt and eval_brain_<version>.json.
  4. The eval JSON shape — cron parses specific fields out of it.

Heavy end-to-end (real DB + real PPO update) lives elsewhere; this file
sticks to fast unit-level checks.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


# Locate the driver module dynamically — it lives under host-tools/, which
# is NOT on the default package path. Loading by spec keeps this test
# decoupled from any future host-tools package layout.
_DRIVER_PATH = (
    Path(__file__).parent.parent.parent / "host-tools" / "ml" / "train_brain.py"
)


@pytest.fixture(scope="module")
def driver_module():
    """Import host-tools/ml/train_brain.py without polluting sys.modules."""
    spec = importlib.util.spec_from_file_location("train_brain", _DRIVER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["train_brain"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("train_brain", None)


# ---------------------------------------------------------------------------
# 1. CLI argument parsing


def test_parser_accepts_canonical_cron_invocation(driver_module) -> None:
    """The nightly cron in scripts/hetzner_brain_cron.sh invokes:
       --window-days N --out-dir DIR --device cpu --version-tag VTAG
       plus optionally --warm-start PATH. The parser must accept it
       without any "unknown argument" errors."""
    parser = driver_module._build_arg_parser()
    args = parser.parse_args([
        "--window-days", "365",
        "--out-dir", "/tmp/rl-cache",
        "--device", "cpu",
        "--version-tag", "v1-test",
    ])
    assert args.window_days == 365
    assert args.out_dir == Path("/tmp/rl-cache")
    assert args.device == "cpu"
    assert args.version_tag == "v1-test"
    assert args.warm_start is None


def test_parser_accepts_warm_start_flag(driver_module) -> None:
    parser = driver_module._build_arg_parser()
    args = parser.parse_args([
        "--out-dir", "/tmp/x",
        "--version-tag", "v1",
        "--warm-start", "/tmp/champion.pt",
    ])
    assert args.warm_start == Path("/tmp/champion.pt")


def test_parser_rejects_unknown_device(driver_module) -> None:
    parser = driver_module._build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "--out-dir", "/tmp/x",
            "--version-tag", "v1",
            "--device", "metal",
        ])


def test_parser_requires_version_tag(driver_module) -> None:
    parser = driver_module._build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--out-dir", "/tmp/x"])


# ---------------------------------------------------------------------------
# 2. Insufficient-data soft-exit


def test_insufficient_transitions_exits_zero(driver_module, tmp_path) -> None:
    """When the loaded transition list is empty (or below the threshold),
    the driver must exit 0 — the cron treats that as a soft failure
    (no Telegram alert, no candidate registered, no crash). The threshold
    is configurable via --min-transitions."""
    with patch.object(
        driver_module, "_load_transitions_async",
        new=AsyncMock(return_value=[]),
    ):
        rc = driver_module.main([
            "--out-dir", str(tmp_path),
            "--version-tag", "v1-empty",
            "--min-transitions", "50",
        ])
    assert rc == 0
    # No checkpoint or eval JSON should have been written on the soft-exit path.
    assert list(tmp_path.glob("ppo_policy_*.pt")) == []
    assert list(tmp_path.glob("eval_brain_*.json")) == []


def test_just_below_threshold_exits_zero(driver_module, tmp_path) -> None:
    """Boundary: --min-transitions=10 and 9 transitions in the buffer."""
    fake_transitions = [None] * 9  # type: ignore[list-item]
    with patch.object(
        driver_module, "_load_transitions_async",
        new=AsyncMock(return_value=fake_transitions),
    ):
        rc = driver_module.main([
            "--out-dir", str(tmp_path),
            "--version-tag", "v1-boundary",
            "--min-transitions", "10",
        ])
    assert rc == 0
    assert list(tmp_path.glob("ppo_policy_*.pt")) == []


# ---------------------------------------------------------------------------
# 3. + 4. Output filename + eval JSON shape (with mocked train_ppo)


def test_writes_expected_filenames_when_training_succeeds(
    driver_module, tmp_path,
) -> None:
    """When training completes, the driver must write:
      - ppo_policy_<version>.pt (torch state_dict)
      - eval_brain_<version>.json (training metrics)
    Both with the exact version_tag in the filename."""
    fake_transitions = [object()] * 100  # any non-empty list

    # Fake PPO result with one epoch of history.
    class _Eph:
        def __init__(self) -> None:
            self.epoch = 0
            self.policy_loss = 0.1234
            self.value_loss = 0.5678
            self.entropy = 0.42
            self.total_loss = 0.99

    class _Result:
        def __init__(self) -> None:
            self.epochs_completed = 1
            self.history = [_Eph()]
            self.final_state_dict = {"layer.weight": "stub"}

    with patch.object(
        driver_module, "_load_transitions_async",
        new=AsyncMock(return_value=fake_transitions),
    ), patch.object(
        driver_module, "train_ppo", return_value=_Result(),
    ), patch.object(
        driver_module, "PolicyNetwork", autospec=True,
    ), patch.object(
        driver_module.torch, "save",  # don't actually serialise the stub
        side_effect=lambda obj, path: Path(path).write_bytes(b"stub"),
    ):
        rc = driver_module.main([
            "--out-dir", str(tmp_path),
            "--version-tag", "v1-success",
            "--min-transitions", "50",
        ])

    assert rc == 0
    ckpt = tmp_path / "ppo_policy_v1-success.pt"
    eval_json = tmp_path / "eval_brain_v1-success.json"
    assert ckpt.exists()
    assert eval_json.exists()

    payload = json.loads(eval_json.read_text())
    # Fields the cron / register_brain.py reads.
    assert payload["version_tag"] == "v1-success"
    assert payload["n_transitions"] == len(fake_transitions)
    assert payload["epochs_completed"] == 1
    assert payload["final_policy_loss"] == pytest.approx(0.1234)
    assert payload["final_value_loss"] == pytest.approx(0.5678)
    assert payload["final_entropy"] == pytest.approx(0.42)
    assert payload["checkpoint_path"].endswith("ppo_policy_v1-success.pt")
    assert len(payload["checkpoint_sha256"]) == 64  # sha256 hex


def test_writes_no_files_when_state_dict_missing(driver_module, tmp_path) -> None:
    """If train_ppo somehow returns final_state_dict=None, refuse to write
    anything and exit non-zero — silently writing an empty checkpoint
    would let register_brain.py promote a useless candidate."""
    fake_transitions = [object()] * 100

    class _Result:
        epochs_completed = 1
        history: list[object] = []
        final_state_dict = None

    with patch.object(
        driver_module, "_load_transitions_async",
        new=AsyncMock(return_value=fake_transitions),
    ), patch.object(
        driver_module, "train_ppo", return_value=_Result(),
    ), patch.object(
        driver_module, "PolicyNetwork", autospec=True,
    ):
        rc = driver_module.main([
            "--out-dir", str(tmp_path),
            "--version-tag", "v1-broken",
            "--min-transitions", "50",
        ])

    assert rc == 1
    assert list(tmp_path.glob("ppo_policy_*.pt")) == []
    assert list(tmp_path.glob("eval_brain_*.json")) == []
