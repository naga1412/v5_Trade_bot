"""SP-8 Phase J — Telegram trade-signal sender + callback handler.

Two functions:

  send_trade_signal_message(...)
    Posts the rendered message body + inline keyboard to the user's
    Telegram chat. Used by the dispatcher when mode=telegram-approve.
    Records the returned Telegram message_id back into the
    telegram_signals row's payload so we can edit the message in
    response to button presses.

  handle_callback(...)
    Called by the polling worker (extension of SP-4 Phase E
    telegram_bot.py) when a `sig:*` callback_data fires. Routes
    the action: approve places the order, adjust re-renders the
    message, skip marks the row, custom prompts for input.

Both are self-contained — they take their HTTP client + DB session
explicitly so unit tests can mock httpx + use SQLite.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.telegram.signals import (
    SignalCandidate,
    parse_callback_data,
    render_message,
)


log = logging.getLogger(__name__)


_TELEGRAM_API_BASE = os.environ.get(
    "TELEGRAM_API_BASE", "https://api.telegram.org",
)
_HTTP_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class TelegramTradeConfig:
    bot_token: str
    chat_id: str


def _api_url(*, bot_token: str, method: str) -> str:
    return f"{_TELEGRAM_API_BASE}/bot{bot_token}/{method}"


# ---- Send -----------------------------------------------------------------


async def send_trade_signal_message(
    session: AsyncSession,
    *,
    signal_id: str,
    config: TelegramTradeConfig,
    http: httpx.AsyncClient | None = None,
) -> int | None:
    """POST sendMessage for one telegram_signals row. Returns message_id.

    Reads the row's payload (which already contains rendered_body +
    inline_keyboard from build_signal_payload), POSTs to Telegram, then
    updates the payload with the returned message_id so subsequent
    edit-on-callback knows which message to update.
    """
    row = (await session.execute(
        sa.text(
            "SELECT payload FROM telegram_signals WHERE id = :id"
        ),
        {"id": signal_id},
    )).first()
    if row is None:
        log.warning("send_trade_signal_message: signal_id=%s not found", signal_id)
        return None

    payload = (
        json.loads(row.payload) if isinstance(row.payload, str) else dict(row.payload)
    )
    body = payload.get("rendered_body")
    keyboard = payload.get("inline_keyboard")
    if not body or not keyboard:
        log.error(
            "send_trade_signal_message: signal_id=%s missing body/keyboard",
            signal_id,
        )
        return None

    msg_payload: dict[str, Any] = {
        "chat_id": config.chat_id,
        "text": body,
        "reply_markup": {"inline_keyboard": keyboard},
        "disable_web_page_preview": True,
    }

    owns_client = http is None
    client = http or httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S)
    try:
        resp = await client.post(
            _api_url(bot_token=config.bot_token, method="sendMessage"),
            json=msg_payload,
        )
        if resp.status_code != 200:
            log.error(
                "telegram trade signal sendMessage non-200: %d %s",
                resp.status_code, resp.text[:200],
            )
            return None
        data = resp.json()
        if not data.get("ok"):
            log.error("telegram trade signal API ok=false: %s", data)
            return None
        message_id = int(data["result"]["message_id"])
    except httpx.HTTPError as e:
        log.error("telegram trade signal HTTP error: %s", e)
        return None
    finally:
        if owns_client:
            await client.aclose()

    # Update payload with message_id so edits target the right message.
    payload["telegram_message_id"] = message_id
    await session.execute(
        sa.text(
            "UPDATE telegram_signals SET payload = :p WHERE id = :id"
        ),
        {"p": json.dumps(payload, separators=(",", ":")), "id": signal_id},
    )
    return message_id


# ---- Callback handler -----------------------------------------------------


@dataclass(frozen=True)
class CallbackOutcome:
    """What handle_callback did. Drives the answerCallbackQuery + audit."""

    signal_id: str
    action: str                  # 'approve' | 'approve_lost_race' | 'adjust' | 'custom' | 'skip' | 'unknown'
    note: str                    # short user-facing toast (≤200 chars)
    leverage: int | None = None  # set for adjust/approve
    placed_order: bool = False


async def handle_callback(
    session: AsyncSession,
    *,
    callback_data: str,
    config: TelegramTradeConfig,
    http: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> CallbackOutcome:
    """Decode + dispatch one Telegram callback_data string.

    Updates telegram_signals.response when the user makes a final
    decision (approved/skipped). The actual order placement for
    'approve' is delegated to the dispatcher — this module only
    parses, persists the response, and edits the message.

    Caller commits.
    """
    n = now or datetime.now(timezone.utc)
    parsed = parse_callback_data(callback_data)
    if parsed is None:
        return CallbackOutcome(
            signal_id="?", action="unknown",
            note=f"unrecognised callback_data: {callback_data!r}",
        )

    if parsed.action == "skip":
        await session.execute(
            sa.text(
                "UPDATE telegram_signals "
                "SET response='skipped', response_at=:ts WHERE id=:id"
            ),
            {"ts": n, "id": parsed.signal_id},
        )
        return CallbackOutcome(
            signal_id=parsed.signal_id, action="skip",
            note="Signal skipped.",
        )

    if parsed.action == "adjust":
        # Re-render the message body + keyboard with the new leverage and
        # editMessageText on Telegram. Don't change the response field —
        # the user is still deciding.
        await _re_render_and_edit(
            session, signal_id=parsed.signal_id,
            new_leverage=parsed.leverage or 1,
            config=config, http=http, now=n,
        )
        return CallbackOutcome(
            signal_id=parsed.signal_id, action="adjust",
            leverage=parsed.leverage,
            note=f"leverage now {parsed.leverage}×",
        )

    if parsed.action == "custom":
        # The polling worker handles the follow-up "reply with leverage"
        # prompt; we just record the intent for the audit row.
        return CallbackOutcome(
            signal_id=parsed.signal_id, action="custom",
            note="reply with leverage (1-10)",
        )

    if parsed.action == "approve":
        # Mark approved + record the chosen leverage. Order placement
        # happens in the polling worker right after this returns,
        # using the dispatcher.fully_auto path.
        #
        # `WHERE response IS NULL` guards against the auto-skip race
        # (PR-MAKE-APPROVAL-TIMEOUT-AND-DRIFT-CONFIGURABLE, 2026-05-26):
        # the auto-skip worker writes `response='auto_skipped'` on
        # signals older than TELEGRAM_APPROVAL_TIMEOUT_SECONDS. If the
        # operator's approve callback arrives a fraction of a second
        # after the auto-skip UPDATE, we must NOT overwrite the
        # auto_skipped marker with 'approved' — otherwise the polling
        # worker would happily call _place_approved_order on a signal
        # the system already declared expired. Detect by rowcount=0
        # and return action="approve_lost_race" so the caller skips
        # order placement.
        result = await session.execute(
            sa.text(
                "UPDATE telegram_signals "
                "SET response='approved', response_at=:ts, "
                "    response_leverage=:lev "
                "WHERE id=:id AND response IS NULL"
            ),
            {"ts": n, "lev": parsed.leverage, "id": parsed.signal_id},
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            return CallbackOutcome(
                signal_id=parsed.signal_id, action="approve_lost_race",
                leverage=parsed.leverage, placed_order=False,
                note=(
                    "approve arrived after auto-skip (or duplicate "
                    "callback) — signal already resolved; no order placed"
                ),
            )
        return CallbackOutcome(
            signal_id=parsed.signal_id, action="approve",
            leverage=parsed.leverage, placed_order=False,
            note=f"approved at {parsed.leverage}× — placing order…",
        )

    return CallbackOutcome(
        signal_id=parsed.signal_id, action="unknown",
        note=f"unhandled action {parsed.action}",
    )


async def _re_render_and_edit(
    session: AsyncSession, *,
    signal_id: str, new_leverage: int,
    config: TelegramTradeConfig,
    http: httpx.AsyncClient | None,
    now: datetime,
) -> None:
    """Read the candidate from the row, re-render with new leverage,
    POST editMessageText to Telegram so the same message updates in place."""
    row = (await session.execute(
        sa.text(
            "SELECT payload FROM telegram_signals WHERE id = :id"
        ),
        {"id": signal_id},
    )).first()
    if row is None:
        return
    payload = (
        json.loads(row.payload) if isinstance(row.payload, str)
        else dict(row.payload)
    )
    message_id = payload.get("telegram_message_id")
    if message_id is None:
        return  # not yet sent; nothing to edit

    # Reconstruct the candidate from the persisted fields.
    candidate = SignalCandidate(
        signal_id=signal_id,
        symbol=payload["symbol"],
        timeframe=payload["timeframe"],
        direction=payload["direction"],
        entry_price=float(payload["entry_price"]),
        stop_loss_price=float(payload["stop_loss_price"]),
        take_profit_price=float(payload["take_profit_price"]),
        confidence_pct=float(payload["confidence_pct"]),
        layer_summary=payload.get("layer_summary", {}),
        margin_usdt=float(payload["margin_usdt"]),
        funding_rate_daily=float(payload["funding_rate_daily"]),
        chart_url=payload["chart_url"],
        sl_distance_pct=float(payload["sl_distance_pct"]),
        rr_ratio=float(payload["rr_ratio"]),
    )
    rendered = render_message(candidate, leverage=new_leverage, now=now)

    edit_payload = {
        "chat_id": config.chat_id,
        "message_id": message_id,
        "text": rendered.body,
        "reply_markup": {"inline_keyboard": rendered.inline_keyboard},
        "disable_web_page_preview": True,
    }

    owns_client = http is None
    client = http or httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S)
    try:
        resp = await client.post(
            _api_url(bot_token=config.bot_token, method="editMessageText"),
            json=edit_payload,
        )
        if resp.status_code != 200:
            log.warning(
                "editMessageText non-200: %d %s",
                resp.status_code, resp.text[:200],
            )
    except httpx.HTTPError as e:
        log.warning("editMessageText HTTP error: %s", e)
    finally:
        if owns_client:
            await client.aclose()

    # Persist the updated rendered_body + keyboard so subsequent edits
    # build from the latest state.
    payload["rendered_body"] = rendered.body
    payload["inline_keyboard"] = rendered.inline_keyboard
    await session.execute(
        sa.text(
            "UPDATE telegram_signals SET payload = :p WHERE id = :id"
        ),
        {"p": json.dumps(payload, separators=(",", ":")), "id": signal_id},
    )


__all__ = [
    "CallbackOutcome",
    "TelegramTradeConfig",
    "handle_callback",
    "send_trade_signal_message",
]
