"""PR9 alert routing — Telegram > SMTP > logs precedence.

Replaces the bare `app.ops.alerts.alert_admin` for stateful-worker
critical alerts. The existing SMTP-only path stays in place (call
sites can keep using it for low-severity alerts), but stateful-worker
watchdog alerts route here so they reach the operator's phone when
SMTP isn't configured (the common prod case — see KNOWN_ISSUES FU-2
note about SMTP).

Decision order:
  1. If TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are set:
     → POST sendMessage; on 200 return True.
     → On HTTP error or non-200: log + fall through to SMTP.
  2. If SMTP_HOST + creds set:
     → reuse alerts.alert_admin (SMTP path).
     → On failure: fall through to log.
  3. Always emit a log line as the last-resort signal.

`level` is the severity tag. Convention:
  "critical" — stateful-worker silent for >max_staleness; routes to
               Telegram first (operator should react fast).
  "warning"  — stateless-worker silent; routes to SMTP if configured,
               else logs. Telegram skip is intentional: don't spam.
  "info"     — diagnostic; logs only (Telegram + SMTP skipped).
"""
from __future__ import annotations

import logging
import os

import httpx


log = logging.getLogger(__name__)


_TELEGRAM_API_BASE = "https://api.telegram.org"
_TELEGRAM_TIMEOUT_S = 10.0


def _telegram_config() -> tuple[str, str] | None:
    """Read Telegram env vars. Return (token, chat_id) or None."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    return token, chat_id


async def _send_telegram(token: str, chat_id: str, message: str) -> bool:
    """POST sendMessage. Returns True on 200, False otherwise."""
    url = f"{_TELEGRAM_API_BASE}/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=_TELEGRAM_TIMEOUT_S) as client:
            resp = await client.post(
                url,
                json={"chat_id": chat_id, "text": message},
            )
    except httpx.HTTPError as e:
        log.warning("alert_routing: telegram HTTP error: %s", e)
        return False
    if resp.status_code != 200:
        log.warning(
            "alert_routing: telegram returned %d: %s",
            resp.status_code, resp.text[:200],
        )
        return False
    return True


async def alert_admin(message: str, *, level: str = "warning") -> bool:
    """Route an alert per the documented precedence.

    Returns True if any of (Telegram, SMTP) succeeded; False if both
    were unavailable OR failed. A False return does NOT mean the
    operator was uninformed — the log line at the end is the
    last-resort signal regardless.

    `level` controls Telegram opt-in: only "critical" tries Telegram.
    Lower levels skip Telegram to avoid alert spam.
    """
    # Always log first — this is the can't-fail audit trail.
    log.warning("alert_admin [%s]: %s", level, message)

    if level == "critical":
        cfg = _telegram_config()
        if cfg is not None:
            token, chat_id = cfg
            tagged = f"[{level.upper()}] {message}"
            if await _send_telegram(token, chat_id, tagged):
                return True
            # else: fall through to SMTP

    # Try SMTP for anything not handled above.
    try:
        from app.ops.alerts import alert_admin as _smtp_alert
        sent = await _smtp_alert(message, severity=level)
        if sent:
            return True
    except Exception as e:  # noqa: BLE001
        log.error("alert_routing: SMTP path raised: %s", e)

    # All paths exhausted; the initial log.warning is the operator's signal.
    return False


__all__ = ["alert_admin"]
