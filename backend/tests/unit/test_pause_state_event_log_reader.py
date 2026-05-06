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
async def test_pause_event_log_returns_pause_and_resume_rows(
    session: AsyncSession,
) -> None:
    await pause_state.set_paused(
        True, by_email="admin@x.com", reason="travel",
        session=session, request_path="/api/v1/admin/system/pause",
    )
    await pause_state.set_paused(
        False, by_email="admin@x.com", reason=None,
        session=session, request_path="/api/v1/admin/system/resume",
    )
    log_ = await pause_state.pause_event_log(session)
    assert len(log_) == 2
    # Most recent first.
    assert log_[0].kind == "system_resumed"
    assert log_[0].reason is None
    assert log_[1].kind == "system_paused"
    assert log_[1].reason == "travel"
    assert log_[1].by_email == "admin@x.com"


@pytest.mark.asyncio
async def test_pause_event_log_excludes_non_pause_rows(
    session: AsyncSession,
) -> None:
    await session.execute(sa.text(
        "INSERT INTO auth_violations (attempted_email, reason) "
        "VALUES ('system', 'audit_chain_broken:predictions:42')"
    ))
    await pause_state.set_paused(
        True, by_email="a@x.com", reason="r",
        session=session, request_path="/api/v1/admin/system/pause",
    )
    log_ = await pause_state.pause_event_log(session)
    assert len(log_) == 1
    assert log_[0].kind == "system_paused"


@pytest.mark.asyncio
async def test_pause_event_log_limit(session: AsyncSession) -> None:
    for i in range(5):
        await pause_state.set_paused(
            True, by_email=f"a{i}@x.com", reason=str(i),
            session=session, request_path="/api/v1/admin/system/pause",
        )
        await pause_state.set_paused(
            False, by_email=f"a{i}@x.com", reason=None,
            session=session, request_path="/api/v1/admin/system/resume",
        )
    log_ = await pause_state.pause_event_log(session, limit=3)
    assert len(log_) == 3
