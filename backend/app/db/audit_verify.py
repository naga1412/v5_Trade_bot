from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import GENESIS_HASH, _filter_for_hash, compute_row_hash


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
    session: AsyncSession, table: str, *, columns: list[str] | None = None
) -> VerifyResult:
    """Walk `table` in id order; recompute row_hash for each row.
    Returns VerifyResult with any breaks logged.

    If ``columns`` is supplied (legacy callers), those columns are selected
    and filtered through ``_filter_for_hash`` before hashing.  If omitted,
    all non-chain-metadata columns are selected and filtered — so recording-only
    columns (not in ``HASH_PAYLOAD_COLUMNS``) are silently dropped from the
    hash computation, matching the behaviour of ``insert_with_chain``.
    """
    _CHAIN_META = {"id", "prev_hash", "row_hash"}

    if columns is not None:
        # Legacy path: explicit column list.  Validate table is whitelisted
        # (fail-secure) then select exactly those columns.
        _filter_for_hash(table, {})  # raises ValueError for unknown table
        select_cols = list(columns)
        cols_sql = ", ".join(["id"] + select_cols + ["prev_hash", "row_hash"])
        rows = (await session.execute(
            sa.text(f"SELECT {cols_sql} FROM {table} ORDER BY id ASC")
        )).all()

        result = VerifyResult(ok=True, rows_checked=len(rows))
        expected_prev = GENESIS_HASH
        for row in rows:
            row_dict: dict[str, Any] = {c: getattr(row, c) for c in select_cols}
            filtered = _filter_for_hash(table, row_dict)
            expected_hash = compute_row_hash(expected_prev, filtered)
            if row.prev_hash != expected_prev or row.row_hash != expected_hash:
                result.ok = False
                result.violations.append(Violation(
                    row_id=row.id, expected=expected_hash, actual=row.row_hash,
                ))
            expected_prev = row.row_hash
        return result

    # Whitelist-native path: select * then filter through HASH_PAYLOAD_COLUMNS.
    # This is the forward path for new callers; tolerates recording-only columns
    # that are present in the schema but absent from the whitelist.
    _filter_for_hash(table, {})  # raises ValueError for unknown table (fail-secure)

    rows = (await session.execute(
        sa.text(f"SELECT * FROM {table} ORDER BY id ASC")
    )).all()

    result = VerifyResult(ok=True, rows_checked=len(rows))
    expected_prev = GENESIS_HASH
    for row in rows:
        row_dict = {
            c: v for c, v in row._mapping.items()
            if c not in _CHAIN_META
        }
        filtered = _filter_for_hash(table, row_dict)
        expected_hash = compute_row_hash(expected_prev, filtered)
        if row.prev_hash != expected_prev or row.row_hash != expected_hash:
            result.ok = False
            result.violations.append(Violation(
                row_id=row.id, expected=expected_hash, actual=row.row_hash,
            ))
        expected_prev = row.row_hash
    return result
