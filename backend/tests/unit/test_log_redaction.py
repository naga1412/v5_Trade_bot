"""PR-AUDIT-FIXES-1 — log-redaction filter masks Telegram bot tokens."""
from __future__ import annotations

import logging
import re

from app.ops.log_redaction import (
    REDACT_PATTERNS,
    SensitiveTokenRedactingFilter,
    install_redaction_filter,
)


def _make_record(msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg=msg, args=args, exc_info=None,
    )


def test_filter_redacts_telegram_bot_token_in_url() -> None:
    """The httpx-format URL containing bot<id>:<secret> must be masked."""
    f = SensitiveTokenRedactingFilter()
    record = _make_record(
        "HTTP Request: POST https://api.telegram.org/"
        "bot8667784732:AAH34jusJonSMDi9G25rHgVZZGEQ-35FBbE/getUpdates",
    )
    f.filter(record)
    formatted = record.getMessage()
    assert "8667784732" not in formatted
    assert "AAH34jusJonSMDi9G25rHgVZZGEQ-35FBbE" not in formatted
    assert "bot***REDACTED***" in formatted


def test_filter_preserves_unrelated_urls() -> None:
    """Binance / non-token URLs must pass through unchanged."""
    f = SensitiveTokenRedactingFilter()
    record = _make_record(
        "HTTP Request: GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT",
    )
    f.filter(record)
    assert (
        record.getMessage()
        == "HTTP Request: GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT"
    )


def test_filter_does_not_match_word_robot() -> None:
    """The pattern must NOT false-positive on prose with 'robot' / 'abbot'."""
    f = SensitiveTokenRedactingFilter()
    for text in (
        "the robot is running",
        "abbot's road",
        "a bot12345:short",   # short trailing token below the min length
    ):
        record = _make_record(text)
        f.filter(record)
        assert "REDACTED" not in record.getMessage(), text


def test_filter_handles_args_interpolation() -> None:
    """When the logger uses %s args, the formatted message is what gets redacted."""
    f = SensitiveTokenRedactingFilter()
    record = _make_record(
        "POST %s OK",
        "https://api.telegram.org/bot8667784732:AAH34jusJonSMDi9G25rHgVZZGEQ-35FBbE/getUpdates",
    )
    f.filter(record)
    formatted = record.getMessage()
    assert "AAH34jusJonSMDi9G25rHgVZZGEQ" not in formatted
    assert "REDACTED" in formatted


def test_install_redaction_filter_idempotent() -> None:
    """Calling install twice on the same logger doesn't stack filters."""
    install_redaction_filter()
    install_redaction_filter()
    httpx_logger = logging.getLogger("httpx")
    by_type = [type(f) for f in httpx_logger.filters]
    assert by_type.count(SensitiveTokenRedactingFilter) == 1
    # Root logger must also not stack — the "root" and "" names alias the
    # same logger, and re-install must respect that.
    root_logger = logging.getLogger()
    root_by_type = [type(f) for f in root_logger.filters]
    assert root_by_type.count(SensitiveTokenRedactingFilter) == 1


def test_filter_handles_record_with_no_args() -> None:
    f = SensitiveTokenRedactingFilter()
    record = _make_record("plain message no token")
    assert f.filter(record) is True
    assert record.getMessage() == "plain message no token"


def test_pattern_compiled_once_at_module_load() -> None:
    """Sanity: each REDACT_PATTERNS entry is a compiled re.Pattern."""
    for pattern, replacement in REDACT_PATTERNS:
        assert isinstance(pattern, re.Pattern)
        assert isinstance(replacement, str)


def test_filter_never_crashes_on_bad_getmessage() -> None:
    """A record whose getMessage() raises (e.g., bad %s/args mismatch) must
    not bring down log emission. The filter swallows the error and returns
    True so the broken record still flows to the handler (handler will
    surface its own error)."""
    f = SensitiveTokenRedactingFilter()
    # msg has two %s placeholders but only one arg → TypeError on
    # interpolation inside record.getMessage().
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="%s %s", args=("only-one",), exc_info=None,
    )
    assert f.filter(record) is True
