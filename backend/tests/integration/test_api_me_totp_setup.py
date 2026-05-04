"""Integration tests for POST /api/v1/me/totp/setup (Phase H4)."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.models import User
from app.auth.secrets import decrypt_for_user
from app.config import get_settings


@pytest.mark.asyncio
async def test_post_me_totp_setup_writes_encrypted_secret(
    friend_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    r = await friend_client.post("/api/v1/me/totp/setup")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provisioning_uri"].startswith("otpauth://totp/")
    assert len(body["secret_for_display"]) >= 16
    assert isinstance(body["backup_codes"], list)
    assert len(body["backup_codes"]) == 8

    async with auth_factory() as s:
        u = await s.get(User, 2)
        assert u is not None
        assert u.totp_secret_encrypted is not None
        assert u.totp_backup_codes_encrypted is not None
        secret = decrypt_for_user(
            u.totp_secret_encrypted,
            passphrase=get_settings().master_passphrase,
            user_id=u.id,
        )
        backup_json = decrypt_for_user(
            u.totp_backup_codes_encrypted,
            passphrase=get_settings().master_passphrase,
            user_id=u.id,
        )
    assert secret == body["secret_for_display"]
    assert json.loads(backup_json) == body["backup_codes"]


@pytest.mark.asyncio
async def test_post_me_totp_setup_overwrites_previous(
    friend_client: httpx.AsyncClient,
) -> None:
    r1 = await friend_client.post("/api/v1/me/totp/setup")
    r2 = await friend_client.post("/api/v1/me/totp/setup")
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Each setup yields a fresh secret.
    assert r1.json()["secret_for_display"] != r2.json()["secret_for_display"]
