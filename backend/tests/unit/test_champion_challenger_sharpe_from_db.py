"""PR-BRAIN-BACKTEST-PHASEB5 — _evaluate_sharpe reads from rl_checkpoints.eval_results."""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ml.champion_challenger import _evaluate_sharpe


CREATE_RL_CHECKPOINTS = (
    "CREATE TABLE rl_checkpoints ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "model_name TEXT NOT NULL, version TEXT NOT NULL, "
    "checkpoint_uri TEXT NOT NULL, sha256 TEXT NOT NULL, "
    "trained_at TEXT NOT NULL, train_data_window TEXT NOT NULL, "
    "eval_results TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 0, "
    "activated_at TEXT, deactivated_at TEXT, notes TEXT, "
    "UNIQUE (model_name, version))"
)


async def _insert_checkpoint(
    session: AsyncSession,
    *,
    cid: int,
    eval_results: dict,
) -> None:
    await session.execute(sa.text(
        "INSERT INTO rl_checkpoints "
        "(id, model_name, version, checkpoint_uri, sha256, trained_at, "
        " train_data_window, eval_results) "
        "VALUES (:i, 'ppo_policy_v1', :v, 'file://x.pt', 'h', "
        "        '2026-05-22T00:00:00Z', '{}', :e)"
    ), {"i": cid, "v": f"v1-test-{cid}", "e": json.dumps(eval_results)})
    await session.commit()


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(CREATE_RL_CHECKPOINTS))
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_evaluate_sharpe_reads_from_eval_results_real_value(
    session: AsyncSession,
) -> None:
    """When eval_results JSON has a real Sharpe, helper returns its float value."""
    await _insert_checkpoint(
        session, cid=1, eval_results={
            "sharpe": 1.234, "max_dd": 0.5, "win_rate": 0.55, "total_trades": 42,
        },
    )
    sharpe = await _evaluate_sharpe(session, 1)
    assert sharpe == pytest.approx(1.234)


@pytest.mark.asyncio
async def test_evaluate_sharpe_returns_zero_for_legacy_deferred(
    session: AsyncSession,
) -> None:
    """Legacy id=1/id=2 carry _status=deferred_to_phase_b5 → no sharpe key → 0.0."""
    await _insert_checkpoint(
        session, cid=2, eval_results={
            "_status": "deferred_to_phase_b5",
            "_note": "old placeholder",
        },
    )
    sharpe = await _evaluate_sharpe(session, 2)
    assert sharpe == 0.0


@pytest.mark.asyncio
async def test_evaluate_sharpe_returns_zero_for_missing_row(
    session: AsyncSession,
) -> None:
    """Non-existent checkpoint_id → 0.0 (no crash)."""
    sharpe = await _evaluate_sharpe(session, 999)
    assert sharpe == 0.0


@pytest.mark.asyncio
async def test_evaluate_sharpe_returns_zero_for_insufficient_data(
    session: AsyncSession,
) -> None:
    """Insufficient-data sentinel → no sharpe key → 0.0."""
    await _insert_checkpoint(
        session, cid=3, eval_results={
            "_status": "insufficient_data",
            "min_required": 20, "had": 5,
        },
    )
    sharpe = await _evaluate_sharpe(session, 3)
    assert sharpe == 0.0


@pytest.mark.asyncio
async def test_evaluate_sharpe_returns_zero_for_malformed_value(
    session: AsyncSession,
) -> None:
    """eval_results.sharpe is a non-numeric string → graceful 0.0."""
    await _insert_checkpoint(
        session, cid=4, eval_results={"sharpe": "not-a-number"},
    )
    sharpe = await _evaluate_sharpe(session, 4)
    assert sharpe == 0.0


@pytest.mark.asyncio
async def test_evaluate_sharpe_handles_negative_sharpe(
    session: AsyncSession,
) -> None:
    """Negative Sharpe is a valid (bad-brain) value — pass through as-is."""
    await _insert_checkpoint(
        session, cid=5, eval_results={"sharpe": -0.75},
    )
    sharpe = await _evaluate_sharpe(session, 5)
    assert sharpe == pytest.approx(-0.75)
