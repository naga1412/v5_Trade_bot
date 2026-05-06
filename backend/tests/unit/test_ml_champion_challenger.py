"""Unit tests for app.ml.champion_challenger.evaluate_challenger.

The function compares a candidate (challenger) checkpoint against the
currently-active (champion) checkpoint over a fixed held-out window. It
returns a ``ChampionChallengerResult`` carrying both MAEs and a boolean
``challenger_wins`` flag set when the challenger beats the champion by
the 5% improvement bar (challenger_mae < champion_mae * 0.95).

Tests inject a stub MAE evaluator so we never hit real OHLCV / Postgres.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


async def _create_ml_checkpoints(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE ml_checkpoints ("
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
    await _create_ml_checkpoints(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _insert_checkpoint(
    session: AsyncSession,
    *,
    model_name: str = "conv_lstm_predictor",
    version: str = "0.1.0",
    is_active: bool = False,
) -> int:
    await session.execute(sa.text(
        "INSERT INTO ml_checkpoints "
        "(model_name, version, checkpoint_uri, sha256, trained_at, "
        " train_data_window, eval_results, is_active) "
        "VALUES (:n, :v, 'b2://x', :s, '2026-05-01T00:00:00Z', "
        " 'x', '{}', :a)"
    ), {"n": model_name, "v": version, "s": version * 32 + "0" * (64 - len(version) * 32),
        "a": 1 if is_active else 0})
    await session.commit()
    row = (await session.execute(sa.text(
        "SELECT id FROM ml_checkpoints WHERE version = :v"
    ), {"v": version})).first()
    assert row is not None
    return int(row.id)


@pytest.mark.asyncio
async def test_champion_better_challenger_loses(session, monkeypatch) -> None:
    """champion MAE 0.010, challenger MAE 0.012 → challenger_wins=False."""
    from app.ml import champion_challenger

    champion_id = await _insert_checkpoint(session, version="0.1.0", is_active=True)
    challenger_id = await _insert_checkpoint(session, version="0.2.0")

    async def fake_mae(_session, checkpoint_id: int) -> float:
        return 0.010 if checkpoint_id == champion_id else 0.012

    monkeypatch.setattr(champion_challenger, "_evaluate_mae", fake_mae)

    result = await champion_challenger.evaluate_challenger(
        session, challenger_checkpoint_id=challenger_id,
    )
    assert result.champion_mae == pytest.approx(0.010)
    assert result.challenger_mae == pytest.approx(0.012)
    assert result.challenger_wins is False


@pytest.mark.asyncio
async def test_challenger_10pct_better_wins(session, monkeypatch) -> None:
    """champion MAE 0.010, challenger MAE 0.009 (10% better) → wins=True."""
    from app.ml import champion_challenger

    champion_id = await _insert_checkpoint(session, version="0.1.0", is_active=True)
    challenger_id = await _insert_checkpoint(session, version="0.2.0")

    async def fake_mae(_session, checkpoint_id: int) -> float:
        return 0.010 if checkpoint_id == champion_id else 0.009

    monkeypatch.setattr(champion_challenger, "_evaluate_mae", fake_mae)

    result = await champion_challenger.evaluate_challenger(
        session, challenger_checkpoint_id=challenger_id,
    )
    assert result.challenger_wins is True


@pytest.mark.asyncio
async def test_challenger_2pct_better_loses_5pct_bar(session, monkeypatch) -> None:
    """challenger only 2% better fails the 5% bar → wins=False."""
    from app.ml import champion_challenger

    champion_id = await _insert_checkpoint(session, version="0.1.0", is_active=True)
    challenger_id = await _insert_checkpoint(session, version="0.2.0")

    async def fake_mae(_session, checkpoint_id: int) -> float:
        return 0.010 if checkpoint_id == champion_id else 0.0098

    monkeypatch.setattr(champion_challenger, "_evaluate_mae", fake_mae)

    result = await champion_challenger.evaluate_challenger(
        session, challenger_checkpoint_id=challenger_id,
    )
    assert result.challenger_wins is False


@pytest.mark.asyncio
async def test_no_champion_any_challenger_wins(session, monkeypatch) -> None:
    """No active champion → challenger always wins (any model > none)."""
    from app.ml import champion_challenger

    challenger_id = await _insert_checkpoint(session, version="0.1.0")

    async def fake_mae(_session, checkpoint_id: int) -> float:
        return 0.020  # Doesn't matter — there's no champion to compare to.

    monkeypatch.setattr(champion_challenger, "_evaluate_mae", fake_mae)

    result = await champion_challenger.evaluate_challenger(
        session, challenger_checkpoint_id=challenger_id,
    )
    assert result.champion_mae is None
    assert result.challenger_mae == pytest.approx(0.020)
    assert result.challenger_wins is True


@pytest.mark.asyncio
async def test_challenger_exactly_5pct_better_wins(session, monkeypatch) -> None:
    """Boundary: challenger exactly 5.001% better → wins; 4.999% → loses.

    The ``< 0.95 *`` strict inequality means exactly-5%-better is not enough.
    """
    from app.ml import champion_challenger

    champion_id = await _insert_checkpoint(session, version="0.1.0", is_active=True)
    challenger_id = await _insert_checkpoint(session, version="0.2.0")

    async def just_under_bar(_session, checkpoint_id: int) -> float:
        # 5.001% better
        return 0.010 if checkpoint_id == champion_id else 0.010 * 0.94999

    monkeypatch.setattr(champion_challenger, "_evaluate_mae", just_under_bar)
    result = await champion_challenger.evaluate_challenger(
        session, challenger_checkpoint_id=challenger_id,
    )
    assert result.challenger_wins is True

    async def just_above_bar(_session, checkpoint_id: int) -> float:
        # 4.999% better — below the 5% bar
        return 0.010 if checkpoint_id == champion_id else 0.010 * 0.95001

    monkeypatch.setattr(champion_challenger, "_evaluate_mae", just_above_bar)
    result = await champion_challenger.evaluate_challenger(
        session, challenger_checkpoint_id=challenger_id,
    )
    assert result.challenger_wins is False


@pytest.mark.asyncio
async def test_challenger_id_must_exist(session, monkeypatch) -> None:
    """Unknown challenger id → raises (admin route translates to 404)."""
    from app.ml import champion_challenger

    async def fake_mae(_session, _checkpoint_id: int) -> float:
        return 0.0

    monkeypatch.setattr(champion_challenger, "_evaluate_mae", fake_mae)

    with pytest.raises(LookupError):
        await champion_challenger.evaluate_challenger(
            session, challenger_checkpoint_id=99999,
        )
