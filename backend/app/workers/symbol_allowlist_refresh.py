"""PR10 daily symbol_allowlist_refresh worker.

Reads closed shadow_trades, computes per-symbol stats over rolling
window, writes one symbol_performance_snapshots row per symbol via
insert_snapshot_row (which uses insert_with_chain under the hood).
Heartbeats per cycle.

Single-writer worker -> FU-24's concurrent-insert race doesn't fire
against symbol_performance_snapshots.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.symbol_performance_snapshots import insert_snapshot_row
from app.ops.heartbeat import record_heartbeat
from app.trading.symbol_allowlist import compute_per_symbol_stats


log = logging.getLogger(__name__)


_POLL_INTERVAL_SECONDS = 86400.0  # 24h


@dataclass(frozen=True)
class _ClosedTradeRow:
    """Internal adapter satisfying ``compute_per_symbol_stats``'s ``_TradeProto``.

    The shadow.stats Trade dataclass lacks a ``symbol`` field; the
    per-symbol aggregator only requires (symbol, closed_at, pnl_usdt,
    pnl_pct), so we use a local shape rather than reshaping
    shadow.stats.Trade.
    """
    symbol: str
    closed_at: datetime
    pnl_usdt: float
    pnl_pct: float


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _coerce_closed_at(value: object) -> datetime:
    """SQLite stringifies datetimes; Postgres returns native. Normalize."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


async def _load_closed_trades_for_window(
    session: AsyncSession, *, window_start: datetime,
) -> list[_ClosedTradeRow]:
    """Read all closed shadow_trades in the window. Aggregates across users."""
    rows = (await session.execute(sa.text(
        "SELECT symbol, closed_at, pnl_usdt, pnl_pct "
        "  FROM shadow_trades "
        " WHERE closed_at IS NOT NULL "
        "   AND closed_at >= :since"
    ), {"since": window_start.isoformat()})).all()
    out: list[_ClosedTradeRow] = []
    for r in rows:
        out.append(_ClosedTradeRow(
            symbol=r.symbol,
            closed_at=_coerce_closed_at(r.closed_at),
            pnl_usdt=float(r.pnl_usdt or 0),
            pnl_pct=float(r.pnl_pct or 0),
        ))
    return out


async def run_one_refresh_cycle(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: object,
    now_fn: Callable[[], datetime] = _utc_now,
) -> int:
    """One cycle: read trades -> compute -> insert one row per symbol.

    Returns count of snapshots written. Heartbeats on success/error.
    """
    now = now_fn()
    window_days: int = int(getattr(settings, "SYMBOL_ALLOWLIST_WINDOW_DAYS", 30))
    window_start = now - timedelta(days=window_days)
    snapshots_written = 0
    try:
        async with session_factory() as session:
            trades = await _load_closed_trades_for_window(
                session, window_start=window_start,
            )
            # _ClosedTradeRow structurally matches the aggregator's
            # _TradeProto (symbol, closed_at, pnl_usdt, pnl_pct); settings
            # is supplied by app.config.get_settings() and satisfies
            # _AggregatorSettingsProto in prod (and is hand-built in tests).
            stats = compute_per_symbol_stats(
                trades,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                now=now,
            )
            for s in stats:
                await insert_snapshot_row(
                    session,
                    symbol=s.symbol,
                    window_start=s.window_start, window_end=s.window_end,
                    trades_count=s.trades_count,
                    win_rate=s.win_rate, sharpe=s.sharpe,
                    allowed=s.allowed, computed_at=now,
                )
            await session.commit()
            snapshots_written = len(stats)
        await record_heartbeat(
            session_factory, "symbol_allowlist_refresh",
            status="ok", details={"snapshots_written": snapshots_written},
        )
        return snapshots_written
    except Exception as e:  # noqa: BLE001
        log.error("symbol_allowlist_refresh cycle failed: %s", e)
        try:
            await record_heartbeat(
                session_factory, "symbol_allowlist_refresh",
                status="error", details={"error": str(e)[:200]},
            )
        except Exception:  # noqa: BLE001
            pass
        return 0


async def run_symbol_allowlist_refresh_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings_factory: Callable[[], object],
    poll_interval_s: float = _POLL_INTERVAL_SECONDS,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Forever-loop. Fires one cycle per day."""
    log.info(
        "symbol_allowlist_refresh: starting (interval=%.0fs)", poll_interval_s,
    )
    while True:
        try:
            await _sleep(poll_interval_s)
        except asyncio.CancelledError:
            raise
        try:
            await run_one_refresh_cycle(
                session_factory=session_factory,
                settings=settings_factory(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("symbol_allowlist_refresh outer-loop error: %s", e)


def start_symbol_allowlist_refresh(
    session_factory: async_sessionmaker[AsyncSession],
    settings_factory: Callable[[], object],
) -> asyncio.Task[None]:
    return asyncio.create_task(run_symbol_allowlist_refresh_loop(
        session_factory=session_factory, settings_factory=settings_factory,
    ))


__all__ = [
    "run_one_refresh_cycle",
    "run_symbol_allowlist_refresh_loop",
    "start_symbol_allowlist_refresh",
]
