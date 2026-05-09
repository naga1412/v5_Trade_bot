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
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.audit import insert_with_chain
from app.exchanges.binance_live import (
    BinanceLiveClient,
    BinanceLiveError,
    OrderRejected,
)
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
    qty = (margin_usdt * leverage) / entry_price

    client = binance_factory()
    try:
        order = await client.place_order(
            symbol=symbol.replace("/", ""),
            side=side, quantity=qty, leverage=leverage,
            order_type="MARKET",
        )
    except (OrderRejected, BinanceLiveError) as e:
        log.error("place_approved_order: Binance rejected: %s", e)
        return None
    finally:
        await client.aclose()

    trade_payload = {
        "user_id": row.user_id or user_id,
        "symbol": symbol,
        "direction": direction,
        "margin_usdt": margin_usdt,
        "leverage": leverage,
        "position_value_usdt": margin_usdt * leverage,
        "entry_price": float(order.avg_fill_price or entry_price),
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "binance_order_id": order.binance_order_id,
        "opened_at": n,
        "mode_at_open": "telegram-approve",
        "approved_via": "telegram",
        "reasoning": json.dumps({
            "signal_id": signal_id,
            "confidence_pct": payload.get("confidence_pct"),
        }),
        "inputs_hash": payload.get("inputs_hash", ""),
    }
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
        async with session_factory() as session:
            outcome = await handle_trade_callback(
                session, callback_data=data,
                config=config.trade_config(), http=http,
            )
            await session.commit()
        log.info("trade-callback %s -> %s: %s",
                 outcome.signal_id, outcome.action, outcome.note)

        # On approve, also fire the live order. We require a binance
        # factory (i.e. vault loaded) — without one we mark the row as
        # 'approved' but the live-trades row never gets written.
        if (
            outcome.action == "approve"
            and outcome.leverage is not None
            and binance_factory is not None
        ):
            try:
                async with session_factory() as session:
                    order_id = await _place_approved_order(
                        session, signal_id=outcome.signal_id,
                        leverage=outcome.leverage, use_testnet=use_testnet,
                        user_id=user_id, binance_factory=binance_factory,
                    )
                    await session.commit()
                if order_id is not None:
                    log.info(
                        "telegram-approve placed order %s for signal %s",
                        order_id, outcome.signal_id,
                    )
            except Exception as e:  # noqa: BLE001
                log.error(
                    "telegram-approve order placement failed (%s): %s",
                    outcome.signal_id, e,
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
                if not ok:
                    await _sleep(backoff)
                    backoff = min(backoff * 2, _BACKOFF_CAP_S)
                    continue
                backoff = _BACKOFF_BASE_S
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as e:
            log.warning("getUpdates HTTP error: %s; backoff %.1fs", e, backoff)
            await _sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_CAP_S)
        except Exception as e:  # noqa: BLE001
            log.error("telegram-poll iteration crashed: %s", e)
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
