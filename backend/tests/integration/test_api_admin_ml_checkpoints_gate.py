"""Integration tests for the SP-7 Phase G2 champion-challenger activation gate.

PATCH /api/v1/admin/ml-checkpoints/{id} with ``{is_active: true}`` now
runs ``evaluate_challenger`` first. If the challenger fails the 5% bar
the request is rejected with 422 and the row stays inactive. The
``?force=true`` query param bypasses the gate (the SP-1.1 first-checkpoint
case where there is no champion to beat OR an emergency rollback).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.ml.champion_challenger import ChampionChallengerResult


async def _create_inactive_checkpoint(admin_client, version: str = "0.1.0") -> int:
    body = {
        "model_name": "conv_lstm_predictor",
        "version": version,
        "checkpoint_uri": f"b2://x/v{version}.pt",
        "sha256": "a" * 64,
        "trained_at": "2026-05-15T12:00:00Z",
        "train_data_window": "x",
        "eval_results": {},
    }
    r = await admin_client.post("/api/v1/admin/ml-checkpoints", json=body)
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


@pytest.mark.asyncio
async def test_activate_blocked_when_challenger_loses(
    admin_client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Challenger does not beat champion → 422, row stays inactive."""
    cid = await _create_inactive_checkpoint(admin_client, version="0.5.0")

    async def fake_eval(_session, **_: Any) -> ChampionChallengerResult:
        return ChampionChallengerResult(
            champion_mae=0.010, challenger_mae=0.0098, challenger_wins=False,
        )

    monkeypatch.setattr(
        "app.api.routes.admin_ml.evaluate_challenger", fake_eval,
    )
    r = await admin_client.patch(
        f"/api/v1/admin/ml-checkpoints/{cid}", json={"is_active": True},
    )
    assert r.status_code == 422, r.text
    assert "challenger did not beat champion" in r.json()["detail"]

    # Verify the row is still inactive.
    r_list = await admin_client.get("/api/v1/admin/ml-checkpoints")
    item = next(c for c in r_list.json() if c["id"] == cid)
    assert item["is_active"] is False


@pytest.mark.asyncio
async def test_activate_succeeds_when_challenger_wins(
    admin_client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Challenger wins the gate → activation proceeds normally."""
    cid = await _create_inactive_checkpoint(admin_client, version="0.6.0")

    async def fake_eval(_session, **_: Any) -> ChampionChallengerResult:
        return ChampionChallengerResult(
            champion_mae=0.010, challenger_mae=0.008, challenger_wins=True,
        )

    monkeypatch.setattr(
        "app.api.routes.admin_ml.evaluate_challenger", fake_eval,
    )
    r = await admin_client.patch(
        f"/api/v1/admin/ml-checkpoints/{cid}", json={"is_active": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is True


@pytest.mark.asyncio
async def test_force_true_bypasses_gate(
    admin_client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``?force=true`` skips evaluate_challenger entirely (operator override)."""
    cid = await _create_inactive_checkpoint(admin_client, version="0.7.0")

    called = {"n": 0}

    async def fake_eval(_session, **_: Any) -> ChampionChallengerResult:  # noqa: RUF029
        called["n"] += 1
        return ChampionChallengerResult(
            champion_mae=0.010, challenger_mae=0.999, challenger_wins=False,
        )

    monkeypatch.setattr(
        "app.api.routes.admin_ml.evaluate_challenger", fake_eval,
    )
    r = await admin_client.patch(
        f"/api/v1/admin/ml-checkpoints/{cid}?force=true",
        json={"is_active": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is True
    assert called["n"] == 0, "evaluate_challenger should NOT run with force=true"


@pytest.mark.asyncio
async def test_no_champion_yet_first_checkpoint_passes(
    admin_client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SP-1.1 case: first checkpoint, no champion → wins=True, activates."""
    cid = await _create_inactive_checkpoint(admin_client, version="0.8.0")

    async def fake_eval(_session, **_: Any) -> ChampionChallengerResult:
        return ChampionChallengerResult(
            champion_mae=None, challenger_mae=0.020, challenger_wins=True,
        )

    monkeypatch.setattr(
        "app.api.routes.admin_ml.evaluate_challenger", fake_eval,
    )
    r = await admin_client.patch(
        f"/api/v1/admin/ml-checkpoints/{cid}", json={"is_active": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is True


@pytest.mark.asyncio
async def test_deactivate_request_skips_gate(
    admin_client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATCH with ``is_active: false`` (deactivation) must NOT run the gate."""
    cid = await _create_inactive_checkpoint(admin_client, version="0.9.0")

    called = {"n": 0}

    async def fake_eval(_session, **_: Any) -> ChampionChallengerResult:  # noqa: RUF029
        called["n"] += 1
        return ChampionChallengerResult(
            champion_mae=None, challenger_mae=0.0, challenger_wins=False,
        )

    monkeypatch.setattr(
        "app.api.routes.admin_ml.evaluate_challenger", fake_eval,
    )
    r = await admin_client.patch(
        f"/api/v1/admin/ml-checkpoints/{cid}", json={"is_active": False},
    )
    assert r.status_code == 200, r.text
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_notes_only_patch_skips_gate(
    admin_client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATCH with only ``notes`` (no is_active) must NOT run the gate."""
    cid = await _create_inactive_checkpoint(admin_client, version="0.91.0")

    called = {"n": 0}

    async def fake_eval(_session, **_: Any) -> ChampionChallengerResult:  # noqa: RUF029
        called["n"] += 1
        return ChampionChallengerResult(
            champion_mae=None, challenger_mae=0.0, challenger_wins=False,
        )

    monkeypatch.setattr(
        "app.api.routes.admin_ml.evaluate_challenger", fake_eval,
    )
    r = await admin_client.patch(
        f"/api/v1/admin/ml-checkpoints/{cid}", json={"notes": "hello"},
    )
    assert r.status_code == 200, r.text
    assert called["n"] == 0
