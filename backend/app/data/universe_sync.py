"""Universe sync worker (SP-3 Phase F, spec §3.4).

Daily diff between an exchange adapter's ``list_symbols()`` and the
``universe_history`` table. INSERT new rows, UPDATE last_synced_at on
still-active rows, set ``delisted_at = now`` on newly-missing rows.

Adapters that return ``[]`` from ``list_symbols`` (Yahoo, TwelveData) short-
circuit when ``skip_if_empty=True`` so the manual seeds are not flipped to
delisted.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.adapters import get_adapter, list_registered
from app.data.adapters._base import ExchangeAdapter, SymbolInfo

log = logging.getLogger(__name__)

# 02:00 UTC — offset from SP-1 universe refresh (00:00 UTC).
DEFAULT_SYNC_HOUR_UTC: int = 2


@dataclass(frozen=True)
class SyncResult:
    added: int
    still_active: int
    newly_delisted: int


async def sync_universe(
    adapter: ExchangeAdapter,
    session: AsyncSession,
    *,
    now: datetime | None = None,
    skip_if_empty: bool = True,
) -> SyncResult:
    """Diff ``adapter.list_symbols()`` against universe_history.

    Caller is responsible for ``session.commit()`` after a successful sync.
    """
    n = now or datetime.now(UTC)
    api_symbols: list[SymbolInfo] = await adapter.list_symbols()

    if not api_symbols and skip_if_empty:
        log.info(
            "sync_universe(%s): adapter returned 0 symbols — skipping",
            adapter.name,
        )
        return SyncResult(added=0, still_active=0, newly_delisted=0)

    api_set = {s.canonical for s in api_symbols}

    existing_rows = (await session.execute(
        sa.text(
            "SELECT symbol, delisted_at FROM universe_history "
            "WHERE exchange = :ex"
        ),
        {"ex": adapter.name},
    )).all()
    existing_active = {
        r.symbol for r in existing_rows if r.delisted_at is None
    }
    existing_delisted = {
        r.symbol for r in existing_rows if r.delisted_at is not None
    }

    added = 0
    still_active = 0
    newly_delisted = 0
    relisted = 0

    for sym in api_symbols:
        if sym.canonical in existing_active:
            await session.execute(
                sa.text(
                    "UPDATE universe_history "
                    "SET last_synced_at = :ts "
                    "WHERE exchange = :ex AND symbol = :s"
                ),
                {"ts": n, "ex": adapter.name, "s": sym.canonical},
            )
            still_active += 1
        elif sym.canonical in existing_delisted:
            # Relisting: clear delisted_at, refresh last_synced_at.
            await session.execute(
                sa.text(
                    "UPDATE universe_history "
                    "SET delisted_at = NULL, last_synced_at = :ts "
                    "WHERE exchange = :ex AND symbol = :s"
                ),
                {"ts": n, "ex": adapter.name, "s": sym.canonical},
            )
            still_active += 1
            relisted += 1
        else:
            await session.execute(
                sa.text(
                    "INSERT INTO universe_history "
                    "(exchange, symbol, asset_class, listed_at, "
                    "last_synced_at, metadata) "
                    "VALUES (:ex, :s, :ac, :listed, :ts, :md)"
                ),
                {
                    "ex": adapter.name, "s": sym.canonical,
                    "ac": sym.asset_class,
                    "listed": sym.listed_at or n,
                    "ts": n,
                    "md": json.dumps({
                        "base": sym.base, "quote": sym.quote,
                        "native": sym.native,
                    }),
                },
            )
            added += 1

    for missing in existing_active - api_set:
        await session.execute(
            sa.text(
                "UPDATE universe_history "
                "SET delisted_at = :ts, last_synced_at = :ts "
                "WHERE exchange = :ex AND symbol = :s"
            ),
            {"ts": n, "ex": adapter.name, "s": missing},
        )
        newly_delisted += 1

    if relisted:
        log.info(
            "sync_universe(%s): %d symbols relisted", adapter.name, relisted,
        )
    return SyncResult(
        added=added,
        still_active=still_active,
        newly_delisted=newly_delisted,
    )


# --- Background loop (mirrors SP-1 start_universe_refresh_task pattern) ---


def _seconds_until_next_utc(hour: int, now: datetime) -> int:
    n = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    target = n.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= n:
        target = target + timedelta(days=1)
    return int((target - n).total_seconds())


async def run_universe_sync_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    wake_at_utc_hour: int = DEFAULT_SYNC_HOUR_UTC,
    exchanges: list[str] | None = None,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    _now: Callable[[], datetime] | None = None,
) -> None:
    """Forever-loop: sleep until 02:00 UTC, then sync each exchange."""
    now_fn = _now if _now is not None else lambda: datetime.now(UTC)
    targets = exchanges if exchanges is not None else list_registered()

    while True:
        wait_s = _seconds_until_next_utc(wake_at_utc_hour, now_fn())
        await _sleep(float(wait_s))

        for ex in targets:
            try:
                adapter = get_adapter(ex)
                async with session_factory() as session:
                    result = await sync_universe(adapter, session)
                    await session.commit()
                log.info(
                    "sync_universe(%s) done: added=%d still_active=%d "
                    "newly_delisted=%d", ex,
                    result.added, result.still_active, result.newly_delisted,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.error("sync_universe(%s) failed: %s", ex, e)


def start_universe_sync_task(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    wake_at_utc_hour: int = DEFAULT_SYNC_HOUR_UTC,
) -> asyncio.Task[None]:
    """Spawn ``run_universe_sync_loop`` as a background task."""
    return asyncio.create_task(run_universe_sync_loop(
        session_factory=session_factory,
        wake_at_utc_hour=wake_at_utc_hour,
    ))


__all__ = [
    "DEFAULT_SYNC_HOUR_UTC",
    "SyncResult",
    "run_universe_sync_loop",
    "start_universe_sync_task",
    "sync_universe",
]
