"""Active RL checkpoint loader + module-scope state (SP-4 Phase A6).

Mirror of :mod:`app.ml.checkpoints` (the SP-1 ConvLSTM loader) but for
the L10 PPO policy stored in the ``rl_checkpoints`` table from migration
0015. Spec sec 6.1 — at backend startup, look up the row in
``rl_checkpoints`` where ``is_active=true AND model_name='ppo_policy_v1'``,
download the file from ``checkpoint_uri``, verify sha256, then load the
state_dict into a fresh :class:`PolicyNetwork` (Phase B builds the
network module; this loader stays generic).

If no active row exists OR the download/verify fails, log a warning and
leave ``_active_policy=None`` — the live-prediction path checks
:func:`get_active_policy_and_checkpoint` and gracefully falls back to
the equal-weight aggregation per spec sec 6.1 graceful-degradation.

Module-state pattern: the loaded model + checkpoint metadata pin to
module-scope vars so the per-tick predictor doesn't go back to the DB
or disk on every call. ``set_active``/``clear_active`` mutate atomically.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import sqlalchemy as sa
import torch
from sqlalchemy.ext.asyncio import AsyncSession


log = logging.getLogger(__name__)


_active_policy: Any = None  # nn.Module — left untyped here so this module
                            # doesn't import the (Phase B) PolicyNetwork
_active_checkpoint: "ActiveRlCheckpoint | None" = None


@dataclass(frozen=True)
class ActiveRlCheckpoint:
    """Lightweight metadata snapshot of the active rl_checkpoints row.

    Held in module state so the predictor doesn't need a DB round-trip
    to know which checkpoint id to record on each ``brain_decisions``
    insert.
    """

    id: int
    model_name: str
    version: str
    sha256: str
    checkpoint_uri: str


def set_active(model: Any, checkpoint: ActiveRlCheckpoint) -> None:
    """Atomically pin (model, checkpoint) as the active pair."""
    global _active_policy, _active_checkpoint
    _active_policy = model
    _active_checkpoint = checkpoint


def clear_active() -> None:
    """Reset module state to empty. Used by tests + on hot-reload errors."""
    global _active_policy, _active_checkpoint
    _active_policy = None
    _active_checkpoint = None


def get_active_policy_and_checkpoint() -> tuple[Any, ActiveRlCheckpoint] | None:
    """Return the active (policy, checkpoint) pair, or None if not loaded."""
    if _active_policy is None or _active_checkpoint is None:
        return None
    return _active_policy, _active_checkpoint


def _download_to_local(uri: str, *, dest: Path) -> Path:
    """Download ``uri`` to ``dest``. Supports b2://, s3://, file://.

    For file:// URIs we just return the source path verbatim — no copy
    needed, the caller hashes/loads it directly. Mirrors the SP-1
    ml/checkpoints._download_to_local helper.
    """
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        # Windows file URIs of the form file:///A:/path land in
        # parsed.path with a leading slash. Strip it for cross-platform
        # compatibility (no-op on POSIX absolute paths).
        raw = parsed.path
        if os.name == "nt" and raw.startswith("/") and len(raw) > 2 and raw[2] == ":":
            raw = raw[1:]
        return Path(raw)
    if parsed.scheme in ("b2", "s3"):
        import boto3  # local import — heavy dep we only need in prod
        endpoint = os.environ.get(
            "B2_S3_ENDPOINT", "https://s3.us-west-002.backblazeb2.com",
        )
        s3 = boto3.client("s3", endpoint_url=endpoint)
        bucket, key = parsed.netloc, parsed.path.lstrip("/")
        s3.download_file(bucket, key, str(dest))
        return dest
    raise ValueError(f"unsupported checkpoint URI scheme: {parsed.scheme}")


async def load_active_checkpoint(
    session: AsyncSession,
    *,
    model_name: str = "ppo_policy_v1",
    model_factory: Any = None,
) -> tuple[Any, ActiveRlCheckpoint] | None:
    """Look up active rl_checkpoint, download, verify, load.

    Args:
        session: live AsyncSession.
        model_name: which model to load. Defaults to ``ppo_policy_v1``.
        model_factory: zero-arg callable returning a fresh ``nn.Module``
            with matching architecture. Required for state_dict load;
            kept as a parameter (not import) so this loader stays
            decoupled from the Phase B PolicyNetwork class.

    Returns:
        (model, ActiveRlCheckpoint) on success; None on any failure
        (no active row, download error, sha mismatch, torch load error).
        On success, also pins the loaded pair to module state via
        :func:`set_active`.
    """
    if model_factory is None:
        log.error(
            "load_active_checkpoint requires a model_factory; got None — "
            "RL inference disabled this run",
        )
        return None

    row = (
        await session.execute(
            sa.text(
                "SELECT id, model_name, version, checkpoint_uri, sha256 "
                "FROM rl_checkpoints WHERE model_name = :n AND is_active = TRUE "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"n": model_name},
        )
    ).first()
    if row is None:
        log.warning(
            "no active RL checkpoint for %s; falling back to equal-weight",
            model_name,
        )
        return None

    ck = ActiveRlCheckpoint(
        id=row.id,
        model_name=row.model_name,
        version=row.version,
        sha256=row.sha256,
        checkpoint_uri=row.checkpoint_uri,
    )

    cache_dir = Path(os.environ.get("RL_CACHE_DIR", "/app/data/rl-cache"))
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.error("could not create RL cache dir %s: %s", cache_dir, e)
        return None

    local_path = cache_dir / f"{ck.model_name}_{ck.version}.pt"
    parsed = urlparse(ck.checkpoint_uri)
    if parsed.scheme == "file":
        try:
            actual_path = _download_to_local(ck.checkpoint_uri, dest=local_path)
        except Exception as e:  # noqa: BLE001
            log.error(
                "checkpoint resolution failed: %s; RL inference disabled", e,
            )
            return None
    else:
        if not local_path.exists():
            try:
                _download_to_local(ck.checkpoint_uri, dest=local_path)
            except Exception as e:  # noqa: BLE001
                log.error(
                    "checkpoint download failed: %s; RL inference disabled", e,
                )
                return None
        actual_path = local_path

    try:
        actual_sha = hashlib.sha256(actual_path.read_bytes()).hexdigest()
    except OSError as e:
        log.error(
            "could not read checkpoint file %s: %s; RL inference disabled",
            actual_path, e,
        )
        return None
    if actual_sha != ck.sha256:
        log.error(
            "sha256 mismatch for %s: expected %s, got %s; RL inference disabled",
            ck.checkpoint_uri, ck.sha256, actual_sha,
        )
        return None

    try:
        model = model_factory()
        # train_brain.py saves {"policy": policy.state_dict(), "asset_table": ...}
        # so we must extract the "policy" key before calling load_state_dict.
        # weights_only=False is safe here — file:// URI is our own container FS.
        state = torch.load(actual_path, map_location="cpu", weights_only=False)
        model.load_state_dict(state["policy"])
        model.eval()
    except Exception as e:  # noqa: BLE001
        log.error("torch load failed: %s; RL inference disabled", e)
        return None

    set_active(model, ck)
    log.info("loaded active RL checkpoint %s v%s", ck.model_name, ck.version)
    return model, ck


__all__ = [
    "ActiveRlCheckpoint",
    "clear_active",
    "get_active_policy_and_checkpoint",
    "load_active_checkpoint",
    "set_active",
]
