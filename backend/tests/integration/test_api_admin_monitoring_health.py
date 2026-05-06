"""Integration tests for GET /api/v1/admin/monitoring/health (SP-7 Phase G3).

Returns a single JSON blob the operator dashboard polls to confirm the
nightly backup ran, the audit-chain verifier hasn't flagged anything,
and the active ML checkpoint is the one we expect. Also embeds links to
Grafana + Prometheus so the operator can drill in without hunting the
right URL.
"""
from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest_asyncio.fixture
async def with_backup_runs(
    auth_factory: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    """Create the SQLite-friendly backup_runs table inside auth_factory's engine.

    Mirror of migration 0012's ``backup_runs`` (BIGSERIAL -> INTEGER
    AUTOINCREMENT, TIMESTAMPTZ -> TEXT, JSONB -> TEXT). Lives here rather
    than in conftest because the SP-7 health endpoint is the only test
    that touches the table from the FastAPI side.
    """
    async with auth_factory() as s:
        await s.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS backup_runs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "started_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "completed_at TEXT, "
            "backup_type TEXT NOT NULL, "
            "target TEXT NOT NULL, "
            "success INTEGER, "
            "size_bytes INTEGER, "
            "duration_seconds REAL, "
            "error_message TEXT, "
            "metadata_json TEXT)"
        ))
        await s.commit()
    return auth_factory


@pytest.mark.asyncio
async def test_health_returns_links_and_nullables(
    admin_client: Any, with_backup_runs: Any,  # noqa: ARG001
) -> None:
    """Empty DB → all live signals null, Grafana/Prometheus URLs present."""
    r = await admin_client.get("/api/v1/admin/monitoring/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["last_backup_run"] is None
    assert body["last_audit_verifier_run"] is None
    assert body["active_checkpoint"] is None
    assert body["grafana_url"].startswith("http")
    assert body["prometheus_url"].startswith("http")


@pytest.mark.asyncio
async def test_health_returns_latest_backup_row(
    admin_client: Any,
    with_backup_runs: async_sessionmaker[AsyncSession],
) -> None:
    """Seed two backup_runs rows → endpoint returns the most-recent one."""
    async with with_backup_runs() as s:
        await s.execute(sa.text(
            "INSERT INTO backup_runs "
            "(started_at, completed_at, backup_type, target, success, "
            " size_bytes, duration_seconds) VALUES "
            "(:s1, :c1, 'nightly_basebackup', 'oracle_local', 1, "
            " 1024, 2.5), "
            "(:s2, :c2, 'recovery_rehearsal', 'b2', 1, 2048, 5.0)"
        ), {
            "s1": "2026-05-01T03:00:00+00:00",
            "c1": "2026-05-01T03:01:00+00:00",
            "s2": "2026-05-02T03:00:00+00:00",
            "c2": "2026-05-02T03:02:00+00:00",
        })
        await s.commit()

    r = await admin_client.get("/api/v1/admin/monitoring/health")
    assert r.status_code == 200
    body = r.json()
    last = body["last_backup_run"]
    assert last is not None
    # Most-recent row wins (started_at DESC).
    assert last["backup_type"] == "recovery_rehearsal"
    assert last["target"] == "b2"
    assert last["success"] is True
    assert last["size_bytes"] == 2048


@pytest.mark.asyncio
async def test_health_returns_active_checkpoint(
    admin_client: Any,
    with_backup_runs: async_sessionmaker[AsyncSession],  # noqa: ARG001
) -> None:
    """Active checkpoint surfaces with id, version, sha256."""
    body = {
        "model_name": "conv_lstm_predictor",
        "version": "1.2.3",
        "checkpoint_uri": "b2://x/v1.2.3.pt",
        "sha256": "f" * 64,
        "trained_at": "2026-05-15T12:00:00Z",
        "train_data_window": "x",
        "eval_results": {},
    }
    r1 = await admin_client.post("/api/v1/admin/ml-checkpoints", json=body)
    cid = r1.json()["id"]
    # ?force=true to bypass the SP-7 G2 champion-challenger gate.
    await admin_client.patch(
        f"/api/v1/admin/ml-checkpoints/{cid}?force=true",
        json={"is_active": True},
    )

    r = await admin_client.get("/api/v1/admin/monitoring/health")
    assert r.status_code == 200
    active = r.json()["active_checkpoint"]
    assert active is not None
    assert active["id"] == cid
    assert active["version"] == "1.2.3"
    assert active["sha256"] == "f" * 64


@pytest.mark.asyncio
async def test_health_audit_verifier_run_from_violations(
    admin_client: Any,
    with_backup_runs: async_sessionmaker[AsyncSession],
) -> None:
    """When auth_violations has a system row, expose its attempted_at.

    The endpoint surfaces the latest attempted_at where
    attempted_email='system' as a proxy for "the last time the verifier
    saw something worth recording". When the chain is healthy and no
    rows exist, it stays null (covered by test_health_returns_links).
    """
    async with with_backup_runs() as s:
        await s.execute(sa.text(
            "INSERT INTO auth_violations (attempted_email, reason, attempted_at) "
            "VALUES ('system', 'audit_chain_broken:predictions:42', :a)"
        ), {"a": "2026-05-05T03:00:00+00:00"})
        await s.commit()

    r = await admin_client.get("/api/v1/admin/monitoring/health")
    assert r.status_code == 200
    body = r.json()
    assert body["last_audit_verifier_run"] is not None
    assert body["last_audit_verifier_run"].startswith("2026-05-05")


@pytest.mark.asyncio
async def test_health_admin_only(
    friend_client: Any, with_backup_runs: Any,  # noqa: ARG001
) -> None:
    """Non-admin gets 403."""
    r = await friend_client.get("/api/v1/admin/monitoring/health")
    assert r.status_code == 403
