"""Register a trained ML checkpoint with the running backend; optionally activate.

SP-1.1 — completes the training pipeline by:
    1. Hashing the .pt file (SHA-256)
    2. Reading the eval JSON from the same run
    3. POSTing a new row to /api/v1/admin/ml-checkpoints
    4. (optional) PATCHing is_active=true with ?force=true (first-checkpoint case)

The checkpoint URI is recorded as ``file:///app/data/ml-cache/<filename>.pt``
to match the path the backend container can see (its ``ML_CACHE_DIR`` env or
default ``/app/data/ml-cache``). Caller is responsible for SCPing the .pt file
to ``/opt/trading-radar/backend/data/ml-cache/`` on the server BEFORE running
this script — the registration row references the path; it doesn't upload.

Usage:
    # On the Hetzner server, after SCPing the .pt:
    python -m tools.ml.register \\
        --checkpoint /opt/trading-radar/backend/data/ml-cache/conv_lstm_v1.pt \\
        --eval /tmp/eval_v1-20260507-180000.json \\
        --base-url http://localhost:8000 \\
        --bearer "$ADMIN_BEARER" \\
        --activate --force

The ``--bearer`` value is whatever the admin's Cloudflare Access JWT or the
internal admin bearer token resolves to; for SP-1.1 the simplest path is to
run this script from the backend container itself with ``--internal`` which
bypasses Cloudflare Access by hitting the FastAPI app directly.

Requires: requests.
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
    """Build the MlCheckpointCreateIn body from the local files."""
    train_window = eval_doc.get("train_data_window", {})
    if isinstance(train_window, dict):
        # Pydantic schema expects a string. Compact JSON keeps it parseable.
        train_window_str = json.dumps(train_window, default=str)
    else:
        train_window_str = str(train_window)

    return {
        "model_name": eval_doc.get("model_name", "conv_lstm_predictor"),
        "version": eval_doc["version"],
        "checkpoint_uri": checkpoint_uri,
        "sha256": sha256,
        "trained_at": eval_doc.get(
            "trained_at", datetime.now(timezone.utc).isoformat()
        ),
        "train_data_window": train_window_str,
        "eval_results": eval_doc.get("regime_results", {}),
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
        f"{base_url}/api/v1/admin/ml-checkpoints",
        json=payload, headers=headers, timeout=30,
    )
    if resp.status_code >= 400:
        log.error("register POST failed %d: %s", resp.status_code, resp.text[:500])
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
        f"{base_url}/api/v1/admin/ml-checkpoints/{checkpoint_id}",
        json={"is_active": True}, headers=headers, params=params, timeout=30,
    )
    if resp.status_code >= 400:
        log.error("activate PATCH failed %d: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
    return resp.json()


def _direct_db_register_and_activate(
    *, payload: dict, eval_doc: dict, activate_flag: bool, force: bool,
) -> dict:
    """Write the row directly via SQLAlchemy, bypassing the HTTP API.

    Used when the script runs INSIDE the backend container with
    ``--direct``. Skips Cloudflare Access auth entirely (the HTTP path
    requires a valid CF JWT in production, which scripts inside the
    container can't easily obtain). Mirrors the SQL the admin_ml route
    would run.

    Returns ``{"id": <new_id>, "version": <ver>, "is_active": <bool>}``
    so the rest of main() can use the same shape as the HTTP path.
    """
    import asyncio
    from datetime import datetime, timezone
    from sqlalchemy import text

    # Late import — only needed for direct mode and only resolves when
    # PYTHONPATH includes /app (i.e. inside the backend container).
    from app.db.session import get_session_factory  # type: ignore[import-not-found]

    async def _go() -> dict:
        sf = get_session_factory()
        async with sf() as s:
            if activate_flag and force:
                # Mirror the champion deactivation the HTTP PATCH does
                # under the hood when activating with force=true.
                await s.execute(
                    text(
                        "UPDATE ml_checkpoints SET is_active=FALSE, "
                        "deactivated_at=NOW() "
                        "WHERE model_name=:m AND is_active=TRUE"
                    ),
                    {"m": payload["model_name"]},
                )

            # Parse trained_at ISO into a real datetime — asyncpg/Postgres
            # rejects ISO strings for TIMESTAMPTZ binds (PR #46 fixed this
            # for predictions; same constraint here).
            trained_at_raw = payload.get("trained_at")
            if isinstance(trained_at_raw, str):
                trained_at = datetime.fromisoformat(trained_at_raw)
            else:
                trained_at = trained_at_raw or datetime.now(timezone.utc)

            r = await s.execute(
                text(
                    "INSERT INTO ml_checkpoints "
                    "(model_name, version, checkpoint_uri, sha256, "
                    " trained_at, train_data_window, eval_results, "
                    " is_active, activated_at, notes) "
                    "VALUES (:m, :v, :u, :h, :t, :w, CAST(:e AS JSONB), "
                    "        :a, CASE WHEN :a THEN NOW() ELSE NULL END, :n) "
                    "RETURNING id, is_active"
                ),
                {
                    "m": payload["model_name"],
                    "v": payload["version"],
                    "u": payload["checkpoint_uri"],
                    "h": payload["sha256"],
                    "t": trained_at,
                    "w": payload["train_data_window"],
                    "e": json.dumps(payload.get("eval_results", {})),
                    "a": activate_flag,
                    "n": payload.get("notes")
                    or "registered via tools.ml.register --direct",
                },
            )
            row = r.first()
            await s.commit()
            return {
                "id": row.id,
                "version": payload["version"],
                "is_active": row.is_active,
            }

    return asyncio.run(_go())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tools.ml.register")
    p.add_argument(
        "--checkpoint", required=True,
        help="local path to the .pt file (used for sha256)",
    )
    p.add_argument(
        "--eval", required=True,
        help="local path to the eval JSON written by tools.ml.train",
    )
    p.add_argument(
        "--checkpoint-uri",
        default=None,
        help=(
            "URI the BACKEND will read at startup; defaults to "
            "file:///app/data/ml-cache/<basename> (the path the backend "
            "container sees after host-side SCP to "
            "/opt/trading-radar/backend/data/ml-cache/<basename>)"
        ),
    )
    p.add_argument(
        "--base-url", default="http://localhost:8000",
        help="backend HTTP base; localhost:8000 from inside the box",
    )
    p.add_argument(
        "--bearer", default=None,
        help="optional admin bearer token for the HTTP path",
    )
    p.add_argument(
        "--direct", action="store_true",
        help=(
            "bypass HTTP and write directly to ml_checkpoints via "
            "SQLAlchemy. Run inside the backend container with "
            "PYTHONPATH=/app so app.db.session imports resolve. "
            "Avoids the Cloudflare Access JWT requirement that the "
            "HTTP route enforces in production."
        ),
    )
    p.add_argument(
        "--activate", action="store_true",
        help="immediately mark is_active=true after register",
    )
    p.add_argument(
        "--force", action="store_true",
        help="bypass champion-challenger gate (first-checkpoint case)",
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
        ckpt_uri = f"file:///app/data/ml-cache/{ckpt_path.name}"
    log.info("checkpoint_uri = %s", ckpt_uri)

    payload = build_payload(
        checkpoint_path=ckpt_path, eval_doc=eval_doc,
        checkpoint_uri=ckpt_uri, sha256=sha, notes=args.notes,
    )

    if args.direct:
        result = _direct_db_register_and_activate(
            payload=payload, eval_doc=eval_doc,
            activate_flag=args.activate, force=args.force,
        )
        log.info(
            "[direct] registered+activated id=%s version=%s is_active=%s",
            result["id"], result["version"], result["is_active"],
        )
        log.info(
            "Restart backend container so the new checkpoint loads:\n"
            "    docker compose -f /opt/trading-radar/docker-compose.yml "
            "restart backend"
        )
        return 0

    sess = requests.Session()
    created = register(
        base_url=args.base_url, bearer=args.bearer,
        payload=payload, session=sess,
    )
    log.info("registered checkpoint id=%s version=%s",
             created["id"], created["version"])

    if args.activate:
        result = activate(
            base_url=args.base_url, bearer=args.bearer,
            checkpoint_id=created["id"], force=args.force, session=sess,
        )
        log.info("activated checkpoint id=%s is_active=%s",
                 result["id"], result["is_active"])
        log.info(
            "Restart backend container so the new checkpoint loads:\n"
            "    docker compose -f /opt/trading-radar/docker-compose.yml "
            "restart backend"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
