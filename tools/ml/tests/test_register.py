"""Smoke tests for tools/ml/register.py.

Validates payload assembly + sha256 hashing + HTTP shape without hitting
the real backend. Live integration is exercised manually (one curl) when
the operator runs the activate flow on the Hetzner box.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.ml import register as R


def _write_eval_json(path: Path, version: str = "v1-test") -> dict:
    doc = {
        "model_name": "conv_lstm_predictor",
        "version": version,
        "trained_at": "2026-05-07T18:00:00+00:00",
        "train_data_window": {
            "train_start": "2017-08-17 00:00:00+00:00",
            "train_end": "2022-12-31 23:00:00+00:00",
            "n_train_bars": 50000,
        },
        "regime_results": {
            "bull_breakout": {
                "mae": 0.012, "samples": 4380,
                "passes_acceptance": True,
                "per_output_mae": [0.011, 0.013, 0.012, 0.012],
            },
        },
        "all_regimes_pass": True,
        "acceptance_threshold": 0.015,
    }
    path.write_text(json.dumps(doc))
    return doc


def test_sha256_of_matches_hashlib(tmp_path: Path) -> None:
    payload = b"hello SP-1.1"
    f = tmp_path / "fake.pt"
    f.write_bytes(payload)
    assert R.sha256_of(f) == hashlib.sha256(payload).hexdigest()


def test_sha256_streaming_works_on_multi_chunk_file(tmp_path: Path) -> None:
    """Verify the 1-MiB chunked read produces the same hash as one-shot."""
    blob = b"x" * (3 * 1024 * 1024 + 7)  # 3 MiB + 7 bytes — crosses chunks
    f = tmp_path / "big.pt"
    f.write_bytes(blob)
    assert R.sha256_of(f) == hashlib.sha256(blob).hexdigest()


def test_build_payload_serializes_train_window_to_string(tmp_path: Path) -> None:
    eval_path = tmp_path / "eval.json"
    eval_doc = _write_eval_json(eval_path)
    ckpt_path = tmp_path / "fake.pt"
    ckpt_path.write_bytes(b"fake")

    payload = R.build_payload(
        checkpoint_path=ckpt_path, eval_doc=eval_doc,
        checkpoint_uri="file:///app/data/ml-cache/fake.pt",
        sha256="a" * 64, notes=None,
    )

    assert payload["model_name"] == "conv_lstm_predictor"
    assert payload["version"] == "v1-test"
    assert payload["checkpoint_uri"] == "file:///app/data/ml-cache/fake.pt"
    assert payload["sha256"] == "a" * 64
    assert isinstance(payload["train_data_window"], str)
    # Round-trips back to dict (defensive parse on the server side is fine).
    round_tripped = json.loads(payload["train_data_window"])
    assert round_tripped["n_train_bars"] == 50000
    # eval_results carries through as the regime_results dict.
    assert "bull_breakout" in payload["eval_results"]


def test_register_calls_post_with_bearer_header() -> None:
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"id": 42, "version": "v1-test"}
    sess.post.return_value = resp

    out = R.register(
        base_url="http://localhost:8000", bearer="tok",
        payload={"version": "v1-test"}, session=sess,
    )

    sess.post.assert_called_once()
    url, = sess.post.call_args[0]
    assert url == "http://localhost:8000/api/v1/admin/ml-checkpoints"
    headers = sess.post.call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer tok"
    assert out["id"] == 42


def test_register_propagates_http_error() -> None:
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 422
    resp.text = '{"detail":"sha256 wrong length"}'
    resp.raise_for_status.side_effect = RuntimeError("HTTP 422")
    sess.post.return_value = resp

    with pytest.raises(RuntimeError, match="HTTP 422"):
        R.register(
            base_url="http://localhost:8000", bearer=None,
            payload={"version": "v1-bad"}, session=sess,
        )


def test_activate_passes_force_query_param() -> None:
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"id": 42, "is_active": True}
    sess.patch.return_value = resp

    R.activate(
        base_url="http://localhost:8000", bearer=None,
        checkpoint_id=42, force=True, session=sess,
    )

    params = sess.patch.call_args[1]["params"]
    assert params == {"force": "true"}


def test_activate_omits_force_when_false() -> None:
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"id": 42, "is_active": True}
    sess.patch.return_value = resp

    R.activate(
        base_url="http://localhost:8000", bearer=None,
        checkpoint_id=42, force=False, session=sess,
    )

    params = sess.patch.call_args[1]["params"]
    assert params == {}


def test_main_with_direct_skips_http_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--direct must NOT touch requests.Session — it writes to the DB instead.

    Regression: production registration via HTTP fails with 401 (CF Access).
    --direct skips the HTTP layer and writes via SQLAlchemy. We mock the DB
    helper so the test doesn't need a live Postgres.
    """
    pt = tmp_path / "fake.pt"
    pt.write_bytes(b"fake-checkpoint-bytes")
    eval_path = tmp_path / "eval.json"
    _write_eval_json(eval_path)

    direct_calls: list[dict] = []

    def fake_direct(*, payload, eval_doc, activate_flag, force):
        direct_calls.append({
            "version": payload["version"],
            "activate_flag": activate_flag,
            "force": force,
        })
        return {"id": 99, "version": payload["version"], "is_active": True}

    monkeypatch.setattr(R, "_direct_db_register_and_activate", fake_direct)

    # Make requests.Session blow up if anyone tries to use the HTTP path —
    # tightest possible regression test that --direct really skips HTTP.
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
