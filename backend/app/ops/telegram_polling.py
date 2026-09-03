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
#
# PR-FIX-GHOST-POSITIONS-ATOMIC-SLTP (2026-05-26) rewrote this from a
# single Binance-then-INSERT pass into a 3-phase pending->open lifecycle:
#
#   Phase 1 (atomic DB): pre-INSERT live_trades(status='pending') with
#     all hash-payload columns set + binance_order_id NULL. Single
#     session, single commit. If this fails, no Binance call is made.
#   Phase 2 (Binance, no DB lock): drift check + place market+SL+TP
#     atomically via atomic_placement.place_with_sltp. Emergency-closes
#     on partial failure.
#   Phase 3 (atomic DB): UPDATE live_trades to status='open' (with the
#     three Binance order IDs + actual fill price) OR status='failed'
#     (with the failure_reason string). Stale_price drift rejection
#     writes 'stale_price' on telegram_signals AND 'failed' on
#     live_trades atomically in one session.
#
# Net effect: a Binance position cannot exist without a corresponding
# live_trades row reflecting it; if Phase 3 itself crashes,
# live_exit_monitor's reconciler picks up the stuck 'pending' row and
# repairs status from the live Binance position.


async def _read_signal_payload(
    session_factory: async_sessionmaker[AsyncSession],
    signal_id: str,
) -> dict[str, Any] | None:
    """Phase 0a: read the persisted signal payload + owning user_id."""
    async with session_factory() as session:
        row = (await session.execute(
            sa.text(
                "SELECT user_id, symbol, direction, payload, symbol_source "
                "FROM telegram_signals WHERE id = :id"
            ),
            {"id": signal_id},
        )).first()
    if row is None:
        log.warning("place_approved_order: signal_id=%s missing", signal_id)
        return None
    parsed_payload = (
        json.loads(row.payload) if isinstance(row.payload, str) else dict(row.payload)
    )
    # Attach the owning user_id so downstream phases don't re-query.
    parsed_payload["__row_user_id__"] = row.user_id
    # Phase 4 Task 9: recover the cohort tag from the telegram_signals
    # row's own `symbol_source` column (written at send-time by
    # dispatcher._send_telegram_signal) rather than duplicating it into
    # the JSON payload -- the column is the source of truth. getattr()
    # with a default keeps pre-Task-9 rows (column NOT NULL DEFAULT
    # 'established_top20' per migration 0038, so this is only a defensive
    # fallback for duck-typed test rows lacking the attribute entirely).
    parsed_payload["symbol_source"] = getattr(row, "symbol_source", None) or "established_top20"
    return parsed_payload


def _coerce_mtf_directions(raw: Any) -> dict[str, int] | None:
    """Coerce telegram_signals.payload['mtf_directions'] to dict[str,int]|None.

    Bit-identical to the previous inline coercion (PR2 §6.3 R3): allows
    only int values, rejects floats / strings / lists to keep the
    auto-path and telegram-approve path symmetric.
    """
    if isinstance(raw, dict) and all(isinstance(v, int) for v in raw.values()):
        return {str(k): int(v) for k, v in raw.items()}
    if raw is not None and not isinstance(raw, dict):
        log.warning(
            "telegram-approve: mtf_directions in payload is %s (not "
            "dict); persisting live_trades row with mtf_directions_json=NULL",
            type(raw).__name__,
        )
    return None


async def _phase1_insert_pending_trade(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    payload: dict[str, Any],
    signal_id: str,
    leverage: int,
    user_id: int,
    qty: float,
    now: datetime,
) -> int | None:
    """Phase 1: atomic pre-INSERT of live_trades(status='pending').

    Returns the trade_id on success; None on DB error. The row is
    fully-formed at the audit-chain level — binance_order_id is set to
    a UNIQUE per-trade placeholder ``"pending-{signal_id}"`` (replaced
    post-Binance via UPDATE on the non-hashed column). status='pending'
    tells live_exit_monitor's reconciler to look up the Binance side
    after a 60s grace window.

    Per-trade unique placeholders are required because
    `live_trades.binance_order_id` carries a UNIQUE constraint
    (migration 0016). A bare ``""`` sentinel would UniqueViolation on
    the second pending row — observed 2026-05-26 in CI on the test
    suite that inserts three test rows with the same placeholder.
    """
    direction = payload["direction"]
    symbol = payload["symbol"]
    entry_price = float(payload["entry_price"])
    stop_loss = float(payload["stop_loss_price"])
    take_profit = float(payload["take_profit_price"])
    margin_usdt = float(payload["margin_usdt"])

    pending_placeholder = f"pending-{signal_id}"
    trade_payload = build_live_trade_payload(
        user_id=payload.get("__row_user_id__") or user_id,
        symbol=symbol,
        direction=direction,
        margin_usdt=margin_usdt,
        leverage=leverage,
        entry_price=entry_price,  # signal-time price; updated to actual fill at Phase 3
        stop_loss=stop_loss,
        take_profit=take_profit,
        binance_order_id=pending_placeholder,
        opened_at=now,
        mode_at_open="telegram-approve",
        approved_via="telegram",
        reasoning_json=json.dumps({
            "signal_id": signal_id,
            "confidence_pct": payload.get("confidence_pct"),
        }),
        inputs_hash=payload.get("inputs_hash", ""),
        mtf_agreement=payload.get("mtf_agreement"),
        mtf_dominant_tf=payload.get("mtf_dominant_tf"),
        mtf_directions=_coerce_mtf_directions(payload.get("mtf_directions")),
        # Phase 4 Task 9: cohort tag recovered by _read_signal_payload
        # from telegram_signals.symbol_source.
        symbol_source=payload.get("symbol_source", "established_top20"),
    )
    try:
        async with session_factory() as session:
            await insert_with_chain(session, "live_trades", trade_payload)
            # Fetch the just-inserted id by the unique placeholder so
            # concurrent pending inserts don't collide on the lookup.
            row = (await session.execute(
                sa.text(
                    "SELECT id FROM live_trades "
                    "WHERE binance_order_id = :ph AND status = 'pending'"
                ),
                {"ph": pending_placeholder},
            )).first()
            await session.commit()
        if row is None:
            log.error(
                "_phase1: INSERT succeeded but row lookup failed for "
                "signal_id=%s — unexpected", signal_id,
            )
            return None
        return int(row.id)
    except Exception as e:  # noqa: BLE001
        log.error(
            "_phase1: INSERT live_trades(pending) failed for "
            "signal_id=%s: %s", signal_id, e,
        )
        return None


async def _phase3_mark_open(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trade_id: int,
    binance_order_id: str,
    sl_order_id: str,
    tp_order_id: str,
    fill_price: float,
    now: datetime,
) -> None:
    """Phase 3 success: promote pending->open with the Binance order IDs."""
    async with session_factory() as session:
        await session.execute(
            sa.text(
                "UPDATE live_trades SET "
                "  status='open', "
                "  binance_order_id=:boid, "
                "  sl_order_id=:sid, "
                "  tp_order_id=:tid, "
                "  entry_price=:ep "
                "WHERE id=:tid_row"
            ),
            {
                "boid": binance_order_id,
                "sid": sl_order_id,
                "tid": tp_order_id,
                "ep": fill_price,
                "tid_row": trade_id,
            },
        )
        await session.commit()
    log.info(
        "trade %d -> open: binance=%s SL=%s TP=%s fill=%.4f (at %s)",
        trade_id, binance_order_id, sl_order_id, tp_order_id, fill_price,
        now.isoformat(),
    )


async def _phase3_mark_failed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trade_id: int,
    failure_reason: str,
    signal_id: str | None = None,
    signal_response_override: str | None = None,
    now: datetime | None = None,
) -> None:
    """Phase 3 failure: mark live_trades.status='failed' with the reason.

    If `signal_response_override` is set (e.g. 'stale_price'), the
    matching telegram_signals row is UPDATEd in the same session so
    audit logs see one atomic transition.
    """
    n = now or datetime.now(timezone.utc)
    async with session_factory() as session:
        await session.execute(
            sa.text(
                "UPDATE live_trades SET status='failed', failure_reason=:r "
                "WHERE id=:tid"
            ),
            {"r": failure_reason[:500], "tid": trade_id},
        )
        if signal_id and signal_response_override:
            await session.execute(
                sa.text(
                    "UPDATE telegram_signals SET response=:r, response_at=:ts "
                    "WHERE id=:id"
                ),
                {"r": signal_response_override, "ts": n, "id": signal_id},
            )
        await session.commit()
    log.warning(
        "trade %d -> failed: %s%s",
        trade_id, failure_reason[:200],
        f" (signal {signal_id} -> {signal_response_override})"
        if signal_response_override else "",
    )


async def _place_approved_order(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    signal_id: str,
    leverage: int,
    use_testnet: bool,
    user_id: int,
    binance_factory: Callable[[], BinanceLiveClient],
    now: datetime | None = None,
) -> str | None:
    """Top-level orchestrator for the telegram-approve placement path.

    See the module-level "Order placement after Telegram approve"
    comment for the 3-phase contract. Returns the Binance entry-order
    id on full success; None on any failure (caller can inspect
    live_trades.status='failed' for the failure_reason).

    Caller is the polling worker; we manage all our own sessions via
    `session_factory` so no transaction is held across the Binance
    network call (which can take seconds).
    """
    n = now or datetime.now(timezone.utc)

    # ─── Phase 0a: read signal payload ────────────────────────────────
    payload = await _read_signal_payload(session_factory, signal_id)
    if payload is None:
        return None

    entry_price = float(payload["entry_price"])
    stop_loss = float(payload["stop_loss_price"])
    take_profit = float(payload["take_profit_price"])
    margin_usdt = float(payload["margin_usdt"])
    direction = payload["direction"]
    symbol = payload["symbol"]
    side: Literal["BUY", "SELL"] = "BUY" if direction == "LONG" else "SELL"
    binance_native_sym = symbol.replace("/", "")

    # ─── Phase 0b: pre-validations (no DB writes, no Binance writes) ──
    # Per-symbol open-position gate (defense-in-depth alongside the
    # dispatch-pre-card check in dispatcher.py). A card can be sent,
    # the operator can tap Approve minutes later, and in the interim
    # another signal on the same symbol may have opened a position —
    # placing a second one here would double-stack the entry with a
    # closePosition=true SL that liquidates the older entry at 2x.
    #
    # APPROVE-TIME policy: FAIL-CLOSED on DB error. This gate exists
    # to prevent position doubling; "allow the order on error" is
    # exactly the harm the gate was built to prevent, at the moment
    # the system is least healthy. Missed trade ~= zero cost
    # (net-zero edge, signal recurs); doubled position = 2x risk on
    # one symbol with an undesigned exit. Distinct outcome name
    # `blocked_position_check_failed` so the operator can tell a
    # verification failure from a real duplicate.
    from app.trading.execution.symbol_position_gate import (
        get_open_position_trade_id,
    )
    try:
        async with session_factory() as _pos_session:
            _existing_id = await get_open_position_trade_id(
                _pos_session, user_id=user_id, symbol=symbol,
            )
    except Exception as exc:  # noqa: BLE001 — fail-closed at approve
        log.error(
            "place_approved_order: blocked_position_check_failed — "
            "DB error verifying per-symbol open-position for %s "
            "(signal_id=%s, user_id=%d): %s — refusing order "
            "(cannot confirm no duplicate)",
            symbol, signal_id, user_id, exc,
        )
        return None
    if _existing_id is not None:
        log.warning(
            "place_approved_order: blocked_symbol_position_open — "
            "symbol %s already has open live_trades id=%d "
            "(signal_id=%s, user_id=%d)",
            symbol, _existing_id, signal_id, user_id,
        )
        return None

    filters = await get_symbol_filters(
        binance_native_sym, use_testnet=use_testnet,
    )
    if filters is None:
        log.error(
            "place_approved_order: no Binance filters for %s; cannot place "
            "(signal_id=%s)", binance_native_sym, signal_id,
        )
        return None
    raw_qty = (margin_usdt * leverage) / entry_price
    qty = quantize_qty(raw_qty, filters.step_size)
    if qty < filters.min_qty:
        log.error(
            "place_approved_order: quantized qty %s below symbol min %s "
            "for %s (signal_id=%s)",
            qty, filters.min_qty, binance_native_sym, signal_id,
        )
        return None
    if filters.min_notional > 0 and qty * entry_price < filters.min_notional:
        log.error(
            "place_approved_order: notional %.2f below symbol min %.2f for %s "
            "(signal_id=%s)",
            qty * entry_price, filters.min_notional, binance_native_sym,
            signal_id,
        )
        return None

    # ─── Phase 1: pre-INSERT live_trades(pending) ─────────────────────
    trade_id = await _phase1_insert_pending_trade(
        session_factory,
        payload=payload, signal_id=signal_id,
        leverage=leverage, user_id=user_id, qty=qty, now=n,
    )
    if trade_id is None:
        # DB error during pre-insert; no Binance call made.
        return None

    # ─── Phase 2: Binance (drift + atomic market+SL+TP) ───────────────
    client = binance_factory()
    try:
        # Drift check
        from app.config import get_settings as _get_drift_settings
        _drift_threshold_pct = _get_drift_settings().APPROVAL_MAX_PRICE_DRIFT_PCT
        current_price = await client.fetch_mark_price(symbol=binance_native_sym)
        if current_price is not None and entry_price > 0:
            drift_pct = abs(current_price - entry_price) / entry_price * 100.0
            if drift_pct > _drift_threshold_pct:
                reason = (
                    f"stale_price: drift={drift_pct:.3f}% > "
                    f"{_drift_threshold_pct:.3f}% threshold "
                    f"(entry={entry_price:.6f} current={current_price:.6f})"
                )
                log.warning("dispatch %s -> %s", signal_id, reason)
                await _phase3_mark_failed(
                    session_factory,
                    trade_id=trade_id,
                    failure_reason=reason,
                    signal_id=signal_id,
                    signal_response_override="stale_price",
                    now=n,
                )
                return None

        # Atomic market+SL+TP
        from app.trading.execution.atomic_placement import (
            GhostPositionError,
            place_with_sltp,
        )
        try:
            result = await place_with_sltp(
                client, symbol=binance_native_sym, side=side,
                quantity=qty, leverage=leverage,
                stop_loss_price=stop_loss, take_profit_price=take_profit,
            )
        except GhostPositionError as ghost_exc:
            # CATASTROPHIC: SL/TP failed AND emergency close failed.
            # Position likely orphan on Binance. live_exit_monitor's
            # reconciler will see status='failed' + try to read Binance.
            await _phase3_mark_failed(
                session_factory,
                trade_id=trade_id,
                failure_reason=f"GHOST: {ghost_exc}",
                now=n,
            )
            log.critical(
                "trade %d signal %s: %s", trade_id, signal_id, ghost_exc,
            )
            return None
        except (OrderRejected, BinanceLiveError) as e:
            # Binance failure; position is flat (either market entry
            # rejected, or emergency close succeeded after SL/TP fail).
            await _phase3_mark_failed(
                session_factory,
                trade_id=trade_id,
                failure_reason=f"binance: {e}",
                now=n,
            )
            return None
    finally:
        await client.aclose()

    # ─── Phase 3: success path — promote pending->open ────────────────
    await _phase3_mark_open(
        session_factory,
        trade_id=trade_id,
        binance_order_id=result.entry_order.binance_order_id,
        sl_order_id=result.sl_order_id,
        tp_order_id=result.tp_order_id,
        fill_price=float(result.entry_order.avg_fill_price or entry_price),
        now=n,
    )
    return result.entry_order.binance_order_id


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
        # PR-FIX-GHOST-POSITIONS-ATOMIC-SLTP (2026-05-26): the
        # placement path is now multi-session (Phase 1 pre-INSERT, Phase
        # 2 Binance, Phase 3 UPDATE — see _place_approved_order
        # docstring). handle_callback's response='approved' UPDATE
        # commits first; if Phase 1 INSERT fails, we have an "approved
        # but no trade row" state — strictly better than the pre-fix
        # "Binance position with no DB row" orphan, and recoverable via
        # SQL audit. The drift-rejection path explicitly flips
        # telegram_signals.response='stale_price' AND live_trades.status
        # ='failed' atomically in a single Phase 3 session, preserving
        # the response/trade coordination PR-FIX-PR264 introduced.
        order_id: str | None = None
        order_placement_error: Exception | None = None
        async with session_factory() as session:
            outcome = await handle_trade_callback(
                session, callback_data=data,
                config=config.trade_config(), http=http,
            )
            await session.commit()
        if (
            outcome.action == "approve"
            and outcome.leverage is not None
            and binance_factory is not None
        ):
            try:
                order_id = await _place_approved_order(
                    session_factory,
                    signal_id=outcome.signal_id,
                    leverage=outcome.leverage, use_testnet=use_testnet,
                    user_id=user_id, binance_factory=binance_factory,
                )
            except Exception as e:  # noqa: BLE001
                # Unexpected (non-Binance, non-Ghost) error escaped the
                # orchestrator. Logged here so the operator sees something
                # even if Phase 3 itself raised before marking failed.
                order_placement_error = e
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
