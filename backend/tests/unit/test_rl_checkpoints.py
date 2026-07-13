"""Tests for app.rl.checkpoints — active checkpoint loader (SP-4 Phase A6)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
import torch
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from torch import nn

from app.rl.checkpoints import (
    ActiveRlCheckpoint,
    clear_active,
    get_active_asset_table_state,
    get_active_policy_and_checkpoint,
    load_active_checkpoint,
    set_active,
)


CREATE_RL_CHECKPOINTS_TABLE = (
    "CREATE TABLE rl_checkpoints ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, model_name TEXT, version TEXT, "
    "checkpoint_uri TEXT, sha256 TEXT, trained_at TEXT, "
    "train_data_window TEXT, eval_results TEXT, "
    "is_active INTEGER NOT NULL DEFAULT 0, activated_at TEXT, "
    "deactivated_at TEXT, notes TEXT)"
)


class _TinyPolicy(nn.Module):
    """Minimal stand-in for the Phase B PolicyNetwork — just enough to
    save+load a state_dict end-to-end."""

    def __init__(self) -> None:
        super().__init__()
        self.fc = nn.Linear(4, 2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc(x)


def _save_production_format(path: Path, policy: nn.Module, *, asset_table_state: dict | None = None) -> str:
    """Save checkpoint in train_brain.py production format; return sha256."""
    payload = {
        "policy": policy.state_dict(),
        "asset_table": asset_table_state or {},
    }
    torch.save(payload, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(CREATE_RL_CHECKPOINTS_TABLE))
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_module_state() -> None:
    """Module-scope state must not leak between tests."""
    clear_active()


def test_module_state_starts_empty() -> None:
    assert get_active_policy_and_checkpoint() is None
    assert get_active_asset_table_state() is None


def test_set_and_get_active() -> None:
    model = _TinyPolicy()
    ck = ActiveRlCheckpoint(
        id=42, model_name="ppo_policy_v1", version="v1-test",
        sha256="a" * 64, checkpoint_uri="file:///x.pt",
    )
    set_active(model, ck)
    got = get_active_policy_and_checkpoint()
    assert got is not None
    assert got[0] is model
    assert got[1] is ck


def test_clear_active_resets_all_state() -> None:
    """clear_active must also clear _active_asset_table_state."""
    model = _TinyPolicy()
    ck = ActiveRlCheckpoint(
        id=1, model_name="ppo_policy_v1", version="v",
        sha256="0" * 64, checkpoint_uri="file:///x.pt",
    )
    set_active(model, ck)
    clear_active()
    assert get_active_policy_and_checkpoint() is None
    assert get_active_asset_table_state() is None


@pytest.mark.asyncio
async def test_load_returns_none_when_no_active_row(session: AsyncSession) -> None:
    out = await load_active_checkpoint(session, model_factory=_TinyPolicy)
    assert out is None


@pytest.mark.asyncio
async def test_load_returns_none_when_factory_missing(session: AsyncSession) -> None:
    """Without a model_factory we have nothing to load weights into."""
    out = await load_active_checkpoint(session, model_factory=None)
    assert out is None


@pytest.mark.asyncio
async def test_load_round_trips_local_file_checkpoint(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Save a checkpoint in production format, register it, load it, verify weights.

    Production format (from train_brain.py save_checkpoint):
        {"policy": policy.state_dict(), "asset_table": asset_table.state_dict()}

    The loader must extract state["policy"] — NOT load the whole dict.
    Regression guard: the original loader did model.load_state_dict(state) which
    would fail with unexpected key(s) 'policy', 'asset_table'.
    """
    monkeypatch.setenv("RL_CACHE_DIR", str(tmp_path / "cache"))

    # 1) Build a tiny policy with known weights, save in production format.
    src = _TinyPolicy()
    with torch.no_grad():
        src.fc.weight.fill_(0.42)
        src.fc.bias.fill_(-0.1)
    ckpt_path = tmp_path / "ppo_policy_v1.pt"
    asset_table_stub = nn.Embedding(1, 4)  # minimal stand-in for AssetEmbeddingTable
    sha = _save_production_format(ckpt_path, src, asset_table_state=asset_table_stub.state_dict())

    # 2) Register it as active in the DB.
    file_uri = ckpt_path.resolve().as_uri()
    await session.execute(sa.text("""
        INSERT INTO rl_checkpoints
        (model_name, version, checkpoint_uri, sha256, trained_at,
         train_data_window, eval_results, is_active)
        VALUES ('ppo_policy_v1', 'v1-test', :u, :h,
                '2026-05-07T00:00:00+00:00', 'window', '{}', 1)
    """), {"u": file_uri, "h": sha})
    await session.commit()

    # 3) Load it.
    out = await load_active_checkpoint(session, model_factory=_TinyPolicy)
    assert out is not None
    model, ck = out
    assert ck.version == "v1-test"
    assert ck.sha256 == sha
    assert torch.allclose(model.fc.weight, torch.full_like(model.fc.weight, 0.42))
    assert torch.allclose(model.fc.bias, torch.full_like(model.fc.bias, -0.1))

    # 4) Module state was pinned.
    pinned = get_active_policy_and_checkpoint()
    assert pinned is not None
    assert pinned[0] is model

    # 5) Asset table state was stored for predictor_glue to restore.
    asset_state = get_active_asset_table_state()
    assert asset_state is not None
    assert "weight" in asset_state  # nn.Embedding state_dict has 'weight'


@pytest.mark.asyncio
async def test_load_rejects_flat_state_dict_format(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flat state_dict (pre-train_brain.py format) must be rejected gracefully.

    If someone saves a bare state_dict with no 'policy' key, the loader must
    return None rather than silently loading garbage weights or raising.
    """
    monkeypatch.setenv("RL_CACHE_DIR", str(tmp_path / "cache"))

    src = _TinyPolicy()
    ckpt_path = tmp_path / "ppo_policy_v1_flat.pt"
    # OLD (wrong) format — flat state dict, no "policy" key wrapper
    torch.save(src.state_dict(), ckpt_path)
    sha = hashlib.sha256(ckpt_path.read_bytes()).hexdigest()

    file_uri = ckpt_path.resolve().as_uri()
    await session.execute(sa.text("""
        INSERT INTO rl_checkpoints
        (model_name, version, checkpoint_uri, sha256, trained_at,
         train_data_window, eval_results, is_active)
        VALUES ('ppo_policy_v1', 'v1-flat', :u, :h,
                '2026-07-12T00:00:00+00:00', 'window', '{}', 1)
    """), {"u": file_uri, "h": sha})
    await session.commit()

    out = await load_active_checkpoint(session, model_factory=_TinyPolicy)
    assert out is None


@pytest.mark.asyncio
async def test_load_returns_none_on_sha_mismatch(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RL_CACHE_DIR", str(tmp_path / "cache"))
    src = _TinyPolicy()
    ckpt_path = tmp_path / "ppo_policy_v1.pt"
    _save_production_format(ckpt_path, src)

    file_uri = ckpt_path.resolve().as_uri()
    bad_sha = "f" * 64  # deliberately wrong
    await session.execute(sa.text("""
        INSERT INTO rl_checkpoints
        (model_name, version, checkpoint_uri, sha256, trained_at,
         train_data_window, eval_results, is_active)
        VALUES ('ppo_policy_v1', 'v1', :u, :h,
                '2026-05-07T00:00:00+00:00', 'window', '{}', 1)
    """), {"u": file_uri, "h": bad_sha})
    await session.commit()

    out = await load_active_checkpoint(session, model_factory=_TinyPolicy)
    assert out is None


@pytest.mark.asyncio
async def test_load_returns_none_when_file_missing(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RL_CACHE_DIR", str(tmp_path / "cache"))
    nonexistent = tmp_path / "does_not_exist.pt"
    await session.execute(sa.text("""
        INSERT INTO rl_checkpoints
        (model_name, version, checkpoint_uri, sha256, trained_at,
         train_data_window, eval_results, is_active)
        VALUES ('ppo_policy_v1', 'v1', :u, :h,
                '2026-05-07T00:00:00+00:00', 'window', '{}', 1)
    """), {"u": nonexistent.resolve().as_uri(), "h": "0" * 64})
    await session.commit()

    out = await load_active_checkpoint(session, model_factory=_TinyPolicy)
    assert out is None
