"""Unit tests for the SP-4 Phase D Sharpe-metric path of evaluate_challenger.

Mirrors test_ml_champion_challenger.py for the L10 RL brain registry.
The function compares Sharpe ratios (HIGHER is better, opposite of MAE)
against the rl_checkpoints table.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


async def _create_rl_checkpoints(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE rl_checkpoints ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "model_name TEXT NOT NULL, version TEXT NOT NULL, "
            "checkpoint_uri TEXT NOT NULL, sha256 TEXT NOT NULL, "
            "trained_at TEXT NOT NULL, "
            "train_data_window TEXT NOT NULL, "
            "eval_results TEXT NOT NULL, "
            "is_active INTEGER NOT NULL DEFAULT 0, "
            "activated_at TEXT, deactivated_at TEXT, notes TEXT, "
            "UNIQUE (model_name, version))"
        ))


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _create_rl_checkpoints(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _insert_rl_checkpoint(
    session: AsyncSession,
    *,
    model_name: str = "ppo_policy_v1",
    version: str = "v1-001",
    is_active: bool = False,
) -> int:
    await session.execute(sa.text(
        "INSERT INTO rl_checkpoints "
        "(model_name, version, checkpoint_uri, sha256, trained_at, "
        " train_data_window, eval_results, is_active) "
        "VALUES (:n, :v, 'file:///x', :s, '2026-05-01T00:00:00Z', "
        " 'x', '{}', :a)"
    ), {
        "n": model_name, "v": version,
        "s": "0" * 64,
        "a": 1 if is_active else 0,
    })
    await session.commit()
    row = (await session.execute(sa.text(
        "SELECT id FROM rl_checkpoints WHERE version = :v"
    ), {"v": version})).first()
    assert row is not None
    return int(row.id)


@pytest.mark.asyncio
async def test_sharpe_challenger_better_wins(session, monkeypatch) -> None:
    """Champion Sharpe 1.5, challenger Sharpe 1.65 (10% better) → wins."""
    from app.ml import champion_challenger as cc

    champion_id = await _insert_rl_checkpoint(session, version="v1-001", is_active=True)
    challenger_id = await _insert_rl_checkpoint(session, version="v1-002")

    async def fake_sharpe(_session, checkpoint_id: int) -> float:
        return 1.5 if checkpoint_id == champion_id else 1.65

    monkeypatch.setattr(cc, "_evaluate_sharpe", fake_sharpe)

    result = await cc.evaluate_challenger(
        session, challenger_checkpoint_id=challenger_id,
        table="rl_checkpoints", metric="sharpe",
    )
    assert result.champion_metric == pytest.approx(1.5)
    assert result.challenger_metric == pytest.approx(1.65)
    assert result.challenger_wins is True
    assert result.metric == "sharpe"


@pytest.mark.asyncio
async def test_sharpe_challenger_worse_loses(session, monkeypatch) -> None:
    """Champion 2.0, challenger 1.5 → loses (no improvement)."""
    from app.ml import champion_challenger as cc

    champion_id = await _insert_rl_checkpoint(session, version="v1-001", is_active=True)
    challenger_id = await _insert_rl_checkpoint(session, version="v1-002")

    async def fake_sharpe(_session, checkpoint_id: int) -> float:
        return 2.0 if checkpoint_id == champion_id else 1.5

    monkeypatch.setattr(cc, "_evaluate_sharpe", fake_sharpe)

    result = await cc.evaluate_challenger(
        session, challenger_checkpoint_id=challenger_id,
        table="rl_checkpoints", metric="sharpe",
    )
    assert result.challenger_wins is False


@pytest.mark.asyncio
async def test_sharpe_3pct_better_loses_5pct_bar(session, monkeypatch) -> None:
    """Challenger only 3% better fails the 5% bar."""
    from app.ml import champion_challenger as cc

    champion_id = await _insert_rl_checkpoint(session, version="v1-001", is_active=True)
    challenger_id = await _insert_rl_checkpoint(session, version="v1-002")

    async def fake_sharpe(_session, checkpoint_id: int) -> float:
        return 1.0 if checkpoint_id == champion_id else 1.03  # 3% better

    monkeypatch.setattr(cc, "_evaluate_sharpe", fake_sharpe)

    result = await cc.evaluate_challenger(
        session, challenger_checkpoint_id=challenger_id,
        table="rl_checkpoints", metric="sharpe",
    )
    assert result.challenger_wins is False


@pytest.mark.asyncio
async def test_sharpe_first_checkpoint_no_champion_auto_passes(
    session, monkeypatch,
) -> None:
    """No active champion → bootstrap path returns wins=True regardless of Sharpe."""
    from app.ml import champion_challenger as cc

    challenger_id = await _insert_rl_checkpoint(session, version="v1-001")

    async def fake_sharpe(_session, checkpoint_id: int) -> float:
        return 0.3  # mediocre — would lose against any champion

    monkeypatch.setattr(cc, "_evaluate_sharpe", fake_sharpe)

    result = await cc.evaluate_challenger(
        session, challenger_checkpoint_id=challenger_id,
        table="rl_checkpoints", metric="sharpe",
    )
    assert result.champion_metric is None
    assert result.challenger_metric == pytest.approx(0.3)
    assert result.challenger_wins is True


@pytest.mark.asyncio
async def test_sharpe_unknown_challenger_raises(session) -> None:
    from app.ml import champion_challenger as cc

    with pytest.raises(LookupError, match="not found in rl_checkpoints"):
        await cc.evaluate_challenger(
            session, challenger_checkpoint_id=99999,
            table="rl_checkpoints", metric="sharpe",
        )


# ---------------------------------------------------------------------------
# sharpe_passes() unit tests — additive-floor semantics
# ---------------------------------------------------------------------------


def test_sharpe_passes_positive_champion_multiplicative_wins():
    """Positive champion: multiplicative bar wins when large enough."""
    from app.ml.champion_challenger import sharpe_passes
    # champion=2.0 → max(2.0*1.05, 2.0+0.05) = max(2.1, 2.05) = 2.1
    assert sharpe_passes(2.11, 2.0) is True
    assert sharpe_passes(2.1, 2.0) is False   # not strictly greater


def test_sharpe_passes_positive_champion_additive_floor_wins():
    """Small positive champion: additive floor is stricter than multiplicative."""
    from app.ml.champion_challenger import sharpe_passes
    # champion=0.5 → max(0.5*1.05, 0.5+0.05) = max(0.525, 0.55) = 0.55
    assert sharpe_passes(0.56, 0.5) is True
    assert sharpe_passes(0.53, 0.5) is False   # beats multiplicative but not additive


def test_sharpe_passes_negative_champion_additive_floor_prevents_inversion():
    """Negative champion: additive floor prevents inverted bar from accepting worse Sharpe."""
    from app.ml.champion_challenger import sharpe_passes
    # Bug scenario: champion=-2.0 → multiplicative threshold=-2.1
    # Without fix: challenger=-2.05 > -2.1 → passes (but -2.05 < -2.0, i.e. worse!)
    # With fix:    max(-2.1, -1.95) = -1.95 → challenger must be > -1.95
    assert sharpe_passes(-2.05, -2.0) is False   # worse Sharpe: must NOT pass
    assert sharpe_passes(-1.9, -2.0) is True     # genuinely better Sharpe: passes


def test_sharpe_passes_zero_champion():
    """Zero champion: additive floor ensures positive improvement required."""
    from app.ml.champion_challenger import sharpe_passes
    # champion=0.0 → max(0.0, 0.05) = 0.05
    assert sharpe_passes(0.06, 0.0) is True
    assert sharpe_passes(0.04, 0.0) is False
    assert sharpe_passes(0.0, 0.0) is False


@pytest.mark.asyncio
async def test_default_metric_still_mae_for_backward_compat(
    session, monkeypatch,
) -> None:
    """Existing admin_ml callers don't pass metric/table — defaults preserve
    the SP-7 MAE path against ml_checkpoints.

    This test exercises the same default-args invocation that
    admin_ml.py uses, against a separately-set-up ml_checkpoints table
    via SP-7's own conftest fixture pattern (not duplicated here —
    we just check the default value of the param).
    """
    from app.ml import champion_challenger as cc
    import inspect
    sig = inspect.signature(cc.evaluate_challenger)
    assert sig.parameters["metric"].default == "mae"
    assert sig.parameters["table"].default == "ml_checkpoints"
