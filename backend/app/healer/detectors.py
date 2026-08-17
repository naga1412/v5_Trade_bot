"""Healer Phase 0 detectors — C1, C2, C3, C4, C6.

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
  C6 — RL checkpoint load-health: rl_checkpoints.is_active=TRUE but the
       in-process loaded pair (app.rl.checkpoints.get_active_policy_and_
       checkpoint) is None or doesn't match → the brain has silently
       fallen back to equal-weight (brain_adjust=1.0) with no durable
       signal that it happened → CRITICAL. (C5 lives in
       :mod:`app.healer.system_truth_detector`, not this module.)
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
from app.rl.checkpoints import get_active_policy_and_checkpoint
from app.shadow.universe import load_current_universe
from app.ws.keepalive import (
    DEFAULT_EXCLUDE,
    KEEPALIVE_TIMEFRAME,
    KEEPALIVE_TOP_N,
    to_pair,
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
# C3 per-symbol-drop repeat suppression (alert-fatigue fix, 2026-08-08):
# the per_symbol_drop branch below used to emit a new finding EVERY tick
# (5 min) even when the stale set was byte-identical to the previous
# tick. Confirmed live via the sql-select probe (2026-08-07): ACE/BABY/
# BICO/ERA USDT@1h produced an unchanged warning every 5 minutes for
# weeks — BABYUSDT had been stale 50+ days, unbroken, all still
# correctly ranked in the live top-20 the whole time (not a universe-
# rotation artifact). That is the exact alert-fatigue failure mode C3
# exists to prevent becoming, not cause. Now only emits when the stale
# SET changes, or after this many seconds have elapsed since the last
# emission for an unchanged set — a standing drop still gets a periodic
# reminder instead of vanishing from operator attention permanently.
C3_PER_SYMBOL_DROP_REMINDER_SECONDS: int = 6 * 3600  # 6h

# In-process dedup state for per_symbol_drop, keyed on the fixed string
# "per_symbol_drop" (single detector scope, mirrors
# _C3_UNIVERSE_STALL_STREAK's pattern). Value is (stale_set, emitted_at).
# In-memory only; resets on container restart — a fresh restart
# re-emits on its first stale tick, which is correct (operator should
# see current state after a deploy, not inherit a pre-restart timer).
_C3_LAST_PER_SYMBOL_DROP: dict[str, tuple[frozenset[tuple[str, str]], datetime]] = {}
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


def _binance_no_slash_to_slash(sym: str) -> str | None:
    """Normalize an ``asset_universe.symbol`` (Binance no-slash form,
    e.g. ``BTCUSDT``) to the ``predictions.symbol`` slash form
    (``BTC/USDT``). Returns None on any symbol that doesn't end in USDT
    — shadow lane is USDT-only; anything else is not in scope.
    """
    if not sym or not sym.endswith("USDT"):
        return None
    base = sym[: -len("USDT")]
    if not base:
        return None
    return f"{base}/USDT"


async def _load_expected_prediction_pairs(
    session: AsyncSession,
) -> set[tuple[str, str]]:
    """C3 helper: return the set of (symbol, timeframe) pairs the live
    prediction stack is EXPECTED to produce.

    Mirrors INTENT, not runtime state — reads the same universe source
    and shares the same top-N constant the fleet uses, so C3 asks "did
    every symbol that SHOULD have a prediction get one?" A runtime-state
    mirror (e.g. reading ws_keepalive_task's children list) would go
    silent about the very failure C3 exists to catch: a child that
    failed to spawn drops out of the runtime set and its absence looks
    normal.

    Composition:
      * Fleet (ws_keepalive_task): top-``KEEPALIVE_TOP_N`` universe
        symbols on ``KEEPALIVE_TIMEFRAME`` = 20 × 1h = 20 pairs, minus
        ``DEFAULT_EXCLUDE`` (BTC/USDT/1h — handled by live_worker).
      * Singleton (live_worker): the pairs the fleet excludes. Adding
        them back ensures BTC is expected even on the (rare) day it
        drops out of top-20 by rank — the singleton runs on it
        regardless.

    Result: `(fleet_pairs) ∪ DEFAULT_EXCLUDE`. When BTC is in top-20
    (usually rank 1), that's exactly ``KEEPALIVE_TOP_N`` pairs; when
    BTC drops out, it's ``KEEPALIVE_TOP_N + |DEFAULT_EXCLUDE|``.

    Ranks 21-30 are OUT OF SCOPE by design — ratified 2026-07-28 per
    ``docs/superpowers/decisions/2026-07-28-fleet-cap-top-20-ratified.md``.
    Their absence from this set is intentional; C3 will not flag them
    even though the shadow_worker subscribes to all 30 for shadow-only
    trades. See the decision doc for the +EV analysis + reversal
    criteria.

    Returns an empty set only on universe-query failure (logged); the
    caller treats empty as an anomaly, not silent skip.
    """
    try:
        entries = await load_current_universe(session)
    except Exception as e:  # noqa: BLE001
        log.warning("healer C3: load_current_universe failed: %s", e)
        return set()

    # Empty universe → empty result; caller's empty-universe guard
    # fires the anomaly finding. Do NOT return DEFAULT_EXCLUDE alone
    # here — the singleton alone isn't a "healthy" state, and the
    # operator needs the alarm.
    if not entries:
        return set()

    # Fleet slice — imports KEEPALIVE_TOP_N so any future retune of the
    # cap flows here automatically (one source of truth).
    fleet_pairs: set[tuple[str, str]] = {
        (to_pair(e.symbol), KEEPALIVE_TIMEFRAME)
        for e in entries[:KEEPALIVE_TOP_N]
    }
    # Union with DEFAULT_EXCLUDE (the singleton's pairs) — fleet's
    # excluded set IS the singleton's covered set by construction, so
    # this restores the singleton's expectation without hard-coding
    # BTC/USDT/1h in a second place.
    return fleet_pairs | set(DEFAULT_EXCLUDE)


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
            # Scope fix (2026-07-28, post prod-promotion #28):
            # narrow the freshness scan from "all universe symbols" to
            # "symbols the fleet+singleton are EXPECTED to write
            # predictions for" = top-KEEPALIVE_TOP_N slice ∪ singleton.
            # Ranks 21-30 are out-of-scope by ratified decision — see
            # docs/superpowers/decisions/2026-07-28-fleet-cap-top-20-
            # ratified.md. Prior universe-wide scope produced ~10
            # permanent false-positive warnings/tick (LINK/UNI/LTC/etc.
            # correctly detected as prediction-less, but there is no
            # WS child to restart for them by architectural design).
            expected_pairs = await _load_expected_prediction_pairs(session)
            # Empty-universe guard: NEVER silently pass. A detector that
            # goes quiet because its input vanished is the worst failure
            # mode. Emit ONE warning finding so healer-status surfaces it
            # and the operator can investigate universe_refresh_task.
            if not expected_pairs:
                _C3_UNIVERSE_STALL_STREAK.pop("universe_stall", None)
                findings.append(HealerFinding(
                    detector_name="per_symbol_prediction_freshness",
                    severity="warning",
                    summary=(
                        "C3 anomaly: expected-prediction set is empty. "
                        "universe_refresh_task may be down or the "
                        "top-N slice is misconfigured; detector cannot "
                        "evaluate freshness this tick."
                    ),
                    details={
                        "class": "universe_input_empty",
                        "reason": (
                            "load_current_universe returned zero "
                            "entries for MAX(snapshot_at)"
                        ),
                    },
                ))
                return findings

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
        # Fleet-eligible scope: skip (symbol, timeframe) pairs the live
        # prediction stack isn't expected to write. Ranks 21-30 and
        # non-1h timeframes for top-20 fall out here — see
        # _load_expected_prediction_pairs docstring for the composition
        # rules and the ratified decision doc for why this scope is
        # correct.
        if (r.symbol, tf) not in expected_pairs:
            continue
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

        # Alert-fatigue suppression: only emit when the stale SET
        # changed since the last emission, or the reminder interval has
        # elapsed for an unchanged set. See C3_PER_SYMBOL_DROP_REMINDER_
        # SECONDS's module-level comment for the live incident that
        # motivated this.
        current_set = frozenset(
            (str(item["symbol"]), str(item["timeframe"])) for item in stale_syms
        )
        dedup_key = "per_symbol_drop"
        last = _C3_LAST_PER_SYMBOL_DROP.get(dedup_key)
        should_emit = (
            last is None
            or last[0] != current_set
            or (now - last[1]).total_seconds() >= C3_PER_SYMBOL_DROP_REMINDER_SECONDS
        )
        if should_emit:
            _C3_LAST_PER_SYMBOL_DROP[dedup_key] = (current_set, now)
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


# ---- C6: RL checkpoint load-health ---------------------------------------


async def detect_rl_checkpoint_load_health(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[HealerFinding]:
    """C6: rl_checkpoints.is_active=TRUE but the in-process pair disagrees.

    ``app.rl.checkpoints.load_active_checkpoint()`` fails open on ANY load
    error (most commonly a shape mismatch after an OBS_DIM change): it
    logs once at ERROR and leaves module state empty (``_active_policy=
    None``), but never clears the DB ``is_active`` flag and never raises
    anywhere durable/queryable. From that point on, every prediction gets
    ``brain_adjust=1.0`` (pure equal-weight, no-op) via
    :func:`app.rl.predictor_glue.compute_brain_adjust_and_persist`'s
    fail-open contract — silently, forever, until someone thinks to check
    backend logs at exactly the right container's boot time.

    This is precisely the incident that produced ZERO ``brain_decisions``
    rows from 2026-08-05T12:30:09Z onward, undetected for 10 days (see
    PR-RL-DROP-L7, 2026-08-15, which both fixes the specific stale row via
    a migration and adds this detector so the failure mode has a durable,
    alerting surface going forward).

    Compares each DB row where ``is_active = TRUE`` against
    :func:`app.rl.checkpoints.get_active_policy_and_checkpoint` (a pure
    in-process read, no DB call). Two anomaly shapes, both CRITICAL:

      * DB says active, nothing is loaded in-process at all.
      * DB says active, but the loaded pair's id/version is a DIFFERENT
        checkpoint than the DB-active row (stale module state from a
        prior activation, or a race).

    No DB-active row at all is the normal pre-training-bootstrap state —
    NOT an anomaly, no finding. A loaded pair that matches the DB-active
    row is the normal healthy state — no finding.

    Scope: ``rl_checkpoints`` / the L10 PPO policy ONLY. Does not touch
    ``ml_checkpoints`` (the separate L8 ConvLSTM checkpoint registry) —
    out of scope for this incident class.

    Detect-only, per this module's contract (see module docstring): never
    deactivates the stale DB row itself. That is an operator decision —
    the accompanying migration is the one-time, reviewed mechanism for
    the row already known to be stale as of this PR; any FUTURE
    occurrence still requires an operator/migration to clear, same as
    today.

    Known tradeoff (accepted, not fixed here): this detector re-fires
    every 5-minute tick for as long as the condition persists — no dedup/
    reminder-interval logic like C3 got after observing real alert
    fatigue in prod. If this proves noisy in practice, that's a candidate
    follow-up, not something to build proactively.
    """
    findings: list[HealerFinding] = []
    try:
        async with session_factory() as session:
            rows = (await session.execute(
                sa.text(
                    "SELECT id, model_name, version FROM rl_checkpoints "
                    "WHERE is_active = TRUE"
                ),
            )).all()
    except Exception as e:  # noqa: BLE001
        log.warning("healer C6: query failed: %s", e)
        return []

    if not rows:
        # No DB-active checkpoint at all — normal pre-training-bootstrap
        # state, not an anomaly.
        return findings

    pair = get_active_policy_and_checkpoint()

    for r in rows:
        db_id = int(r.id)
        db_model_name = str(r.model_name)
        db_version = str(r.version)

        if pair is None:
            findings.append(HealerFinding(
                detector_name="rl_checkpoint_load_health",
                severity="critical",
                summary=(
                    f"rl_checkpoints id={db_id} ({db_model_name} "
                    f"v{db_version}) is DB-active but NOT loaded "
                    f"in-process — brain_adjust has been neutral (1.0, "
                    f"equal-weight no-op) since the load failure. Check "
                    f"backend logs for \"RL brain checkpoint load failed\"."
                ),
                details={
                    "db_checkpoint_id": db_id,
                    "db_model_name": db_model_name,
                    "db_version": db_version,
                    "loaded_checkpoint_id": None,
                    "loaded_version": None,
                    "reason": "not_loaded",
                },
            ))
            continue

        _model, loaded_ck = pair
        if loaded_ck.id != db_id or loaded_ck.version != db_version:
            findings.append(HealerFinding(
                detector_name="rl_checkpoint_load_health",
                severity="critical",
                summary=(
                    f"rl_checkpoints id={db_id} ({db_model_name} "
                    f"v{db_version}) is DB-active but the loaded checkpoint "
                    f"is id={loaded_ck.id} v{loaded_ck.version} — mismatch. "
                    f"brain_adjust has been neutral (1.0, equal-weight "
                    f"no-op) for the DB-active row since the load failure. "
                    f"Check backend logs for \"RL brain checkpoint load "
                    f"failed\"."
                ),
                details={
                    "db_checkpoint_id": db_id,
                    "db_model_name": db_model_name,
                    "db_version": db_version,
                    "loaded_checkpoint_id": loaded_ck.id,
                    "loaded_version": loaded_ck.version,
                    "reason": "mismatch",
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
    "detect_rl_checkpoint_load_health",
    "detect_score_distribution_anomaly",
]
