"""Healer Phase 0 detectors — C1, C2, C3, C4.

Each detector is a pure async function that takes a session and returns
a list of :class:`HealerFinding` (empty when nothing is wrong). The
runner (:mod:`app.healer.runner`) drives them on a fixed cadence and
writes findings via :func:`app.healer.findings.record_finding`.

Detect-only: no code path in this module mutates dispatch tables,
live_trades, users, or env vars. Every detector is safe to run at any
cadence against prod state.

Detector menu:
  C1 — dispatch-outcome monitor: aggregates dispatcher exceptions by
       type over the last hour; alarms on >5/hr OR on any novel type
       (never seen in healer_known_error_types).
  C2 — score-distribution anomaly: all-NEUTRAL / all-zero scores across
       the universe for >1h → predictor likely broken → CRITICAL.
  C3 — per-symbol prediction freshness: any universe symbol with no
       prediction for >2× timeframe → per-symbol silent drop → alarm
       (distinct from worker-heartbeat detection).
  C4 — blocked-rate anomaly: >95% of dispatches blocked for >2h → info
       (gates over-blocking; NOT critical in the current no-funds state).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.healer.findings import (
    DISPATCH_EXCEPTION_DETECTOR,
    HealerFinding,
    upsert_known_error_type,
)


log = logging.getLogger(__name__)


# Tunable thresholds — module-level constants so tests can monkey-patch.
C1_HOURLY_RATE_LIMIT: int = 5
C2_UNIVERSE_QUIET_LOOKBACK_MINUTES: int = 60
C3_TIMEFRAME_MULTIPLIER: int = 2
# C3 boot grace (Phase 0 completion 2026-07-24): suppress C3 until we've
# been up long enough for at least one candle close on the longest TF
# we run (currently 1h). Before that, predictions.ts holds pre-boot values
# and every symbol looks stale → 85 warnings on first tick = cry-wolf.
C3_BOOT_GRACE_SECONDS: int = 3600 + 300  # 1h + 5min slack
# C3 severity split (Phase 0 completion): if EVERY symbol/tf pair is
# stale AND the shadow_worker heartbeat is fresh, the producer is alive
# but producing nothing — a real silent-drop class not covered by the
# worker heartbeat check. Elevated to critical. Subset stale stays
# warning (the per-symbol silent-drop is C3's original value).
C3_UNIVERSE_STALL_RATIO: float = 1.0  # 100% stale = universe stall
C3_SHADOW_WORKER_FRESH_SECONDS: int = 30 * 60  # matches worker_registry
# C3 universe-stall streak (Phase 0 completion 2026-07-24 follow-up):
# transient post-restart timing races (boot grace expires seconds after
# the last hourly close was 2h old) can single-tick a universe_stall
# critical. Require N consecutive tick observations before escalating to
# CRITICAL — same pattern B1 uses for heartbeat_error. On tick 1 the
# finding is emitted as WARNING (per_symbol_drop shape); on tick N the
# escalated CRITICAL fires. Detector tick cadence is
# ``HEALER_TICK_SECONDS = 5 * 60`` (5 min) — so the escalation delay is
# 5 min per extra tick required.
C3_UNIVERSE_STALL_STREAK_FOR_CRITICAL: int = 2

# In-process streak counter, keyed on "universe_stall" (single detector
# scope). In-memory only; resets on container restart, which is the
# right semantics — a fresh restart resets the boot grace and the
# streak together. Tests can monkey-patch this dict to seed a streak.
_C3_UNIVERSE_STALL_STREAK: dict[str, int] = {}
C4_BLOCKED_RATE_THRESHOLD: float = 0.95
C4_LOOKBACK_HOURS: int = 2
# C4 min-sample guard (Phase 0 completion): 2/2 = 100% is noise. Require
# at least this many signals in the window before evaluating the rate.
C4_MIN_SAMPLE_SIZE: int = 10

# Module-load time as a proxy for container start. The healer module is
# imported when main.py:lifespan spawns start_healer_detector_task, so
# this captures within a few seconds of container boot.
_MODULE_LOAD_TIME: float = time.time()


def _container_uptime_seconds() -> float:
    """Seconds since the healer module was imported ≈ container uptime."""
    return time.time() - _MODULE_LOAD_TIME


# ---- C1: dispatch-outcome monitor ---------------------------------------


async def detect_dispatch_error_rate(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[HealerFinding]:
    """C1: aggregate dispatcher exceptions in the last hour by type.

    Alarms:
      * ``critical`` on any exception type never before recorded in
        ``healer_known_error_types``. Novel classes are exactly the
        long-tail bug surface — see the .isoformat() TIMESTAMPTZ class
        that shipped three times undetected.
      * ``warning`` when a known type crosses C1_HOURLY_RATE_LIMIT hits
        in the last hour.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    findings: list[HealerFinding] = []
    try:
        async with session_factory() as session:
            rows = (await session.execute(
                sa.text(
                    "SELECT COALESCE(details->>'exception_type', 'unknown') "
                    "         AS exception_type, "
                    "       COUNT(*) AS n "
                    "  FROM healer_findings "
                    " WHERE detector_name = :d "
                    "   AND detected_at >= :since "
                    " GROUP BY exception_type"
                ),
                {"d": DISPATCH_EXCEPTION_DETECTOR, "since": since},
            )).all()
            for r in rows:
                exc_type = str(r.exception_type)
                count = int(r.n)
                is_novel = await upsert_known_error_type(session, exc_type)
                if is_novel:
                    findings.append(HealerFinding(
                        detector_name="dispatch_error_rate",
                        severity="critical",
                        summary=(
                            f"NEVER-BEFORE-SEEN dispatch exception: "
                            f"{exc_type} (n={count} in last 1h)"
                        ),
                        details={
                            "exception_type": exc_type,
                            "hits_last_hour": count,
                            "reason": "novel_class",
                        },
                    ))
                elif count > C1_HOURLY_RATE_LIMIT:
                    findings.append(HealerFinding(
                        detector_name="dispatch_error_rate",
                        severity="warning",
                        summary=(
                            f"dispatch exception rate: {exc_type} "
                            f"hit {count} times in last 1h "
                            f"(threshold {C1_HOURLY_RATE_LIMIT})"
                        ),
                        details={
                            "exception_type": exc_type,
                            "hits_last_hour": count,
                            "reason": "rate_exceeded",
                        },
                    ))
            await session.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("healer C1: detection failed: %s", e)
    return findings


# ---- C2: score-distribution anomaly -------------------------------------


async def detect_score_distribution_anomaly(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[HealerFinding]:
    """C2: universe-wide all-NEUTRAL / all-zero scores for >1h → CRITICAL.

    The predictor is likely broken. Not "gate blocking all signals" (C4);
    the SCORE itself has degenerated across every symbol.
    """
    since = datetime.now(timezone.utc) - timedelta(
        minutes=C2_UNIVERSE_QUIET_LOOKBACK_MINUTES,
    )
    findings: list[HealerFinding] = []
    try:
        async with session_factory() as session:
            row = (await session.execute(
                sa.text(
                    "SELECT COUNT(*) AS total, "
                    "       SUM(CASE WHEN direction = 'NEUTRAL' THEN 1 "
                    "                ELSE 0 END) AS neutral, "
                    "       SUM(CASE WHEN ABS(final_score) < 0.01 THEN 1 "
                    "                ELSE 0 END) AS near_zero, "
                    "       MAX(ABS(final_score)) AS max_abs "
                    "  FROM predictions "
                    " WHERE ts >= :since"
                ),
                {"since": since},
            )).first()
    except Exception as e:  # noqa: BLE001
        log.warning("healer C2: query failed: %s", e)
        return []
    if row is None or row.total is None or int(row.total) == 0:
        return []
    total = int(row.total)
    neutral = int(row.neutral or 0)
    near_zero = int(row.near_zero or 0)
    max_abs = float(row.max_abs or 0.0)

    # Only alarm on a healthy sample size — <30 predictions in an hour
    # means the predictor is silent for another reason (WS down, universe
    # empty) and other detectors handle that.
    if total < 30:
        return findings

    if neutral == total or (near_zero == total and max_abs < 0.01):
        findings.append(HealerFinding(
            detector_name="score_distribution_anomaly",
            severity="critical",
            summary=(
                f"universe-wide predictor degradation: total={total} "
                f"NEUTRAL={neutral} near_zero={near_zero} "
                f"max|score|={max_abs:.4f} over last "
                f"{C2_UNIVERSE_QUIET_LOOKBACK_MINUTES}min"
            ),
            details={
                "total": total,
                "neutral": neutral,
                "near_zero": near_zero,
                "max_abs_score": max_abs,
                "lookback_minutes": C2_UNIVERSE_QUIET_LOOKBACK_MINUTES,
            },
        ))
    return findings


# ---- C3: per-symbol prediction freshness --------------------------------


# Fixed timeframe → seconds map. Matches the same table on the scanner
# route so freshness rules stay consistent across the app.
_TF_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


async def _shadow_worker_is_fresh(
    session: AsyncSession, *, now: datetime,
) -> bool:
    """C3 helper: True iff shadow_worker's last heartbeat is fresh.

    Used to distinguish "worker alive but producing nothing" (a real
    silent stall not caught by heartbeat checks) from "worker itself
    is down" (which the watchdog would already alarm on)."""
    try:
        row = (await session.execute(
            sa.text(
                "SELECT beat_at FROM worker_heartbeats "
                "WHERE worker_name = 'shadow_worker'"
            ),
        )).first()
    except Exception as e:  # noqa: BLE001
        log.warning("healer C3: shadow_worker heartbeat query failed: %s", e)
        return False
    if row is None or row[0] is None:
        return False
    beat_at = row[0]
    if isinstance(beat_at, str):
        beat_at = datetime.fromisoformat(beat_at.replace("Z", "+00:00"))
    if beat_at.tzinfo is None:
        beat_at = beat_at.replace(tzinfo=timezone.utc)
    return (now - beat_at).total_seconds() <= C3_SHADOW_WORKER_FRESH_SECONDS


async def detect_per_symbol_prediction_freshness(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[HealerFinding]:
    """C3: prediction-freshness detector with severity split.

    Two failure classes:

      * **Universe stall** (critical): EVERY (symbol, tf) pair stale
        AND shadow_worker heartbeat is fresh — producer alive but
        producing nothing. A real silent-drop class not covered by
        the watchdog heartbeat check.

      * **Per-symbol drop** (warning): SUBSET stale while others fresh.
        C3's original value — one symbol silently drops out of the WS
        fleet while its siblings keep flowing.

    Boot grace: suppressed for the first C3_BOOT_GRACE_SECONDS after
    the healer module loaded (~= container start). Before the grace
    elapses every prediction is pre-boot vintage; every symbol looks
    stale → cry-wolf on every deploy (85 warnings observed on the
    prod-promotion #27 first tick, 2026-07-24).
    """
    findings: list[HealerFinding] = []
    uptime = _container_uptime_seconds()
    if uptime < C3_BOOT_GRACE_SECONDS:
        # Suppressed. Deploys don't produce noise; the next tick after
        # the grace window will fire if anything is actually stale.
        return findings

    now = datetime.now(timezone.utc)
    try:
        async with session_factory() as session:
            rows = (await session.execute(
                sa.text(
                    "WITH latest AS ("
                    "  SELECT symbol, timeframe, MAX(ts) AS max_ts "
                    "    FROM predictions "
                    "   WHERE timeframe IN ('1h', '15m') "
                    "   GROUP BY symbol, timeframe"
                    ") "
                    "SELECT symbol, timeframe, max_ts FROM latest"
                ),
            )).all()
            shadow_fresh = await _shadow_worker_is_fresh(session, now=now)
    except Exception as e:  # noqa: BLE001
        log.warning("healer C3: query failed: %s", e)
        return []

    stale_syms: list[dict[str, object]] = []
    total_pairs = 0
    for r in rows:
        tf = str(r.timeframe)
        tf_seconds = _TF_SECONDS.get(tf)
        if tf_seconds is None:
            continue
        max_ts = r.max_ts
        if max_ts is None:
            continue
        if isinstance(max_ts, str):
            max_ts = datetime.fromisoformat(max_ts.replace("Z", "+00:00"))
        if max_ts.tzinfo is None:
            max_ts = max_ts.replace(tzinfo=timezone.utc)
        total_pairs += 1
        age_seconds = (now - max_ts).total_seconds()
        if age_seconds > tf_seconds * C3_TIMEFRAME_MULTIPLIER:
            stale_syms.append({
                "symbol": r.symbol,
                "timeframe": tf,
                "age_seconds": int(age_seconds),
                "threshold_seconds": tf_seconds * C3_TIMEFRAME_MULTIPLIER,
            })

    if not stale_syms:
        # Everything fresh — reset any accumulated universe_stall streak.
        _C3_UNIVERSE_STALL_STREAK.pop("universe_stall", None)
        return findings

    stale_ratio = len(stale_syms) / total_pairs if total_pairs else 0.0
    universe_stall = (
        stale_ratio >= C3_UNIVERSE_STALL_RATIO
        and shadow_fresh
        and total_pairs > 0
    )

    if universe_stall:
        # Streak gate: escalate to CRITICAL only after N consecutive
        # tick observations. On tick 1 emit as WARNING to avoid paging
        # the operator on a transient post-restart timing race (boot
        # grace expiring seconds after the last hourly close aged past
        # 2× threshold). Once the streak crosses the threshold, emit
        # CRITICAL; the streak resets on any tick where universe_stall
        # doesn't hold (see _c3_reset_universe_stall_streak below).
        streak = _C3_UNIVERSE_STALL_STREAK.get("universe_stall", 0) + 1
        _C3_UNIVERSE_STALL_STREAK["universe_stall"] = streak
        if streak >= C3_UNIVERSE_STALL_STREAK_FOR_CRITICAL:
            findings.append(HealerFinding(
                detector_name="per_symbol_prediction_freshness",
                severity="critical",
                summary=(
                    f"UNIVERSE STALL (streak={streak}) — "
                    f"{len(stale_syms)}/{total_pairs} symbol/tf pair(s) "
                    f"stale while shadow_worker heartbeat is fresh across "
                    f"{streak} consecutive ticks. Producer alive but "
                    f"producing nothing."
                ),
                details={
                    "stale_count": len(stale_syms),
                    "total_pairs": total_pairs,
                    "stale_ratio": stale_ratio,
                    "shadow_worker_fresh": True,
                    "sample": stale_syms[:20],
                    "class": "universe_stall",
                    "streak": streak,
                    "streak_threshold": C3_UNIVERSE_STALL_STREAK_FOR_CRITICAL,
                },
            ))
        else:
            # First-observation warning: same shape as per_symbol_drop
            # so downstream readers can filter, but details.class marks
            # it as pending-escalation universe_stall.
            findings.append(HealerFinding(
                detector_name="per_symbol_prediction_freshness",
                severity="warning",
                summary=(
                    f"universe stall observed (streak={streak}/"
                    f"{C3_UNIVERSE_STALL_STREAK_FOR_CRITICAL}) — "
                    f"{len(stale_syms)}/{total_pairs} stale, shadow_worker "
                    f"fresh. Escalates to CRITICAL on next tick if the "
                    f"condition holds."
                ),
                details={
                    "stale_count": len(stale_syms),
                    "total_pairs": total_pairs,
                    "stale_ratio": stale_ratio,
                    "shadow_worker_fresh": True,
                    "sample": stale_syms[:20],
                    "class": "universe_stall_pending",
                    "streak": streak,
                    "streak_threshold": C3_UNIVERSE_STALL_STREAK_FOR_CRITICAL,
                },
            ))
    else:
        # Not a universe stall on this tick — reset any accumulated
        # streak so a transient post-restart tick doesn't count toward
        # a later, unrelated stall.
        _C3_UNIVERSE_STALL_STREAK.pop("universe_stall", None)
        findings.append(HealerFinding(
            detector_name="per_symbol_prediction_freshness",
            severity="warning",
            summary=(
                f"{len(stale_syms)}/{total_pairs} symbol/tf pair(s) with "
                f"predictions older than {C3_TIMEFRAME_MULTIPLIER}× "
                f"timeframe"
            ),
            details={
                "stale_count": len(stale_syms),
                "total_pairs": total_pairs,
                "stale_ratio": stale_ratio,
                "shadow_worker_fresh": shadow_fresh,
                "sample": stale_syms[:20],
                "class": "per_symbol_drop",
            },
        ))
    return findings


# ---- C4: blocked-rate anomaly -------------------------------------------


async def detect_blocked_rate_anomaly(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[HealerFinding]:
    """C4: >95% of dispatches blocked for >2h → gates likely over-blocking.

    NOT critical in the current no-funds state — the operator explicitly
    down-rates this to `info`. The finding still surfaces via the
    healer-status probe so strategy agent can factor it into gate
    tuning conversations.

    We measure "blocked" as telegram_signals rows with response in the
    known blocked-outcome set. Zero rows in the lookback window → no
    finding (silent bot is C2/C3's problem, not C4's).
    """
    since = datetime.now(timezone.utc) - timedelta(hours=C4_LOOKBACK_HOURS)
    findings: list[HealerFinding] = []
    try:
        async with session_factory() as session:
            row = (await session.execute(
                sa.text(
                    "SELECT COUNT(*) AS total, "
                    "       SUM(CASE WHEN response IN "
                    "                ('auto_skipped', 'stale_price', "
                    "                 'approve_lost_race') "
                    "                THEN 1 ELSE 0 END) AS blocked "
                    "  FROM telegram_signals "
                    " WHERE sent_at >= :since"
                ),
                {"since": since},
            )).first()
    except Exception as e:  # noqa: BLE001
        log.warning("healer C4: query failed: %s", e)
        return []
    if row is None or row.total is None:
        return []
    total = int(row.total)
    blocked = int(row.blocked or 0)
    # Min-sample guard (Phase 0 completion 2026-07-24): 2/2 = 100% is
    # noise, not detection. Wait until the sample size is meaningful
    # before evaluating the threshold. The observed prod-promotion #27
    # first tick had 2/2 telegram signals blocked = 100% — clearly a
    # cold-start artifact, not a real gate over-blocking.
    if total < C4_MIN_SAMPLE_SIZE:
        return []
    if total == 0:
        return findings
    rate = blocked / total
    if rate >= C4_BLOCKED_RATE_THRESHOLD:
        findings.append(HealerFinding(
            detector_name="blocked_rate_anomaly",
            severity="info",
            summary=(
                f"{blocked}/{total} ({rate:.0%}) telegram signals blocked "
                f"over last {C4_LOOKBACK_HOURS}h "
                f"(threshold {C4_BLOCKED_RATE_THRESHOLD:.0%})"
            ),
            details={
                "total": total,
                "blocked": blocked,
                "blocked_rate": rate,
                "lookback_hours": C4_LOOKBACK_HOURS,
                "threshold": C4_BLOCKED_RATE_THRESHOLD,
            },
        ))
    return findings


__all__ = [
    "C1_HOURLY_RATE_LIMIT",
    "C2_UNIVERSE_QUIET_LOOKBACK_MINUTES",
    "C3_BOOT_GRACE_SECONDS",
    "C3_SHADOW_WORKER_FRESH_SECONDS",
    "C3_TIMEFRAME_MULTIPLIER",
    "C3_UNIVERSE_STALL_RATIO",
    "C3_UNIVERSE_STALL_STREAK_FOR_CRITICAL",
    "C4_BLOCKED_RATE_THRESHOLD",
    "C4_LOOKBACK_HOURS",
    "C4_MIN_SAMPLE_SIZE",
    "detect_blocked_rate_anomaly",
    "detect_dispatch_error_rate",
    "detect_per_symbol_prediction_freshness",
    "detect_score_distribution_anomaly",
]
