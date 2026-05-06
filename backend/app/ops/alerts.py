"""Email alert dispatcher.

Sends ops alerts via SMTP. If SMTP env vars are unset, falls back to a
logger.warning so the operator still gets a signal in container logs even
without a configured mailer. SMTP failures are caught and logged — alert
dispatch is best-effort and must NEVER bring down the calling task
(typically the nightly verifier loop, see app.ops.verifier_scheduler).

Env vars (all required for SMTP delivery; any missing → log fallback):
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERT_RECIPIENT_EMAIL
"""

from __future__ import annotations

import asyncio
import logging
import os
from email.message import EmailMessage
from smtplib import SMTP

log = logging.getLogger(__name__)

_REQUIRED_ENV: tuple[str, ...] = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "ALERT_RECIPIENT_EMAIL",
)


def _smtp_config() -> dict[str, str] | None:
    """Read SMTP env vars; return None if any required key is missing/blank."""
    values = {k: os.environ.get(k, "") for k in _REQUIRED_ENV}
    if any(not v for v in values.values()):
        return None
    return values


def _build_message(message: str, severity: str, cfg: dict[str, str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"[trading-radar] [{severity}] {message[:120]}"
    msg["From"] = cfg["SMTP_USER"]
    msg["To"] = cfg["ALERT_RECIPIENT_EMAIL"]
    msg.set_content(message)
    return msg


def _send_sync(cfg: dict[str, str], msg: EmailMessage) -> None:
    """Synchronous SMTP delivery — invoked under asyncio.to_thread."""
    with SMTP(cfg["SMTP_HOST"], int(cfg["SMTP_PORT"]), timeout=10.0) as smtp:
        try:
            smtp.starttls()
        except Exception:  # noqa: BLE001 — server may not support STARTTLS
            pass
        smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
        smtp.send_message(msg)


async def alert_admin(message: str, *, severity: str = "warning") -> bool:
    """Best-effort email dispatch. Never raises.

    Returns True on successful SMTP send, False otherwise (missing config or
    delivery failure — both downgrade to a log line).
    """
    cfg = _smtp_config()
    if cfg is None:
        log.warning(
            "alert_admin: SMTP not configured: [%s] %s", severity, message,
        )
        return False

    msg = _build_message(message, severity, cfg)
    try:
        await asyncio.to_thread(_send_sync, cfg, msg)
    except Exception as exc:  # noqa: BLE001 — best-effort; log + swallow
        log.error("alert_admin: alert dispatch failed: %s", exc)
        return False
    return True


__all__ = ["alert_admin"]
