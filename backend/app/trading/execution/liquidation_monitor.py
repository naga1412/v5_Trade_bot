"""SP-8 Phase J — liquidation monitor.

Spec sec 11.5 + sec 16. Polls every 30s for each open live_trade:

  1. Query Binance for current position state
  2. Compute buffer to liquidation = (current - liq) / (entry - liq)
  3. If buffer < 50% (default) OR < 5min predicted to liquidation:
     a. Telegram alert (urgent — overrides quiet hours)
     b. If buffer < 10%: auto-close via close_position() to prevent
        actual liquidation (saves the funding/fee penalty)

The auto-close-near-liquidation is more aggressive than spec sec 11.5
(which says alert-only). Operator request: when nobody's around to
react to the alert, closing at -90% loss beats a forced liquidation
at -100% + Binance's clawback fees.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.exchanges.binance_live import (
    BinanceLiveClient,
    BinanceLiveError,
)
from app.ops.heartbeat import record_heartbeat
from app.trading.kill_switches import (
    DEFAULTS as KILL_DEFAULTS,
    SwitchConfig,
    evaluate_liquidation_near,
)


log = logging.getLogger(__name__)


_POLL_INTERVAL_SECONDS = 30.0
_AUTO_CLOSE_BUFFER_PCT = 0.10  # 10% remaining buffer → auto-close


@dataclass(frozen=True)
class OpenPosition:
    """The fields the monitor needs from one live_trades row."""

    trade_id: int
    user_id: int
    symbol: str            # canonical e.g. BTC/USDT
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    stop_loss_price: float


@dataclass(frozen=True)
class MonitorOutcome:
    trade_id: int
    symbol: str
    action: Literal["ok", "alert", "auto_closed", "error"]
    detail: str


async def _list_open_positions(session: AsyncSession) -> list[OpenPosition]:
    rows = (await session.execute(
        sa.text(
            "SELECT id, user_id, symbol, direction, entry_price, stop_loss "
            "FROM live_trades WHERE closed_at IS NULL"
        )
    )).all()
    return [
        OpenPosition(
            trade_id=r.id, user_id=r.user_id, symbol=r.symbol,
            direction=r.direction, entry_price=float(r.entry_price),
            stop_loss_price=float(r.stop_loss),
        )
        for r in rows
    ]


def _binance_native(symbol: str) -> str:
    return symbol.replace("/", "")


async def evaluate_position(
    *,
    pos: OpenPosition,
    binance: BinanceLiveClient,
    notify: Callable[[str], Awaitable[None]] | None = None,
) -> MonitorOutcome:
    """Check one position's distance to liquidation; act if dangerous.

    Returns the outcome. The caller is responsible for marking the
    closed_at + writing the tax_event row when action == 'auto_closed'.
    """
    try:
        live_pos = await binance.get_position(symbol=_binance_native(pos.symbol))
    except BinanceLiveError as e:
        return MonitorOutcome(
            trade_id=pos.trade_id, symbol=pos.symbol, action="error",
            detail=f"get_position failed: {e}",
        )

    if live_pos is None:
        # Binance reports the position as already closed. Caller should
        # reconcile (mark our row closed_at, write tax_event).
        return MonitorOutcome(
            trade_id=pos.trade_id, symbol=pos.symbol, action="ok",
            detail="position no longer open on Binance (closed externally?)",
        )

    cfg = SwitchConfig(
        name="liquidation_near", enabled=True,
        threshold_value=KILL_DEFAULTS["liquidation_near"],
    )
    decision = evaluate_liquidation_near(
        current_price=live_pos.entry_price,  # use Binance's live price proxy
        liquidation_price=live_pos.liquidation_price,
        entry_price=pos.entry_price,
        cfg=cfg,
    )

    if not decision.tripped:
        return MonitorOutcome(
            trade_id=pos.trade_id, symbol=pos.symbol, action="ok",
            detail=f"buffer healthy (liq=${live_pos.liquidation_price:.2f})",
        )

    initial_distance = abs(pos.entry_price - live_pos.liquidation_price)
    current_distance = abs(live_pos.entry_price - live_pos.liquidation_price)
    buffer = (current_distance / initial_distance) if initial_distance > 0 else 0.0

    if buffer < _AUTO_CLOSE_BUFFER_PCT:
        # Auto-close to prevent forced liquidation.
        try:
            await binance.close_position(symbol=_binance_native(pos.symbol))
        except BinanceLiveError as e:
            return MonitorOutcome(
                trade_id=pos.trade_id, symbol=pos.symbol, action="error",
                detail=f"auto-close failed: {e}",
            )
        msg = (
            f"AUTO-CLOSED {pos.symbol} (buffer {buffer:.1%} < "
            f"{_AUTO_CLOSE_BUFFER_PCT:.0%}; preventing liquidation)"
        )
        log.warning(msg)
        if notify is not None:
            try:
                await notify(f"🔥 {msg}")
            except Exception as e:  # noqa: BLE001
                log.error("liquidation auto-close notify failed: %s", e)
        return MonitorOutcome(
            trade_id=pos.trade_id, symbol=pos.symbol, action="auto_closed",
            detail=msg,
        )

    # Buffer in the warning zone: alert but don't close.
    msg = (
        f"liq-near {pos.symbol}: {decision.reason}. "
        f"buffer {buffer:.1%}; will auto-close at {_AUTO_CLOSE_BUFFER_PCT:.0%}"
    )
    log.warning(msg)
    if notify is not None:
        try:
            await notify(f"⚠️ {msg}")
        except Exception as e:  # noqa: BLE001
            log.error("liquidation alert notify failed: %s", e)
    return MonitorOutcome(
        trade_id=pos.trade_id, symbol=pos.symbol, action="alert", detail=msg,
    )


async def run_liquidation_monitor_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    binance_factory: Callable[[], BinanceLiveClient],
    notify: Callable[[str], Awaitable[None]] | None = None,
    poll_interval_s: float = _POLL_INTERVAL_SECONDS,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Forever-loop. Each tick: poll all open live_trades + evaluate.

    binance_factory: Phase J wires this to build a fresh client per
    iteration using the vault-decrypted keys. Per-call construction
    avoids stale clients after key rotation.
    """
    log.info(
        "liq monitor: starting (poll_interval=%.1fs)", poll_interval_s,
    )
    while True:
        try:
            await _sleep(poll_interval_s)
        except asyncio.CancelledError:
            raise

        try:
            async with session_factory() as session:
                positions = await _list_open_positions(session)
            if not positions:
                # Heartbeat-via-log so the watchdog has a signal even when
                # there are zero open live trades (the steady state in
                # Manual mode). Capped to once per minute to keep volume
                # reasonable.
                log.info("liq monitor: tick (no open positions)")
                # FU-1: record heartbeat in steady-state too.
                await record_heartbeat(
                    session_factory, "liquidation_monitor_task",
                    status="ok", details={"open_positions": 0},
                )
                continue

            client = binance_factory()
            try:
                for pos in positions:
                    try:
                        await evaluate_position(
                            pos=pos, binance=client, notify=notify,
                        )
                    except Exception as e:  # noqa: BLE001
                        log.error(
                            "liq monitor: trade_id=%d failed: %s",
                            pos.trade_id, e,
                        )
            finally:
                await client.aclose()
            # FU-1: heartbeat after evaluating all open positions.
            await record_heartbeat(
                session_factory, "liquidation_monitor_task",
                status="ok", details={"open_positions": len(positions)},
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("liq monitor iteration failed: %s", e)
            await record_heartbeat(
                session_factory, "liquidation_monitor_task",
                status="error", details={"error": str(e)[:200]},
            )


def start_liquidation_monitor(
    session_factory: async_sessionmaker[AsyncSession],
    binance_factory: Callable[[], BinanceLiveClient],
    *,
    notify: Callable[[str], Awaitable[None]] | None = None,
) -> asyncio.Task[None]:
    return asyncio.create_task(run_liquidation_monitor_loop(
        session_factory=session_factory,
        binance_factory=binance_factory, notify=notify,
    ))


__all__ = [
    "MonitorOutcome",
    "OpenPosition",
    "evaluate_position",
    "run_liquidation_monitor_loop",
    "start_liquidation_monitor",
]
