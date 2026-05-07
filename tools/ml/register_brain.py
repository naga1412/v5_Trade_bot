"""Register a trained RL brain checkpoint with the running backend.

SP-4 Phase D — completes the brain training pipeline by:
    1. Hashing the .pt file (SHA-256, streaming)
    2. Reading the eval JSON written by ``tools/ml/train_brain.py``
    3. POSTing a new row to ``/api/v1/admin/rl-checkpoints``
    4. (optional) PATCHing ``is_active=true`` with ``?force=true``
       (first-checkpoint bypass — no champion to beat yet)

The checkpoint URI is recorded as
``file:///app/data/rl-cache/<filename>.pt`` to match the path the
backend container sees (``RL_CACHE_DIR`` env, default
``/app/data/rl-cache``). Caller is responsible for SCPing the .pt file
to ``/opt/trading-radar/backend/data/rl-cache/`` on the server BEFORE
running this script — the registration row references the path; it
doesn't upload.

Mirrors ``tools/ml/register.py`` (the SP-1 ConvLSTM registrar), with
two differences:
* posts to ``/api/v1/admin/rl-checkpoints`` (Phase D endpoints)
* the eval JSON shape is the brain's (Sharpe / max_dd / per_asset),
  not the ConvLSTM's (per-regime MAE)

Usage (from inside the backend container):
    docker compose exec -T backend python /app/tools/ml/register_brain.py \\
        --checkpoint /app/data/rl-cache/ppo_policy_v1-20260514-033000.pt \\
        --eval /tmp/eval_brain_v1-20260514-033000.json \\
        --base-url http://localhost:8000 \\
        --activate --force

For nightly cron use: ``backend/scripts/hetzner_brain_cron.sh`` will
shell out to this once Phase E (Telegram approval) wires the trigger
side. For now, register-only (no auto-activate) is the safer default.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):  # 1 MiB chunks
            h.update(chunk)
    return h.hexdigest()


def build_payload(
    *, checkpoint_path: Path, eval_doc: dict, checkpoint_uri: str,
    sha256: str, notes: str | None,
) -> dict:
    """Build the RlCheckpointCreateIn body from the local files."""
    train_window = eval_doc.get("train_data_window", {})
    if isinstance(train_window, dict):
        train_window_str = json.dumps(train_window, default=str)
    else:
        train_window_str = str(train_window)

    return {
        "model_name": eval_doc.get("model_name", "ppo_policy_v1"),
        "version": eval_doc["version"],
        "checkpoint_uri": checkpoint_uri,
        "sha256": sha256,
        "trained_at": eval_doc.get(
            "trained_at", datetime.now(timezone.utc).isoformat()
        ),
        "train_data_window": train_window_str,
        "eval_results": eval_doc.get("backtest_results") or eval_doc.get("eval_results", {}),
        "notes": notes,
    }


def register(
    *, base_url: str, bearer: str | None, payload: dict,
    session: requests.Session,
) -> dict:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    resp = session.post(
        f"{base_url}/api/v1/admin/rl-checkpoints",
        json=payload, headers=headers, timeout=30,
    )
    if resp.status_code >= 400:
        log.error(
            "register POST failed %d: %s", resp.status_code, resp.text[:500],
        )
        resp.raise_for_status()
    return resp.json()


def activate(
    *, base_url: str, bearer: str | None, checkpoint_id: int,
    force: bool, session: requests.Session,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    params = {"force": "true"} if force else {}
    resp = session.patch(
        f"{base_url}/api/v1/admin/rl-checkpoints/{checkpoint_id}",
        json={"is_active": True}, headers=headers, params=params, timeout=30,
    )
    if resp.status_code >= 400:
        log.error(
            "activate PATCH failed %d: %s", resp.status_code, resp.text[:500],
        )
        resp.raise_for_status()
    return resp.json()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tools.ml.register_brain")
    p.add_argument(
        "--checkpoint", required=True,
        help="local path to the .pt file (used for sha256)",
    )
    p.add_argument(
        "--eval", required=True,
        help="local path to the eval JSON written by tools.ml.train_brain",
    )
    p.add_argument(
        "--checkpoint-uri",
        default=None,
        help=(
            "URI the BACKEND will read at startup; defaults to "
            "file:///app/data/rl-cache/<basename> (the path the backend "
            "container sees after host-side SCP to "
            "/opt/trading-radar/backend/data/rl-cache/<basename>)"
        ),
    )
    p.add_argument(
        "--base-url", default="http://localhost:8000",
        help="backend HTTP base; localhost:8000 from inside the container",
    )
    p.add_argument(
        "--bearer", default=None,
        help="optional admin bearer token (skip when running inside the container)",
    )
    p.add_argument(
        "--activate", action="store_true",
        help="immediately PATCH is_active=true after register",
    )
    p.add_argument(
        "--force", action="store_true",
        help="bypass champion-challenger Sharpe gate (first-checkpoint case)",
    )
    p.add_argument("--notes", default=None)
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    ckpt_path = Path(args.checkpoint)
    if not ckpt_path.exists():
        log.error("checkpoint not found: %s", ckpt_path)
        return 1
    eval_path = Path(args.eval)
    if not eval_path.exists():
        log.error("eval json not found: %s", eval_path)
        return 1

    eval_doc = json.loads(eval_path.read_text())
    sha = sha256_of(ckpt_path)
    log.info("sha256(%s) = %s", ckpt_path.name, sha)

    if args.checkpoint_uri:
        ckpt_uri = args.checkpoint_uri
    else:
        ckpt_uri = f"file:///app/data/rl-cache/{ckpt_path.name}"
    log.info("checkpoint_uri = %s", ckpt_uri)

    payload = build_payload(
        checkpoint_path=ckpt_path, eval_doc=eval_doc,
        checkpoint_uri=ckpt_uri, sha256=sha, notes=args.notes,
    )

    sess = requests.Session()
    created = register(
        base_url=args.base_url, bearer=args.bearer,
        payload=payload, session=sess,
    )
    log.info(
        "registered RL checkpoint id=%s version=%s",
        created["id"], created["version"],
    )

    if args.activate:
        result = activate(
            base_url=args.base_url, bearer=args.bearer,
            checkpoint_id=created["id"], force=args.force, session=sess,
        )
        log.info(
            "activated RL checkpoint id=%s is_active=%s",
            result["id"], result["is_active"],
        )
        log.info(
            "Restart backend container so the new checkpoint loads:\n"
            "    docker compose -f /opt/trading-radar/docker-compose.yml "
            "restart backend"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
