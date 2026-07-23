"""PR10 persistence — symbol_performance_snapshots round-trip.

Append-only writes via `insert_with_chain` (hash-chained per audit
convention). Reads via `load_latest_snapshots_per_symbol` — returns
dict keyed on symbol holding the row with the newest `computed_at`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import insert_with_chain


@dataclass(frozen=True)
class SymbolSnapshot:
    """One row from symbol_performance_snapshots, post-load."""
    symbol: str
    window_start: datetime
    window_end: datetime
    trades_count: int
    win_rate: float | None
    sharpe: float | None
    allowed: bool
    computed_at: datetime


def _to_dt(value: Any) -> datetime:
    """SQLite stringifies datetimes; Postgres returns native. Normalize."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


async def insert_snapshot_row(
    session: AsyncSession,
    *,
    symbol: str,
    window_start: datetime,
    window_end: datetime,
    trades_count: int,
    win_rate: float | None,
    sharpe: float | None,
    allowed: bool,
    computed_at: datetime,
) -> str:
    """Append one snapshot row via insert_with_chain. Returns row_hash."""
    # Bind datetime objects directly — insert_with_chain forwards the
    # payload dict as SQL bind params against TIMESTAMPTZ columns. See
    # test_no_isoformat_in_sql_bind for the bug class this avoids.
    # Hash impact: symbol_performance_snapshots' HASH_PAYLOAD_COLUMNS
    # excludes all datetime fields, so switching from ISO string to
    # datetime does not alter the row_hash.
    payload = {
        "symbol": symbol,
        "window_start": window_start,
        "window_end": window_end,
        "trades_count": trades_count,
        "win_rate": win_rate,
        "sharpe": sharpe,
        "allowed": allowed,
        "computed_at": computed_at,
        "inputs_hash": None,
    }
    return await insert_with_chain(
        session, "symbol_performance_snapshots", payload,
    )


async def load_latest_snapshots_per_symbol(
    session: AsyncSession,
) -> dict[str, SymbolSnapshot]:
    """Return the most-recent snapshot per symbol.

    Uses a correlated subquery on MAX(computed_at) — portable across
    SQLite (tests) and Postgres (prod) without a dialect branch.
    Returns dict keyed on symbol.
    """
    sql = sa.text(
        "SELECT symbol, window_start, window_end, trades_count, "
        "       win_rate, sharpe, allowed, computed_at "
        "  FROM symbol_performance_snapshots t1 "
        " WHERE computed_at = ( "
        "       SELECT MAX(computed_at) "
        "         FROM symbol_performance_snapshots t2 "
        "        WHERE t2.symbol = t1.symbol "
        "     )"
    )
    rows = (await session.execute(sql)).all()
    out: dict[str, SymbolSnapshot] = {}
    for r in rows:
        out[r.symbol] = SymbolSnapshot(
            symbol=r.symbol,
            window_start=_to_dt(r.window_start),
            window_end=_to_dt(r.window_end),
            trades_count=int(r.trades_count),
            win_rate=float(r.win_rate) if r.win_rate is not None else None,
            sharpe=float(r.sharpe) if r.sharpe is not None else None,
            allowed=bool(r.allowed),
            computed_at=_to_dt(r.computed_at),
        )
    return out
