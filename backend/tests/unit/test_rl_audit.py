"""Tests for app.rl.audit (SP-4 Phase C3)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.rl.audit import record_brain_decision
from app.rl.inference import BrainDecision


CREATE_BRAIN_DECISIONS_TABLE = (
    "CREATE TABLE brain_decisions ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "ts TEXT NOT NULL, symbol TEXT NOT NULL, "
    "checkpoint_id INTEGER NOT NULL, "
    "observation TEXT NOT NULL, action TEXT NOT NULL, "
    "action_logits TEXT NOT NULL, value_estimate REAL, "
    "smoothed_action TEXT NOT NULL, "
    "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL)"
)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(CREATE_BRAIN_DECISIONS_TABLE))
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


def _decision(action: str = "LONG_FULL", smoothed: str = "LONG_FULL") -> BrainDecision:
    return BrainDecision(
        raw_action=action,
        smoothed_action=smoothed,
        logits=[0.1, 0.2, -0.1, 0.0, 0.0],
        value_estimate=0.42,
        checkpoint_id=42,
        checkpoint_version="v1-test",
    )


@pytest.mark.asyncio
async def test_first_row_uses_genesis_prev_hash(session: AsyncSession) -> None:
    obs = np.zeros(58, dtype=np.float32)
    new_hash = await record_brain_decision(
        session, decision=_decision(), symbol="BTC/USDT", observation=obs,
        ts=datetime(2024, 4, 1, 12, 0, tzinfo=timezone.utc),
    )
    await session.commit()
    assert len(new_hash) == 64

    row = (await session.execute(sa.text(
        "SELECT prev_hash, row_hash, action, smoothed_action, "
        "checkpoint_id, value_estimate FROM brain_decisions"
    ))).first()
    assert row.prev_hash == "0" * 64
    assert row.row_hash == new_hash
    assert row.action == "LONG_FULL"
    assert row.smoothed_action == "LONG_FULL"
    assert row.checkpoint_id == 42
    assert row.value_estimate == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_chain_links_correctly_across_rows(session: AsyncSession) -> None:
    obs = np.arange(58, dtype=np.float32)
    h1 = await record_brain_decision(
        session, decision=_decision(), symbol="BTC/USDT", observation=obs,
    )
    h2 = await record_brain_decision(
        session, decision=_decision(action="FLAT", smoothed="FLAT"),
        symbol="BTC/USDT", observation=obs,
    )
    h3 = await record_brain_decision(
        session, decision=_decision(action="SHORT_HALF", smoothed="SHORT_HALF"),
        symbol="ETH/USDT", observation=obs,
    )
    await session.commit()

    rows = (await session.execute(sa.text(
        "SELECT id, prev_hash, row_hash FROM brain_decisions ORDER BY id"
    ))).all()
    assert len(rows) == 3
    assert rows[0].row_hash == h1
    assert rows[1].prev_hash == h1 and rows[1].row_hash == h2
    assert rows[2].prev_hash == h2 and rows[2].row_hash == h3


@pytest.mark.asyncio
async def test_observation_serialised_as_json_string(session: AsyncSession) -> None:
    obs = np.array([1.5, -2.0, 0.0] + [0.0] * 55, dtype=np.float32)
    await record_brain_decision(
        session, decision=_decision(), symbol="BTC/USDT", observation=obs,
    )
    await session.commit()
    row = (await session.execute(sa.text(
        "SELECT observation FROM brain_decisions"
    ))).first()
    deserialised = json.loads(row.observation)
    assert isinstance(deserialised, list)
    assert len(deserialised) == 58
    assert deserialised[0] == 1.5
    assert deserialised[1] == -2.0


@pytest.mark.asyncio
async def test_logits_serialised_as_json(session: AsyncSession) -> None:
    obs = np.zeros(58, dtype=np.float32)
    await record_brain_decision(
        session, decision=_decision(), symbol="BTC/USDT", observation=obs,
    )
    await session.commit()
    row = (await session.execute(sa.text(
        "SELECT action_logits FROM brain_decisions"
    ))).first()
    parsed = json.loads(row.action_logits)
    assert parsed == [0.1, 0.2, -0.1, 0.0, 0.0]


@pytest.mark.asyncio
async def test_accepts_plain_list_observation(session: AsyncSession) -> None:
    """Defensive: caller may pass a Python list instead of numpy array."""
    obs = [0.0] * 58
    h = await record_brain_decision(
        session, decision=_decision(), symbol="BTC/USDT", observation=obs,
    )
    await session.commit()
    assert len(h) == 64


@pytest.mark.asyncio
async def test_default_ts_is_now_utc(session: AsyncSession) -> None:
    """If caller doesn't supply ts, the row gets a fresh UTC timestamp."""
    obs = np.zeros(58, dtype=np.float32)
    before = datetime.now(timezone.utc)
    await record_brain_decision(
        session, decision=_decision(), symbol="BTC/USDT", observation=obs,
    )
    await session.commit()
    after = datetime.now(timezone.utc)

    row = (await session.execute(sa.text(
        "SELECT ts FROM brain_decisions"
    ))).first()
    ts = datetime.fromisoformat(row.ts)
    assert before <= ts <= after
