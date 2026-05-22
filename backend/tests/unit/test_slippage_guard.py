"""FU-33 — symbol-level slippage circuit-breaker unit tests.

Covers:
  * default-OFF flag short-circuits both decision + persistence.
  * threshold arithmetic (no-slippage, below, above).
  * symbol_halt_state row INSERT/UPSERT on a halt trigger.
  * per-symbol scope — halting BTC does NOT halt ETH.
  * cooldown expiry returns is_symbol_halted=False.
  * Telegram alert dispatched on trigger via app.ops.alert_routing.alert_admin.
  * zero expected_sl_pct is handled defensively (no /0).

Uses in-memory SQLite + async_sessionmaker per the existing
test_pr_plumbing_1_pr1_persist.py pattern.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.trading.slippage_guard import (
    SlippageDecision,
    check_slippage,
    is_symbol_halted,
)


# SQLite mirror of the alembic 0026 migration. UPSERT uses ON CONFLICT,
# which SQLite 3.24+ supports — matches the prod Postgres path.
_HALT_DDL = (
    "CREATE TABLE symbol_halt_state ("
    "symbol TEXT PRIMARY KEY, "
    "halted_until TEXT NOT NULL, "
    "reason TEXT NOT NULL, "
    "created_at TEXT NOT NULL)"
)


@dataclass
class _StubSettings:
    SLIPPAGE_GUARD_ENABLED: bool = True
    SLIPPAGE_THRESHOLD_MULTIPLIER: float = 3.0
    SLIPPAGE_HALT_COOLDOWN_HOURS: int = 4


async def _fresh_engine() -> AsyncEngine:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_HALT_DDL))
    return engine


# --------------------------------------------------------------------------- #
# 1. expected == actual → no trigger.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_no_slippage_no_trigger() -> None:
    engine = await _fresh_engine()
    async with AsyncSession(engine) as session:
        decision = await check_slippage(
            session, symbol="BTCUSDT",
            expected_sl_pct=-1.5, actual_sl_pct=-1.5,
            settings=_StubSettings(),
        )
    assert isinstance(decision, SlippageDecision)
    assert decision.halt is False
    assert decision.halt_until is None


# --------------------------------------------------------------------------- #
# 2. 1.5× expected with threshold=3.0 → no trigger.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_below_threshold_no_trigger() -> None:
    engine = await _fresh_engine()
    async with AsyncSession(engine) as session:
        decision = await check_slippage(
            session, symbol="BTCUSDT",
            expected_sl_pct=-1.0, actual_sl_pct=-1.5,
            settings=_StubSettings(SLIPPAGE_THRESHOLD_MULTIPLIER=3.0),
        )
        # No row written when no halt.
        row_count = (await session.execute(sa.text(
            "SELECT COUNT(*) FROM symbol_halt_state"
        ))).scalar_one()
    assert decision.halt is False
    assert row_count == 0


# --------------------------------------------------------------------------- #
# 3. 4× expected with threshold=3.0 → halt + row inserted.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_above_threshold_triggers_halt() -> None:
    engine = await _fresh_engine()
    async with AsyncSession(engine) as session:
        decision = await check_slippage(
            session, symbol="BTCUSDT",
            expected_sl_pct=-1.0, actual_sl_pct=-4.0,
            settings=_StubSettings(
                SLIPPAGE_THRESHOLD_MULTIPLIER=3.0,
                SLIPPAGE_HALT_COOLDOWN_HOURS=4,
            ),
        )
        await session.commit()
        row = (await session.execute(sa.text(
            "SELECT symbol, halted_until, reason FROM symbol_halt_state"
        ))).first()
    assert decision.halt is True
    assert decision.halt_until is not None
    assert row is not None
    assert row.symbol == "BTCUSDT"
    # Reason captures the ratio for forensic auditing.
    assert "slippage_ratio" in decision.reason


# --------------------------------------------------------------------------- #
# 4. Per-symbol scope: halting BTCUSDT does NOT halt ETHUSDT.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_halt_blocks_new_entries_for_symbol_only() -> None:
    engine = await _fresh_engine()
    async with AsyncSession(engine) as session:
        await check_slippage(
            session, symbol="BTCUSDT",
            expected_sl_pct=-1.0, actual_sl_pct=-4.0,
            settings=_StubSettings(),
        )
        await session.commit()

        assert await is_symbol_halted(session, symbol="BTCUSDT") is True
        assert await is_symbol_halted(session, symbol="ETHUSDT") is False


# --------------------------------------------------------------------------- #
# 5. Cooldown expiry → is_symbol_halted returns False.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_halt_expires_after_cooldown() -> None:
    engine = await _fresh_engine()
    past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    async with AsyncSession(engine) as session:
        # Pre-seed an expired halt row directly.
        await session.execute(sa.text(
            "INSERT INTO symbol_halt_state (symbol, halted_until, reason, created_at) "
            "VALUES (:s, :u, :r, :c)"
        ), {
            "s": "BTCUSDT", "u": past.isoformat(),
            "r": "test_expired", "c": past.isoformat(),
        })
        await session.commit()
        assert await is_symbol_halted(session, symbol="BTCUSDT") is False


# --------------------------------------------------------------------------- #
# 6. Telegram alert dispatched on trigger.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_telegram_alert_on_trigger() -> None:
    engine = await _fresh_engine()
    with patch(
        "app.trading.slippage_guard.alert_admin",
        new=AsyncMock(return_value=True),
    ) as mock_alert:
        async with AsyncSession(engine) as session:
            await check_slippage(
                session, symbol="FIDAUSDT",
                expected_sl_pct=-1.0, actual_sl_pct=-14.5,
                settings=_StubSettings(),
            )
            await session.commit()
    assert mock_alert.await_count == 1
    call = mock_alert.await_args
    assert call is not None
    msg = call.args[0]
    assert "FIDAUSDT" in msg
    assert "slippage" in msg.lower()
    # Critical-severity routes to Telegram first (per alert_routing precedence).
    assert call.kwargs.get("level") == "critical"


# --------------------------------------------------------------------------- #
# 7. Flag-off short-circuit.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_flag_off_disables_circuit_breaker() -> None:
    engine = await _fresh_engine()
    async with AsyncSession(engine) as session:
        decision = await check_slippage(
            session, symbol="BTCUSDT",
            expected_sl_pct=-1.0, actual_sl_pct=-10.0,  # 10× expected
            settings=_StubSettings(SLIPPAGE_GUARD_ENABLED=False),
        )
        await session.commit()
        row_count = (await session.execute(sa.text(
            "SELECT COUNT(*) FROM symbol_halt_state"
        ))).scalar_one()
    assert decision.halt is False
    assert decision.reason == "guard_disabled"
    # No state mutation when the flag is off.
    assert row_count == 0


# --------------------------------------------------------------------------- #
# 8. Zero expected_sl_pct defensively short-circuits.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_zero_expected_sl_no_trigger() -> None:
    engine = await _fresh_engine()
    async with AsyncSession(engine) as session:
        decision = await check_slippage(
            session, symbol="BTCUSDT",
            expected_sl_pct=0.0, actual_sl_pct=-5.0,
            settings=_StubSettings(),
        )
    assert decision.halt is False
    assert decision.reason == "zero_expected_sl"
