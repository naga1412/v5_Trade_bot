"""PR9 alert_routing — Telegram > SMTP > log precedence tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.ops import alert_routing


@pytest.mark.asyncio
async def test_critical_routes_to_telegram_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Critical + Telegram configured → Telegram sends; SMTP NOT called."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    smtp_mock = AsyncMock(return_value=True)
    with patch(
        "app.ops.alert_routing._send_telegram",
        new=AsyncMock(return_value=True),
    ) as tg_mock, patch(
        "app.ops.alerts.alert_admin", new=smtp_mock,
    ):
        result = await alert_routing.alert_admin(
            "worker silent", level="critical",
        )
    assert result is True
    tg_mock.assert_awaited_once()
    smtp_mock.assert_not_called()


@pytest.mark.asyncio
async def test_critical_falls_through_to_smtp_when_telegram_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Critical + Telegram configured but call fails → SMTP attempt."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    with patch(
        "app.ops.alert_routing._send_telegram",
        new=AsyncMock(return_value=False),
    ), patch(
        "app.ops.alerts.alert_admin", new=AsyncMock(return_value=True),
    ) as smtp_mock:
        result = await alert_routing.alert_admin(
            "worker silent", level="critical",
        )
    assert result is True
    smtp_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_critical_falls_to_log_when_both_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Telegram + no SMTP → returns False but log still emits."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    with patch(
        "app.ops.alerts.alert_admin",
        new=AsyncMock(return_value=False),
    ) as smtp_mock:
        result = await alert_routing.alert_admin(
            "worker silent", level="critical",
        )
    assert result is False
    # SMTP path still attempted even if no Telegram config
    smtp_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_warning_skips_telegram_uses_smtp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warning level skips Telegram to avoid alert spam — SMTP only."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    tg_mock = AsyncMock(return_value=True)
    with patch(
        "app.ops.alert_routing._send_telegram", new=tg_mock,
    ), patch(
        "app.ops.alerts.alert_admin",
        new=AsyncMock(return_value=True),
    ):
        result = await alert_routing.alert_admin(
            "minor blip", level="warning",
        )
    assert result is True
    tg_mock.assert_not_called()


@pytest.mark.asyncio
async def test_partial_telegram_config_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only TELEGRAM_BOT_TOKEN set (no chat_id) → Telegram skipped."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    tg_mock = AsyncMock(return_value=True)
    with patch(
        "app.ops.alert_routing._send_telegram", new=tg_mock,
    ), patch(
        "app.ops.alerts.alert_admin", new=AsyncMock(return_value=True),
    ) as smtp_mock:
        result = await alert_routing.alert_admin(
            "test", level="critical",
        )
    tg_mock.assert_not_called()
    smtp_mock.assert_awaited_once()
    assert result is True


@pytest.mark.asyncio
async def test_smtp_exception_does_not_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If SMTP path raises, alert_admin returns False — never bubbles."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with patch(
        "app.ops.alerts.alert_admin",
        new=AsyncMock(side_effect=RuntimeError("smtp boom")),
    ):
        result = await alert_routing.alert_admin("test", level="critical")
    assert result is False
