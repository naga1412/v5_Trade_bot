"""Admin monitoring health endpoint (SP-7 Phase G3, spec §6.16).

GET /api/v1/admin/monitoring/health returns a single JSON blob the
operator dashboard polls to confirm:

* the nightly Postgres backup ran (latest ``backup_runs`` row);
* the audit-chain verifier hasn't flagged anything since the last poll
  (latest ``auth_violations`` row with ``attempted_email='system'`` —
  ``None`` is the healthy state);
* the active ML checkpoint is the one we expect (id, version, sha256);
* deep-links to the Grafana dashboard + Prometheus query UI so the
  operator can drill in without hunting URLs.

Admin-gated via ``Depends(require_admin)``. The Grafana / Prometheus
URLs come from env (``GRAFANA_URL`` / ``PROMETHEUS_URL``) with sane
local-dev defaults.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_admin
from app.db.session import get_session

router = APIRouter(
    prefix="/api/v1/admin/monitoring",
    tags=["admin-monitoring"],
    dependencies=[Depends(require_admin)],
)


class BackupRunSummary(BaseModel):
    id: int
    started_at: datetime | None
    completed_at: datetime | None
    backup_type: str
    target: str
    success: bool | None
    size_bytes: int | None
    duration_seconds: float | None
    error_message: str | None


class ActiveCheckpointSummary(BaseModel):
    id: int
    model_name: str
    version: str
    sha256: str


class MonitoringHealthOut(BaseModel):
    last_backup_run: BackupRunSummary | None
    last_audit_verifier_run: datetime | None
    active_checkpoint: ActiveCheckpointSummary | None
    grafana_url: str
    prometheus_url: str


def _coerce_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


@router.get("/health", response_model=MonitoringHealthOut)
async def monitoring_health(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> MonitoringHealthOut:
    """Compose the live operator-facing health summary."""
    last_backup: BackupRunSummary | None = None
    try:
        row = (await session.execute(sa.text(
            "SELECT id, started_at, completed_at, backup_type, target, "
            "success, size_bytes, duration_seconds, error_message "
            "FROM backup_runs ORDER BY started_at DESC, id DESC LIMIT 1"
        ))).first()
    except Exception:  # noqa: BLE001
        # Migration 0012 not yet applied (e.g. fresh dev DB without
        # backup_runs). Treat as "no data" rather than 500.
        row = None
    if row is not None:
        last_backup = BackupRunSummary(
            id=int(row.id),
            started_at=_coerce_dt(row.started_at),
            completed_at=_coerce_dt(row.completed_at),
            backup_type=str(row.backup_type),
            target=str(row.target),
            success=_coerce_bool(row.success),
            size_bytes=(
                int(row.size_bytes) if row.size_bytes is not None else None
            ),
            duration_seconds=(
                float(row.duration_seconds)
                if row.duration_seconds is not None else None
            ),
            error_message=row.error_message,
        )

    last_verifier_at: datetime | None = None
    try:
        v_row = (await session.execute(sa.text(
            "SELECT attempted_at FROM auth_violations "
            "WHERE attempted_email = 'system' "
            "ORDER BY attempted_at DESC, id DESC LIMIT 1"
        ))).first()
        if v_row is not None:
            last_verifier_at = _coerce_dt(v_row.attempted_at)
    except Exception:  # noqa: BLE001
        # auth_violations table missing in the env (shouldn't happen post
        # SP-0.7 — guard for forward compat).
        last_verifier_at = None

    active_checkpoint: ActiveCheckpointSummary | None = None
    try:
        cp_row = (await session.execute(sa.text(
            "SELECT id, model_name, version, sha256 "
            "FROM ml_checkpoints WHERE is_active = 1 LIMIT 1"
        ))).first()
        if cp_row is not None:
            active_checkpoint = ActiveCheckpointSummary(
                id=int(cp_row.id),
                model_name=str(cp_row.model_name),
                version=str(cp_row.version),
                sha256=str(cp_row.sha256),
            )
    except Exception:  # noqa: BLE001
        active_checkpoint = None

    return MonitoringHealthOut(
        last_backup_run=last_backup,
        last_audit_verifier_run=last_verifier_at,
        active_checkpoint=active_checkpoint,
        grafana_url=os.environ.get("GRAFANA_URL", "http://localhost:3000"),
        prometheus_url=os.environ.get(
            "PROMETHEUS_URL", "http://localhost:9090",
        ),
    )
