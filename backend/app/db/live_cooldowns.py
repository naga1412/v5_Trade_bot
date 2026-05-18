"""PR8 live_cooldowns persistence — UPSERT / LOAD / DELETE.

Mirrors `app.shadow.persistence` patterns. Postgres uses
`ON CONFLICT ... DO UPDATE`; SQLite uses `INSERT OR REPLACE` (test-only
path). Dialect detection runs once per call from the bound session.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class LiveCooldown:
    user_id: int
    symbol: str
    cooldown_until: datetime
    last_exit_reason: str
    last_mtf_agreement: int | None
    updated_at: datetime


def _to_dt(value: Any) -> datetime:
    """Coerce a DB-returned timestamp into a datetime.

    Mirrors `app.shadow.persistence._to_dt` — asyncpg returns native
    datetime; SQLite returns ISO strings.
    """
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _is_pg(session: AsyncSession) -> bool:
    bind = session.bind
    return bind is not None and bind.dialect.name == "postgresql"


async def upsert_cooldown(
    session: AsyncSession,
    *,
    user_id: int,
    symbol: str,
    cooldown_until: datetime,
    last_exit_reason: str,
    last_mtf_agreement: int | None,
) -> None:
    """Insert or overwrite the (user_id, symbol) cooldown row.

    Idempotent: subsequent closes on the same symbol overwrite the
    cooldown_until + last_exit_reason + last_mtf_agreement.
    """
    now = datetime.now(tz=timezone.utc)
    params = {
        "uid": user_id,
        "sym": symbol,
        "cu": cooldown_until,
        "reason": last_exit_reason,
        "mtf": last_mtf_agreement,
        "upd": now,
    }
    if _is_pg(session):
        sql = sa.text(
            "INSERT INTO live_cooldowns "
            "(user_id, symbol, cooldown_until, last_exit_reason, "
            " last_mtf_agreement, updated_at) "
            "VALUES (:uid, :sym, :cu, :reason, :mtf, :upd) "
            "ON CONFLICT (user_id, symbol) DO UPDATE SET "
            "  cooldown_until = EXCLUDED.cooldown_until, "
            "  last_exit_reason = EXCLUDED.last_exit_reason, "
            "  last_mtf_agreement = EXCLUDED.last_mtf_agreement, "
            "  updated_at = EXCLUDED.updated_at"
        )
    else:
        sql = sa.text(
            "INSERT OR REPLACE INTO live_cooldowns "
            "(user_id, symbol, cooldown_until, last_exit_reason, "
            " last_mtf_agreement, updated_at) "
            "VALUES (:uid, :sym, :cu, :reason, :mtf, :upd)"
        )
    await session.execute(sql, params)


async def load_cooldown(
    session: AsyncSession, *, user_id: int, symbol: str,
) -> LiveCooldown | None:
    row = (await session.execute(
        sa.text(
            "SELECT user_id, symbol, cooldown_until, last_exit_reason, "
            "       last_mtf_agreement, updated_at "
            "FROM live_cooldowns "
            "WHERE user_id = :uid AND symbol = :sym"
        ),
        {"uid": user_id, "sym": symbol},
    )).first()
    if row is None:
        return None
    return LiveCooldown(
        user_id=row.user_id,
        symbol=row.symbol,
        cooldown_until=_to_dt(row.cooldown_until),
        last_exit_reason=row.last_exit_reason,
        last_mtf_agreement=row.last_mtf_agreement,
        updated_at=_to_dt(row.updated_at),
    )


async def delete_cooldown(
    session: AsyncSession, *, user_id: int, symbol: str,
) -> None:
    await session.execute(
        sa.text(
            "DELETE FROM live_cooldowns "
            "WHERE user_id = :uid AND symbol = :sym"
        ),
        {"uid": user_id, "sym": symbol},
    )
