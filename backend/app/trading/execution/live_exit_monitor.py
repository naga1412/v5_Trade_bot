"""PR8 — live_exit_monitor.

Polls open `live_trades` every 30s. For each row, fetches the live
position from Binance. When the position has closed (Binance returns
None) OR the live price has crossed TP/SL/timeout, classifies the
outcome, writes `live_trades.exit_reason`, and upserts
`live_cooldowns`.

Sister worker to `liquidation_monitor`:
  - liquidation_monitor: detects + acts on impending liquidation
    (buffer < 10% → auto-close). On auto-close, writes
    `exit_reason="liquidation_buffer_breach"` + cooldown.
  - live_exit_monitor (this): detects + writes outcomes when a
    position closes for any OTHER reason — TP, SL, timeout, or an
    external close that liquidation_monitor didn't trigger.

MANUAL_CLOSE classification is out of scope for PR8 — the Telegram-
approve close flow doesn't currently write exit_reason. Operator can
read PnL directly; the missing exit_reason means those rows don't set
a cooldown, which matches the LIVE_COOLDOWN_HOURS_BY_OUTCOME table's
0h baseline for manual_close anyway.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.live_cooldowns import upsert_cooldown
from app.db.live_exit_reasons import LiveExitReason
from app.exchanges.binance_live import BinanceLiveClient, BinanceLiveError
from app.ops.heartbeat import record_heartbeat
from app.trading.cooldown_compute import compute_cooldown_duration


log = logging.getLogger(__name__)


_POLL_INTERVAL_SECONDS = 30.0

# Default per-trade hold timeout. Beyond this age + no TP/SL hit, the
# monitor classifies as TIMEOUT. Operator-tunable would be nice but PR8
# uses the same number as shadow's 1h baseline.
_TIMEOUT_HOURS = 24.0


@dataclass(frozen=True)
class OpenLiveTrade:
    """Subset of live_trades a row carries through the monitor."""
    trade_id: int
    user_id: int
    symbol: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    stop_loss: float
    take_profit: float
    opened_at: datetime
    mtf_agreement: int | None  # PR2-populated; None for older rows


@dataclass(frozen=True)
class ExitClassification:
    """Outcome the monitor decided on a closed-position evidence set."""
    trade_id: int
    exit_reason: LiveExitReason
    exit_price: float | None  # last known price; None if Binance was silent


def _binance_native(symbol: str) -> str:
    return symbol.replace("/", "")


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _list_open_live_trades(
    session: AsyncSession,
) -> list[OpenLiveTrade]:
    rows = (await session.execute(sa.text(
        "SELECT id, user_id, symbol, direction, entry_price, "
        "       stop_loss, take_profit, opened_at, mtf_agreement "
        "FROM live_trades WHERE closed_at IS NULL"
    ))).all()
    return [
        OpenLiveTrade(
            trade_id=r.id, user_id=r.user_id, symbol=r.symbol,
            direction=r.direction,
            entry_price=float(r.entry_price),
            stop_loss=float(r.stop_loss),
            take_profit=float(r.take_profit),
            opened_at=r.opened_at,
            mtf_agreement=getattr(r, "mtf_agreement", None),
        )
        for r in rows
    ]


def classify_exit(
    *,
    pos: OpenLiveTrade,
    current_price: float | None,
    now: datetime,
    timeout_hours: float = _TIMEOUT_HOURS,
) -> ExitClassification | None:
    """Decide whether `pos` has closed and if so, what reason.

    Returns None if the position is still open (no boundary crossed,
    no timeout, Binance reports it as live).

    Tie-breaking on simultaneous TP+SL boundary cross (typical of a
    flash candle): TP wins for LONG when current >= TP and current >=
    SL only matters in the reverse direction. We classify by which
    boundary is on the winning side first:
      LONG: TP > entry > SL. current >= TP → TP; current <= SL → SL.
      SHORT: SL > entry > TP. current >= SL → SL; current <= TP → TP.
    """
    if current_price is None:
        # Binance returned None for get_position (or errored). Position
        # is no longer open; reason is opaque to us → EXTERNAL_CLOSE.
        # The cooldown for external_close is 0h (no penalty).
        return ExitClassification(
            trade_id=pos.trade_id,
            exit_reason=LiveExitReason.EXTERNAL_CLOSE,
            exit_price=None,
        )

    if pos.direction == "LONG":
        if current_price >= pos.take_profit:
            return ExitClassification(
                trade_id=pos.trade_id,
                exit_reason=LiveExitReason.TAKE_PROFIT,
                exit_price=current_price,
            )
        if current_price <= pos.stop_loss:
            return ExitClassification(
                trade_id=pos.trade_id,
                exit_reason=LiveExitReason.STOP_LOSS,
                exit_price=current_price,
            )
    else:  # SHORT
        if current_price <= pos.take_profit:
            return ExitClassification(
                trade_id=pos.trade_id,
                exit_reason=LiveExitReason.TAKE_PROFIT,
                exit_price=current_price,
            )
        if current_price >= pos.stop_loss:
            return ExitClassification(
                trade_id=pos.trade_id,
                exit_reason=LiveExitReason.STOP_LOSS,
                exit_price=current_price,
            )

    # No boundary hit — check timeout.
    age = now - pos.opened_at
    if age >= timedelta(hours=timeout_hours):
        return ExitClassification(
            trade_id=pos.trade_id,
            exit_reason=LiveExitReason.TIMEOUT,
            exit_price=current_price,
        )

    return None  # still open


async def _close_live_trade_and_cooldown(
    session: AsyncSession,
    *,
    pos: OpenLiveTrade,
    cls: ExitClassification,
    now: datetime,
    settings,
) -> None:
    """Single atomic close: UPDATE live_trades + UPSERT live_cooldowns."""
    await session.execute(
        sa.text(
            "UPDATE live_trades SET "
            "  exit_reason = :reason, "
            "  exit_price = COALESCE(exit_price, :px), "
            "  closed_at = :ts "
            "WHERE id = :id AND closed_at IS NULL"
        ),
        {
            "reason": cls.exit_reason.value,
            "px": cls.exit_price,
            "ts": now,
            "id": pos.trade_id,
        },
    )
    duration = compute_cooldown_duration(cls.exit_reason.value, settings)
    if duration > timedelta(0):
        await upsert_cooldown(
            session,
            user_id=pos.user_id,
            symbol=pos.symbol,
            cooldown_until=now + duration,
            last_exit_reason=cls.exit_reason.value,
            last_mtf_agreement=pos.mtf_agreement,
        )


async def _process_one(
    *,
    pos: OpenLiveTrade,
    binance: BinanceLiveClient,
    session: AsyncSession,
    settings,
    now_fn: Callable[[], datetime] = _utc_now,
    timeout_hours: float = _TIMEOUT_HOURS,
) -> ExitClassification | None:
    """Read Binance state + classify + persist close (if applicable).

    Returns the classification, or None if position still open.
    """
    try:
        live = await binance.get_position(symbol=_binance_native(pos.symbol))
    except BinanceLiveError as e:
        log.error(
            "live_exit_monitor: trade_id=%d get_position failed: %s",
            pos.trade_id, e,
        )
        return None

    if live is None:
        # Binance reports closed — classify EXTERNAL_CLOSE (overridden
        # by classify_exit when current_price is None).
        cls = classify_exit(
            pos=pos, current_price=None, now=now_fn(),
            timeout_hours=timeout_hours,
        )
    else:
        cls = classify_exit(
            pos=pos, current_price=live.entry_price,  # live mark price proxy
            now=now_fn(), timeout_hours=timeout_hours,
        )

    if cls is None:
        return None

    await _close_live_trade_and_cooldown(
        session, pos=pos, cls=cls, now=now_fn(), settings=settings,
    )
    return cls


async def run_live_exit_monitor_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    binance_factory: Callable[[], BinanceLiveClient],
    settings_factory: Callable[[], object],
    poll_interval_s: float = _POLL_INTERVAL_SECONDS,
    timeout_hours: float = _TIMEOUT_HOURS,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Forever-loop. Each tick: list open live_trades, classify + persist."""
    log.info(
        "live_exit_monitor: starting (poll_interval=%.1fs, timeout=%.1fh)",
        poll_interval_s, timeout_hours,
    )
    while True:
        try:
            await _sleep(poll_interval_s)
        except asyncio.CancelledError:
            raise

        try:
            settings = settings_factory()
            async with session_factory() as session:
                positions = await _list_open_live_trades(session)
                if not positions:
                    await record_heartbeat(
                        session_factory, "live_exit_monitor",
                        status="ok", details={"open_positions": 0},
                    )
                    continue

                client = binance_factory()
                closed_count = 0
                try:
                    for pos in positions:
                        try:
                            cls = await _process_one(
                                pos=pos, binance=client, session=session,
                                settings=settings, timeout_hours=timeout_hours,
                            )
                            if cls is not None:
                                closed_count += 1
                                log.info(
                                    "live_exit_monitor: closed trade_id=%d "
                                    "symbol=%s reason=%s",
                                    pos.trade_id, pos.symbol,
                                    cls.exit_reason.value,
                                )
                        except Exception as e:  # noqa: BLE001
                            log.error(
                                "live_exit_monitor: trade_id=%d failed: %s",
                                pos.trade_id, e,
                            )
                finally:
                    await client.aclose()
                await session.commit()

            await record_heartbeat(
                session_factory, "live_exit_monitor",
                status="ok",
                details={
                    "open_positions": len(positions),
                    "closed_this_tick": closed_count,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("live_exit_monitor iteration failed: %s", e)
            try:
                await record_heartbeat(
                    session_factory, "live_exit_monitor",
                    status="error", details={"error": str(e)[:200]},
                )
            except Exception as hb_err:  # noqa: BLE001
                log.warning(
                    "live_exit_monitor error-heartbeat itself failed; "
                    "suppressing: %s", hb_err,
                )


def start_live_exit_monitor(
    session_factory: async_sessionmaker[AsyncSession],
    binance_factory: Callable[[], BinanceLiveClient],
    settings_factory: Callable[[], object],
) -> asyncio.Task[None]:
    return asyncio.create_task(run_live_exit_monitor_loop(
        session_factory=session_factory,
        binance_factory=binance_factory,
        settings_factory=settings_factory,
    ))


__all__ = [
    "ExitClassification",
    "OpenLiveTrade",
    "classify_exit",
    "run_live_exit_monitor_loop",
    "start_live_exit_monitor",
]
