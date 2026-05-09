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
    from app.auth.rate_limit import reset_for_tests
    reset_for_tests()
    r = await friend_client.post(
        "/api/v1/me/totp/verify", json={"code": "123456"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_post_me_totp_verify_rate_limit_429(
    friend_client: httpx.AsyncClient,
) -> None:
    """6th attempt within 60s returns 429 with Retry-After."""
    from app.auth.rate_limit import reset_for_tests
    reset_for_tests()
    await friend_client.post("/api/v1/me/totp/setup")
    # 5 fast attempts succeed (returns ok=False, but 200).
    for _ in range(5):
        r = await friend_client.post(
            "/api/v1/me/totp/verify", json={"code": "000000"},
        )
        assert r.status_code == 200
    # 6th hits the limit.
    r = await friend_client.post(
        "/api/v1/me/totp/verify", json={"code": "000000"},
    )
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) >= 1
