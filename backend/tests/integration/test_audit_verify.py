import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.db.audit import insert_with_chain
from app.db.audit_verify import verify_chain


@pytest.mark.asyncio
async def test_unbroken_chain_passes() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "data TEXT, prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
        ))
    async with AsyncSession(engine) as session:
        for i in range(5):
            await insert_with_chain(session, "t", {"data": f"row-{i}"})
        await session.commit()
        result = await verify_chain(session, "t", columns=["data"])
    assert result.ok
    assert result.violations == []


@pytest.mark.asyncio
async def test_tampered_row_detected() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "data TEXT, prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
        ))
    async with AsyncSession(engine) as session:
        for i in range(5):
            await insert_with_chain(session, "t", {"data": f"row-{i}"})
        await session.commit()
        # tamper row 3
        await session.execute(sa.text("UPDATE t SET data='HACKED' WHERE id=3"))
        await session.commit()
        result = await verify_chain(session, "t", columns=["data"])
    assert not result.ok
    assert any(v.row_id == 3 for v in result.violations)
