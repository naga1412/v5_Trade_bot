"""Unit tests for app.ops.alerts.alert_admin — Phase D1.

Validates the three contract paths:
    1. Missing SMTP env vars → log.error fallback, no raise
    2. All env vars set → SMTP send invoked with correct subject/body/recipient
    3. SMTP raises → log.error swallow (best-effort, never re-raise)
"""
from __future__ import annotations

import logging

import pytest

from app.ops.alerts import alert_admin


def _set_smtp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "noreply@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "x")
    monkeypatch.setenv("ALERT_RECIPIENT_EMAIL", "admin@example.com")


def _clear_smtp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
        "SMTP_PASSWORD", "ALERT_RECIPIENT_EMAIL",
    ):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.asyncio
async def test_alert_admin_logs_when_smtp_not_configured(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing SMTP env vars → log.error, no raise."""
    _clear_smtp_env(monkeypatch)
    caplog.set_level(logging.WARNING, logger="app.ops.alerts")
    result = await alert_admin("test message", severity="warning")
    assert result is False
    assert any(
        "SMTP not configured" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_alert_admin_sends_via_smtp_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All env vars set → SMTP send is invoked with the right subject/body."""
    _set_smtp_env(monkeypatch)

    sent: dict = {}

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
            sent["host"] = host
            sent["port"] = port

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def starttls(self) -> None:
            sent["starttls"] = True

        def login(self, user: str, pw: str) -> None:
            sent["user"] = user
            sent["pw"] = pw

        def send_message(self, msg: object) -> None:
            sent["subject"] = msg["Subject"]  # type: ignore[index]
            sent["to"] = msg["To"]  # type: ignore[index]
            sent["from"] = msg["From"]  # type: ignore[index]
            sent["body"] = msg.get_content()  # type: ignore[attr-defined]

    import app.ops.alerts as alerts_mod
    monkeypatch.setattr(alerts_mod, "SMTP", FakeSMTP)

    result = await alert_admin("disk full on Oracle", severity="critical")
    assert result is True
    assert sent["host"] == "smtp.example.com"
    assert sent["port"] == 587
    assert sent["user"] == "noreply@example.com"
    assert "[critical]" in sent["subject"]
    assert "disk full on Oracle" in sent["body"]
    assert sent["to"] == "admin@example.com"
    assert sent["from"] == "noreply@example.com"


@pytest.mark.asyncio
async def test_alert_admin_swallows_smtp_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """SMTP raises → log.error but never re-raise (alert is best-effort)."""
    _set_smtp_env(monkeypatch)

    class BoomSMTP:
        def __init__(self, *a: object, **kw: object) -> None:
            raise ConnectionError("net down")

    import app.ops.alerts as alerts_mod
    monkeypatch.setattr(alerts_mod, "SMTP", BoomSMTP)
    caplog.set_level(logging.ERROR, logger="app.ops.alerts")

    result = await alert_admin("test", severity="warning")  # must not raise
    assert result is False
    assert any(
        "alert dispatch failed" in r.message for r in caplog.records
    )
