"""SP-8 Phase J — PATCH /api/v1/me/trading-mode integration tests."""
from __future__ import annotations

import httpx
import pyotp
import pytest


@pytest.mark.asyncio
async def test_downgrade_succeeds_without_totp(
    friend_client: httpx.AsyncClient,
    auth_factory,
) -> None:
    """telegram-approve / fully-auto -> manual is always allowed."""
    import sqlalchemy as sa
    me = (await friend_client.get("/api/v1/me")).json()
    user_id = me["id"]

    # Force the user into telegram-approve via the same factory the
    # API uses so the change is visible to the endpoint under test.
    async with auth_factory() as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='telegram-approve' WHERE id=:u"
        ), {"u": user_id})
        await s.commit()

    r = await friend_client.patch(
        "/api/v1/me/trading-mode",
        json={"new_mode": "manual"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_mode"] == "manual"
    assert body["old_mode"] == "telegram-approve"
    assert body["is_upgrade"] is False
    assert body["audit_row_hash"]


@pytest.mark.asyncio
async def test_upgrade_400_when_no_totp_code(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.patch(
        "/api/v1/me/trading-mode",
        json={"new_mode": "telegram-approve"},
    )
    assert r.status_code == 400
    assert "TOTP" in r.json()["detail"]


@pytest.mark.asyncio
async def test_upgrade_400_when_totp_invalid(
    friend_client: httpx.AsyncClient,
) -> None:
    await friend_client.post("/api/v1/me/totp/setup")
    r = await friend_client.patch(
        "/api/v1/me/trading-mode",
        json={"new_mode": "telegram-approve", "totp_code": "000000"},
    )
    assert r.status_code == 400
    assert "invalid TOTP" in r.json()["detail"]


@pytest.mark.asyncio
async def test_upgrade_400_when_gates_failed(
    friend_client: httpx.AsyncClient,
) -> None:
    """Empty shadow_trades table → gate snapshot has 0 trades, fails."""
    setup = (await friend_client.post("/api/v1/me/totp/setup")).json()
    code = pyotp.TOTP(setup["secret_for_display"]).now()

    r = await friend_client.patch(
        "/api/v1/me/trading-mode",
        json={"new_mode": "telegram-approve", "totp_code": code},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["error"] == "gate_failed"
    assert detail["current_mode"] == "manual"
    assert detail["requested_mode"] == "telegram-approve"
    assert detail["failures"]
