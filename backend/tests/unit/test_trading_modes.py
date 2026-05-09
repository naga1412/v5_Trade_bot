"""SP-8 Phase B — mode state machine.

Tests against an in-memory SQLite that mirrors the columns the modes
module touches: users.trading_mode + the mode_change_log audit table.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.trading.modes import (
    ModeChangeError,
    get_mode,
    is_downgrade,
    is_upgrade,
    set_mode,
)
from app.trading.promotion import compute_stats


_NOW = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)


async def _setup_db() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE users ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  email TEXT, trading_mode TEXT NOT NULL DEFAULT 'manual')"
        ))
        await conn.execute(sa.text(
            "INSERT INTO users (id, email, trading_mode) "
            "VALUES (1, 'test@x', 'manual')"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE mode_change_log ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  user_id INTEGER NOT NULL,"
            "  old_mode TEXT NOT NULL, new_mode TEXT NOT NULL,"
            "  triggered_by TEXT NOT NULL, reason TEXT, gate_snapshot TEXT,"
            "  changed_at TEXT NOT NULL,"
            "  prev_hash TEXT NOT NULL,"
            "  row_hash TEXT NOT NULL UNIQUE)"
        ))
    return engine


# ---- Pure rank helpers ----------------------------------------------------


def test_is_upgrade_and_downgrade() -> None:
    assert is_upgrade("manual", "telegram-approve") is True
    assert is_upgrade("telegram-approve", "fully-auto") is True
    assert is_upgrade("manual", "fully-auto") is True
    assert is_upgrade("fully-auto", "manual") is False
    assert is_upgrade("manual", "manual") is False
    assert is_downgrade("fully-auto", "telegram-approve") is True
    assert is_downgrade("telegram-approve", "manual") is True
    assert is_downgrade("manual", "telegram-approve") is False


# ---- get_mode -------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_mode_returns_user_value() -> None:
    engine = await _setup_db()
    async with AsyncSession(engine) as s:
        assert await get_mode(s, 1) == "manual"


@pytest.mark.asyncio
async def test_get_mode_returns_manual_for_unknown_user() -> None:
    engine = await _setup_db()
    async with AsyncSession(engine) as s:
        assert await get_mode(s, 999) == "manual"


# ---- set_mode (downgrades) -----------------------------------------------


@pytest.mark.asyncio
async def test_downgrade_always_allowed_no_snapshot_required() -> None:
    engine = await _setup_db()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='fully-auto' WHERE id=1"
        ))
        await set_mode(
            s, user_id=1, new_mode="manual",
            triggered_by="user", reason="user requested",
            now=_NOW,
        )
        await s.commit()
        assert await get_mode(s, 1) == "manual"


# ---- set_mode (upgrades require gate snapshot) ---------------------------


@pytest.mark.asyncio
async def test_upgrade_without_snapshot_raises() -> None:
    engine = await _setup_db()
    async with AsyncSession(engine) as s:
        with pytest.raises(ModeChangeError, match="no gate snapshot"):
            await set_mode(
                s, user_id=1, new_mode="telegram-approve",
                triggered_by="user", now=_NOW,
            )


@pytest.mark.asyncio
async def test_upgrade_with_failing_gates_raises() -> None:
    engine = await _setup_db()
    snap = compute_stats([], span_days=10, now=_NOW)  # 0 trades, 10 days
    async with AsyncSession(engine) as s:
        with pytest.raises(ModeChangeError, match="trades=0"):
            await set_mode(
                s, user_id=1, new_mode="telegram-approve",
                triggered_by="user",
                gate_snapshot=snap, now=_NOW,
            )


def _passing_pnls(n: int = 100) -> list[float]:
    """Bresenham-interleaved 11W+9L block × N — keeps drawdown low so
    promotion gates pass."""
    block: list[float] = []
    losses_placed = 0
    for i in range(20):
        if losses_placed < (i + 1) * 9 / 20 and losses_placed < 9:
            block.append(-1.5)
            losses_placed += 1
        else:
            block.append(2.0)
    out: list[float] = []
    while len(out) < n:
        out.extend(block)
    return out[:n]


@pytest.mark.asyncio
async def test_upgrade_with_passing_gates_succeeds() -> None:
    """A passing snapshot makes the upgrade go through."""
    engine = await _setup_db()
    snap = compute_stats(_passing_pnls(100), span_days=30, now=_NOW)
    assert snap.passes("telegram-approve") is True
    async with AsyncSession(engine) as s:
        row_hash = await set_mode(
            s, user_id=1, new_mode="telegram-approve",
            triggered_by="user", reason="manual upgrade",
            gate_snapshot=snap, now=_NOW,
        )
        await s.commit()
        assert isinstance(row_hash, str) and len(row_hash) == 64
        assert await get_mode(s, 1) == "telegram-approve"


@pytest.mark.asyncio
async def test_audit_row_written_with_snapshot_and_reason() -> None:
    engine = await _setup_db()
    snap = compute_stats(_passing_pnls(100), span_days=30, now=_NOW)
    async with AsyncSession(engine) as s:
        await set_mode(
            s, user_id=1, new_mode="telegram-approve",
            triggered_by="user", reason="post-soak upgrade",
            gate_snapshot=snap, now=_NOW,
        )
        await s.commit()
        row = (await s.execute(sa.text(
            "SELECT * FROM mode_change_log WHERE user_id=1"
        ))).first()
    assert row is not None
    assert row.old_mode == "manual"
    assert row.new_mode == "telegram-approve"
    assert row.triggered_by == "user"
    assert row.reason == "post-soak upgrade"
    assert row.gate_snapshot is not None
    assert "telegram-approve" in row.gate_snapshot


@pytest.mark.asyncio
async def test_auto_demote_triggered_by_label_persisted() -> None:
    """Spec §4.4 — auto-demotion writes triggered_by='auto-demote'."""
    engine = await _setup_db()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='fully-auto' WHERE id=1"
        ))
        await set_mode(
            s, user_id=1, new_mode="telegram-approve",
            triggered_by="auto-demote",
            reason="daily loss > 5%",
            now=_NOW,
        )
        await s.commit()
        row = (await s.execute(sa.text(
            "SELECT triggered_by FROM mode_change_log WHERE user_id=1"
        ))).first()
    assert row is not None
    assert row.triggered_by == "auto-demote"
