"""Email alert dispatcher.

Implementation lands in Phase D1. SMTP envvars (SMTP_HOST, SMTP_PORT,
SMTP_USER, SMTP_PASSWORD); fallback to log.error if not configured.
"""
from __future__ import annotations


async def alert_admin(  # pragma: no cover — stub
    message: str, *, severity: str = "warning",
) -> None:
    """Send an email to the operator's configured alert address. Phase D1.

    TODO(SP-7 Phase D1): wire aiosmtplib + render plaintext + html bodies
    from spec §3.2; on missing SMTP config, downgrade to structlog.error.
    """
    raise NotImplementedError("alert_admin: Phase D1 deliverable")
