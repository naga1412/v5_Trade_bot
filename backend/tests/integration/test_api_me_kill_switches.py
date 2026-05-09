"""SP-8 Phase J — GET/PATCH /api/v1/me/kill-switches integration tests."""
from __future__ import annotations

import httpx
import pyotp
import pytest
import sqlalchemy as sa


_SWITCH_NAMES = {
    "daily_loss",
    "consecutive_losses",
    "network_outage",
    "slippage",
    "liquidation_near",
    "funding_rate_guard",
}


@pytest.mark.asyncio
async def test_list_returns_all_six_with_defaults(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.get("/api/v1/me/kill-switches")
    assert r.status_code == 200
    body = r.json()
    names = {s["name"] for s in body["switches"]}
    assert names == _SWITCH_NAMES
    for s in body["switches"]:
        assert s["enabled"] is True  # defaults
        assert s["is_tripped"] is False
        assert s["default_threshold"] > 0  # spec defaults


@pytest.mark.asyncio
async def test_patch_enable_to_disable_requires_totp(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.patch(
        "/api/v1/me/kill-switches/daily_loss",
        json={"enabled": False},
    )
    assert r.status_code == 400
    assert "TOTP" in r.json()["detail"]


@pytest.mark.asyncio
async def test_patch_disable_with_valid_totp_succeeds(
    friend_client: httpx.AsyncClient,
) -> None:
    setup = (await friend_client.post("/api/v1/me/totp/setup")).json()
    code = pyotp.TOTP(setup["secret_for_display"]).now()
    r = await friend_client.patch(
        "/api/v1/me/kill-switches/daily_loss",
        json={"enabled": False, "totp_code": code},
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False


@pytest.mark.asyncio
async def test_patch_threshold_only_no_totp_needed(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.patch(
        "/api/v1/me/kill-switches/slippage",
        json={"threshold_value": 0.01},
    )
    assert r.status_code == 200
    assert r.json()["threshold_value"] == 0.01
    assert r.json()["enabled"] is True


@pytest.mark.asyncio
async def test_patch_unknown_switch_404(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.patch(
        "/api/v1/me/kill-switches/not_a_switch",
        json={"enabled": True},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_reflects_persisted_state(
    friend_client: httpx.AsyncClient, auth_factory,
) -> None:
    """A row that's tripped + has custom threshold appears in GET."""
    me = (await friend_client.get("/api/v1/me")).json()
    user_id = me["id"]
    async with auth_factory() as s:
        await s.execute(sa.text(
            "INSERT INTO kill_switch_state "
            "(user_id, switch_name, enabled, threshold_value, "
            " is_tripped, tripped_reason) "
            "VALUES (:u, 'daily_loss', 1, 0.05, 1, 'over budget')"
        ), {"u": user_id})
        await s.commit()
    r = await friend_client.get("/api/v1/me/kill-switches")
    daily = next(s for s in r.json()["switches"] if s["name"] == "daily_loss")
    assert daily["threshold_value"] == 0.05
    assert daily["is_tripped"] is True
    assert daily["tripped_reason"] == "over budget"
