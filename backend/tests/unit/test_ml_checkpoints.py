"""SP-1 Phase D6: active ML checkpoint loader + module-state pattern.

Spec §6.1 — at backend startup, look up `ml_checkpoints` row where
is_active=true AND model_name='conv_lstm_predictor', download from
`checkpoint_uri` (B2 in prod, file:// in tests), verify sha256, load into
a fresh ConvLSTMPredictor and pin to module-scope state.

If no active row exists OR the download/verify fails, return None and
leave _active_model untouched. The worker (D1) checks
`get_active_model_and_checkpoint() is None` and gracefully skips ghost
candle prediction in that case.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import sqlalchemy as sa
import torch
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.ml.checkpoints import (
    ActiveCheckpoint,
    clear_active,
    get_active_model_and_checkpoint,
    load_active_checkpoint,
    set_active,
)
from app.ml.model import ConvLSTMPredictor


CREATE_ML_CHECKPOINTS_TABLE = (
    "CREATE TABLE ml_checkpoints ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, model_name TEXT, version TEXT, "
    "checkpoint_uri TEXT, sha256 TEXT, trained_at TEXT, "
    "train_data_window TEXT, eval_results TEXT, "
    "is_active INTEGER NOT NULL DEFAULT 0, activated_at TEXT, "
    "deactivated_at TEXT, notes TEXT)"
)


def test_module_state_starts_empty() -> None:
    clear_active()
    assert get_active_model_and_checkpoint() is None


def test_set_and_get_active() -> None:
    clear_active()
    model = ConvLSTMPredictor()
    ck = ActiveCheckpoint(
        id=42,
        model_name="conv_lstm_predictor",
        version="0.1.0",
        sha256="abc",
        checkpoint_uri="b2://x",
    )
    set_active(model, ck)
    got = get_active_model_and_checkpoint()
    assert got is not None
    m, c = got
    assert c.id == 42
    assert c.version == "0.1.0"
    assert m is model
    clear_active()


@pytest.mark.asyncio
async def test_load_active_returns_none_when_no_active_row() -> None:
    clear_active()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(CREATE_ML_CHECKPOINTS_TABLE))
    async with AsyncSession(engine) as session:
        result = await load_active_checkpoint(
            session, model_name="conv_lstm_predictor"
        )
    assert result is None
    # Module state must remain empty.
    assert get_active_model_and_checkpoint() is None


@pytest.mark.asyncio
async def test_load_active_downloads_and_loads_state_dict(tmp_path: Path) -> None:
    """When an active row exists, download the checkpoint, sha-verify, load it."""
    clear_active()
    # Build a real ConvLSTMPredictor checkpoint to use as the "downloaded" file.
    ck_path = tmp_path / "ckpt.pt"
    m = ConvLSTMPredictor()
    torch.save(m.state_dict(), ck_path)
    sha = hashlib.sha256(ck_path.read_bytes()).hexdigest()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(CREATE_ML_CHECKPOINTS_TABLE))
        await conn.execute(
            sa.text(
                "INSERT INTO ml_checkpoints (model_name, version, "
                "checkpoint_uri, sha256, trained_at, train_data_window, "
                "eval_results, is_active) "
                "VALUES ('conv_lstm_predictor', '0.1.0', :uri, :sha, "
                "'2026-05-05T00:00:00', '2017-2023', '{}', 1)"
            ),
            {"uri": f"file://{ck_path}", "sha": sha},
        )

    async with AsyncSession(engine) as session:
        loaded = await load_active_checkpoint(
            session, model_name="conv_lstm_predictor"
        )
    assert loaded is not None
    model, ck = loaded
    assert isinstance(model, ConvLSTMPredictor)
    assert ck.version == "0.1.0"
    assert ck.sha256 == sha
    # Module state was updated.
    pinned = get_active_model_and_checkpoint()
    assert pinned is not None
    assert pinned[1].id == ck.id
    clear_active()


@pytest.mark.asyncio
async def test_load_active_returns_none_on_sha_mismatch(tmp_path: Path) -> None:
    """If sha256 doesn't match what the row says, refuse to load."""
    clear_active()
    ck_path = tmp_path / "ckpt.pt"
    m = ConvLSTMPredictor()
    torch.save(m.state_dict(), ck_path)
    bad_sha = "0" * 64

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(CREATE_ML_CHECKPOINTS_TABLE))
        await conn.execute(
            sa.text(
                "INSERT INTO ml_checkpoints (model_name, version, "
                "checkpoint_uri, sha256, trained_at, train_data_window, "
                "eval_results, is_active) "
                "VALUES ('conv_lstm_predictor', '0.1.0', :uri, :sha, "
                "'2026-05-05T00:00:00', '2017-2023', '{}', 1)"
            ),
            {"uri": f"file://{ck_path}", "sha": bad_sha},
        )

    async with AsyncSession(engine) as session:
        result = await load_active_checkpoint(
            session, model_name="conv_lstm_predictor"
        )
    assert result is None
    # Module state must stay clean.
    assert get_active_model_and_checkpoint() is None
