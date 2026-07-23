"""Healer detector runner — periodic loop over all C-series detectors.

Runs every 5 minutes (adequate cadence — detectors look at hourly / 2-h
lookbacks, so more frequent runs don't produce more signal). Heartbeats
per tick so the worker_watchdog can catch a stalled healer.

The critical-severity finding from any detector fans out to
:func:`app.ops.alert_routing.alert_admin` at severity='critical' so the
operator's phone lights up. Non-critical findings are structured-log
only + persist to ``healer_findings`` for the healer-status probe.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.healer.detectors import (
    detect_blocked_rate_anomaly,
    detect_dispatch_error_rate,
    detect_per_symbol_prediction_freshness,
    detect_score_distribution_anomaly,
)
from app.healer.findings import HealerFinding, record_finding
from app.ops.alert_routing import alert_admin
from app.ops.heartbeat import record_heartbeat


log = logging.getLogger(__name__)


WORKER_NAME: str = "healer_detector_task"
HEALER_TICK_SECONDS: int = 5 * 60


# Detector menu — tuple of (name, coroutine). Adding a detector is
# one line here + one function in detectors.py.
Detector = Callable[
    [async_sessionmaker[AsyncSession]],
    Awaitable[list[HealerFinding]],
]

_DETECTORS: tuple[tuple[str, Detector], ...] = (
    ("C1_dispatch_error_rate", detect_dispatch_error_rate),
    ("C2_score_distribution_anomaly", detect_score_distribution_anomaly),
    ("C3_per_symbol_prediction_freshness",
     detect_per_symbol_prediction_freshness),
    ("C4_blocked_rate_anomaly", detect_blocked_rate_anomaly),
)


async def run_one_tick(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, int]:
    """One pass over every detector. Returns per-detector finding counts.

    Every finding is persisted via :func:`record_finding`. Critical
    findings additionally fan out to :func:`alert_admin` at
    severity='critical' — that's the Telegram-first path per the healer
    Phase 0 alerting policy.
    """
    counts: dict[str, int] = {}
    for label, detector in _DETECTORS:
        try:
            findings = await detector(session_factory)
        except Exception as e:  # noqa: BLE001
            # A broken detector must not kill the tick.
            log.exception("healer: detector %s raised: %s", label, e)
            findings = []
        counts[label] = len(findings)
        for f in findings:
            await record_finding(
                session_factory,
                detector_name=f.detector_name,
                severity=f.severity,
                summary=f.summary,
                details=f.details,
            )
            log.warning(
                "healer[%s] %s: %s", f.severity, f.detector_name, f.summary,
            )
            if f.severity == "critical":
                try:
                    await alert_admin(
                        f"[HEALER] {f.detector_name}: {f.summary}",
                        level="critical",
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("healer: alert_admin raised: %s", e)
    return counts


async def run_healer_detector_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    tick_seconds: float = HEALER_TICK_SECONDS,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Forever loop. Fires one detector tick, heartbeats, then sleeps."""
    log.info(
        "healer_detector_task: starting (tick=%.0fs, %d detectors)",
        tick_seconds, len(_DETECTORS),
    )
    while True:
        try:
            counts = await run_one_tick(session_factory)
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="ok",
                details={"tick_counts": counts},
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("healer_detector_task: tick failed: %s", e)
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="error", details={"error": str(e)[:200]},
            )
        try:
            await _sleep(tick_seconds)
        except asyncio.CancelledError:
            raise


def start_healer_detector_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    """Spawn the detector loop. Wired from main:lifespan."""
    return asyncio.create_task(run_healer_detector_loop(
        session_factory=session_factory,
    ))


__all__ = [
    "HEALER_TICK_SECONDS",
    "WORKER_NAME",
    "run_healer_detector_loop",
    "run_one_tick",
    "start_healer_detector_task",
]
