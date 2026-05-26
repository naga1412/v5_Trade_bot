"""SP-8 Phase J — Telegram polling worker.

Long-polls Telegram getUpdates for callback_query events and routes
them by data prefix:

  ``sig:<signal_id>:<action>[:<lev>]`` -> trade-signal callbacks
      Decoded by ``app.telegram.trade_signals.handle_callback`` which
      writes the response into the telegram_signals row. On approve,
      this worker also fires the dispatcher's fully-auto path so the
      Binance order is placed immediately (no second queue).

  ``rl_*:<checkpoint_id>`` -> SP-4 brain-checkpoint approvals
      Forwarded to ``app.ops.telegram_bot.handle_callback_query``
      unchanged.

The worker self-throttles on Telegram errors (5xx / 429 / network) with
exponential backoff so a temporary outage doesn't burn CPU. A single
in-memory ``offset`` keeps each update processed exactly once until
restart; on restart the next getUpdates returns from the previous
acked offset (Telegram persists 24h of unacked updates).

Started by ``app.main:lifespan`` only when:

  1. AUTONOMOUS_TRADING_ENABLED=true,
  2. Pre-flight passed,
  3. Both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set.

Otherwise the worker would either have nothing to authenticate against
(missing creds) or no orders to route (autonomous trading off).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.audit import insert_with_chain
from app.db.payload_builders import build_live_trade_payload
from app.exchanges.binance_filters import (
    get_symbol_filters,
    quantize_qty,
)
from app.exchanges.binance_live import (
    BinanceLiveClient,
    BinanceLiveError,
    OrderRejected,
)
from app.ops.heartbeat import record_heartbeat
from app.ops.telegram_bot import (
    TelegramConfig as BrainConfig,
    handle_callback_query as handle_brain_callback,
)
from app.telegram.trade_signals import (
    TelegramTradeConfig,
    handle_callback as handle_trade_callback,
)


log = logging.getLogger(__name__)


_LONG_POLL_TIMEOUT_S = 30
_HTTP_TIMEOUT_S = float(_LONG_POLL_TIMEOUT_S + 10)  # 30s long-poll + 10s slack
_BACKOFF_BASE_S = 2.0
_BACKOFF_CAP_S = 60.0


@dataclass(frozen=True)
class PollerConfig:
    """Bundles the two TelegramConfigs the poller needs.

    Brain config is for the SP-4 admin_rl PATCH path (loopback URL),
    trade config is for the SP-8 send/edit + future user-facing chats.
    Both share the bot_token + chat_id; we keep them as separate
    dataclasses to avoid coupling SP-4 + SP-8 across releases.
    """

    bot_token: str
    chat_id: str
    backend_internal_url: str = "http://localhost:8000"

    def brain_config(self) -> BrainConfig:
        return BrainConfig(
            bot_token=self.bot_token,
            chat_id=self.chat_id,
            backend_internal_url=self.backend_internal_url,
        )

    def trade_config(self) -> TelegramTradeConfig:
        return TelegramTradeConfig(
            bot_token=self.bot_token,
            chat_id=self.chat_id,
        )


def _api_url(*, bot_token: str, method: str) -> str:
    from app.ops.telegram_bot import TELEGRAM_API_BASE
    return f"{TELEGRAM_API_BASE}/bot{bot_token}/{method}"


# ---- Auto-skip stale approvals ------------------------------------------


async def _auto_skip_expired_signals(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    timeout_seconds: int,
    now: datetime | None = None,
) -> int:
    """Mark pending signals older than `timeout_seconds` as auto_skipped.

    PR-MAKE-APPROVAL-TIMEOUT-AND-DRIFT-CONFIGURABLE (2026-05-26): the
    "Auto-skip in Ns" UI text was previously cosmetic — there was no
    actual server-side enforcement. Operators slept through approvals
    overnight because they (reasonably) believed the signal had
    auto-skipped at the displayed deadline, when in fact it stayed
    pending indefinitely.

    This helper runs once per telegram_poller cycle (~30s). It writes
    `response='auto_skipped'` on any `telegram_signals` row where
    `response IS NULL AND sent_at < (now - timeout_seconds)`. The
    `WHERE response IS NULL` guard prevents overwriting a legitimate
    'approved' / 'skipped' / 'stale_price' value if the operator
    interacted in the same cycle.

    Returns the number of rows that were auto-skipped. Never raises —
    a DB failure here is logged + the poller continues. Caller commits
    its own session.
    """
    n = now or datetime.now(timezone.utc)
    cutoff = n - timedelta(seconds=timeout_seconds)
    # Bind datetime objects directly. The previous .isoformat() pattern
    # was based on a wrong assumption that asyncpg auto-casts ISO strings
    # to TIMESTAMPTZ — it actually rejects them with DataError ("expected
    # a datetime.date or datetime.datetime instance, got 'str'"), which
    # was firing every 30s in prod for 6+ hours after PR #264 shipped.
    # SQLAlchemy passes datetime objects through to asyncpg as typed
    # parameters; for SQLite tests, both this query and the test row
    # seeds bind plain datetime objects so the resulting TEXT
    # stringifications match (format `'YYYY-MM-DD HH:MM:SS+TZ'`) and
    # lex-comparison in `sent_at < :cutoff` behaves correctly.
    try:
        async with session_factory() as session:
            result = await session.execute(
                sa.text(
                    "UPDATE telegram_signals "
                    "SET response='auto_skipped', response_at=:ts "
                    "WHERE response IS NULL AND sent_at < :cutoff"
                ),
                {"ts": n, "cutoff": cutoff},
            )
            await session.commit()
    except Exception as e:  # noqa: BLE001 — never let cleanup kill the poller
        log.warning("auto_skip_expired_signals: DB error (failing open): %s", e)
        return 0
    # SQLAlchemy's async UPDATE result is a CursorResult exposing rowcount;
    # the static type is Result which doesn't declare the attribute.
    rowcount = (
        result.rowcount if result.rowcount is not None else 0  # type: ignore[attr-defined]
    )
    if rowcount > 0:
        log.info(
            "telegram-auto-skip: marked %d signal(s) auto_skipped "
            "(timeout=%ds)", rowcount, timeout_seconds,
        )
    return int(rowcount)


# ---- Order placement after Telegram approve ------------------------------


async def _place_approved_order(
    session: AsyncSession,
    *,
    signal_id: str,
    leverage: int,
    use_testnet: bool,
    user_id: int,
    binance_factory: Callable[[], BinanceLiveClient],
    now: datetime | None = None,
) -> str | None:
    """Read the telegram_signals payload, place the Binance order, write
    the live_trades row. Returns the binance order id on success.

    Pulled out of the dispatcher so the polling worker can call it
    without re-running the funding-guard / max-positions checks (those
    were enforced when the message was first sent).
    """
    n = now or datetime.now(timezone.utc)
    row = (await session.execute(
        sa.text(
            "SELECT user_id, symbol, direction, payload "
            "FROM telegram_signals WHERE id = :id"
        ),
        {"id": signal_id},
    )).first()
    if row is None:
        log.warning("place_approved_order: signal_id=%s missing", signal_id)
        return None

    payload = (
        json.loads(row.payload) if isinstance(row.payload, str) else dict(row.payload)
    )
    entry_price = float(payload["entry_price"])
    stop_loss = float(payload["stop_loss_price"])
    take_profit = float(payload["take_profit_price"])
    margin_usdt = float(payload["margin_usdt"])
    direction = payload["direction"]
    symbol = payload["symbol"]
    side: Literal["BUY", "SELL"] = "BUY" if direction == "LONG" else "SELL"
    raw_qty = (margin_usdt * leverage) / entry_price

    # Quantize qty to the symbol's LOT_SIZE.stepSize (same fix as
    # _place_live_order — Binance rejects raw floats with -1111).
    binance_native_sym = symbol.replace("/", "")
    filters = await get_symbol_filters(
        binance_native_sym, use_testnet=use_testnet,
    )
    if filters is None:
        log.error(
            "place_approved_order: no Binance filters for %s; cannot place",
            binance_native_sym,
        )
        return None
    qty = quantize_qty(raw_qty, filters.step_size)
    if qty < filters.min_qty:
        log.error(
            "place_approved_order: quantized qty %s below symbol min %s "
            "for %s (margin=%s, leverage=%s)",
            qty, filters.min_qty, binance_native_sym, margin_usdt, leverage,
        )
        return None
    notional = qty * entry_price
    if filters.min_notional > 0 and notional < filters.min_notional:
        log.error(
            "place_approved_order: notional %.2f below symbol min %.2f for %s",
            notional, filters.min_notional, binance_native_sym,
        )
        return None

    client = binance_factory()
    try:
        # ----- PR-MAKE-APPROVAL-TIMEOUT-AND-DRIFT-CONFIGURABLE -----------
        # Drift guard: reject the approval if the current mark price has
        # drifted more than APPROVAL_MAX_PRICE_DRIFT_PCT (%) from the
        # signal's original entry_price. Prevents an approve-after-sleep
        # scenario where the operator OKs a 10-minute-old signal but the
        # market has moved well past the original entry zone — the
        # resulting order would fill at a price the original SL/TP was
        # never designed for.
        #
        # fail-open: when fetch_mark_price returns None (HTTP error,
        # unknown symbol, parse failure) we DO NOT block — same
        # philosophy as the funding-rate / borrow lookups. A stuck
        # ticker should not silently shut down the approve path.
        from app.config import get_settings as _get_drift_settings
        _drift_threshold_pct = _get_drift_settings().APPROVAL_MAX_PRICE_DRIFT_PCT
        current_price = await client.fetch_mark_price(symbol=binance_native_sym)
        if current_price is not None and entry_price > 0:
            drift_pct = abs(current_price - entry_price) / entry_price * 100.0
            if drift_pct > _drift_threshold_pct:
                # Mark the row stale_price (overwrites the 'approved'
                # state handle_callback just wrote). Brief flip-flop is
                # tolerated: caller commits this session right after
                # this function returns, so the row never publishes a
                # 'approved' state externally before becoming 'stale_price'.
                await session.execute(
                    sa.text(
                        "UPDATE telegram_signals "
                        "SET response='stale_price', response_at=:ts "
                        "WHERE id=:id"
                    ),
                    {"ts": n, "id": signal_id},
                )
                log.warning(
                    "dispatch %s -> stale_price: drift=%.3f%% > %.3f%% "
                    "threshold (entry=%.6f current=%.6f)",
                    signal_id, drift_pct, _drift_threshold_pct,
                    entry_price, current_price,
                )
                return None
        # ----- end drift guard ------------------------------------------

        order = await client.place_order(
            symbol=binance_native_sym,
            side=side, quantity=qty, leverage=leverage,
            order_type="MARKET",
        )
    except (OrderRejected, BinanceLiveError) as e:
        log.error("place_approved_order: Binance rejected: %s", e)
        return None
    finally:
        await client.aclose()

    # PR2 §4.4: read MTF state captured at send-time from
    # telegram_signals.payload (build_signal_payload writes these). The
    # auto-path and telegram-approve path persist matching mtf_* on
    # live_trades — symmetric per spec §6.3 R3. mtf_directions in the
    # JSONB is the parsed dict (or null); we forward it as-is to the
    # builder, which canonical-serialises it back to JSON for the column.
    # Float values are rejected (set the whole field None) — matches the
    # glue._parse_mtf_directions_json fail-open contract, so the live
    # trade row mirrors the auto-path semantics exactly.
    _mtf_directions_raw = payload.get("mtf_directions")
    mtf_directions_dict: dict[str, int] | None
    if isinstance(_mtf_directions_raw, dict) and all(
        isinstance(v, int) for v in _mtf_directions_raw.values()
    ):
        mtf_directions_dict = {
            str(k): int(v) for k, v in _mtf_directions_raw.items()
        }
    else:
        mtf_directions_dict = None
        if _mtf_directions_raw is not None and not isinstance(
            _mtf_directions_raw, dict,
        ):
            log.warning(
                "telegram-approve: mtf_directions in payload is %s (not "
                "dict); persisting live_trades row with mtf_directions_json=NULL",
                type(_mtf_directions_raw).__name__,
            )

    trade_payload = build_live_trade_payload(
        user_id=row.user_id or user_id,
        symbol=symbol,
        direction=direction,
        margin_usdt=margin_usdt,
        leverage=leverage,
        entry_price=float(order.avg_fill_price or entry_price),
        stop_loss=stop_loss,
        take_profit=take_profit,
        binance_order_id=order.binance_order_id,
        opened_at=n,
        mode_at_open="telegram-approve",
        approved_via="telegram",
        reasoning_json=json.dumps({
            "signal_id": signal_id,
            "confidence_pct": payload.get("confidence_pct"),
        }),
        inputs_hash=payload.get("inputs_hash", ""),
        mtf_agreement=payload.get("mtf_agreement"),
        mtf_dominant_tf=payload.get("mtf_dominant_tf"),
        mtf_directions=mtf_directions_dict,
    )
    await insert_with_chain(session, "live_trades", trade_payload)
    return order.binance_order_id


# ---- Update routing ------------------------------------------------------


def _is_authorised_callback(
    callback_query: dict[str, Any], *, allowed_chat_id: str,
) -> bool:
    """Reject callbacks not from the configured chat / sender.

    Telegram bot tokens occasionally leak (in logs, in screenshots, in
    abandoned repos). Without this check, anyone who DMs the bot can
    fire ``sig:<id>:approve`` and the polling worker would happily
    place a real Binance order. We refuse any callback whose
    ``message.chat.id`` OR ``from.id`` doesn't match the operator's
    chat_id (which is also their personal user_id since they DM the
    bot directly).
    """
    chat = (callback_query.get("message") or {}).get("chat") or {}
    sender = callback_query.get("from") or {}
    chat_id = str(chat.get("id", ""))
    sender_id = str(sender.get("id", ""))
    return (
        chat_id == allowed_chat_id and sender_id == allowed_chat_id
    )


async def _route_callback(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    callback_query: dict[str, Any],
    config: PollerConfig,
    binance_factory: Callable[[], BinanceLiveClient] | None,
    use_testnet: bool,
    user_id: int,
    http: httpx.AsyncClient,
) -> None:
    """Route one callback_query to the right handler by data prefix.

    Refuses any callback not from ``config.chat_id`` (both the message
    chat AND the sender). Logs the rejection at WARNING with the
    callback_query.id so an operator can spot abuse attempts in the
    backend log.
    """
    if not _is_authorised_callback(
        callback_query, allowed_chat_id=config.chat_id,
    ):
        log.warning(
            "telegram-poll: REJECT unauthorised callback id=%s chat=%s from=%s",
            callback_query.get("id"),
            (callback_query.get("message") or {}).get("chat", {}).get("id"),
            (callback_query.get("from") or {}).get("id"),
        )
        return

    data = (callback_query.get("data") or "").strip()
    if not data:
        return

    if data.startswith("sig:"):
        # Single-session txn spans both handle_trade_callback (which writes
        # response='approved' tentatively for the 'approve' branch) and
        # _place_approved_order (which may overwrite to 'stale_price' if
        # the drift guard rejects the approval). Splitting these across
        # two sessions previously committed 'approved' first and then
        # failed the 'stale_price' update against the CHECK constraint,
        # leaving the row stuck at 'approved' with resulted_in_trade_id
        # NULL. See PR-FIX-PR264-RESPONSE-CHECK-CONSTRAINT-AND-AUTOSKIP.
        order_id: str | None = None
        order_placement_error: Exception | None = None
        async with session_factory() as session:
            outcome = await handle_trade_callback(
                session, callback_data=data,
                config=config.trade_config(), http=http,
            )
            # On approve, fire the live order WITHIN the same session
            # before commit so any drift-guard rejection of the tentative
            # 'approved' state becomes 'stale_price' atomically.
            if (
                outcome.action == "approve"
                and outcome.leverage is not None
                and binance_factory is not None
            ):
                try:
                    order_id = await _place_approved_order(
                        session, signal_id=outcome.signal_id,
                        leverage=outcome.leverage, use_testnet=use_testnet,
                        user_id=user_id, binance_factory=binance_factory,
                    )
                except Exception as e:  # noqa: BLE001
                    # Capture but don't re-raise: we still want to commit
                    # whatever response state was written (e.g. drift
                    # guard already updated to 'stale_price', or the
                    # tentative 'approved' if the failure was upstream).
                    order_placement_error = e
            await session.commit()
        log.info("trade-callback %s -> %s: %s",
                 outcome.signal_id, outcome.action, outcome.note)
        if order_placement_error is not None:
            log.error(
                "telegram-approve order placement failed (%s): %s",
                outcome.signal_id, order_placement_error,
            )
        elif order_id is not None:
            log.info(
                "telegram-approve placed order %s for signal %s",
                order_id, outcome.signal_id,
            )

        # Acknowledge the callback so the spinner stops on the operator's
        # phone. Best-effort.
        try:
            await http.post(
                _api_url(bot_token=config.bot_token,
                         method="answerCallbackQuery"),
                json={
                    "callback_query_id": str(callback_query.get("id", "")),
                    "text": outcome.note[:200],
                },
            )
        except httpx.HTTPError as e:
            log.warning("answerCallbackQuery failed: %s", e)
        return

    # Brain-checkpoint approvals (SP-4 path). The handler does its own
    # answerCallbackQuery + editMessageText.
    if data.startswith(("rl_approve:", "rl_reject:", "rl_details:")):
        await handle_brain_callback(
            config=config.brain_config(),
            callback_query=callback_query,
            client=http,
        )
        return

    log.warning("telegram-poll: unhandled callback_data %r", data[:80])


# ---- Polling loop --------------------------------------------------------


async def _poll_once(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    config: PollerConfig,
    http: httpx.AsyncClient,
    offset: int,
    long_poll_timeout: int,
    binance_factory: Callable[[], BinanceLiveClient] | None,
    use_testnet: bool,
    user_id: int,
) -> tuple[int, bool]:
    """One poll cycle. Returns (next_offset, ok).

    ok=False means the iteration hit a transient failure and the caller
    should back off before retrying. The next_offset always advances
    past every successfully-acked update; on failure it's unchanged.
    """
    resp = await http.post(
        _api_url(bot_token=config.bot_token, method="getUpdates"),
        json={
            "timeout": long_poll_timeout, "offset": offset,
            "allowed_updates": ["callback_query"],
        },
    )
    if resp.status_code >= 500 or resp.status_code == 429:
        log.warning("getUpdates %d", resp.status_code)
        return offset, False
    if resp.status_code != 200:
        log.error("getUpdates non-200 %d %s", resp.status_code, resp.text[:200])
        return offset, False
    body = resp.json()
    if not body.get("ok"):
        log.error("getUpdates ok=false: %s", body)
        return offset, False

    updates = body.get("result", []) or []
    for upd in updates:
        update_id = int(upd.get("update_id", 0))
        offset = max(offset, update_id + 1)
        cb = upd.get("callback_query")
        if not cb:
            continue
        try:
            await _route_callback(
                session_factory,
                callback_query=cb, config=config,
                binance_factory=binance_factory,
                use_testnet=use_testnet, user_id=user_id,
                http=http,
            )
        except Exception as e:  # noqa: BLE001
            log.error(
                "telegram-poll route failed for update %d: %s",
                update_id, e,
            )
    return offset, True


async def run_telegram_poller(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    config: PollerConfig,
    binance_factory: Callable[[], BinanceLiveClient] | None,
    use_testnet: bool,
    user_id: int,
    long_poll_timeout: int = _LONG_POLL_TIMEOUT_S,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Forever-loop. Each iteration delegates to _poll_once + sleeps on
    transient failure with exponential backoff."""
    offset = 0
    backoff = _BACKOFF_BASE_S
    while True:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as http:
                offset, ok = await _poll_once(
                    session_factory=session_factory, config=config,
                    http=http, offset=offset,
                    long_poll_timeout=long_poll_timeout,
                    binance_factory=binance_factory,
                    use_testnet=use_testnet, user_id=user_id,
                )
                # PR-MAKE-APPROVAL-TIMEOUT-AND-DRIFT-CONFIGURABLE: piggyback
                # the auto-skip cleanup on the poll cycle (~30s cadence,
                # naturally). Reads the configured timeout each tick so an
                # operator can adjust the env without redeploying the poller.
                # Helper is fail-open — a DB blip here logs WARNING and
                # returns 0; the poller continues unaffected.
                try:
                    from app.config import get_settings as _get_skip_settings
                    _timeout_s = _get_skip_settings().TELEGRAM_APPROVAL_TIMEOUT_SECONDS
                    await _auto_skip_expired_signals(
                        session_factory, timeout_seconds=_timeout_s,
                    )
                except Exception as e:  # noqa: BLE001 — never let cleanup kill poller
                    log.warning("telegram-poll: auto_skip tick failed: %s", e)

                # FU-1: heartbeat after each long-poll cycle. Watchdog uses
                # this to detect Telegram connectivity / poller crashes.
                # Telegram returning ``{"ok": false}`` is routine flood-control
                # backoff, not a worker error — heartbeat as ``ok`` so we
                # don't leave `last_status='error'` on the row during normal
                # operation. Real failures (HTTPError, unhandled exception)
                # fire status='error' from the except branches below.
                await record_heartbeat(
                    session_factory, "telegram_poller_task",
                    status="ok",
                    details={
                        "offset": offset,
                        "backoff_s": backoff,
                        "tg_ok": ok,
                    },
                )
                if not ok:
                    await _sleep(backoff)
                    backoff = min(backoff * 2, _BACKOFF_CAP_S)
                    continue
                backoff = _BACKOFF_BASE_S
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as e:
            log.warning("getUpdates HTTP error: %s; backoff %.1fs", e, backoff)
            await record_heartbeat(
                session_factory, "telegram_poller_task",
                status="error", details={"error": str(e)[:200]},
            )
            await _sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_CAP_S)
        except Exception as e:  # noqa: BLE001
            log.error("telegram-poll iteration crashed: %s", e)
            await record_heartbeat(
                session_factory, "telegram_poller_task",
                status="error", details={"error": str(e)[:200]},
            )
            await _sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_CAP_S)


def start_telegram_poller(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    config: PollerConfig,
    binance_factory: Callable[[], BinanceLiveClient] | None,
    use_testnet: bool,
    user_id: int,
) -> asyncio.Task[None]:
    return asyncio.create_task(run_telegram_poller(
        session_factory=session_factory, config=config,
        binance_factory=binance_factory, use_testnet=use_testnet,
        user_id=user_id,
    ))


__all__ = [
    "PollerConfig",
    "run_telegram_poller",
    "start_telegram_poller",
]
