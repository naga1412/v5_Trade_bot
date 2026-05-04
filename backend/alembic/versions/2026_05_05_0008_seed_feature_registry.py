"""Seed feature_registry with initial indicator set

Revision ID: 0008_seed_feature_registry
Revises: 0007_ml_tables_ghost
Create Date: 2026-05-05

Loads app/ml/seeds/feature_registry.json. Uses raw SQL via op.execute() with
ON CONFLICT (name) DO NOTHING so the migration is safely re-runnable. This is
intentionally a sync data migration (not the async seeds_loader used by tests)
because alembic's online runner is already inside an event loop and
asyncio.run() from inside a migration would either fail (loop already running)
or use a different connection than the one alembic just used to create the
table.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op


revision: str = "0008_seed_feature_registry"
down_revision: str | None = "0007_ml_tables_ghost"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SEED_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "app" / "ml" / "seeds" / "feature_registry.json"
)


def upgrade() -> None:
    entries = json.loads(_SEED_PATH.read_text())
    for e in entries:
        op.execute(
            sa.text(
                "INSERT INTO feature_registry "
                "(name, version, description, dtype, layer, computation) "
                "VALUES (:n, :v, :d, :dt, :l, :c) "
                "ON CONFLICT (name) DO NOTHING"
            ).bindparams(
                sa.bindparam("n", e["name"]),
                sa.bindparam("v", e.get("version", 1)),
                sa.bindparam("d", e["description"]),
                sa.bindparam("dt", e["dtype"]),
                sa.bindparam("l", e.get("layer")),
                sa.bindparam("c", e["computation"]),
            )
        )


def downgrade() -> None:
    op.execute("DELETE FROM feature_registry;")
