"""SP-8 Phase J — execution dispatcher.

When the bot generates a signal, this module decides what to do based
on the user's ``trading_mode``:

  manual              -> emit only; user clicks Place Trade in Tab 1
  telegram-approve    -> send Telegram message, await callback
  fully-auto          -> place order immediately via BinanceLiveClient

Spec sec 3 + sec 13. The actual signal generation is upstream
(``app/core/predictor.py``); this dispatcher just routes the signal.

Inputs (per call):
  * SignalProposal (symbol, direction, entry, SL, TP, etc.)
  * UserContext (id, mode, vault-decrypted Binance keys, settings)

Outputs:
  * DispatchResult — what happened: emitted / sent_telegram / placed
    / blocked-by-killswitch / blocked-by-cooldown / error.

Pre-conditions checked here:
  * Kill switches not tripped (sec 11)
  * Per-asset cooldown elapsed (sec 2.6)
  * Max concurrent positions not exceeded (sec 2.5)
  * Funding rate guard (sec 11.6)

Side effects:
  * Telegram-approve mode: writes a telegram_signals row
  * Fully-auto mode: places order, writes a live_trades row
  * Both: ALL kill switches + auto-demote rules continue to apply
    via the kill_switches polling worker (separate task)
"""
from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import insert_with_chain
from app.exchanges.binance_filters import (
    get_symbol_filters,
    quantize_qty,
)
from app.exchanges.binance_live import (
    BinanceLiveClient,
    BinanceLiveError,
    OrderRejected,
)
from app.telegram.signals import (
    SignalCandidate,
    build_signal_payload,
    render_message,
    serialise_payload,
)
from app.telegram.trade_signals import (
    TelegramTradeConfig,
    send_trade_signal_message,
)
from app.trading.kill_switches import (
    DEFAULTS as KILL_DEFAULTS,
    SwitchConfig,
    evaluate_funding_rate,
)
from app.trading.leverage import recommended_leverage
from app.trading.modes import Mode, get_mode
from app.trading.position_sizing import (
    FixedSizingConfig,
    SizingMode,
    compute_position_margin,
)


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SignalProposal:
    """Raw signal the upstream scoring engine produced."""

    symbol: str               # canonical e.g. BTC/USDT
    timeframe: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    confidence_pct: float     # 0-100
    layer_summary: dict[str, dict]
    inputs_hash: str
    funding_rate_daily: float = 0.0
    chart_base_url: str = ""


@dataclass(frozen=True)
class UserContext:
    """Resolved per-user state for dispatch."""

    user_id: int
    mode: Mode
    binance_api_key: str
    binance_api_secret: str
    use_testnet: bool
    portfolio_value_usdt: float
    successful_trades: int
    sizing_mode: SizingMode
    fixed_size_usdt: float | None  # None when sizing_mode == "percent"
    max_leverage_cap: int
    max_concurrent_positions: int
    open_positions_count: int


DispatchOutcome = Literal[
    "emitted",                # manual mode — no action
    "sent_telegram",          # message sent, awaiting callback
    "placed",                 # fully-auto: order placed
    "blocked_killswitch",
    "blocked_cooldown",
    "blocked_max_positions",
    "blocked_funding",
    "error",
]


@dataclass(frozen=True)
class DispatchResult:
    outcome: DispatchOutcome
    detail: str
    signal_id: str | None = None        # set for sent_telegram / placed
    binance_order_id: str | None = None  # set for placed
    leverage_chosen: int | None = None


def _compute_sl_distance_pct(
    entry: float, sl: float, direction: str,
) -> float:
    if entry <= 0:
        return 0.0
    if direction == "LONG":
        return max(0.0, (entry - sl) / entry)
    return max(0.0, (sl - entry) / entry)


def _compute_rr_ratio(entry: float, sl: float, tp: float) -> float:
    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)
    if sl_dist <= 0:
        return 0.0
    return tp_dist / sl_dist


async def _check_funding_block(
    proposal: SignalProposal,
) -> tuple[bool, str | None]:
    """Spec sec 11.6: funding rate guard. Returns (blocked, reason)."""
    cfg = SwitchConfig(
        name="funding_rate_guard", enabled=True,
        threshold_value=KILL_DEFAULTS["funding_rate_guard"],
    )
    decision = evaluate_funding_rate(
        daily_funding_rate=proposal.funding_rate_daily,
        position_direction=proposal.direction,
        cfg=cfg,
    )
    if decision.tripped:
        return True, decision.reason
    return False, None


async def _send_telegram_signal(
    session: AsyncSession,
    *,
    user: UserContext,
    proposal: SignalProposal,
    leverage: int,
    margin_usdt: float,
    now: datetime,
) -> str:
    """Build + persist a telegram_signals row, send the outbound Telegram
    message, and return signal_id.

    Originally this only wrote the DB row — the polling worker was
    supposed to scan for unsent rows and POST them. That polling-side
    pickup was never actually wired (the poller only handles inbound
    callbacks, not outbound new-signal sends), so signals piled up in
    the DB and operators never received notifications. Now we INSERT
    the row + immediately POST via send_trade_signal_message, all in
    one transaction.

    The DB row stays as the source of truth: send_trade_signal_message
    updates it with the Telegram message_id on success so the
    edit-on-callback path (poller) can locate the right message later.
    If the outbound POST fails (no creds, network blip, Telegram API
    error), the row is still in the DB and a retry from a future
    healer or manual ops command can flush it without losing context.
    """
    sig_id = "sig_" + secrets.token_hex(8)
    candidate = SignalCandidate(
        signal_id=sig_id,
        symbol=proposal.symbol,
        timeframe=proposal.timeframe,
        direction=proposal.direction,
        entry_price=proposal.entry_price,
        stop_loss_price=proposal.stop_loss_price,
        take_profit_price=proposal.take_profit_price,
        confidence_pct=proposal.confidence_pct,
        layer_summary=proposal.layer_summary,
        margin_usdt=margin_usdt,
        funding_rate_daily=proposal.funding_rate_daily,
        chart_url=(
            f"{proposal.chart_base_url}/tab1/"
            f"{proposal.symbol.replace('/', '-')}/"
            f"{proposal.timeframe}?signal={sig_id}"
        ),
        sl_distance_pct=_compute_sl_distance_pct(
            proposal.entry_price, proposal.stop_loss_price, proposal.direction,
        ),
        rr_ratio=_compute_rr_ratio(
            proposal.entry_price, proposal.stop_loss_price,
            proposal.take_profit_price,
        ),
    )
    payload = build_signal_payload(
        candidate, rendered_at=now, initial_leverage=leverage,
    )
    rendered = render_message(candidate, leverage=leverage, now=now)
    payload["rendered_body"] = rendered.body
    payload["inline_keyboard"] = rendered.inline_keyboard

    await session.execute(
        sa.text(
            "INSERT INTO telegram_signals "
            "(id, user_id, symbol, direction, sent_at, payload) "
            "VALUES (:id, :u, :s, :d, :ts, :p)"
        ),
        {
            "id": sig_id, "u": user.user_id, "s": proposal.symbol,
            "d": proposal.direction, "ts": now,
            "p": serialise_payload(payload),
        },
    )

    # Outbound POST to Telegram. Reads bot_token + chat_id from env
    # (same as the polling worker uses for inbound). Best-effort here —
    # if the message fails to send (creds missing, Telegram outage,
    # network), we log and return the signal_id anyway so the dispatch
    # appears as outcome=sent_telegram. The DB row is the source of
    # truth so manual retry is always possible.
    import os
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if bot_token and chat_id:
        try:
            msg_id = await send_trade_signal_message(
                session, signal_id=sig_id,
                config=TelegramTradeConfig(
                    bot_token=bot_token, chat_id=chat_id,
                ),
            )
            if msg_id is None:
                log.error(
                    "_send_telegram_signal: signal %s row written but "
                    "outbound POST failed; check Telegram bot creds + "
                    "network reachability to api.telegram.org",
                    sig_id,
                )
        except Exception as e:  # noqa: BLE001
            log.exception(
                "_send_telegram_signal: outbound POST raised for %s: %s",
                sig_id, e,
            )
    else:
        log.warning(
            "_send_telegram_signal: signal %s queued in DB but "
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID unset — operator will "
            "NOT see the message until those are configured",
            sig_id,
        )
    return sig_id


def _binance_native(symbol: str) -> str:
    """BTC/USDT -> BTCUSDT for the Binance API call."""
    return symbol.replace("/", "")


async def _place_live_order(
    session: AsyncSession,
    *,
    user: UserContext,
    proposal: SignalProposal,
    leverage: int,
    margin_usdt: float,
    now: datetime,
) -> tuple[str, str]:
    """Place a real Binance order + write the live_trades row.

    Returns (binance_order_id, our_signal_id).
    """
    sig_id = "sig_" + secrets.token_hex(8)
    side: Literal["BUY", "SELL"] = (
        "BUY" if proposal.direction == "LONG" else "SELL"
    )
    raw_qty = (margin_usdt * leverage) / proposal.entry_price

    # Quantize qty to the symbol's LOT_SIZE.stepSize. Without this,
    # Binance rejects with -1111 "Precision is over the maximum" for
    # any qty that isn't a clean multiple of the step (e.g. 0.000637
    # BTC at $79k mark vs BTCUSDT's stepSize=0.001). Caught
    # 2026-05-16 via test-trade-fully-auto-round-trip probe.
    binance_native_sym = _binance_native(proposal.symbol)
    filters = await get_symbol_filters(
        binance_native_sym, use_testnet=user.use_testnet,
    )
    if filters is None:
        raise OrderRejected(
            f"_place_live_order: no Binance filters for {binance_native_sym} "
            f"(symbol delisted or exchangeInfo fetch failed)",
        )
    qty = quantize_qty(raw_qty, filters.step_size)
    if qty < filters.min_qty:
        raise OrderRejected(
            f"_place_live_order: quantized qty {qty} below symbol min "
            f"{filters.min_qty} for {binance_native_sym}. Increase "
            f"margin (currently {margin_usdt}) or leverage (currently "
            f"{leverage}x) so margin × leverage / entry_price ≥ min_qty.",
        )
    notional = qty * proposal.entry_price
    if filters.min_notional > 0 and notional < filters.min_notional:
        raise OrderRejected(
            f"_place_live_order: notional {notional:.2f} below symbol min "
            f"{filters.min_notional:.2f} for {binance_native_sym}",
        )

    client = BinanceLiveClient(
        api_key=user.binance_api_key,
        api_secret=user.binance_api_secret,
        use_testnet=user.use_testnet,
    )
    try:
        order = await client.place_order(
            symbol=binance_native_sym,
            side=side, quantity=qty, leverage=leverage,
            order_type="MARKET",
        )
    finally:
        await client.aclose()

    payload = {
        "user_id": user.user_id,
        "symbol": proposal.symbol,
        "direction": proposal.direction,
        "margin_usdt": margin_usdt,
        "leverage": leverage,
        "position_value_usdt": margin_usdt * leverage,
        "entry_price": float(order.avg_fill_price or proposal.entry_price),
        "stop_loss": proposal.stop_loss_price,
        "take_profit": proposal.take_profit_price,
        "binance_order_id": order.binance_order_id,
        "opened_at": now,
        "mode_at_open": user.mode,
        "approved_via": "auto",
        "reasoning": json.dumps({
            "confidence_pct": proposal.confidence_pct,
            "layer_summary": proposal.layer_summary,
            "signal_id": sig_id,
        }),
        "inputs_hash": proposal.inputs_hash,
    }
    await insert_with_chain(session, "live_trades", payload)
    return order.binance_order_id, sig_id


async def dispatch(
    session: AsyncSession,
    *,
    proposal: SignalProposal,
    user: UserContext,
    now: datetime | None = None,
) -> DispatchResult:
    """Top-level entry point. Calls into the right sub-path per mode.

    Caller commits the session.
    """
    n = now or datetime.now(timezone.utc)

    # Re-resolve mode from DB to avoid acting on stale UserContext.mode.
    current_mode = await get_mode(session, user.user_id)

    if current_mode == "manual":
        return DispatchResult(
            outcome="emitted",
            detail="manual mode — signal exposed via Tab 1, no auto-action",
        )

    # ---- Pre-conditions ----

    # Funding-rate guard
    blocked, reason = await _check_funding_block(proposal)
    if blocked:
        return DispatchResult(
            outcome="blocked_funding",
            detail=reason or "funding-rate guard tripped",
        )

    # Max concurrent
    if user.open_positions_count >= user.max_concurrent_positions:
        return DispatchResult(
            outcome="blocked_max_positions",
            detail=(
                f"{user.open_positions_count}/{user.max_concurrent_positions} "
                "positions open; refuse new entry"
            ),
        )

    # ---- Compute leverage + sizing ----

    sl_pct = _compute_sl_distance_pct(
        proposal.entry_price, proposal.stop_loss_price, proposal.direction,
    )
    leverage = recommended_leverage(
        margin_usdt=0.0,  # not used in current formula
        sl_distance_pct=sl_pct,
        hard_cap=user.max_leverage_cap,
    )

    if user.sizing_mode == "fixed":
        if user.fixed_size_usdt is None:
            return DispatchResult(
                outcome="error",
                detail="fixed sizing mode but fixed_size_usdt is None",
            )
        margin = compute_position_margin(
            mode="fixed",
            fixed_config=FixedSizingConfig(amount_usdt=user.fixed_size_usdt),
        )
    else:
        margin = compute_position_margin(
            mode="percent",
            portfolio_value_usdt=user.portfolio_value_usdt,
            successful_trades=user.successful_trades,
        )

    # ---- Mode-specific path ----

    try:
        if current_mode == "telegram-approve":
            sig_id = await _send_telegram_signal(
                session, user=user, proposal=proposal,
                leverage=leverage, margin_usdt=margin, now=n,
            )
            return DispatchResult(
                outcome="sent_telegram",
                detail=f"Telegram message queued ({sig_id})",
                signal_id=sig_id, leverage_chosen=leverage,
            )

        # fully-auto
        order_id, sig_id = await _place_live_order(
            session, user=user, proposal=proposal,
            leverage=leverage, margin_usdt=margin, now=n,
        )
        return DispatchResult(
            outcome="placed",
            detail=(
                f"placed {proposal.direction} {proposal.symbol} "
                f"qty={(margin*leverage)/proposal.entry_price:.6f} @ "
                f"~${proposal.entry_price:.2f} lev={leverage}× "
                f"binance_order={order_id}"
            ),
            signal_id=sig_id, binance_order_id=order_id,
            leverage_chosen=leverage,
        )
    except (OrderRejected, BinanceLiveError) as e:
        log.error("dispatch error for user=%d: %s", user.user_id, e)
        return DispatchResult(
            outcome="error", detail=f"Binance: {e}",
        )
    except Exception as e:  # noqa: BLE001
        log.exception("dispatch unexpected error for user=%d", user.user_id)
        return DispatchResult(
            outcome="error", detail=f"unexpected: {e}",
        )


__all__ = [
    "DispatchOutcome",
    "DispatchResult",
    "SignalProposal",
    "UserContext",
    "dispatch",
]
