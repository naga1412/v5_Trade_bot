from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import GENESIS_HASH, compute_row_hash


@dataclass
class Violation:
    row_id: int
    expected: str
    actual: str


@dataclass
class VerifyResult:
    ok: bool
    rows_checked: int = 0
    violations: list[Violation] = field(default_factory=list)


async def verify_chain(
    session: AsyncSession, table: str, *, columns: list[str]
) -> VerifyResult:
    """Walk `table` in id order; recompute row_hash for each row.
    Returns VerifyResult with any breaks logged.
    """
    cols_sql = ", ".join(["id"] + columns + ["prev_hash", "row_hash"])
    rows = (await session.execute(
        sa.text(f"SELECT {cols_sql} FROM {table} ORDER BY id ASC")
    )).all()

    result = VerifyResult(ok=True, rows_checked=len(rows))
    expected_prev = GENESIS_HASH
    for row in rows:
        payload = {c: getattr(row, c) for c in columns}
        expected_hash = compute_row_hash(expected_prev, payload)
        if row.prev_hash != expected_prev or row.row_hash != expected_hash:
            result.ok = False
            result.violations.append(Violation(
                row_id=row.id, expected=expected_hash, actual=row.row_hash,
            ))
        expected_prev = row.row_hash
    return result
