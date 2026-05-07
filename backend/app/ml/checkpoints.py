"""Active ML checkpoint loader + module-scope state.

Spec §6.1 — at backend startup, look up the row in `ml_checkpoints` where
`is_active=true AND model_name='conv_lstm_predictor'`. Download the file
from `checkpoint_uri` (B2 bucket URI or local file:// for testing), verify
sha256 matches, then load the state_dict into a fresh ConvLSTMPredictor.

If no active row exists OR the download/verify fails, log a warning and
leave `_active_model=None` — the live-prediction worker (D1) checks
`get_active_model_and_checkpoint()` and gracefully skips ghost-candle
prediction in that case.

Module-state pattern: the loaded model + checkpoint metadata are pinned
to module-scope vars so the worker can read them without going back to
the database on every tick. `set_active`/`clear_active` mutate them
atomically (both or neither).
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import sqlalchemy as sa
import torch
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.model import ConvLSTMPredictor

log = logging.getLogger(__name__)

_active_model: ConvLSTMPredictor | None = None
_active_checkpoint: "ActiveCheckpoint | None" = None


@dataclass(frozen=True)
class ActiveCheckpoint:
    """Lightweight metadata snapshot of the active checkpoint row.

    Held in module state so the worker doesn't need a DB round-trip to
    know which checkpoint id to record on each prediction.
    """

    id: int
    model_name: str
    version: str
    sha256: str
    checkpoint_uri: str


def set_active(model: ConvLSTMPredictor, checkpoint: ActiveCheckpoint) -> None:
    """Atomically pin (model, checkpoint) as the active pair."""
    global _active_model, _active_checkpoint
    _active_model = model
    _active_checkpoint = checkpoint


def clear_active() -> None:
    """Reset module state to empty. Used by tests + on hot-reload errors."""
    global _active_model, _active_checkpoint
    _active_model = None
    _active_checkpoint = None


def get_active_model_and_checkpoint() -> (
    tuple[ConvLSTMPredictor, ActiveCheckpoint] | None
):
    """Return the active (model, checkpoint) pair, or None if not loaded."""
    if _active_model is None or _active_checkpoint is None:
        return None
    return _active_model, _active_checkpoint


def _download_to_local(uri: str, *, dest: Path) -> Path:
    """Download `uri` to `dest`. Supports b2://, s3://, file://.

    For file:// URIs we just return the source path verbatim — no copy
    needed, the caller hashes/loads it directly.
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
    model_name: str = "conv_lstm_predictor",
) -> tuple[ConvLSTMPredictor, ActiveCheckpoint] | None:
    """Look up active checkpoint, download, verify, load. Returns None if absent.

    On success, also pins the loaded pair to module state via `set_active`.
    On any failure (no active row, download error, sha mismatch, torch
    load error), logs a warning and returns None — the worker's graceful
    degradation path takes over.
    """
    row = (
        await session.execute(
            sa.text(
                "SELECT id, model_name, version, checkpoint_uri, sha256 "
                "FROM ml_checkpoints WHERE model_name = :n AND is_active = TRUE "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"n": model_name},
        )
    ).first()
    if row is None:
        log.warning(
            "no active ML checkpoint for %s; ghost candles disabled", model_name
        )
        return None

    ck = ActiveCheckpoint(
        id=row.id,
        model_name=row.model_name,
        version=row.version,
        sha256=row.sha256,
        checkpoint_uri=row.checkpoint_uri,
    )

    cache_dir = Path(os.environ.get("ML_CACHE_DIR", "/app/data/ml-cache"))
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.error("could not create ML cache dir %s: %s", cache_dir, e)
        return None

    local_path = cache_dir / f"{ck.model_name}_{ck.version}.pt"
    parsed = urlparse(ck.checkpoint_uri)
    # For file:// URIs `_download_to_local` returns the source path
    # directly (no copy) so we hash + load that path.
    if parsed.scheme == "file":
        try:
            actual_path = _download_to_local(ck.checkpoint_uri, dest=local_path)
        except Exception as e:  # noqa: BLE001
            log.error("checkpoint resolution failed: %s; ghost candles disabled", e)
            return None
    else:
        if not local_path.exists():
            try:
                _download_to_local(ck.checkpoint_uri, dest=local_path)
            except Exception as e:  # noqa: BLE001
                log.error(
                    "checkpoint download failed: %s; ghost candles disabled", e
                )
                return None
        actual_path = local_path

    actual_sha = hashlib.sha256(actual_path.read_bytes()).hexdigest()
    if actual_sha != ck.sha256:
        log.error(
            "sha256 mismatch for %s: expected %s, got %s; ghost candles disabled",
            ck.checkpoint_uri,
            ck.sha256,
            actual_sha,
        )
        return None

    try:
        model = ConvLSTMPredictor()
        # weights_only=True is the safe load mode — only allows tensors
        # in the state dict, no arbitrary pickle code execution.
        state = torch.load(actual_path, map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        model.eval()
    except Exception as e:  # noqa: BLE001
        log.error("torch load failed: %s; ghost candles disabled", e)
        return None

    set_active(model, ck)
    log.info("loaded active checkpoint %s v%s", ck.model_name, ck.version)
    return model, ck
