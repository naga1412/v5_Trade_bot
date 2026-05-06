import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_pause_returns_state_and_flips_redis(admin_client) -> None:
    from app.ops import pause_state
    resp = await admin_client.post(
        "/api/v1/admin/system/pause", json={"reason": "travel"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["paused"] is True
    assert body["by_email"] == "admin@x.com"
    assert body["reason"] == "travel"
    assert body["since"] is not None
    pause_state._CACHE = None
    assert await pause_state.is_paused() is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_pause_requires_reason(admin_client) -> None:
    resp = await admin_client.post(
        "/api/v1/admin/system/pause", json={"reason": ""},
    )
    assert resp.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_friend_cannot_pause(friend_client) -> None:
    resp = await friend_client.post(
        "/api/v1/admin/system/pause", json={"reason": "n"},
    )
    assert resp.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_resume_clears_state(admin_client) -> None:
    from app.ops import pause_state
    await admin_client.post(
        "/api/v1/admin/system/pause", json={"reason": "r"},
    )
    resp = await admin_client.post("/api/v1/admin/system/resume", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["paused"] is False
    assert body["since"] is None
    pause_state._CACHE = None
    assert await pause_state.is_paused() is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_predict_returns_423_when_paused(admin_client, friend_client) -> None:
    await admin_client.post(
        "/api/v1/admin/system/pause", json={"reason": "r"},
    )
    # Friend tries a non-allowlisted POST.
    resp = await friend_client.post(
        "/api/v1/predict", json={"symbol": "BTC/USDT"},
    )
    assert resp.status_code == 423
    assert resp.json()["detail"] == "system_paused"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bot_status_returns_200_when_paused(admin_client, friend_client) -> None:
    await admin_client.post(
        "/api/v1/admin/system/pause", json={"reason": "r"},
    )
    resp = await friend_client.get("/api/v1/bot-status/overview")
    assert resp.status_code == 200
