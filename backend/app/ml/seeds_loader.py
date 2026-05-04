"""Idempotent seed loader for feature_registry.

Used by alembic 0008 and by tests. Reads the JSON seed file and inserts rows
with ON CONFLICT(name) DO NOTHING semantics. SQLite doesn't support that exact
clause; we emulate by reading existing names first then inserting only the new
ones. Postgres production path follows the same path (it's a no-op on conflict
because we filter via the read first).
"""
from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

_SEED_PATH = Path(__file__).parent / "seeds" / "feature_registry.json"


async def load_feature_registry_seed(session: AsyncSession) -> int:
    """Insert rows from feature_registry.json that aren't already present.

    Returns the number of newly-inserted rows.
    """
    entries = json.loads(_SEED_PATH.read_text())
    result = await session.execute(sa.text("SELECT name FROM feature_registry"))
    existing = {row[0] for row in result.all()}
    inserted = 0
    for e in entries:
        if e["name"] in existing:
            continue
        await session.execute(
            sa.text(
                "INSERT INTO feature_registry "
                "(name, version, description, dtype, layer, computation) "
                "VALUES (:n, :v, :d, :dt, :l, :c)"
            ),
            {
                "n": e["name"],
                "v": e.get("version", 1),
                "d": e["description"],
                "dt": e["dtype"],
                "l": e.get("layer"),
                "c": e["computation"],
            },
        )
        inserted += 1
    return inserted
