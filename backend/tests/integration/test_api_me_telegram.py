"""Integration tests for POST /api/v1/me/telegram (Phase H3)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.models import User
from app.auth.secrets import get_telegram
from app.config import get_settings


@pytest.mark.asyncio
async def test_post_me_telegram_round_trip(
    friend_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    body = {"bot_token": "BOT-TOKEN-123", "chat_id": "CHAT-456"}
    r = await friend_client.post("/api/v1/me/telegram", json=body)
    assert r.status_code == 204, r.text

    async with auth_factory() as s:
        u = await s.get(User, 2)
        assert u is not None
        assert u.telegram_bot_token_encrypted is not None
        assert u.telegram_bot_token_encrypted != "BOT-TOKEN-123"
        # chat_id stored plain (not a secret).
        assert u.telegram_chat_id == "CHAT-456"
        tg = get_telegram(u, passphrase=get_settings().master_passphrase)
    assert tg.bot_token == "BOT-TOKEN-123"
    assert tg.chat_id == "CHAT-456"
