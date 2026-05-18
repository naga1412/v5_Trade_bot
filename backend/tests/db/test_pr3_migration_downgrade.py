"""FU-10 anticipation: PR3 migration upgrade → downgrade → upgrade round-trip.

Per PR3 spec §6.4: PR3's migration is non-trivial (PK extensions, raw SQL
constraint changes, G1 column additions on two tables). FU-10 (untested
downgrade paths repo-wide) is queued separately; PR3 exercises its OWN
round-trip locally so the rollback path is proven before merge.

Postgres-only. SQLite tests skip.

Strategy: run alembic CLI as a subprocess (not the in-process Python API)
to mirror exactly what an operator would run during a Stage-2 rollback.
The subprocess inherits DATABASE_URL from os.environ.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


_DSN = os.environ.get("DATABASE_URL", "")
_IS_PG = _DSN.startswith("postgresql")

_REV = "0021_pr3_shadow_per_tf"
_PRIOR = "0020_pr1_record_only_columns"

_BACKEND_DIR = Path(__file__).resolve().parents[2]


pytestmark = pytest.mark.skipif(
    not _IS_PG,
    reason="Postgres DATABASE_URL not set — migration tests are CI-only.",
)


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    """Run `python -m alembic <args>` in backend/, returning the result."""
    return subprocess.run(
        ["python", "-m", "alembic", *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        cwd=str(_BACKEND_DIR),
        check=False,
    )


def test_pr3_migration_round_trip() -> None:
    """upgrade → downgrade → upgrade. Each step exits 0."""
    # 1. Ensure we're at PR3 head before round-trip (CI alembic-upgrade-head
    #    has already run; this is a safety upgrade for local runs).
    r = _alembic("upgrade", _REV)
    assert r.returncode == 0, f"upgrade-to-PR3 failed: stderr={r.stderr}"

    # 2. Downgrade to the prior PR1 revision.
    r = _alembic("downgrade", _PRIOR)
    assert r.returncode == 0, (
        f"downgrade from {_REV} to {_PRIOR} failed: stderr={r.stderr}"
    )

    # 3. Re-upgrade to PR3 head — proves the downgrade left no orphan state.
    r = _alembic("upgrade", _REV)
    assert r.returncode == 0, (
        f"re-upgrade to {_REV} after downgrade failed: stderr={r.stderr}"
    )


def test_pr3_downgrade_drops_g1_columns_and_restores_old_pk() -> None:
    """Spot-check the downgrade did what the migration body says.

    Runs the downgrade once, asserts old schema is back, then re-upgrades
    so the test leaves the DB in the canonical PR3 state for follow-on tests.
    """
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine
    import asyncio

    async def _check() -> None:
        engine = create_async_engine(_DSN)
        async with engine.connect() as conn:
            # G1 columns should be absent post-downgrade
            cols = (await conn.execute(sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='shadow_trades' "
                "  AND column_name IN ('hold_scaling_factor', 'hold_timeout_bars');"
            ))).all()
            assert cols == [], (
                f"downgrade should have dropped G1 columns; still present: {cols}"
            )
            # shadow_cooldowns PK should be back to (user_id, symbol)
            pk_rows = (await conn.execute(sa.text(
                "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
                "  ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = 'shadow_cooldowns'::regclass "
                "  AND i.indisprimary ORDER BY array_position(i.indkey, a.attnum);"
            ))).all()
            pk = [r.attname for r in pk_rows]
            assert pk == ["user_id", "symbol"], (
                f"downgraded PK should be (user_id, symbol); got {pk}"
            )
        await engine.dispose()

    # Downgrade
    r = _alembic("downgrade", _PRIOR)
    assert r.returncode == 0, f"downgrade failed: {r.stderr}"

    # Inspect
    asyncio.run(_check())

    # Re-upgrade so the DB ends in PR3 state for follow-on tests
    r = _alembic("upgrade", _REV)
    assert r.returncode == 0, f"re-upgrade failed: {r.stderr}"
