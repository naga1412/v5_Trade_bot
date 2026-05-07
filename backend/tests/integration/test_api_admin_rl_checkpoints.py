"""Integration tests for /api/v1/admin/rl-checkpoints REST endpoints (SP-4 Phase D).

Mirrors test_api_admin_ml_checkpoints.py — uses the shared `admin_client`
fixture (current admin user_id=1) and the in-memory `auth_factory` engine
extended with the `rl_checkpoints` table (added in conftest.py alongside
the existing ml_checkpoints mirror).
"""
from __future__ import annotations

import pytest


def _valid_body(version: str = "v1-test") -> dict:
    return {
        "model_name": "ppo_policy_v1",
        "version": version,
        "checkpoint_uri": f"file:///app/data/rl-cache/ppo_policy_{version}.pt",
        "sha256": "a" * 64,
        "trained_at": "2026-05-15T03:30:00Z",
        "train_data_window": '{"n_transitions": 412, "asset_count": 30}',
        "eval_results": {
            "sharpe": 1.87, "sortino": 2.4, "max_drawdown": 0.112,
            "n_trades": 412, "vs_baseline_sharpe_pct": 15.4,
        },
        "notes": "first PPO checkpoint",
    }


@pytest.mark.asyncio
async def test_post_creates_inactive_checkpoint(admin_client) -> None:
    r = await admin_client.post("/api/v1/admin/rl-checkpoints", json=_valid_body())
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["is_active"] is False
    assert out["model_name"] == "ppo_policy_v1"
    assert out["version"] == "v1-test"
    assert out["eval_results"]["sharpe"] == 1.87


@pytest.mark.asyncio
async def test_post_short_sha_rejected(admin_client) -> None:
    body = _valid_body()
    body["sha256"] = "short"
    r = await admin_client.post("/api/v1/admin/rl-checkpoints", json=body)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_returns_all_checkpoints(admin_client) -> None:
    for v in ("v1-001", "v1-002"):
        r = await admin_client.post("/api/v1/admin/rl-checkpoints", json=_valid_body(v))
        assert r.status_code == 201, r.text
    r = await admin_client.get("/api/v1/admin/rl-checkpoints")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 2
    versions = {c["version"] for c in items}
    assert {"v1-001", "v1-002"} <= versions


@pytest.mark.asyncio
async def test_patch_force_activates_first_checkpoint(admin_client) -> None:
    """force=true bypasses the champion-challenger gate (first-checkpoint case)."""
    create = await admin_client.post(
        "/api/v1/admin/rl-checkpoints", json=_valid_body(),
    )
    cid = create.json()["id"]
    r = await admin_client.patch(
        f"/api/v1/admin/rl-checkpoints/{cid}?force=true",
        json={"is_active": True},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["is_active"] is True
    assert out["activated_at"] is not None


@pytest.mark.asyncio
async def test_patch_activation_atomically_deactivates_previous(admin_client) -> None:
    """When v1 is active and we activate v2, v1 flips inactive in the same call."""
    body1 = _valid_body("v1-001")
    body2 = _valid_body("v1-002")
    r1 = await admin_client.post("/api/v1/admin/rl-checkpoints", json=body1)
    r2 = await admin_client.post("/api/v1/admin/rl-checkpoints", json=body2)
    id1, id2 = r1.json()["id"], r2.json()["id"]

    # Force-activate v1 (first checkpoint, no champion).
    await admin_client.patch(
        f"/api/v1/admin/rl-checkpoints/{id1}?force=true",
        json={"is_active": True},
    )
    # Force-activate v2 — atomically deactivates v1.
    await admin_client.patch(
        f"/api/v1/admin/rl-checkpoints/{id2}?force=true",
        json={"is_active": True},
    )
    listing = (await admin_client.get("/api/v1/admin/rl-checkpoints")).json()
    by_id = {c["id"]: c for c in listing}
    assert by_id[id1]["is_active"] is False
    assert by_id[id2]["is_active"] is True


@pytest.mark.asyncio
async def test_patch_unknown_id_returns_404(admin_client) -> None:
    r = await admin_client.patch(
        "/api/v1/admin/rl-checkpoints/99999?force=true",
        json={"is_active": True},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_updates_notes(admin_client) -> None:
    create = await admin_client.post(
        "/api/v1/admin/rl-checkpoints", json=_valid_body(),
    )
    cid = create.json()["id"]
    r = await admin_client.patch(
        f"/api/v1/admin/rl-checkpoints/{cid}",
        json={"notes": "promoted manually after Telegram review"},
    )
    assert r.status_code == 200
    assert r.json()["notes"] == "promoted manually after Telegram review"


@pytest.mark.asyncio
async def test_delete_soft_deactivates(admin_client) -> None:
    create = await admin_client.post(
        "/api/v1/admin/rl-checkpoints", json=_valid_body(),
    )
    cid = create.json()["id"]
    # Activate then delete.
    await admin_client.patch(
        f"/api/v1/admin/rl-checkpoints/{cid}?force=true",
        json={"is_active": True},
    )
    r = await admin_client.delete(f"/api/v1/admin/rl-checkpoints/{cid}")
    assert r.status_code == 204
    # Row still appears in list, just inactive.
    listing = (await admin_client.get("/api/v1/admin/rl-checkpoints")).json()
    by_id = {c["id"]: c for c in listing}
    assert cid in by_id
    assert by_id[cid]["is_active"] is False
    assert by_id[cid]["deactivated_at"] is not None


@pytest.mark.asyncio
async def test_delete_unknown_id_returns_404(admin_client) -> None:
    r = await admin_client.delete("/api/v1/admin/rl-checkpoints/99999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_without_force_runs_champion_challenger_gate(
    admin_client, monkeypatch,
) -> None:
    """Without ?force=true, evaluate_challenger() runs.

    For the FIRST checkpoint (no champion), the bootstrap path returns
    challenger_wins=True automatically — even without force. This test
    verifies the no-force path on a first checkpoint succeeds.
    """
    # Stub _evaluate_sharpe so we don't hit the backtester.
    from app.ml import champion_challenger as cc
    async def fake_sharpe(session, checkpoint_id):  # noqa: ARG001
        return 1.5
    monkeypatch.setattr(cc, "_evaluate_sharpe", fake_sharpe)

    create = await admin_client.post(
        "/api/v1/admin/rl-checkpoints", json=_valid_body(),
    )
    cid = create.json()["id"]
    r = await admin_client.patch(
        f"/api/v1/admin/rl-checkpoints/{cid}",   # no force
        json={"is_active": True},
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is True


@pytest.mark.asyncio
async def test_patch_without_force_rejects_when_challenger_loses(
    admin_client, monkeypatch,
) -> None:
    """Second checkpoint with worse Sharpe than the active champion → 422."""
    from app.ml import champion_challenger as cc
    # Champion = v1-001 with Sharpe=2.0 ; challenger = v1-002 with Sharpe=1.0.
    # 1.0 < 2.0 * 1.05 → challenger LOSES.
    sharpe_by_id: dict[int, float] = {}

    async def fake_sharpe(session, checkpoint_id):  # noqa: ARG001
        return sharpe_by_id.get(checkpoint_id, 0.0)
    monkeypatch.setattr(cc, "_evaluate_sharpe", fake_sharpe)

    r1 = await admin_client.post("/api/v1/admin/rl-checkpoints", json=_valid_body("v1-001"))
    id1 = r1.json()["id"]
    sharpe_by_id[id1] = 2.0
    await admin_client.patch(
        f"/api/v1/admin/rl-checkpoints/{id1}?force=true",
        json={"is_active": True},
    )

    r2 = await admin_client.post("/api/v1/admin/rl-checkpoints", json=_valid_body("v1-002"))
    id2 = r2.json()["id"]
    sharpe_by_id[id2] = 1.0  # worse than champion

    r = await admin_client.patch(
        f"/api/v1/admin/rl-checkpoints/{id2}",  # NO force — gate runs
        json={"is_active": True},
    )
    assert r.status_code == 422
    assert "challenger did not beat champion" in r.json()["detail"]
    assert "champion_sharpe=2.0" in r.json()["detail"]


@pytest.mark.asyncio
async def test_friend_user_cannot_access(friend_client) -> None:
    """SP-0.7: non-admin gets 403 on every admin-rl route."""
    r = await friend_client.get("/api/v1/admin/rl-checkpoints")
    assert r.status_code == 403
    r = await friend_client.post(
        "/api/v1/admin/rl-checkpoints", json=_valid_body(),
    )
    assert r.status_code == 403
