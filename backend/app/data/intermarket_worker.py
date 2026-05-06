"""SP-3.5 intermarket snapshot + cleanup workers.

Stubbed in Phase A4; implemented in Phase C1–C4. The two ``start_*_task``
helpers mirror the SP-9 news worker pattern so ``app.main.lifespan`` can
gate them by ``settings.env not in {"test", "ci"} and settings.worker_enabled``.
"""
from __future__ import annotations

import asyncio
from typing import Any


async def run_intermarket_snapshot_loop(session_factory: Any) -> None:
    raise NotImplementedError("SP-3.5 Phase C1")


async def run_intermarket_cleanup_loop(session_factory: Any) -> None:
    raise NotImplementedError("SP-3.5 Phase C3")


def start_intermarket_snapshot_task(session_factory: Any) -> asyncio.Task[None]:
    raise NotImplementedError("SP-3.5 Phase C4")


def start_intermarket_cleanup_task(session_factory: Any) -> asyncio.Task[None]:
    raise NotImplementedError("SP-3.5 Phase C4")
