"""Tests for the brain RL training driver at tools/ml/train_brain.py.

The driver is a thin orchestration layer over already-tested components
(load_from_shadow_trades, PolicyNetwork, train_ppo). The tests here cover:

  1. CLI argument parsing — the cron passes specific flags we must accept.
  2. The insufficient-data soft-exit path — when _build_buffer returns an
     empty list, the driver must exit 2 and leave no partial output files.
  3. The output filename convention — cron expects
     ppo_policy_<version>.pt and eval_brain_<version>.json.
  4. The eval JSON shape — cron parses specific fields out of it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


# Canonical copy — mounted into container at /app/host-tools via bind mount.
# backend/host-tools/ml/train_brain.py was a stale duplicate and has been
# deleted; tests now point to tools/ml/train_brain.py directly.
_DRIVER_PATH = (
    Path(__file__).resolve().parents[3] / "tools" / "ml" / "train_brain.py"
)


@pytest.fixture(scope="module")
def driver_module():
    """Import tools/ml/train_brain.py without polluting sys.modules."""
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
# 2. Insufficient-data soft-exit (returns 2, no partial files written)


def test_insufficient_transitions_exits_two(driver_module, tmp_path) -> None:
    """When _build_buffer returns an empty list the driver exits 2 — the cron
    treats that as a soft failure (no Telegram alert, no crash, no files)."""
    with patch.object(
        driver_module, "maybe_warm_start", return_value=True,  # skip DB symbol discovery
    ), patch.object(
        driver_module, "_build_buffer",
        new=AsyncMock(return_value=[]),
    ):
        rc = driver_module.main([
            "--out-dir", str(tmp_path),
            "--version-tag", "v1-empty",
        ])
    assert rc == 2
    assert list(tmp_path.glob("ppo_policy_*.pt")) == []
    assert list(tmp_path.glob("eval_brain_*.json")) == []


# ---------------------------------------------------------------------------
# 3. + 4. Output filename + eval JSON shape (with mocked train_ppo)


def test_writes_expected_filenames_when_training_succeeds(
    driver_module, tmp_path,
) -> None:
    """When training completes, the driver must write:
      - ppo_policy_<version>.pt (torch state_dict)
      - eval_brain_<version>.json (training metrics)
    Both with the exact version_tag in the filename."""

    class _FakeTrans:
        symbol = "BTCUSDT"
        asset_id = 0
        opened_at_iso = "2026-01-01T00:00:00+00:00"

    fake_transitions = [_FakeTrans()] * 100

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
        driver_module, "maybe_warm_start", return_value=True,
    ), patch.object(
        driver_module, "_build_buffer",
        new=AsyncMock(return_value=fake_transitions),
    ), patch.object(
        driver_module, "train_ppo", return_value=_Result(),
    ), patch.object(
        driver_module, "evaluate_brain_on_holdout", return_value=None,
    ), patch.object(
        driver_module, "PolicyNetwork", autospec=True,
    ), patch.object(
        driver_module.torch, "save",  # don't actually serialise the stub
        side_effect=lambda obj, path: Path(path).write_bytes(b"stub"),
    ):
        rc = driver_module.main([
            "--out-dir", str(tmp_path),
            "--version-tag", "v1-success",
        ])

    assert rc == 0
    ckpt = tmp_path / "ppo_policy_v1-success.pt"
    eval_json = tmp_path / "eval_brain_v1-success.json"
    assert ckpt.exists()
    assert eval_json.exists()

    payload = json.loads(eval_json.read_text())
    # Fields the cron / register_brain.py reads.
    assert payload["version"] == "v1-success"
    assert payload["train_data_window"]["n_transitions"] == len(fake_transitions)
    assert payload["training"]["epochs_completed"] == 1
    assert payload["training"]["final_policy_loss"] == pytest.approx(0.1234)
    assert payload["training"]["final_value_loss"] == pytest.approx(0.5678)
    assert payload["training"]["final_entropy"] == pytest.approx(0.42)
    assert len(payload["sha256"]) == 64  # sha256 hex of the .pt file


