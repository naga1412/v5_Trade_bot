"""Smoke tests for tools/ml/register_brain.py (SP-4 Phase D).

Mirrors test_register.py for the brain registrar — covers payload
assembly, sha256 streaming, HTTP shape for register + activate.
End-to-end smoke (full register → activate → backend reload) is
manual; this file just guards the CLI plumbing.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_register_brain():
    """Direct file-path import to dodge the backend/tools/ml legacy package."""
    path = _REPO_ROOT / "tools" / "ml" / "register_brain.py"
    spec = importlib.util.spec_from_file_location("_sp4_register_brain", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = _load_register_brain()


def _write_eval_json(path: Path, version: str = "v1-test") -> dict:
    doc = {
        "model_name": "ppo_policy_v1",
        "version": version,
        "trained_at": "2026-05-14T03:30:00+00:00",
        "train_data_window": {
            "n_transitions": 412,
            "asset_count": 30,
            "first_open": "2024-04-01T12:00:00+00:00",
            "last_open": "2025-04-01T12:00:00+00:00",
        },
        "training": {
            "epochs_completed": 30,
            "final_policy_loss": -0.034,
            "final_value_loss": 0.012,
            "final_entropy": 0.92,
        },
        "all_regimes_pass": True,
        "backtest_results": {
            "sharpe": 1.87, "sortino": 2.4, "max_drawdown": 0.112,
            "n_trades": 412, "vs_baseline_sharpe_pct": 15.4,
        },
    }
    path.write_text(json.dumps(doc))
    return doc


def test_sha256_of_matches_hashlib(tmp_path: Path) -> None:
    payload = b"hello SP-4"
    f = tmp_path / "fake.pt"
    f.write_bytes(payload)
    assert R.sha256_of(f) == hashlib.sha256(payload).hexdigest()


def test_sha256_streaming_works_on_multi_chunk_file(tmp_path: Path) -> None:
    blob = b"x" * (3 * 1024 * 1024 + 7)  # 3 MiB + 7 bytes
    f = tmp_path / "big.pt"
    f.write_bytes(blob)
    assert R.sha256_of(f) == hashlib.sha256(blob).hexdigest()


def test_build_payload_serialises_train_window_to_string(tmp_path: Path) -> None:
    eval_path = tmp_path / "eval.json"
    eval_doc = _write_eval_json(eval_path)
    ckpt = tmp_path / "fake.pt"
    ckpt.write_bytes(b"fake")

    payload = R.build_payload(
        checkpoint_path=ckpt, eval_doc=eval_doc,
        checkpoint_uri="file:///app/data/rl-cache/fake.pt",
        sha256="a" * 64, notes=None,
    )
    assert payload["model_name"] == "ppo_policy_v1"
    assert payload["version"] == "v1-test"
    assert payload["checkpoint_uri"] == "file:///app/data/rl-cache/fake.pt"
    assert payload["sha256"] == "a" * 64
    assert isinstance(payload["train_data_window"], str)
    round_tripped = json.loads(payload["train_data_window"])
    assert round_tripped["n_transitions"] == 412
    # eval_results pulls from backtest_results when present.
    assert payload["eval_results"]["sharpe"] == 1.87


def test_build_payload_falls_back_to_eval_results_when_no_backtest() -> None:
    """Pre-Phase-B5 eval JSONs only have training metrics, no backtest_results."""
    eval_doc = {
        "model_name": "ppo_policy_v1",
        "version": "v1-old",
        "trained_at": "2026-05-14T03:30:00+00:00",
        "train_data_window": "manual",
        "eval_results": {"placeholder": True},
    }
    ckpt_path = Path("/tmp/fake.pt")  # not opened
    payload = R.build_payload(
        checkpoint_path=ckpt_path, eval_doc=eval_doc,
        checkpoint_uri="file:///app/data/rl-cache/fake.pt",
        sha256="b" * 64, notes=None,
    )
    assert payload["eval_results"] == {"placeholder": True}


def test_register_calls_post_with_bearer_header() -> None:
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"id": 7, "version": "v1-test"}
    sess.post.return_value = resp

    out = R.register(
        base_url="http://localhost:8000", bearer="tok",
        payload={"version": "v1-test"}, session=sess,
    )

    sess.post.assert_called_once()
    url, = sess.post.call_args[0]
    assert url == "http://localhost:8000/api/v1/admin/rl-checkpoints"
    headers = sess.post.call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer tok"
    assert out["id"] == 7


def test_register_propagates_http_error() -> None:
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 422
    resp.text = '{"detail":"bad sha"}'
    resp.raise_for_status.side_effect = RuntimeError("HTTP 422")
    sess.post.return_value = resp

    with pytest.raises(RuntimeError, match="HTTP 422"):
        R.register(
            base_url="http://localhost:8000", bearer=None,
            payload={"version": "bad"}, session=sess,
        )


def test_activate_passes_force_query_param() -> None:
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"id": 7, "is_active": True}
    sess.patch.return_value = resp

    R.activate(
        base_url="http://localhost:8000", bearer=None,
        checkpoint_id=7, force=True, session=sess,
    )
    params = sess.patch.call_args[1]["params"]
    assert params == {"force": "true"}


def test_activate_omits_force_when_false() -> None:
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"id": 7, "is_active": True}
    sess.patch.return_value = resp

    R.activate(
        base_url="http://localhost:8000", bearer=None,
        checkpoint_id=7, force=False, session=sess,
    )
    params = sess.patch.call_args[1]["params"]
    assert params == {}


def test_activate_url_targets_rl_endpoint() -> None:
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"id": 7, "is_active": True}
    sess.patch.return_value = resp

    R.activate(
        base_url="http://localhost:8000", bearer=None,
        checkpoint_id=7, force=True, session=sess,
    )
    url, = sess.patch.call_args[0]
    assert url.endswith("/api/v1/admin/rl-checkpoints/7")


def test_main_with_direct_skips_http_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--direct must NOT touch requests.Session for the brain registrar."""
    pt = tmp_path / "fake.pt"
    pt.write_bytes(b"fake-brain-bytes")
    eval_path = tmp_path / "eval.json"
    _write_eval_json(eval_path)

    direct_calls: list[dict] = []

    def fake_direct(*, payload, activate_flag, force):
        direct_calls.append({
            "version": payload["version"],
            "activate_flag": activate_flag,
            "force": force,
        })
        return {"id": 7, "version": payload["version"], "is_active": True}

    monkeypatch.setattr(R, "_direct_db_register_and_activate", fake_direct)

    def boom(*a, **kw):
        raise AssertionError("HTTP path must not run when --direct is set")
    monkeypatch.setattr(R.requests, "Session", boom)

    rc = R.main([
        "--checkpoint", str(pt),
        "--eval", str(eval_path),
        "--direct", "--activate", "--force",
    ])
    assert rc == 0
    assert len(direct_calls) == 1
    assert direct_calls[0]["activate_flag"] is True
    assert direct_calls[0]["force"] is True
    assert direct_calls[0]["version"] == "v1-test"
