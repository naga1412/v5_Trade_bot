"""SP-4 Phase C end-to-end integration smoke for the brain glue.

Validates the production path the predictor takes:

1. No active checkpoint → ``compute_brain_adjust_and_persist`` returns
   ``brain_adjust=1.0`` (no-op) and ``decision=None``. **Critical:** this
   is the day-1 production behaviour, must be bit-identical to
   pre-SP-4 equal-weight aggregation.

2. Active checkpoint → returns the multiplier mapped from the brain's
   action, AND writes a hash-chained ``brain_decisions`` row using its
   own dedicated session (not the caller's session).

3. The caller's session is never committed by the glue (isolation contract).

4. Audit chain integrity preserved across multiple calls.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
import sqlalchemy as sa
import torch
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.rl.checkpoints import (
    ActiveRlCheckpoint,
    clear_active,
    set_active,
)
from app.rl.inference import reset_smoothing_state
from app.rl.obs import MacroFeatures, MarketFeatures, PositionState
from app.rl.policy import PolicyNetwork
from app.rl.predictor_glue import (
    compute_brain_adjust_and_persist,
    reset_glue_state,
)


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
async def _db_engine() -> AsyncEngine:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(CREATE_BRAIN_DECISIONS_TABLE))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def sm(_db_engine: AsyncEngine) -> async_sessionmaker:
    """Shared session factory pointing at the test sqlite engine."""
    return async_sessionmaker(_db_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(sm: async_sessionmaker) -> AsyncSession:
    async with sm() as s:
        yield s


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    clear_active()
    reset_smoothing_state()
    reset_glue_state()


def _market() -> MarketFeatures:
    return MarketFeatures(
        atr_pct=0.02, funding_rate=0.0001, oi_delta_24h=0.05,
        regime="bull_breakout",
    )


def _position() -> PositionState:
    return PositionState(cur_position=0, unrealized_pnl_R=0.0, bars_in_position=0)


def _macro() -> MacroFeatures:
    return MacroFeatures(weekend=False, asia_open=False)


def _layers() -> tuple[float, ...]:
    return tuple(0.1 * i for i in range(1, 10))


def _activate_long_full_policy() -> None:
    policy = PolicyNetwork()
    with torch.no_grad():
        policy.policy_head.weight.zero_()
        policy.policy_head.bias.zero_()
        policy.policy_head.bias[0] = 10.0  # ALL_ACTIONS[0] = LONG_FULL
    set_active(
        policy,
        ActiveRlCheckpoint(
            id=42, model_name="ppo_policy_v1", version="v1-test",
            sha256="0" * 64, checkpoint_uri="file:///x.pt",
        ),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_no_active_checkpoint_returns_no_op_multiplier(
    session: AsyncSession,
    sm: async_sessionmaker,
) -> None:
    """Day-1 production: no checkpoint loaded, brain_adjust=1.0 + no DB row."""
    result = await compute_brain_adjust_and_persist(
        symbol="BTC/USDT",
        proposed_direction="LONG",
        layer_scores=_layers(),
        market=_market(),
        position=_position(),
        macro=_macro(),
        session=session,
        _session_factory=sm,
    )
    assert result.brain_adjust == 1.0
    assert result.decision is None

    async with sm() as verify:
        rows = (await verify.execute(sa.text(
            "SELECT count(*) AS n FROM brain_decisions"
        ))).first()
    assert rows.n == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_active_checkpoint_returns_multiplier_and_writes_row(
    session: AsyncSession,
    sm: async_sessionmaker,
) -> None:
    _activate_long_full_policy()
    result = await compute_brain_adjust_and_persist(
        symbol="BTC/USDT",
        proposed_direction="LONG",
        layer_scores=_layers(),
        market=_market(),
        position=_position(),
        macro=_macro(),
        session=session,
        _session_factory=sm,
    )
    # Glue commits via its own dedicated session — caller never commits.

    # LONG_FULL + proposed=LONG → boost to 1 + SPREAD
    # (PR-BRAIN-SOFTER-ACTIONS: spread-parametric mapping, default 0.15)
    spread = get_settings().BRAIN_ACTION_MULTIPLIER_SPREAD
    assert result.brain_adjust == pytest.approx(1.0 + spread)
    assert result.decision is not None
    assert result.decision.raw_action == "LONG_FULL"
    assert result.decision.smoothed_action == "LONG_FULL"

    # One audit row written, hash-chained from genesis.
    async with sm() as verify:
        rows = (await verify.execute(sa.text(
            "SELECT prev_hash, row_hash, action, smoothed_action, "
            "checkpoint_id FROM brain_decisions"
        ))).all()
    assert len(rows) == 1
    assert rows[0].prev_hash == "0" * 64
    assert rows[0].action == "LONG_FULL"
    assert rows[0].smoothed_action == "LONG_FULL"
    assert rows[0].checkpoint_id == 42


@pytest.mark.integration
@pytest.mark.asyncio
async def test_active_checkpoint_brain_disagrees_suppresses(
    session: AsyncSession,
    sm: async_sessionmaker,
) -> None:
    """Brain says LONG_FULL but L1..L9 proposed SHORT → suppress (1 - SPREAD/2)."""
    _activate_long_full_policy()
    result = await compute_brain_adjust_and_persist(
        symbol="BTC/USDT",
        proposed_direction="SHORT",
        layer_scores=_layers(),
        market=_market(),
        position=_position(),
        macro=_macro(),
        session=session,
        _session_factory=sm,
    )
    # PR-BRAIN-SOFTER-ACTIONS: disagree → 1 - SPREAD/2 (default 0.925).
    spread = get_settings().BRAIN_ACTION_MULTIPLIER_SPREAD
    assert result.brain_adjust == pytest.approx(1.0 - spread / 2)
    assert result.decision is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_chain_links_across_multiple_predictions(
    session: AsyncSession,
    sm: async_sessionmaker,
) -> None:
    """3 sequential predictor ticks → 3 hash-chained brain_decisions rows."""
    _activate_long_full_policy()
    for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
        await compute_brain_adjust_and_persist(
            symbol=symbol,
            proposed_direction="LONG",
            layer_scores=_layers(),
            market=_market(),
            position=_position(),
            macro=_macro(),
            session=session,
            _session_factory=sm,
        )
    # Each call commits its own dedicated session — no caller commit needed.

    async with sm() as verify:
        rows = (await verify.execute(sa.text(
            "SELECT id, prev_hash, row_hash, symbol FROM brain_decisions ORDER BY id"
        ))).all()
    assert len(rows) == 3
    assert rows[0].prev_hash == "0" * 64
    assert rows[1].prev_hash == rows[0].row_hash
    assert rows[2].prev_hash == rows[1].row_hash
    assert [r.symbol for r in rows] == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_none_skips_persistence(sm: async_sessionmaker) -> None:
    """Legacy callers without a session: still get the multiplier, no DB write."""
    _activate_long_full_policy()
    result = await compute_brain_adjust_and_persist(
        symbol="BTC/USDT",
        proposed_direction="LONG",
        layer_scores=_layers(),
        market=_market(),
        position=_position(),
        macro=_macro(),
        session=None,
        _session_factory=sm,
    )
    # PR-BRAIN-SOFTER-ACTIONS: agree-full → 1 + SPREAD (default 1.15).
    spread = get_settings().BRAIN_ACTION_MULTIPLIER_SPREAD
    assert result.brain_adjust == pytest.approx(1.0 + spread)
    assert result.decision is not None
    async with sm() as verify:
        rows = (await verify.execute(sa.text(
            "SELECT count(*) AS n FROM brain_decisions"
        ))).first()
    assert rows.n == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_brain_hook_does_not_commit_caller_session(
    sm: async_sessionmaker,
) -> None:
    """Isolation contract: glue commits its own dedicated session, never the caller's.

    The l9_session in live_prediction / shadow worker is a read-only pipeline
    session. Committing it mid-pipeline would flush unrelated pending state and
    break single-unit-of-work semantics. This test asserts that contract.
    """
    _activate_long_full_policy()

    # Spy on a mock caller session — the glue must not call commit() on it.
    caller_session = MagicMock(spec=AsyncSession)
    caller_session.commit = AsyncMock()

    await compute_brain_adjust_and_persist(
        symbol="BTC/USDT",
        proposed_direction="LONG",
        layer_scores=_layers(),
        market=_market(),
        position=_position(),
        macro=_macro(),
        session=caller_session,  # type: ignore[arg-type]
        _session_factory=sm,
    )

    caller_session.commit.assert_not_called()

    # Row was still written — just via the dedicated session, not the caller's.
    async with sm() as verify:
        rows = (await verify.execute(sa.text(
            "SELECT count(*) AS n FROM brain_decisions"
        ))).first()
    assert rows.n == 1, "brain_decisions row missing; dedicated session did not commit"
