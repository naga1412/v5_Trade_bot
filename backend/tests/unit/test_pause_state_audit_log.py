from typing import Any

import fakeredis.aioredis
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.ops import pause_state


@pytest.fixture
async def session(monkeypatch: pytest.MonkeyPatch) -> Any:
    pause_state._reset_for_tests()
    monkeypatch.setattr(
        pause_state, "_get_redis",
        lambda: fakeredis.aioredis.FakeRedis(decode_responses=True),
    )
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE auth_violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempted_email TEXT NOT NULL,
                attempted_at TEXT NOT NULL DEFAULT (datetime('now')),
                reason TEXT NOT NULL,
                jwt_sub TEXT,
                request_path TEXT
            )
        """))
    async with AsyncSession(engine) as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_set_paused_true_inserts_audit_row(session: AsyncSession) -> None:
    await pause_state.set_paused(
        True, by_email="admin@x.com", reason="travel",
        session=session, request_path="/api/v1/admin/system/pause",
    )
    rows = (await session.execute(sa.text(
        "SELECT attempted_email, reason, request_path FROM auth_violations"
    ))).all()
    assert len(rows) == 1
    assert rows[0].attempted_email == "admin@x.com"
    assert rows[0].reason == "system_paused: travel"
    assert rows[0].request_path == "/api/v1/admin/system/pause"


@pytest.mark.asyncio
async def test_set_paused_false_inserts_resume_row(session: AsyncSession) -> None:
    await pause_state.set_paused(
        True, by_email="admin@x.com", reason="r",
        session=session, request_path="/api/v1/admin/system/pause",
    )
    await pause_state.set_paused(
        False, by_email="admin@x.com", reason=None,
        session=session, request_path="/api/v1/admin/system/resume",
    )
    rows = (await session.execute(sa.text(
        "SELECT reason, request_path FROM auth_violations ORDER BY id"
    ))).all()
    assert len(rows) == 2
    assert rows[1].reason == "system_resumed"
    assert rows[1].request_path == "/api/v1/admin/system/resume"


@pytest.mark.asyncio
async def test_set_paused_no_reason_pauses_with_blank_message(
    session: AsyncSession,
) -> None:
    await pause_state.set_paused(
        True, by_email="admin@x.com", reason=None,
        session=session, request_path="/api/v1/admin/system/pause",
    )
    rows = (await session.execute(sa.text(
        "SELECT reason FROM auth_violations"
    ))).all()
    assert rows[0].reason == "system_paused: "
