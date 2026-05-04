"""Integration tests for POST /api/v1/me/totp/verify (Phase H5)."""

from __future__ import annotations

import httpx
import pyotp
import pytest


@pytest.mark.asyncio
async def test_post_me_totp_verify_accepts_current_code(
    friend_client: httpx.AsyncClient,
) -> None:
    setup_r = await friend_client.post("/api/v1/me/totp/setup")
    secret = setup_r.json()["secret_for_display"]
    code = pyotp.TOTP(secret).now()

    r = await friend_client.post(
        "/api/v1/me/totp/verify", json={"code": code},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_post_me_totp_verify_rejects_wrong_code(
    friend_client: httpx.AsyncClient,
) -> None:
    await friend_client.post("/api/v1/me/totp/setup")
    r = await friend_client.post(
        "/api/v1/me/totp/verify", json={"code": "000000"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False


@pytest.mark.asyncio
async def test_post_me_totp_verify_400_when_no_secret(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.post(
        "/api/v1/me/totp/verify", json={"code": "123456"},
    )
    assert r.status_code == 400
