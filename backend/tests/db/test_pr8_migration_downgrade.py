"""FU-10 anticipation: PR8 migration upgrade → downgrade → upgrade round-trip.

PR8 ships the live_cooldowns table + active-only partial index. Both
need to round-trip cleanly so a Stage-2 rollback (revert main + alembic
downgrade) leaves the schema in the prior PR3 state. PR8 exercises its
OWN round-trip locally so the rollback path is proven before merge.

Postgres-only. SQLite tests skip.

Strategy mirrors test_pr3_migration_downgrade.py — alembic CLI as a
subprocess so this matches exactly what an operator would run during
rollback.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


_DSN = os.environ.get("DATABASE_URL", "")
_IS_PG = _DSN.startswith("postgresql")

_REV = "0022_pr8_live_cooldowns"
_PRIOR = "0021_pr3_shadow_per_tf"

_BACKEND_DIR = Path(__file__).resolve().parents[2]


pytestmark = pytest.mark.skipif(
    not _IS_PG,
    reason="Postgres DATABASE_URL not set — migration tests are CI-only.",
)


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python", "-m", "alembic", *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        cwd=str(_BACKEND_DIR),
        check=False,
    )


def test_pr8_migration_round_trip() -> None:
    """upgrade → downgrade → upgrade. Each step exits 0."""
    r = _alembic("upgrade", _REV)
    assert r.returncode == 0, f"upgrade-to-PR8 failed: stderr={r.stderr}"

    r = _alembic("downgrade", _PRIOR)
    assert r.returncode == 0, (
        f"downgrade from {_REV} to {_PRIOR} failed: stderr={r.stderr}"
    )

    r = _alembic("upgrade", _REV)
    assert r.returncode == 0, (
        f"re-upgrade to {_REV} after downgrade failed: stderr={r.stderr}"
    )


def test_pr8_downgrade_drops_live_cooldowns_table_and_index() -> None:
    """Spot-check: downgrade removes the table + partial index.

    Runs the downgrade once, asserts the table is gone, then re-upgrades
    so the test leaves the DB in the canonical PR8 state for follow-on tests.
    """
    import asyncio

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _check() -> None:
        # Downgrade first
        r = _alembic("downgrade", _PRIOR)
        assert r.returncode == 0, f"downgrade failed: {r.stderr}"

        engine = create_async_engine(_DSN)
        try:
            async with engine.connect() as conn:
                tbl_rows = (await conn.execute(sa.text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'live_cooldowns'"
                ))).all()
                assert tbl_rows == [], "live_cooldowns survived downgrade"

                idx_rows = (await conn.execute(sa.text(
                    "SELECT 1 FROM pg_indexes "
                    "WHERE indexname = 'ix_live_cooldowns_active'"
                ))).all()
                assert idx_rows == [], "partial index survived downgrade"
        finally:
            await engine.dispose()

        # Re-upgrade so the test leaves DB at PR8 head
        r = _alembic("upgrade", _REV)
        assert r.returncode == 0, f"re-upgrade failed: {r.stderr}"

    asyncio.run(_check())
