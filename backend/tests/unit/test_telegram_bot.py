"""Tests for app.ops.telegram_bot (SP-4 Phase E1).

Mocks httpx so tests never hit api.telegram.org or the backend's own
admin_rl loopback. Covers message formatting, inline-keyboard layout,
callback parsing, and the dispatch flow for approve / reject / details.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.ops.telegram_bot import (
    APPROVAL_TIMEOUT_DAYS,
    REMINDER_INTERVAL_DAYS,
    CandidateSummary,
    TelegramConfig,
    _build_inline_keyboard,
    format_candidate_message,
    handle_callback_query,
    load_config_from_env,
    parse_callback_data,
    send_candidate_approval,
)


def _summary(**overrides) -> CandidateSummary:
    base = dict(
        checkpoint_id=42,
        version="v1-20260514-033000",
        sharpe=1.87,
        champion_sharpe=1.62,
        pct_improvement=15.4,
        max_drawdown=0.112,
        n_trades=412,
        win_rate=0.47,
    )
    base.update(overrides)
    return CandidateSummary(**base)


def _config() -> TelegramConfig:
    return TelegramConfig(
        bot_token="testtoken123",
        chat_id="98765",
        backend_internal_url="http://localhost:8000",
    )


# ---- constants ---------------------------------------------------------


def test_constants_match_spec_section_5_4() -> None:
    assert APPROVAL_TIMEOUT_DAYS == 7
    assert REMINDER_INTERVAL_DAYS == 1


# ---- load_config_from_env ----------------------------------------------


def test_load_config_returns_none_when_token_missing(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1234")
    assert load_config_from_env() is None


def test_load_config_returns_none_when_chat_missing(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert load_config_from_env() is None


def test_load_config_returns_none_when_blank_strings(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "  ")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1234")
    assert load_config_from_env() is None


def test_load_config_picks_up_set_env(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1234")
    monkeypatch.setenv("BACKEND_INTERNAL_URL", "http://other:9000")
    cfg = load_config_from_env()
    assert cfg is not None
    assert cfg.bot_token == "tok"
    assert cfg.chat_id == "1234"
    assert cfg.backend_internal_url == "http://other:9000"


def test_load_config_default_backend_url(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1234")
    monkeypatch.delenv("BACKEND_INTERNAL_URL", raising=False)
    cfg = load_config_from_env()
    assert cfg is not None
    assert cfg.backend_internal_url == "http://localhost:8000"


# ---- format_candidate_message + keyboard --------------------------------


def test_format_message_contains_required_metrics() -> None:
    text = format_candidate_message(_summary())
    assert "v1-20260514-033000" in text
    assert "1.87" in text
    assert "1.62" in text
    assert "15.4" in text
    assert "11.2%" in text  # max DD
    assert "412" in text
    assert "47" in text  # win rate %


def test_format_message_omits_pct_when_no_champion() -> None:
    """First-checkpoint case: no champion_sharpe → no '(champion ..., +X%)' suffix."""
    text = format_candidate_message(
        _summary(champion_sharpe=None, pct_improvement=None),
    )
    assert "champion" not in text
    assert "+" not in text


def test_inline_keyboard_has_three_buttons() -> None:
    kb = _build_inline_keyboard(checkpoint_id=42)
    buttons = kb["inline_keyboard"][0]
    assert len(buttons) == 3
    assert {b["text"] for b in buttons} == {
        "✅ Approve & Activate", "❌ Reject", "📊 Details",
    }
    assert all(b["callback_data"].endswith(":42") for b in buttons)


def test_inline_keyboard_callback_data_short_enough() -> None:
    """Telegram limits callback_data to 64 bytes; spec uses ``<kind>:<id>``."""
    kb = _build_inline_keyboard(checkpoint_id=999_999)
    for btn in kb["inline_keyboard"][0]:
        assert len(btn["callback_data"].encode()) <= 64


# ---- parse_callback_data ------------------------------------------------


def test_parse_valid_callback() -> None:
    assert parse_callback_data("rl_approve:42") == ("rl_approve", 42)
    assert parse_callback_data("rl_reject:1") == ("rl_reject", 1)
    assert parse_callback_data("rl_details:999") == ("rl_details", 999)


def test_parse_missing_colon_raises() -> None:
    with pytest.raises(ValueError, match="missing ':'"):
        parse_callback_data("rl_approve_42")


def test_parse_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown callback kind"):
        parse_callback_data("foo_bar:42")


def test_parse_non_integer_id_raises() -> None:
    with pytest.raises(ValueError, match="bad checkpoint id"):
        parse_callback_data("rl_approve:not_a_number")


# ---- send_candidate_approval -------------------------------------------


@pytest.mark.asyncio
async def test_send_returns_message_id_on_success() -> None:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 5678}}
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_resp)

    msg_id = await send_candidate_approval(
        config=_config(), summary=_summary(), client=mock_client,
    )
    assert msg_id == 5678
    mock_client.post.assert_awaited_once()
    url = mock_client.post.call_args[0][0]
    assert "sendMessage" in url
    assert "/bottesttoken123/" in url
    posted = mock_client.post.call_args[1]["json"]
    assert posted["chat_id"] == "98765"
    assert "v1-20260514-033000" in posted["text"]
    assert posted["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "rl_approve:42"


@pytest.mark.asyncio
async def test_send_returns_none_on_non_200() -> None:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 401
    mock_resp.text = '{"ok":false,"error":"unauthorized"}'
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_resp)

    out = await send_candidate_approval(
        config=_config(), summary=_summary(), client=mock_client,
    )
    assert out is None


@pytest.mark.asyncio
async def test_send_returns_none_on_api_ok_false() -> None:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": False, "error": "rate limited"}
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_resp)

    out = await send_candidate_approval(
        config=_config(), summary=_summary(), client=mock_client,
    )
    assert out is None


@pytest.mark.asyncio
async def test_send_returns_none_on_http_error() -> None:
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("dns failed"))

    out = await send_candidate_approval(
        config=_config(), summary=_summary(), client=mock_client,
    )
    assert out is None


# ---- handle_callback_query ---------------------------------------------


def _callback_query(
    *, data: str, callback_id: str = "cb-1", message_id: int = 100,
) -> dict:
    return {
        "id": callback_id,
        "from": {"id": 999, "username": "operator"},
        "data": data,
        "message": {"message_id": message_id},
    }


@pytest.mark.asyncio
async def test_handle_approve_patches_admin_rl_with_force() -> None:
    """Approve → PATCH /api/v1/admin/rl-checkpoints/42?force=true with is_active=true."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = "{}"
    mock_resp.json.return_value = {"ok": True}
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.patch = AsyncMock(return_value=mock_resp)
    mock_client.post = AsyncMock(return_value=mock_resp)

    kind = await handle_callback_query(
        config=_config(),
        callback_query=_callback_query(data="rl_approve:42"),
        client=mock_client,
    )
    assert kind == "rl_approve"
    # PATCH was called once on the admin_rl endpoint
    mock_client.patch.assert_awaited_once()
    url = mock_client.patch.call_args[0][0]
    assert url.endswith("/api/v1/admin/rl-checkpoints/42")
    assert mock_client.patch.call_args[1]["params"] == {"force": "true"}
    assert mock_client.patch.call_args[1]["json"] == {"is_active": True}
    # answerCallbackQuery + editMessageText posted
    posted_urls = [c.args[0] for c in mock_client.post.await_args_list]
    assert any("answerCallbackQuery" in u for u in posted_urls)
    assert any("editMessageText" in u for u in posted_urls)


@pytest.mark.asyncio
async def test_handle_reject_patches_is_active_false_no_force() -> None:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = "{}"
    mock_resp.json.return_value = {"ok": True}
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.patch = AsyncMock(return_value=mock_resp)
    mock_client.post = AsyncMock(return_value=mock_resp)

    kind = await handle_callback_query(
        config=_config(),
        callback_query=_callback_query(data="rl_reject:42"),
        client=mock_client,
    )
    assert kind == "rl_reject"
    mock_client.patch.assert_awaited_once()
    assert mock_client.patch.call_args[1]["json"] == {"is_active": False}
    assert mock_client.patch.call_args[1]["params"] == {}


@pytest.mark.asyncio
async def test_handle_details_does_not_patch() -> None:
    """Details button only acknowledges the callback; no admin_rl call."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = "{}"
    mock_resp.json.return_value = {"ok": True}
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.patch = AsyncMock(return_value=mock_resp)
    mock_client.post = AsyncMock(return_value=mock_resp)

    kind = await handle_callback_query(
        config=_config(),
        callback_query=_callback_query(data="rl_details:42"),
        client=mock_client,
    )
    assert kind == "rl_details"
    mock_client.patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_unparseable_payload_returns_none() -> None:
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.patch = AsyncMock()
    mock_client.post = AsyncMock()

    kind = await handle_callback_query(
        config=_config(),
        callback_query=_callback_query(data="garbage"),
        client=mock_client,
    )
    assert kind is None
    mock_client.patch.assert_not_awaited()
    mock_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_approve_failure_still_acks_callback() -> None:
    """admin_rl PATCH returns 422 (challenger lost the gate) → we still
    answer the callback so Telegram doesn't show a stuck spinner."""
    fail_resp = MagicMock(spec=httpx.Response)
    fail_resp.status_code = 422
    fail_resp.text = '{"detail":"challenger lost"}'
    ok_resp = MagicMock(spec=httpx.Response)
    ok_resp.status_code = 200
    ok_resp.text = "{}"
    ok_resp.json.return_value = {"ok": True}

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.patch = AsyncMock(return_value=fail_resp)
    mock_client.post = AsyncMock(return_value=ok_resp)

    kind = await handle_callback_query(
        config=_config(),
        callback_query=_callback_query(data="rl_approve:42"),
        client=mock_client,
    )
    assert kind == "rl_approve"
    # Both answer + edit still happen
    posted_urls = [c.args[0] for c in mock_client.post.await_args_list]
    assert any("answerCallbackQuery" in u for u in posted_urls)
    assert any("editMessageText" in u for u in posted_urls)
