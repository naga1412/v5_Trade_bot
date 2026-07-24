"""Healer Phase 0 — detect-only monitoring layer.

Operator's long-term vision: "healer must solve everything by itself."
This module is the DETECTION foundation Phase 1's auto-recovery will
stand on. Every detector in this package is read-only against prod
state; nothing here writes to dispatch tables, live_trades, users, or
env vars. Detect-only is a hard boundary.

Public surface:
  * :func:`record_finding` — canonical writer to ``healer_findings``.
  * :func:`record_dispatch_error` — dispatcher's outermost try/except
    should call this so C1 (dispatch-outcome monitor) has data.
  * :func:`run_healer_detector_loop` — the periodic detector loop.
  * :func:`start_healer_detector_task` — factory used by main:lifespan.
"""
from __future__ import annotations

from app.healer.findings import (
    HealerFinding,
    record_dispatch_error,
    record_finding,
)
from app.healer.runner import (
    HEALER_TICK_SECONDS,
    run_healer_detector_loop,
    start_healer_detector_task,
)

__all__ = [
    "HEALER_TICK_SECONDS",
    "HealerFinding",
    "record_dispatch_error",
    "record_finding",
    "run_healer_detector_loop",
    "start_healer_detector_task",
]
