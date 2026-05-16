"""Schema-vs-whitelist drift detector.

Walks each chained table's actual column schema (introspected via
SQLAlchemy inspect) and asserts every column is in EITHER
HASH_PAYLOAD_COLUMNS[table] (tamper-evident) OR
NON_HASHED_ALLOW_LIST[table] (recording-only).

Fails if any column appears on the table without an explicit decision.
Forces every future PR adding a column to a chained table to
consciously decide its audit-chain status.

NOTE: This test requires a real database with the application schema
migrated (it introspects actual table columns via SQLAlchemy inspect).
It is skipped automatically when the DATABASE_URL points to an in-memory
SQLite instance that has no tables, or when the table does not exist.
Run it against the alembic-migrated Postgres test DB to validate schema
consistency.
"""

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import NoSuchTableError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.audit import HASH_PAYLOAD_COLUMNS, NON_HASHED_ALLOW_LIST


@pytest.fixture
async def db_inspector():
    """Use a real session against the configured test DB.

    Uses the application's standard DATABASE_URL env var so this test
    runs against whatever schema is currently migrated.
    """
    url = os.environ["DATABASE_URL"]
    engine = create_async_engine(url)
    yield engine
    await engine.dispose()


@pytest.mark.parametrize("table", sorted(HASH_PAYLOAD_COLUMNS.keys()))
async def test_every_column_classified(db_inspector, table):
    """Every column on `table` must be in whitelist OR allow-list."""
    try:
        async with db_inspector.connect() as conn:
            def _inspect(sync_conn):
                insp = sa.inspect(sync_conn)
                return [c["name"] for c in insp.get_columns(table)]
            actual_cols = set(await conn.run_sync(_inspect))
    except (OperationalError, ProgrammingError, NoSuchTableError) as exc:
        pytest.skip(
            f"Table `{table}` not found in test DB (run against migrated schema): {exc}"
        )

    classified = HASH_PAYLOAD_COLUMNS[table] | NON_HASHED_ALLOW_LIST.get(
        table, frozenset()
    )
    unclassified = actual_cols - classified
    assert not unclassified, (
        f"Table `{table}` has columns not in HASH_PAYLOAD_COLUMNS or "
        f"NON_HASHED_ALLOW_LIST: {sorted(unclassified)}. "
        f"Decide for each: tamper-evident (add to HASH_PAYLOAD_COLUMNS) "
        f"or recording-only (add to NON_HASHED_ALLOW_LIST)."
    )


@pytest.mark.parametrize("table", sorted(HASH_PAYLOAD_COLUMNS.keys()))
def test_no_overlap_between_lists(table):
    """A column can't be both hashed and unhashed."""
    overlap = HASH_PAYLOAD_COLUMNS[table] & NON_HASHED_ALLOW_LIST.get(
        table, frozenset()
    )
    assert not overlap, (
        f"Column(s) {sorted(overlap)} on `{table}` are in BOTH "
        f"HASH_PAYLOAD_COLUMNS and NON_HASHED_ALLOW_LIST — pick one."
    )
