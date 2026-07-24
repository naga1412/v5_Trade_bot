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
C4_BLOCKED_RATE_THRESHOLD: float = 0.95
C4_LOOKBACK_HOURS: int = 2


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


async def detect_per_symbol_prediction_freshness(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[HealerFinding]:
    """C3: any universe symbol with no prediction for >2× its timeframe.

    Catches the per-symbol silent-drop class the worker-heartbeat check
    would miss (shadow_worker keeps beating for the fleet while one
    symbol silently drops out of the prediction stream).

    Fires ONE aggregate finding per detector tick if any symbols are
    stale; keeps the details compact.
    """
    now = datetime.now(timezone.utc)
    findings: list[HealerFinding] = []
    try:
        async with session_factory() as session:
            # Latest asset_universe snapshot symbol list (matches the
            # scanner's freshness contract). We look at 1h and 15m TFs —
            # the two the shadow worker currently runs.
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
    except Exception as e:  # noqa: BLE001
        log.warning("healer C3: query failed: %s", e)
        return []
    stale_syms: list[dict[str, object]] = []
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
        age_seconds = (now - max_ts).total_seconds()
        if age_seconds > tf_seconds * C3_TIMEFRAME_MULTIPLIER:
            stale_syms.append({
                "symbol": r.symbol,
                "timeframe": tf,
                "age_seconds": int(age_seconds),
                "threshold_seconds": tf_seconds * C3_TIMEFRAME_MULTIPLIER,
            })
    if stale_syms:
        # Keep the details JSON compact — first 20 stale symbols is
        # enough to diagnose. If the whole universe is stale (>20 hits)
        # C2 will already have fired critical.
        findings.append(HealerFinding(
            detector_name="per_symbol_prediction_freshness",
            severity="warning",
            summary=(
                f"{len(stale_syms)} symbol/tf pair(s) with predictions "
                f"older than {C3_TIMEFRAME_MULTIPLIER}× timeframe"
            ),
            details={
                "stale_count": len(stale_syms),
                "sample": stale_syms[:20],
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
    "C3_TIMEFRAME_MULTIPLIER",
    "C4_BLOCKED_RATE_THRESHOLD",
    "C4_LOOKBACK_HOURS",
    "detect_blocked_rate_anomaly",
    "detect_dispatch_error_rate",
    "detect_per_symbol_prediction_freshness",
    "detect_score_distribution_anomaly",
]
