"""FU-10 anticipation: PR9 migration upgrade → downgrade → upgrade round-trip.

PR9 adds users.balance_tier. The round-trip proves the downgrade drops
the column cleanly. Always leaves DB at alembic head so follow-on tests
(if any future migration lands) see canonical state — see the
test_pr3_migration_downgrade.py note for the rationale.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


_DSN = os.environ.get("DATABASE_URL", "")
_IS_PG = _DSN.startswith("postgresql")

_REV = "0023_pr9_users_balance_tier"
_PRIOR = "0022_pr8_live_cooldowns"

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


def test_pr9_migration_round_trip() -> None:
    """upgrade → downgrade → upgrade → head.

    Always leaves DB at alembic head so follow-on test files don't see a
    pinned revision (would break the moment PR_N+1 adds a migration).
    """
    r = _alembic("upgrade", _REV)
    assert r.returncode == 0, f"upgrade-to-PR9 failed: stderr={r.stderr}"

    r = _alembic("downgrade", _PRIOR)
    assert r.returncode == 0, (
        f"downgrade from {_REV} to {_PRIOR} failed: stderr={r.stderr}"
    )

    r = _alembic("upgrade", "head")
    assert r.returncode == 0, f"final upgrade to head failed: stderr={r.stderr}"


def test_pr9_downgrade_drops_balance_tier_column() -> None:
    """Spot-check: downgrade removes the balance_tier column.

    Runs the downgrade once, asserts the column is gone, then re-upgrades
    so the test leaves the DB at alembic head for follow-on tests.
    """
    import asyncio

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _check() -> None:
        r = _alembic("downgrade", _PRIOR)
        assert r.returncode == 0, f"downgrade failed: {r.stderr}"

        engine = create_async_engine(_DSN)
        try:
            async with engine.connect() as conn:
                col_rows = (await conn.execute(sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'users' "
                    "  AND column_name = 'balance_tier'"
                ))).all()
                assert col_rows == [], "balance_tier survived downgrade"
        finally:
            await engine.dispose()

        r = _alembic("upgrade", "head")
        assert r.returncode == 0, f"re-upgrade failed: {r.stderr}"

    asyncio.run(_check())
