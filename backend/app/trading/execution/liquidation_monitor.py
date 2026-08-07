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
from datetime import datetime, timedelta, timezone
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
# PR8 — write liquidation-buffer cooldown on auto-close
from app.db.live_cooldowns import upsert_cooldown
from app.db.live_exit_reasons import LiveExitReason
from app.trading.cooldown_compute import compute_cooldown_duration
from app.trading.pnl import compute_realized_pnl


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
    # PR8 — carried along so the auto-close path can stamp the cooldown
    # row with the trade's mtf_agreement (consulted later when the next
    # signal on this symbol fires; see app/trading/cooldown_compute.py).
    mtf_agreement: int | None = None
    # TIER 1 (defect sweep 2026-08-06): needed to compute pnl_usdt on close.
    position_value_usdt: float = 0.0


@dataclass(frozen=True)
class MonitorOutcome:
    trade_id: int
    symbol: str
    action: Literal["ok", "alert", "auto_closed", "error"]
    detail: str
    # TIER 1 (defect sweep 2026-08-06): the mark price used for the buffer
    # calc on auto_closed, so the DB write can compute pnl. None otherwise.
    exit_price: float | None = None


async def _list_open_positions(session: AsyncSession) -> list[OpenPosition]:
    # `status='open'` is the canonical predicate. `closed_at IS NULL`
    # alone would sweep up May-vintage legacy rows (id=7 WLD/USDT,
    # id=9 BTC/USDT) that are status='closed' AND closed_at NULL AND
    # exit_reason NULL — the monitor would poll Binance for symbols it
    # closed months ago and thrash on the stale entry_price.
    rows = (await session.execute(
        sa.text(
            "SELECT id, user_id, symbol, direction, entry_price, stop_loss, "
            "       mtf_agreement, position_value_usdt "
            "FROM live_trades WHERE status = 'open'"
        )
    )).all()
    return [
        OpenPosition(
            trade_id=r.id, user_id=r.user_id, symbol=r.symbol,
            direction=r.direction, entry_price=float(r.entry_price),
            stop_loss_price=float(r.stop_loss),
            mtf_agreement=getattr(r, "mtf_agreement", None),
            position_value_usdt=float(r.position_value_usdt),
        )
        for r in rows
    ]


async def _write_liquidation_buffer_close(
    session: AsyncSession,
    *,
    pos: OpenPosition,
    exit_price: float,
    settings,
    now: datetime,
) -> None:
    """PR8: stamp live_trades.exit_reason + upsert live_cooldowns.

    Called by run_liquidation_monitor_loop after evaluate_position
    reports action='auto_closed'. Keeps evaluate_position DB-agnostic
    so its unit tests can mock Binance only.

    The cooldown duration is whatever the operator configures for
    LIQUIDATION_BUFFER_BREACH (default 24h). Fail-open: if either write
    raises, the caller logs and the loop continues.

    TIER 1 (defect sweep 2026-08-06): pnl_usdt/pnl_pct were never written
    by any close path. ``exit_price`` is the mark price evaluate_position
    used for its buffer calculation — the real closing price for this
    auto-close, always known (unlike live_exit_monitor's EXTERNAL_CLOSE
    case), so pnl is always computable here.
    """
    pnl_pct, pnl_usdt = compute_realized_pnl(
        direction=pos.direction, entry_price=pos.entry_price,
        exit_price=exit_price, position_value_usdt=pos.position_value_usdt,
    )
    await session.execute(
        sa.text(
            "UPDATE live_trades SET "
            "  exit_reason = :reason, "
            "  exit_price = COALESCE(exit_price, :px), "
            "  pnl_pct = COALESCE(pnl_pct, :pnl_pct), "
            "  pnl_usdt = COALESCE(pnl_usdt, :pnl_usdt), "
            "  closed_at = COALESCE(closed_at, :ts) "
            "WHERE id = :id"
        ),
        {
            "reason": LiveExitReason.LIQUIDATION_BUFFER_BREACH.value,
            "px": exit_price,
            "pnl_pct": pnl_pct,
            "pnl_usdt": pnl_usdt,
            "ts": now,
            "id": pos.trade_id,
        },
    )
    duration = compute_cooldown_duration(
        LiveExitReason.LIQUIDATION_BUFFER_BREACH.value, settings,
    )
    if duration > timedelta(0):
        await upsert_cooldown(
            session,
            user_id=pos.user_id,
            symbol=pos.symbol,
            cooldown_until=now + duration,
            last_exit_reason=LiveExitReason.LIQUIDATION_BUFFER_BREACH.value,
            last_mtf_agreement=pos.mtf_agreement,
        )


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
            # live_pos.entry_price is Binance's live mark-price proxy (see
            # the comment above), not a re-entry — this is the actual
            # closing price for pnl purposes.
            exit_price=live_pos.entry_price,
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
                # PR8: settings consulted only on auto-close path; lazy
                # import keeps the dispatcher → settings → import graph
                # tidy.
                from app.config import get_settings as _get_pr8_settings
                pr8_settings = _get_pr8_settings()
                for pos in positions:
                    try:
                        outcome = await evaluate_position(
                            pos=pos, binance=client, notify=notify,
                        )
                        # PR8: when auto-close fires, stamp the cooldown
                        # state. We open a fresh session for the writes
                        # because `session` above was closed; reusing it
                        # would raise InvalidStateError. Failure here
                        # logs + continues — the position IS closed on
                        # Binance regardless.
                        if outcome.action == "auto_closed":
                            try:
                                # TIER 1 (defect sweep 2026-08-06): evaluate_
                                # position always sets exit_price when it
                                # returns "auto_closed" — this is a genuine
                                # invariant check, not defensive noise, so
                                # pnl can never silently go unset here.
                                assert outcome.exit_price is not None, (
                                    "auto_closed outcome missing exit_price"
                                )
                                async with session_factory() as wsession:
                                    await _write_liquidation_buffer_close(
                                        wsession, pos=pos,
                                        exit_price=outcome.exit_price,
                                        settings=pr8_settings,
                                        now=datetime.now(tz=timezone.utc),
                                    )
                                    await wsession.commit()
                            except Exception as e:  # noqa: BLE001
                                log.error(
                                    "liq monitor: liquidation-buffer "
                                    "exit_reason/cooldown write failed "
                                    "for trade_id=%d: %s",
                                    pos.trade_id, e,
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
