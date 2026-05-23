"""Log-record filter that redacts sensitive tokens from log messages.

PR-AUDIT-FIXES-1 (2026-05-23): PR-FULL-SYSTEM-AUDIT (Cat I4.1) found that
httpx logs the full URL of every HTTP request at INFO level, and the
telegram_poller calls go to URLs of the shape:

  https://api.telegram.org/bot<bot_id>:<secret>/getUpdates

Both the numeric bot_id AND the secret token are in the URL path. Anyone
with backend log access (vendor, contractor, AI agent) can lift the bot
token verbatim.

This filter sits on the httpx logger (and any other logger that may
emit URLs containing the same shape) and rewrites the token portion to
``bot***REDACTED***`` before the record is emitted. The redaction
runs in-process — log files / Docker stdout never see the real token.

Other sensitive-pattern redactions can be added to ``REDACT_PATTERNS``
as we discover them.
"""

from __future__ import annotations

import logging
import re


# Patterns are compiled once at module load. Each pattern's `sub` replacement
# is applied to every log record's formatted message.
#
# (pattern, replacement) tuples. The pattern must match the literal token
# format so non-token strings are left alone (e.g., "robot" should NOT be
# matched by the Telegram bot pattern).
REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Telegram bot token: `bot<digits>:<base64-ish>`.
    # The bot_id is 8-10 digits and the secret is 30-45 alphanumeric +
    # `_` + `-` characters. Anchoring on `bot` followed by `\d+:` makes
    # this very unlikely to false-positive on prose like "robot" or
    # "abbot" (those have a different preceding char OR no `\d+:`).
    (
        re.compile(r"bot\d{6,15}:[A-Za-z0-9_-]{20,80}"),
        "bot***REDACTED***",
    ),
]


class SensitiveTokenRedactingFilter(logging.Filter):
    """Filter that rewrites the formatted ``message`` to redact tokens.

    Subtle: ``logging.Filter.filter`` runs BEFORE the formatter, so we
    can't safely modify ``record.msg`` + ``record.args`` together (the
    formatter would re-interpolate the args into the original ``msg``
    template, undoing our redaction). Instead, we pre-format the
    message via ``record.getMessage()``, run the regex subs on the
    formatted string, then attach the redacted output as a new
    ``record.msg`` with empty args. This is the standard pattern for
    log-content rewriting.

    Returns True to allow the record to be emitted.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            formatted = record.getMessage()
        except Exception:  # noqa: BLE001 — log emission must never crash
            return True
        redacted = formatted
        for pattern, replacement in REDACT_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        if redacted != formatted:
            record.msg = redacted
            # Python logging documents record.args as a tuple/dict.
            # Empty tuple is the portable "no args" sentinel.
            record.args = ()
        return True


def install_redaction_filter() -> None:
    """Attach the filter to the loggers that emit URL-bearing messages.

    Called once at app startup, BEFORE the first HTTP request is made.
    Attaching to ``httpx`` covers the common case (httpx logs every
    request URL at INFO). Attaching to the root logger as a belt-and-
    suspenders backstop catches anything else that emits the token
    pattern.

    Idempotent: re-running the install is harmless (a duplicate filter
    instance just runs the same regex twice — no behavior change).
    """
    instance = SensitiveTokenRedactingFilter()
    # Loggers we KNOW emit URL-bearing records. httpx is the primary
    # target; the telegram-bot lib may also log via its own logger name.
    for logger_name in ("httpx", "telegram", "root", ""):
        logger = logging.getLogger(logger_name)
        # Avoid stacking the same filter instance class twice on the
        # same logger across hot-reload / test fixtures.
        existing_types = {type(f) for f in logger.filters}
        if SensitiveTokenRedactingFilter in existing_types:
            continue
        logger.addFilter(instance)


__all__ = [
    "REDACT_PATTERNS",
    "SensitiveTokenRedactingFilter",
    "install_redaction_filter",
]
