"""FU-10 anticipation: PR10 upgrade → downgrade → upgrade → head round-trip."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_DSN = os.environ.get("DATABASE_URL", "")
_IS_PG = _DSN.startswith("postgresql")

_REV = "0024_pr10_symbol_perf_snapshots"
_PRIOR = "0023_pr9_users_balance_tier"

_BACKEND_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(not _IS_PG, reason="Postgres-only")


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python", "-m", "alembic", *args],
        capture_output=True, text=True,
        env=os.environ.copy(), cwd=str(_BACKEND_DIR), check=False,
    )


def test_pr10_migration_round_trip() -> None:
    r = _alembic("upgrade", _REV)
    assert r.returncode == 0, f"upgrade failed: {r.stderr}"
    r = _alembic("downgrade", _PRIOR)
    assert r.returncode == 0, f"downgrade failed: {r.stderr}"
    r = _alembic("upgrade", "head")
    assert r.returncode == 0, f"final upgrade to head failed: {r.stderr}"


def test_pr10_downgrade_drops_table() -> None:
    import asyncio
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _check() -> None:
        r = _alembic("downgrade", _PRIOR)
        assert r.returncode == 0
        engine = create_async_engine(_DSN)
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(sa.text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'symbol_performance_snapshots'"
                ))).all()
                assert rows == []
        finally:
            await engine.dispose()
        r = _alembic("upgrade", "head")
        assert r.returncode == 0

    asyncio.run(_check())
