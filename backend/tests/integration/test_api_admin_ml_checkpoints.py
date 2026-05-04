"""Integration tests for /api/v1/admin/ml-checkpoints REST endpoints (SP-1 §6.4).

Mirrors the SP-0.7 admin-routes test pattern: uses the shared `admin_client`
fixture (current admin user_id=1) and the in-memory `auth_factory` engine
extended with the `ml_checkpoints` table.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_post_creates_inactive_checkpoint(admin_client) -> None:
    body = {
        "model_name": "conv_lstm_predictor",
        "version": "0.1.0",
        "checkpoint_uri": "b2://trading-radar-models/conv_lstm_v0.1.0.pt",
        "sha256": "a" * 64,
        "trained_at": "2026-05-15T12:00:00Z",
        "train_data_window": "2017-08 to 2023-12",
        "eval_results": {"bull_breakout": 0.013, "bear_crash": 0.025},
        "notes": "first run",
    }
    r = await admin_client.post("/api/v1/admin/ml-checkpoints", json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["is_active"] is False
    assert out["version"] == "0.1.0"
    assert out["model_name"] == "conv_lstm_predictor"
    assert out["eval_results"] == {"bull_breakout": 0.013, "bear_crash": 0.025}


@pytest.mark.asyncio
async def test_list_returns_all_checkpoints(admin_client) -> None:
    for v in ("0.1.0", "0.2.0"):
        await admin_client.post("/api/v1/admin/ml-checkpoints", json={
            "model_name": "conv_lstm_predictor", "version": v,
            "checkpoint_uri": f"b2://x/v{v}.pt", "sha256": "a" * 64,
            "trained_at": "2026-05-15T12:00:00Z",
            "train_data_window": "x", "eval_results": {},
        })
    r = await admin_client.get("/api/v1/admin/ml-checkpoints")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 2
    versions = {c["version"] for c in items}
    assert {"0.1.0", "0.2.0"} <= versions


@pytest.mark.asyncio
async def test_patch_activate_deactivates_previous(admin_client) -> None:
    # Create v0.1.0, activate it.
    r1 = await admin_client.post("/api/v1/admin/ml-checkpoints", json={
        "model_name": "conv_lstm_predictor", "version": "0.1.0",
        "checkpoint_uri": "b2://x/v1.pt", "sha256": "a" * 64,
        "trained_at": "2026-05-15T12:00:00Z",
        "train_data_window": "x", "eval_results": {},
    })
    id1 = r1.json()["id"]
    r_act1 = await admin_client.patch(
        f"/api/v1/admin/ml-checkpoints/{id1}", json={"is_active": True},
    )
    assert r_act1.status_code == 200, r_act1.text
    assert r_act1.json()["is_active"] is True

    # Create v0.2.0 + activate — v0.1.0 must be deactivated atomically.
    r2 = await admin_client.post("/api/v1/admin/ml-checkpoints", json={
        "model_name": "conv_lstm_predictor", "version": "0.2.0",
        "checkpoint_uri": "b2://x/v2.pt", "sha256": "b" * 64,
        "trained_at": "2026-05-16T12:00:00Z",
        "train_data_window": "x", "eval_results": {},
    })
    id2 = r2.json()["id"]
    await admin_client.patch(
        f"/api/v1/admin/ml-checkpoints/{id2}", json={"is_active": True},
    )

    r_list = await admin_client.get("/api/v1/admin/ml-checkpoints")
    items = {c["id"]: c for c in r_list.json()}
    assert items[id1]["is_active"] is False
    assert items[id1]["deactivated_at"] is not None
    assert items[id2]["is_active"] is True
    assert items[id2]["activated_at"] is not None


@pytest.mark.asyncio
async def test_patch_updates_notes_only(admin_client) -> None:
    r1 = await admin_client.post("/api/v1/admin/ml-checkpoints", json={
        "model_name": "conv_lstm_predictor", "version": "0.4.0",
        "checkpoint_uri": "b2://x/v4.pt", "sha256": "d" * 64,
        "trained_at": "2026-05-18T12:00:00Z",
        "train_data_window": "x", "eval_results": {},
    })
    cid = r1.json()["id"]
    r = await admin_client.patch(
        f"/api/v1/admin/ml-checkpoints/{cid}", json={"notes": "updated note"},
    )
    assert r.status_code == 200
    assert r.json()["notes"] == "updated note"


@pytest.mark.asyncio
async def test_delete_soft_marks_deactivated(admin_client) -> None:
    r1 = await admin_client.post("/api/v1/admin/ml-checkpoints", json={
        "model_name": "conv_lstm_predictor", "version": "0.3.0",
        "checkpoint_uri": "b2://x/v3.pt", "sha256": "c" * 64,
        "trained_at": "2026-05-17T12:00:00Z",
        "train_data_window": "x", "eval_results": {},
    })
    cid = r1.json()["id"]
    r_del = await admin_client.delete(f"/api/v1/admin/ml-checkpoints/{cid}")
    assert r_del.status_code == 204
    r_list = await admin_client.get("/api/v1/admin/ml-checkpoints")
    item = next(c for c in r_list.json() if c["id"] == cid)
    assert item["is_active"] is False
    assert item["deactivated_at"] is not None


@pytest.mark.asyncio
async def test_patch_404_when_not_found(admin_client) -> None:
    r = await admin_client.patch(
        "/api/v1/admin/ml-checkpoints/9999", json={"is_active": True},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_404_when_not_found(admin_client) -> None:
    r = await admin_client.delete("/api/v1/admin/ml-checkpoints/9999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_non_admin_rejected(friend_client) -> None:
    """Non-admin user (friend) must get 403 from any of these endpoints."""
    r = await friend_client.get("/api/v1/admin/ml-checkpoints")
    assert r.status_code == 403
    r = await friend_client.post("/api/v1/admin/ml-checkpoints", json={
        "model_name": "x", "version": "y", "checkpoint_uri": "z",
        "sha256": "a" * 64, "trained_at": "2026-05-15T12:00:00Z",
        "train_data_window": "x", "eval_results": {},
    })
    assert r.status_code == 403
