"""SP-4 Phase E — Telegram inline-button approval flow for brain checkpoints.

Spec sec 5.4 — when the nightly brain retrain on Hetzner produces a
candidate that beats the active champion by ≥5% Sharpe, an inline-
button Telegram message arrives:

    🧠 Brain candidate v1-20260514-033000 ready
    Sharpe 1.87 (champion 1.62, +15.4%)
    Max DD 11.2%, 412 trades, win-rate 47%

    [Approve & Activate]   [Reject]   [Details]

Operator taps a button → the FastAPI poller (Phase E.next, started in
``app.main:lifespan``) pulls the callback_query → routes to
:func:`handle_callback_query` here → dispatches the admin_rl PATCH or
DELETE → edits the original message with the result.

7-day timeout: if no callback arrives within
``APPROVAL_TIMEOUT_DAYS``, the candidate auto-rejects and the operator
gets a daily reminder to start a fresh training run.

Environment:
    TELEGRAM_BOT_TOKEN  — bot token from @BotFather
    TELEGRAM_CHAT_ID    — admin chat id (from @userinfobot or getUpdates)
    BACKEND_INTERNAL_URL — http://localhost:8000 by default; the bot
                           reaches admin_rl via the loopback so it
                           skips Cloudflare Access auth (the SP-7 path).

If any of these are unset, the module falls back to log-only mode
(callbacks are processed as no-ops; `send_candidate_approval` returns
False) — same convention as :mod:`app.ops.alerts`.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Literal

import httpx


log = logging.getLogger(__name__)


# Telegram API root. Override via TELEGRAM_API_BASE for tests.
TELEGRAM_API_BASE: str = os.environ.get(
    "TELEGRAM_API_BASE", "https://api.telegram.org",
)
APPROVAL_TIMEOUT_DAYS: int = 7
REMINDER_INTERVAL_DAYS: int = 1
HTTP_TIMEOUT_S: float = 15.0


CallbackKind = Literal["rl_approve", "rl_reject", "rl_details"]


@dataclass(frozen=True)
class TelegramConfig:
    """Bot credentials + the loopback URL for admin_rl PATCH calls."""

    bot_token: str
    chat_id: str
    backend_internal_url: str = "http://localhost:8000"


@dataclass(frozen=True)
class CandidateSummary:
    """The eval data shown in the inline-button approval message."""

    checkpoint_id: int
    version: str
    sharpe: float
    champion_sharpe: float | None
    pct_improvement: float | None
    max_drawdown: float
    n_trades: int
    win_rate: float


def load_config_from_env() -> TelegramConfig | None:
    """Return None when bot creds are missing — caller falls back to log-only."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return None
    return TelegramConfig(
        bot_token=token,
        chat_id=chat,
        backend_internal_url=os.environ.get(
            "BACKEND_INTERNAL_URL", "http://localhost:8000",
        ),
    )


def _api_url(*, bot_token: str, method: str) -> str:
    return f"{TELEGRAM_API_BASE}/bot{bot_token}/{method}"


def _build_inline_keyboard(checkpoint_id: int) -> dict[str, Any]:
    """Three-button inline keyboard for approval / reject / details.

    Telegram callback_data has a 64-byte limit; keep the format short:
    ``<kind>:<checkpoint_id>`` so the parser at the other end is trivial.
    """
    return {
        "inline_keyboard": [[
            {
                "text": "✅ Approve & Activate",
                "callback_data": f"rl_approve:{checkpoint_id}",
            },
            {
                "text": "❌ Reject",
                "callback_data": f"rl_reject:{checkpoint_id}",
            },
            {
                "text": "📊 Details",
                "callback_data": f"rl_details:{checkpoint_id}",
            },
        ]],
    }


def format_candidate_message(summary: CandidateSummary) -> str:
    """Render the message body shown above the inline buttons."""
    lines = [
        f"🧠 Brain candidate {summary.version} ready",
        "",
        f"Sharpe: {summary.sharpe:.2f}",
    ]
    if summary.champion_sharpe is not None and summary.pct_improvement is not None:
        lines[-1] += f" (champion {summary.champion_sharpe:.2f}, "
        lines[-1] += f"+{summary.pct_improvement:.1f}%)"
    lines.extend([
        f"Max DD: {summary.max_drawdown:.1%}",
        f"Trades: {summary.n_trades}   Win-rate: {summary.win_rate:.1%}",
        "",
        "Tap a button below to act.",
    ])
    return "\n".join(lines)


def parse_callback_data(payload: str) -> tuple[CallbackKind, int]:
    """Split ``rl_approve:42`` into kind + checkpoint_id.

    Raises ValueError on unknown kind or non-integer id.
    """
    if ":" not in payload:
        raise ValueError(f"missing ':' in callback_data: {payload!r}")
    kind_raw, id_raw = payload.split(":", 1)
    if kind_raw not in {"rl_approve", "rl_reject", "rl_details"}:
        raise ValueError(f"unknown callback kind: {kind_raw!r}")
    try:
        cid = int(id_raw)
    except ValueError as e:
        raise ValueError(f"bad checkpoint id: {id_raw!r}") from e
    return kind_raw, cid  # type: ignore[return-value]


async def send_candidate_approval(
    *,
    config: TelegramConfig,
    summary: CandidateSummary,
    client: httpx.AsyncClient | None = None,
) -> int | None:
    """POST sendMessage with inline keyboard. Returns message_id on success.

    Caller is responsible for the lifecycle of ``client`` if supplied;
    otherwise a short-lived AsyncClient is created and closed inline.
    Returns None on any failure — always logs.
    """
    body = format_candidate_message(summary)
    kb = _build_inline_keyboard(summary.checkpoint_id)
    payload = {
        "chat_id": config.chat_id,
        "text": body,
        "reply_markup": kb,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)
    try:
        try:
            resp = await client.post(
                _api_url(bot_token=config.bot_token, method="sendMessage"),
                json=payload,
            )
        except httpx.HTTPError as e:
            log.error("telegram_bot.send_candidate_approval: HTTP error %s", e)
            return None
        if resp.status_code != 200:
            log.error(
                "telegram_bot.send_candidate_approval: non-200 %d %s",
                resp.status_code, resp.text[:200],
            )
            return None
        data = resp.json()
        if not data.get("ok"):
            log.error("telegram_bot: API returned ok=false: %s", data)
            return None
        return int(data["result"]["message_id"])
    finally:
        if owns_client:
            await client.aclose()


async def _patch_rl_checkpoint(
    *,
    config: TelegramConfig,
    checkpoint_id: int,
    is_active: bool,
    client: httpx.AsyncClient,
) -> bool:
    """Hit /api/v1/admin/rl-checkpoints/{id} via the loopback URL.

    Returns True on 200; False on any non-200. The loopback bypasses
    Cloudflare Access (which only sits in front of the public route),
    so no JWT is needed; FastAPI's ``Depends(require_admin)`` accepts
    requests on the trusted internal port per SP-0.7 §6.3.
    """
    url = (
        f"{config.backend_internal_url}/api/v1/admin/rl-checkpoints/"
        f"{checkpoint_id}"
    )
    params = {"force": "true"} if is_active else {}
    body = {"is_active": is_active}
    try:
        resp = await client.patch(url, params=params, json=body)
    except httpx.HTTPError as e:
        log.error("telegram_bot._patch_rl_checkpoint: HTTP error %s", e)
        return False
    if resp.status_code >= 400:
        log.error(
            "telegram_bot._patch_rl_checkpoint: PATCH %s returned %d: %s",
            url, resp.status_code, resp.text[:200],
        )
        return False
    return True


async def _answer_callback_query(
    *,
    config: TelegramConfig,
    callback_query_id: str,
    text: str,
    client: httpx.AsyncClient,
) -> None:
    """Required Telegram acknowledgement so the spinner stops on the
    operator's phone. Best-effort; never raises."""
    try:
        await client.post(
            _api_url(bot_token=config.bot_token, method="answerCallbackQuery"),
            json={
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": False,
            },
        )
    except httpx.HTTPError as e:
        log.warning("telegram_bot.answer_callback_query: HTTP error %s", e)


async def _edit_message_text(
    *,
    config: TelegramConfig,
    message_id: int,
    new_text: str,
    client: httpx.AsyncClient,
) -> None:
    """Replace the inline-button message body once a decision is in.

    Strips the keyboard so the buttons don't get tapped again.
    """
    try:
        await client.post(
            _api_url(bot_token=config.bot_token, method="editMessageText"),
            json={
                "chat_id": config.chat_id,
                "message_id": message_id,
                "text": new_text,
                "parse_mode": "HTML",
            },
        )
    except httpx.HTTPError as e:
        log.warning("telegram_bot.edit_message_text: HTTP error %s", e)


async def handle_callback_query(
    *,
    config: TelegramConfig,
    callback_query: dict[str, Any],
    client: httpx.AsyncClient | None = None,
) -> CallbackKind | None:
    """Dispatch one Telegram callback_query event.

    Expected ``callback_query`` shape (subset of Telegram's):
        {
          "id": "<callback id>",
          "from": {"id": ..., "username": ...},
          "data": "rl_approve:42",
          "message": {"message_id": ..., ...}
        }

    Returns the dispatched ``CallbackKind`` on success, or ``None`` if
    the callback was malformed / wasn't an rl_* kind.
    """
    payload = callback_query.get("data") or ""
    try:
        kind, cid = parse_callback_data(payload)
    except ValueError as e:
        log.warning("telegram_bot: skipping unparseable callback %r: %s",
                    payload, e)
        return None

    cb_id = str(callback_query.get("id", ""))
    msg = callback_query.get("message") or {}
    message_id = int(msg.get("message_id", 0))

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)
    try:
        if kind == "rl_approve":
            ok = await _patch_rl_checkpoint(
                config=config, checkpoint_id=cid, is_active=True, client=client,
            )
            ack = "✅ Activating brain v" + str(cid) if ok else "❌ Activation failed; see backend logs"
            await _answer_callback_query(
                config=config, callback_query_id=cb_id, text=ack, client=client,
            )
            if message_id:
                final_text = (
                    f"✅ <b>Approved & Activated</b> — checkpoint {cid}"
                    if ok else
                    f"⚠️ Activation failed for checkpoint {cid}; see backend logs"
                )
                await _edit_message_text(
                    config=config, message_id=message_id,
                    new_text=final_text, client=client,
                )
        elif kind == "rl_reject":
            ok = await _patch_rl_checkpoint(
                config=config, checkpoint_id=cid, is_active=False, client=client,
            )
            ack = "❌ Rejected"
            await _answer_callback_query(
                config=config, callback_query_id=cb_id, text=ack, client=client,
            )
            if message_id:
                await _edit_message_text(
                    config=config, message_id=message_id,
                    new_text=f"❌ <b>Rejected</b> — checkpoint {cid} stays inactive",
                    client=client,
                )
        else:  # rl_details
            await _answer_callback_query(
                config=config, callback_query_id=cb_id,
                text="See backend log for full eval JSON",
                client=client,
            )
        return kind
    finally:
        if owns_client:
            await client.aclose()


__all__ = [
    "APPROVAL_TIMEOUT_DAYS",
    "CallbackKind",
    "CandidateSummary",
    "REMINDER_INTERVAL_DAYS",
    "TELEGRAM_API_BASE",
    "TelegramConfig",
    "format_candidate_message",
    "handle_callback_query",
    "load_config_from_env",
    "parse_callback_data",
    "send_candidate_approval",
]
