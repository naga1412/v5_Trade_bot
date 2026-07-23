"""Healer selftest — unit-level tests for the alert-path canary script.

The script's actual purpose (physical Telegram delivery) can only be
confirmed by the operator with the ops-debug probe. What we CAN pin
here in CI:

  * The [SELFTEST] tag is present in the alert body — downstream readers
    (log aggregators, ops-debug filters) rely on it to distinguish
    canary from real alarms.
  * alert_admin is called with level='critical' — the level string that
    opts into alert_routing's Telegram-first precedence.
  * When alert_admin returns True, the script exit code is 0.
  * When alert_admin returns False (all channels exhausted), exit is 1.
  * A healer_findings row is recorded per run, tagged
    detector_name='healer_selftest'.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from scripts import healer_selftest


class _CapturedFinding:
    """Test double for record_finding — records call kwargs."""
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def __call__(self, session_factory, **kwargs: object) -> None:
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_selftest_message_carries_selftest_tag() -> None:
    """The message body must include [SELFTEST] so downstream readers
    can filter canary from real alarms."""
    captured_msg: dict[str, object] = {}

    async def fake_alert(msg: str, *, level: str = "warning") -> bool:
        captured_msg["msg"] = msg
        captured_msg["level"] = level
        return True

    with patch.object(healer_selftest, "alert_admin", new=fake_alert), \
         patch.object(
             healer_selftest, "record_finding", new=_CapturedFinding(),
         ), \
         patch.object(healer_selftest, "get_session_factory", new=lambda: None):
        rc = await healer_selftest._run()
    assert rc == 0
    assert "[SELFTEST]" in captured_msg["msg"]
    assert captured_msg["level"] == "critical"


@pytest.mark.asyncio
async def test_selftest_records_findings_row_with_routed_ok_true() -> None:
    """When routing succeeded, the healer_findings row must reflect it."""
    captured = _CapturedFinding()
    with patch.object(
        healer_selftest, "alert_admin",
        new=AsyncMock(return_value=True),
    ), \
         patch.object(healer_selftest, "record_finding", new=captured), \
         patch.object(healer_selftest, "get_session_factory", new=lambda: None):
        rc = await healer_selftest._run()
    assert rc == 0
    assert len(captured.calls) == 1
    call = captured.calls[0]
    assert call["detector_name"] == "healer_selftest"
    assert call["severity"] == "info"
    assert call["details"]["routed_ok"] is True
    assert call["details"]["tag"] == "[SELFTEST]"


@pytest.mark.asyncio
async def test_selftest_exits_nonzero_when_every_channel_fails() -> None:
    """alert_admin returns False when both Telegram and SMTP were
    unavailable. The probe must exit non-zero so the ops-debug run
    marks itself failed."""
    captured = _CapturedFinding()
    with patch.object(
        healer_selftest, "alert_admin",
        new=AsyncMock(return_value=False),
    ), \
         patch.object(healer_selftest, "record_finding", new=captured), \
         patch.object(healer_selftest, "get_session_factory", new=lambda: None):
        rc = await healer_selftest._run()
    assert rc == 1
    assert captured.calls[0]["details"]["routed_ok"] is False


@pytest.mark.asyncio
async def test_selftest_survives_alert_admin_exception() -> None:
    """A broken alert path (e.g. httpx TimeoutError) must NOT propagate.
    The probe records the exception + exits non-zero."""
    captured = _CapturedFinding()

    async def raising_alert(msg: str, *, level: str = "warning") -> bool:
        raise RuntimeError("simulated telegram outage")

    with patch.object(healer_selftest, "alert_admin", new=raising_alert), \
         patch.object(healer_selftest, "record_finding", new=captured), \
         patch.object(healer_selftest, "get_session_factory", new=lambda: None):
        rc = await healer_selftest._run()
    assert rc == 1
    call = captured.calls[0]
    assert "simulated telegram outage" in str(call["details"]["exception"])
