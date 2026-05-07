"""Tests for app.ops.telegram_timeout (SP-4 Phase E3)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ops.telegram_bot import TelegramConfig
from app.ops.telegram_timeout import (
    expire_pending_approvals,
    list_pending_approvals,
    make_pending_notes,
    parse_pending_notes,
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
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
            "activated_at TEXT, deactivated_at TEXT, notes TEXT)"
        ))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _insert_pending(
    session: AsyncSession,
    *,
    version: str,
    notes: str,
    is_active: bool = False,
    deactivated_at: str | None = None,
) -> int:
    await session.execute(sa.text(
        "INSERT INTO rl_checkpoints "
        "(model_name, version, checkpoint_uri, sha256, trained_at, "
        " train_data_window, eval_results, is_active, deactivated_at, notes) "
        "VALUES ('ppo_policy_v1', :v, 'file:///x', :s, "
        " '2026-05-01T00:00:00Z', 'x', '{}', :a, :d, :n)"
    ), {
        "v": version, "s": "0" * 64,
        "a": 1 if is_active else 0, "d": deactivated_at, "n": notes,
    })
    await session.commit()
    row = (await session.execute(sa.text(
        "SELECT id FROM rl_checkpoints WHERE version = :v"
    ), {"v": version})).first()
    assert row is not None
    return int(row.id)


def _config() -> TelegramConfig:
    return TelegramConfig(bot_token="tok", chat_id="42")


# ---- parse_pending_notes / make_pending_notes ---------------------------


def test_make_then_parse_round_trips() -> None:
    deadline = datetime(2026, 5, 21, 3, 30, tzinfo=timezone.utc)
    notes = make_pending_notes(deadline=deadline)
    parsed_d, parsed_r = parse_pending_notes(notes)
    assert parsed_d == deadline
    assert parsed_r is None


def test_make_with_reminder_round_trips() -> None:
    deadline = datetime(2026, 5, 21, 3, 30, tzinfo=timezone.utc)
    reminder = datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)
    notes = make_pending_notes(deadline=deadline, last_reminder_at=reminder)
    parsed_d, parsed_r = parse_pending_notes(notes)
    assert parsed_d == deadline
    assert parsed_r == reminder


def test_parse_handles_None_notes() -> None:
    d, r = parse_pending_notes(None)
    assert d is None and r is None


def test_parse_handles_empty_or_unrelated_notes() -> None:
    d, r = parse_pending_notes("")
    assert d is None and r is None
    d, r = parse_pending_notes("just an operator comment")
    assert d is None and r is None


# ---- list_pending_approvals --------------------------------------------


@pytest.mark.asyncio
async def test_list_returns_only_pending_with_marker(session) -> None:
    # Pending — should be returned
    deadline = datetime(2026, 5, 21, 3, 30, tzinfo=timezone.utc)
    await _insert_pending(
        session, version="v1-001",
        notes=make_pending_notes(deadline=deadline),
    )
    # Active — should NOT be returned
    await _insert_pending(
        session, version="v1-002",
        notes=make_pending_notes(deadline=deadline),
        is_active=True,
    )
    # Already-rejected (deactivated_at set) — should NOT be returned
    await _insert_pending(
        session, version="v1-003",
        notes=make_pending_notes(deadline=deadline),
        deactivated_at="2026-05-22T00:00:00+00:00",
    )
    # Inactive without pending notes — should NOT be returned
    await _insert_pending(
        session, version="v1-004",
        notes="some other operator note",
    )

    pending = await list_pending_approvals(session)
    assert [p.version for p in pending] == ["v1-001"]


# ---- expire_pending_approvals ------------------------------------------


@pytest.mark.asyncio
async def test_no_pending_no_op(session) -> None:
    res = await expire_pending_approvals(
        session=session, config=None,
        now=datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc),
    )
    assert res.auto_rejected == 0
    assert res.reminders_sent == 0
    assert res.still_pending == 0


@pytest.mark.asyncio
async def test_past_deadline_auto_rejects(session) -> None:
    deadline = datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc)
    cid = await _insert_pending(
        session, version="v1-001",
        notes=make_pending_notes(deadline=deadline),
    )

    # Now is one second past the deadline.
    now = deadline + timedelta(seconds=1)
    res = await expire_pending_approvals(
        session=session, config=None, now=now,
    )
    await session.commit()

    assert res.auto_rejected == 1
    assert res.reminders_sent == 0
    assert res.still_pending == 0

    row = (await session.execute(sa.text(
        "SELECT deactivated_at FROM rl_checkpoints WHERE id = :i"
    ), {"i": cid})).first()
    assert row.deactivated_at is not None


@pytest.mark.asyncio
async def test_within_reminder_window_first_ping(session) -> None:
    """now is inside the last 24h before deadline; no prior reminder → send one."""
    deadline = datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc)
    cid = await _insert_pending(
        session, version="v1-001",
        notes=make_pending_notes(deadline=deadline),
    )

    # 12 hours before deadline.
    now = deadline - timedelta(hours=12)
    res = await expire_pending_approvals(
        session=session, config=None, now=now,
    )
    await session.commit()

    assert res.reminders_sent == 1
    assert res.auto_rejected == 0
    # last_reminder_at written into notes.
    row = (await session.execute(sa.text(
        "SELECT notes FROM rl_checkpoints WHERE id = :i"
    ), {"i": cid})).first()
    _, last = parse_pending_notes(row.notes)
    assert last == now


@pytest.mark.asyncio
async def test_recent_reminder_no_repeat(session) -> None:
    """last_reminder_at <24h ago → don't spam; still pending."""
    deadline = datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc)
    last_reminder = deadline - timedelta(hours=12)
    await _insert_pending(
        session, version="v1-001",
        notes=make_pending_notes(
            deadline=deadline, last_reminder_at=last_reminder,
        ),
    )

    # 6 hours after the last reminder, 6 hours before deadline.
    now = last_reminder + timedelta(hours=6)
    res = await expire_pending_approvals(
        session=session, config=None, now=now,
    )

    assert res.reminders_sent == 0
    assert res.auto_rejected == 0
    assert res.still_pending == 1


@pytest.mark.asyncio
async def test_outside_reminder_window_still_pending(session) -> None:
    """now is well before the last 24h; no reminder, no expire."""
    deadline = datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc)
    await _insert_pending(
        session, version="v1-001",
        notes=make_pending_notes(deadline=deadline),
    )

    # 5 days before deadline.
    now = deadline - timedelta(days=5)
    res = await expire_pending_approvals(
        session=session, config=None, now=now,
    )

    assert res.reminders_sent == 0
    assert res.auto_rejected == 0
    assert res.still_pending == 1


@pytest.mark.asyncio
async def test_telegram_send_fired_on_auto_reject(session) -> None:
    """When config supplied, expire pings Telegram on each auto-reject."""
    deadline = datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc)
    await _insert_pending(
        session, version="v1-expired",
        notes=make_pending_notes(deadline=deadline),
    )

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = "{}"
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=mock_resp)

    res = await expire_pending_approvals(
        session=session, config=_config(),
        now=deadline + timedelta(hours=1), client=client,
    )
    assert res.auto_rejected == 1
    client.post.assert_awaited_once()
    posted = client.post.call_args
    assert "sendMessage" in posted.args[0]
    assert "auto-rejected" in posted.kwargs["json"]["text"]
