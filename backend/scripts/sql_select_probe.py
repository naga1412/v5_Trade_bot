"""Guarded read-only SQL SELECT probe for ops-debug.

Reads SQL from stdin. Runs against ``DATABASE_URL`` on the backend
container. Enforced read-only in three layers:

  1. Shape validation (client-side): reject if the statement is not
     exactly one SELECT or WITH statement after stripping ``--`` line
     comments. Multiple statements, DDL, DML, and utility commands are
     rejected before we ever touch the DB.
  2. Session-level read-only via ``SET SESSION CHARACTERISTICS AS
     TRANSACTION READ ONLY`` — any implicit transaction that opens
     against this connection defaults to read-only, so an INSERT /
     UPDATE / DELETE inside a WITH clause still errors at the DB.
  3. ``SET statement_timeout = '15000ms'`` on the same session — the
     probe cannot pin the DB for more than 15 s regardless of query
     shape.

Row cap: at most 500 rows printed. If the query returns more, a
``-- truncated at 500 rows --`` marker is emitted at end of output.

Usage (via ops-debug ``sql-select`` probe):
    echo "SELECT 1" | python /app/scripts/sql_select_probe.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

MAX_ROWS = 500
STATEMENT_TIMEOUT_MS = 15_000


def strip_sql_line_comments(sql: str) -> str:
    """Remove ``--`` line comments. Enough for our first-keyword check."""
    out: list[str] = []
    for line in sql.splitlines():
        idx = line.find("--")
        if idx >= 0:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def validate_shape(sql: str) -> tuple[bool, str]:
    """Return (ok, reason).

    Rejects empty / multi-statement / non-SELECT-non-WITH input before
    the DB ever sees it. Trailing semicolon is tolerated.
    """
    stripped = strip_sql_line_comments(sql).strip()
    if not stripped:
        return False, "empty statement"
    parts = [p.strip() for p in stripped.rstrip(";").split(";") if p.strip()]
    if len(parts) > 1:
        return (
            False,
            f"multiple statements ({len(parts)} found); "
            "only one SELECT/WITH permitted",
        )
    match = re.match(r"^\s*(\w+)", parts[0])
    if not match:
        return False, "no leading keyword"
    keyword = match.group(1).upper()
    if keyword not in ("SELECT", "WITH"):
        return (
            False,
            f"first keyword is {keyword!r}; only SELECT/WITH permitted",
        )
    return True, ""


def _to_asyncpg_url(url: str) -> str:
    """Strip SQLAlchemy dialect prefix so asyncpg accepts the URL."""
    for prefix in ("postgresql+asyncpg://", "postgres+asyncpg://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    return url


async def run_query(sql: str) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1
    conn = await asyncpg.connect(_to_asyncpg_url(url))
    try:
        await conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        await conn.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
        # Stream via a server-side cursor so a runaway result set never
        # buffers more than MAX_ROWS+1 records in the probe process.
        # Cursors require an explicit transaction.
        async with conn.transaction():
            cursor = conn.cursor(sql)
            rows = await cursor.fetch(MAX_ROWS + 1)
    finally:
        await conn.close()

    if not rows:
        print("(no rows)")
        return 0
    columns = list(rows[0].keys())
    print("\t".join(columns))
    for row in rows[:MAX_ROWS]:
        print("\t".join(
            "" if row[c] is None else str(row[c]) for c in columns
        ))
    if len(rows) > MAX_ROWS:
        print(f"-- truncated at {MAX_ROWS} rows --")
    return 0


def main() -> int:
    sql = sys.stdin.read()
    ok, reason = validate_shape(sql)
    if not ok:
        print(f"REJECTED: {reason}", file=sys.stderr)
        return 3
    try:
        return asyncio.run(run_query(sql))
    except asyncpg.exceptions.ReadOnlySQLTransactionError as e:
        print(f"DB REJECTED (read-only violation): {e}", file=sys.stderr)
        return 4
    except asyncpg.exceptions.QueryCanceledError:
        print(
            f"QUERY TIMED OUT after {STATEMENT_TIMEOUT_MS}ms — narrow "
            "the WHERE clause and try again",
            file=sys.stderr,
        )
        return 5
    except asyncpg.exceptions.PostgresError as e:
        print(f"DB ERROR: {e}", file=sys.stderr)
        return 6


if __name__ == "__main__":
    sys.exit(main())
