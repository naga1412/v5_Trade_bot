"""Admin REST endpoints (SP-0.7 Phase G).

All routes under /api/v1/admin/* are gated by `Depends(require_admin)`. Spec
§5 — admin REST surface for user CRUD, invitations, impersonation start/stop,
unified audit trail.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.deps import require_admin

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
