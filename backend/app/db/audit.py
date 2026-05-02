import hashlib
import json
from typing import Any

GENESIS_HASH: str = "0" * 64


def canonical_row_json(row: dict[str, Any]) -> str:
    """Canonical JSON serialization for hashing.

    sort_keys=True and the compact separators give a deterministic
    byte representation, so the same row always hashes to the same value.
    """
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def compute_row_hash(prev_hash: str, row: dict[str, Any]) -> str:
    payload = (prev_hash + canonical_row_json(row)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def _last_row_hash(session: AsyncSession, table: str) -> str:
    result = await session.execute(
        sa.text(f"SELECT row_hash FROM {table} ORDER BY id DESC LIMIT 1")
    )
    row = result.first()
    return row.row_hash if row else GENESIS_HASH


async def insert_with_chain(
    session: AsyncSession, table: str, payload: dict[str, Any]
) -> str:
    """Insert payload + computed prev_hash/row_hash. Returns row_hash."""
    prev = await _last_row_hash(session, table)
    new_hash = compute_row_hash(prev, payload)
    full = {**payload, "prev_hash": prev, "row_hash": new_hash}
    cols = ", ".join(full.keys())
    params = ", ".join(f":{k}" for k in full.keys())
    await session.execute(
        sa.text(f"INSERT INTO {table} ({cols}) VALUES ({params})"), full
    )
    return new_hash
