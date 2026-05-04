"""Unit tests for feature_registry seed JSON + idempotent loader (SP-1 A3)."""
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

SEED_PATH = Path("app/ml/seeds/feature_registry.json")


def test_seed_json_loadable_and_has_min_set() -> None:
    data = json.loads(SEED_PATH.read_text())
    names = {e["name"] for e in data}
    assert {
        "open", "high", "low", "close", "volume",
        "rsi_14", "macd_line", "macd_signal", "macd_hist",
        "atr_14", "ema_20", "ema_50",
    } <= names
    for entry in data:
        assert entry["dtype"] in {"float", "int", "bool", "category"}
        assert isinstance(entry["computation"], str) and entry["computation"]


@pytest.mark.asyncio
async def test_seed_inserts_idempotently_on_clean_db() -> None:
    """Seed function used by alembic 0008 must be importable + idempotent."""
    from app.ml.seeds_loader import load_feature_registry_seed

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE feature_registry ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL UNIQUE, version INTEGER NOT NULL DEFAULT 1, "
            "description TEXT NOT NULL, dtype TEXT NOT NULL, "
            "layer INTEGER, computation TEXT NOT NULL, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
        ))

    async with AsyncSession(engine) as session:
        n1 = await load_feature_registry_seed(session)
        await session.commit()
        n2 = await load_feature_registry_seed(session)
        await session.commit()

    assert n1 >= 12
    assert n2 == 0  # idempotent re-run inserts zero new rows

    await engine.dispose()
