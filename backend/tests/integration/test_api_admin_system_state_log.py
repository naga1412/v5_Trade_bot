import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_state_returns_200_when_unpaused(admin_client) -> None:
    resp = await admin_client.get("/api/v1/admin/system/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["paused"] is False
    assert body["since"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_state_returns_200_when_paused(admin_client) -> None:
    await admin_client.post(
        "/api/v1/admin/system/pause", json={"reason": "r"},
    )
    resp = await admin_client.get("/api/v1/admin/system/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["paused"] is True
    assert body["by_email"] == "admin@x.com"
    assert body["reason"] == "r"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_log_returns_recent_events_newest_first(admin_client) -> None:
    for i in range(3):
        await admin_client.post(
            "/api/v1/admin/system/pause", json={"reason": f"r{i}"},
        )
        await admin_client.post(
            "/api/v1/admin/system/resume", json={},
        )
    resp = await admin_client.get("/api/v1/admin/system/log?limit=4")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) == 4
    assert events[0]["kind"] == "system_resumed"
    assert events[1]["kind"] == "system_paused"
    assert events[1]["reason"] == "r2"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_friend_cannot_get_state(friend_client) -> None:
    resp = await friend_client.get("/api/v1/admin/system/state")
    assert resp.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_friend_cannot_get_log(friend_client) -> None:
    resp = await friend_client.get("/api/v1/admin/system/log")
    assert resp.status_code == 403
