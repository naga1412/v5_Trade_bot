import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.db.audit import insert_with_chain
from app.db.audit_verify import verify_chain


@pytest.mark.asyncio
async def test_unbroken_chain_passes() -> None:
    # Use a whitelisted table (predictions) with minimal schema.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT, prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
        ))
    async with AsyncSession(engine) as session:
        for i in range(5):
            await insert_with_chain(session, "predictions", {"symbol": f"SYM{i}"})
        await session.commit()
        result = await verify_chain(session, "predictions", columns=["symbol"])
    assert result.ok
    assert result.violations == []


@pytest.mark.asyncio
async def test_tampered_row_detected() -> None:
    # Use a whitelisted table (predictions) with minimal schema.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT, prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
        ))
    async with AsyncSession(engine) as session:
        for i in range(5):
            await insert_with_chain(session, "predictions", {"symbol": f"SYM{i}"})
        await session.commit()
        # tamper row 3
        await session.execute(sa.text("UPDATE predictions SET symbol='HACKED' WHERE id=3"))
        await session.commit()
        result = await verify_chain(session, "predictions", columns=["symbol"])
    assert not result.ok
    assert any(v.row_id == 3 for v in result.violations)
