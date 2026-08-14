"""Healer detector C5 — system-truth: the whole "always NULL / always
CONSTANT / never fires, invisible because nothing checked" bug class in
one daily sweep.

Ported from the ops-debug `system-truth` probe (2026-08-06). Same four
sections (field liveness, layer/feature/gate health, trap health,
endpoint health), same flag rules. The differences from the standalone
probe:

  * Runs once per SYSTEM_TRUTH_INTERVAL_HOURS (not on every 5-min healer
    tick) — a module-level last-run timestamp gates the real work,
    mirroring the C3 in-memory-streak pattern already used in
    detectors.py. Cheap to check, so it's safe to leave in the regular
    5-min detector loop rather than needing its own scheduler.
  * Baseline-diffs against the previous day's finding set (persisted as
    a dedicated ``system_truth_baseline`` finding's ``details`` JSON,
    reusing ``healer_findings`` rather than a new table). Only a finding
    that is NEW since yesterday pages at critical; a persisting
    known-bad state is rolled up as one informational summary so it
    does not spam every day. The very first run ever seeds the baseline
    without paging on anything — a fresh baseline is not a regression.
  * Section 3 (traps) cross-references the real trap registry
    (``app.core.scoring.traps.ALL_TRAPS``) since this runs in-process,
    unlike the SSH-based probe which could only see DB-recorded fires
    and explicitly could not tell "never fires" from "not a trap at
    all" without that registry.

Detect-only, per the healer's hard boundary: nothing here mutates
dispatch tables, live_trades, users, or env vars.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.healer.findings import HealerFinding

log = logging.getLogger(__name__)

SYSTEM_TRUTH_INTERVAL_HOURS: float = 24.0
CONSTANT_MIN_N: int = 100
BASELINE_DETECTOR_NAME: str = "system_truth_baseline"
FINDING_DETECTOR_NAME: str = "system_truth"

# 2026-08-14 severity recalibration: a table younger than this (by age
# of its oldest row AND total row count) never has CONSTANT judged on
# any of its columns at all — a newly-populating table's design-constant
# field (e.g. flow_feature_snapshots.source) is not a regression, and
# judging it before there's enough data to mean anything just pages the
# operator for a non-event. Scoped to CONSTANT only: BROKEN/STALLED/
# SUSPECT stay ungated since those ARE worth an immediate look even on
# a young table.
CONSTANT_GRACE_PERIOD_DAYS: float = 7.0
CONSTANT_GRACE_PERIOD_MIN_ROWS: int = 200

# Module-level last-run gate — resets on container restart, which is
# fine: a restart just means the next tick re-checks immediately rather
# than waiting out the rest of yesterday's window.
_LAST_RUN_AT: float | None = None

TABLES: dict[str, dict] = {
    "predictions": {
        "ts_col": "ts",
        "columns": [
            ("symbol", "cat", None), ("timeframe", "cat", None),
            ("final_score", "num", (-1.0, 1.0)), ("direction", "cat", None),
            ("confidence", "num", (0.0, 1.0)), ("cold_start", "cat", None),
            ("ghost_close", "num", None), ("ghost_uncertainty", "num", (0.0, None)),
            ("model_checkpoint_id", "cat", None), ("mtf_agreement", "cat", None),
            ("mtf_dominant_tf", "cat", None), ("p_win", "num", (0.0, 1.0)),
            ("effective_score", "num", (-1.0, 1.0)),
            ("realized_vol_20d", "num", (0.0, None)),
            ("funding_directional_adj", "num", None),
            ("mtf_directions_json", "json", None), ("layer_scores", "json", None),
        ],
    },
    "shadow_observations": {
        "ts_col": "opened_at",
        "columns": [("symbol", "cat", None), ("components", "json", None)],
    },
    "shadow_trades": {
        "ts_col": "opened_at",
        "columns": [
            ("symbol", "cat", None), ("timeframe", "cat", None),
            ("direction", "cat", None), ("entry_price", "num", (0.0, None)),
            ("entry_score", "num", (-1.0, 1.0)),
            ("entry_confidence", "num", (0.0, 1.0)),
            ("entry_atr", "num", (0.0, None)), ("exit_price", "num", (0.0, None)),
            ("exit_reason", "cat", None), ("pnl_pct", "num", None),
            ("pnl_usdt", "num", None), ("bars_held", "num", (0, None)),
            ("model_version", "cat", None), ("mtf_agreement", "cat", None),
            ("mtf_dominant_tf", "cat", None), ("p_win", "num", (0.0, 1.0)),
            ("effective_score", "num", (-1.0, 1.0)),
            ("realized_vol_20d", "num", (0.0, None)),
            ("funding_directional_adj", "num", None),
            ("hold_scaling_factor", "num", None),
            ("hold_timeout_bars", "num", (0, None)), ("layer_scores", "json", None),
        ],
    },
    "live_trades": {
        "ts_col": "opened_at",
        "columns": [
            ("symbol", "cat", None), ("direction", "cat", None),
            ("margin_usdt", "num", (0.0, None)), ("leverage", "num", (1, None)),
            ("entry_price", "num", (0.0, None)), ("exit_price", "num", (0.0, None)),
            ("stop_loss", "num", (0.0, None)), ("take_profit", "num", (0.0, None)),
            ("pnl_usdt", "num", None), ("pnl_pct", "num", None),
            ("fees_paid_usdt", "num", (0.0, None)), ("exit_reason", "cat", None),
            ("mode_at_open", "cat", None), ("approved_via", "cat", None),
            ("mtf_agreement", "cat", None), ("mtf_dominant_tf", "cat", None),
            ("p_win", "num", (0.0, 1.0)), ("effective_score", "num", (-1.0, 1.0)),
            ("realized_vol_20d", "num", (0.0, None)), ("status", "cat", None),
            ("failure_reason", "cat", None),
        ],
    },
    "flow_feature_snapshots": {
        "ts_col": "captured_at",
        "columns": [
            ("symbol", "cat", None), ("ls_account_ratio", "num", (0.0, 1.0)),
            ("taker_buy_sell_ratio", "num", (0.0, 1.0)),
            ("oi_4h_delta", "num", None), ("oi_24h_delta", "num", None),
            ("source", "cat", None),
        ],
    },
}

LAYER_NAMES = {
    "1": "L1_macro", "2": "L2_patterns", "3": "L3_momentum", "4": "L4_SMC",
    "5": "L5_volume", "6": "L6_micro", "7": "L7_xgboost_stub",
    "8": "L8_convlstm", "9": "L9_news", "10": "L10_brain",
}

ENDPOINTS: list[tuple[str, str, dict, str | None]] = [
    ("spot_klines", "https://api.binance.com/api/v3/klines",
     {"symbol": "BTCUSDT", "interval": "1h", "limit": "2"}, "[0][4]"),
    ("spot_exchangeInfo", "https://api.binance.com/api/v3/exchangeInfo", {}, "symbols"),
    ("futures_klines", "https://fapi.binance.com/fapi/v1/klines",
     {"symbol": "BTCUSDT", "interval": "1h", "limit": "2"}, "[0][4]"),
    ("futures_premiumIndex", "https://fapi.binance.com/fapi/v1/premiumIndex",
     {"symbol": "BTCUSDT"}, "lastFundingRate"),
    ("futures_OI_hist", "https://fapi.binance.com/futures/data/openInterestHist",
     {"symbol": "BTCUSDT", "period": "1h", "limit": "5"}, "[0].sumOpenInterest"),
    ("futures_ls_account_ratio",
     "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
     {"symbol": "BTCUSDT", "period": "5m", "limit": "1"}, "[0].longAccount"),
    ("futures_taker_ratio", "https://fapi.binance.com/futures/data/takerlongshortRatio",
     {"symbol": "BTCUSDT", "period": "5m", "limit": "1"}, "[0].buyVol"),
    ("bybit_kline", "https://api.bybit.com/v5/market/kline",
     {"category": "linear", "symbol": "BTCUSDT", "interval": "60", "limit": "2"},
     "result.list"),
    ("fear_greed", "https://api.alternative.me/fng/", {}, "data[0].value"),
]


async def _section1(
    session: AsyncSession,
) -> tuple[dict[str, list[str]], set[str]]:
    """Returns (findings, mature_tables).

    ``mature_tables`` is every table old enough (oldest row >=
    CONSTANT_GRACE_PERIOD_DAYS) AND big enough (>= CONSTANT_GRACE_PERIOD_MIN_ROWS
    total rows) for its columns' CONSTANT flag to mean anything. A query
    failure is treated as NOT mature (fail-closed on this specific
    check) — the whole point of the grace period is to avoid paging on
    shaky-looking data, so an inability to even establish the table's
    age is itself a reason not to judge it yet.
    """
    findings: dict[str, list[str]] = {}
    mature_tables: set[str] = set()
    for table, cfg in TABLES.items():
        ts_col = cfg["ts_col"]
        try:
            age_row = (await session.execute(sa.text(
                f"SELECT MIN({ts_col}) AS first_ts, COUNT(*) AS total_rows FROM {table}"
            ))).one()
        except Exception as e:  # noqa: BLE001
            log.warning("system_truth: maturity query failed for %s: %s", table, e)
            age_row = None
        if age_row is not None and age_row.first_ts is not None:
            age_days = (datetime.now(timezone.utc) - age_row.first_ts).total_seconds() / 86400
            if age_days >= CONSTANT_GRACE_PERIOD_DAYS and age_row.total_rows > CONSTANT_GRACE_PERIOD_MIN_ROWS:
                mature_tables.add(table)
        is_mature = table in mature_tables

        for col, kind, plausible in cfg["columns"]:
            is_num = kind == "num"
            extra = (
                f"MIN({col}) FILTER (WHERE {ts_col} >= now() - interval '7 days') AS min_v, "
                f"MAX({col}) FILTER (WHERE {ts_col} >= now() - interval '7 days') AS max_v"
                if is_num else "NULL AS min_v, NULL AS max_v"
            )
            sql = f"""
                SELECT
                    COUNT(*) FILTER (WHERE {ts_col} >= now() - interval '7 days') AS n_7d,
                    COUNT({col}) FILTER (WHERE {ts_col} >= now() - interval '7 days') AS nn_7d,
                    COUNT(DISTINCT {col}) FILTER (WHERE {ts_col} >= now() - interval '7 days') AS distinct_7d,
                    MAX({ts_col}) FILTER (WHERE {col} IS NOT NULL) AS last_non_null_at,
                    {extra}
                FROM {table}
            """
            try:
                row = (await session.execute(sa.text(sql))).one()
            except Exception as e:  # noqa: BLE001
                log.warning("system_truth: section1 query failed %s.%s: %s", table, col, e)
                continue

            null_rate_7d = 100 * (1 - row.nn_7d / row.n_7d) if row.n_7d else None
            flags: list[str] = []
            if null_rate_7d is not None and null_rate_7d > 50:
                flags.append("BROKEN")
            if is_mature and row.distinct_7d == 1 and row.nn_7d > CONSTANT_MIN_N:
                flags.append("CONSTANT")
            if row.last_non_null_at is not None:
                age_h = (datetime.now(timezone.utc) - row.last_non_null_at).total_seconds() / 3600
                if age_h > 24:
                    flags.append(f"STALLED({age_h:.0f}h)")
            elif row.n_7d:
                flags.append("STALLED(never-non-null-in-7d)")
            if plausible is not None and is_num:
                lo, hi = plausible
                if row.min_v is not None and lo is not None and float(row.min_v) < lo:
                    flags.append(f"SUSPECT(min<{lo})")
                if row.max_v is not None and hi is not None and float(row.max_v) > hi:
                    flags.append(f"SUSPECT(max>{hi})")
            if flags:
                findings[f"{table}.{col}"] = flags
    return findings, mature_tables


async def _section2(session: AsyncSession) -> dict[str, list[str]]:
    findings: dict[str, list[str]] = {}
    for key, name in LAYER_NAMES.items():
        sql = f"""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE layer_scores -> '{key}' IS NOT NULL
                                      AND layer_scores -> '{key}' != 'null') AS evaluated
            FROM predictions WHERE ts >= now() - interval '7 days'
        """
        row = (await session.execute(sa.text(sql))).one()
        abstain_rate = 100 * (1 - row.evaluated / row.total) if row.total else None
        if abstain_rate is not None and abstain_rate > 90:
            findings[f"layer.{name}"] = [f"ABSTAIN_RATE={abstain_rate:.1f}%"]

    for feat_key in ("ls_account_ratio", "taker_buy_sell_ratio", "oi_4h_delta", "oi_24h_delta"):
        sql = f"""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE layer_scores -> 'features' ->> '{feat_key}' IS NOT NULL) AS non_null,
                   COUNT(DISTINCT layer_scores -> 'features' ->> '{feat_key}') AS distinct_v
            FROM predictions WHERE ts >= now() - interval '7 days'
        """
        row = (await session.execute(sa.text(sql))).one()
        null_rate = 100 * (1 - row.non_null / row.total) if row.total else None
        flags = []
        if null_rate is not None and null_rate > 50:
            flags.append("BROKEN")
        if row.non_null > CONSTANT_MIN_N and row.distinct_v == 1:
            flags.append("CONSTANT")
        if flags:
            findings[f"W4.{feat_key}"] = flags
    return findings


async def _section3(session: AsyncSession) -> dict[str, list[str]]:
    from app.core.scoring.traps import ALL_TRAPS

    findings: dict[str, list[str]] = {}
    sql = """
        SELECT jsonb_array_elements(layer_scores -> 'traps_fired') ->> 'trap_id' AS trap_id
        FROM predictions
        WHERE ts >= now() - interval '30 days'
          AND jsonb_typeof(layer_scores -> 'traps_fired') = 'array'
    """
    rows = (await session.execute(sa.text(sql))).all()
    fired_ids = {r.trap_id for r in rows}
    for trap in ALL_TRAPS:
        if trap.trap_id not in fired_ids:
            findings[f"trap.{trap.trap_id}"] = ["ZERO_FIRES_30D"]
    return findings


async def _section4() -> dict[str, list[str]]:
    findings: dict[str, list[str]] = {}
    async with httpx.AsyncClient() as client:
        for name, url, params, expected in ENDPOINTS:
            try:
                resp = await client.get(url, params=params, timeout=10.0)
            except Exception as e:  # noqa: BLE001
                findings[f"endpoint.{name}"] = [f"ERROR:{str(e)[:80]}"]
                continue
            if resp.status_code != 200:
                findings[f"endpoint.{name}"] = [f"HTTP_{resp.status_code}"]
                continue
            if expected is None:
                continue
            try:
                data = resp.json()
                cursor = data
                for part in [p for p in expected.replace("]", "").replace("[", ".").split(".") if p]:
                    cursor = cursor[int(part)] if part.isdigit() else cursor[part]
            except Exception:  # noqa: BLE001
                findings[f"endpoint.{name}"] = [f"SHAPE_MISMATCH:{expected}"]
    return findings


async def detect_system_truth(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[HealerFinding]:
    """C5: daily field/layer/trap/endpoint liveness sweep, baseline-diffed.

    Gated to run at most once per SYSTEM_TRUTH_INTERVAL_HOURS regardless
    of how often the 5-min healer tick calls this function — cheap to
    check every tick, expensive to actually run, so the gate lives here
    rather than needing a separate scheduler.
    """
    global _LAST_RUN_AT
    now_ts = time.time()
    if _LAST_RUN_AT is not None and (now_ts - _LAST_RUN_AT) < SYSTEM_TRUTH_INTERVAL_HOURS * 3600:
        return []
    _LAST_RUN_AT = now_ts

    current: dict[str, list[str]] = {}
    mature_tables: set[str] = set()
    try:
        async with session_factory() as session:
            section1_findings, mature_tables = await _section1(session)
            current.update(section1_findings)
            current.update(await _section2(session))
            current.update(await _section3(session))
        current.update(await _section4())
    except Exception as e:  # noqa: BLE001
        log.warning("system_truth: sweep failed: %s", e)
        return []

    # ---- load yesterday's baseline (most recent baseline row) ----------
    previous: dict[str, list[str]] = {}
    previous_ever_varying: set[str] = set()
    baseline_exists = False
    try:
        async with session_factory() as session:
            row = (await session.execute(sa.text(
                "SELECT details FROM healer_findings "
                "WHERE detector_name = :d ORDER BY detected_at DESC LIMIT 1"
            ), {"d": BASELINE_DETECTOR_NAME})).first()
            if row is not None and row.details:
                baseline_exists = True
                # asyncpg auto-decodes jsonb -> dict for typed columns, but
                # raw sa.text() execution isn't guaranteed to -- handle
                # both a native dict and a JSON string defensively.
                raw = row.details
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                previous = dict(parsed.get("keys", {}))
                # Absent on any baseline written before the 2026-08-14
                # severity recalibration shipped -- defaults to empty.
                # One-time, accepted blind spot: a column that regressed
                # from varying to constant on the very first run after
                # this deploy won't have prior-variance evidence yet, so
                # it's filed at warning instead of critical that one day.
                # Everything already flagged CONSTANT under the old code
                # is in `previous` as a persisting key, not a new one, so
                # it never hits this path at all.
                previous_ever_varying = set(parsed.get("ever_varying", []))
    except Exception as e:  # noqa: BLE001
        log.warning("system_truth: baseline lookup failed: %s", e)

    new_keys = sorted(set(current) - set(previous))
    resolved_keys = sorted(set(previous) - set(current))
    persisting_keys = sorted(set(current) & set(previous))

    # 2026-08-14 severity recalibration: cumulative "has this mature
    # column ever been observed NOT constant" tracking. A CONSTANT
    # finding with no such evidence on record is "constant since we
    # started being able to judge it" (new field, by-design or
    # coincidental) rather than a regression -- see the grace-period
    # constants' docstring above for the paging rationale.
    all_mature_column_keys = {
        f"{table}.{col}"
        for table in mature_tables
        for col, _, _ in TABLES[table]["columns"]
    }
    today_non_constant_mature = {
        key for key in all_mature_column_keys
        if "CONSTANT" not in current.get(key, [])
    }
    ever_varying = previous_ever_varying | today_non_constant_mature

    out: list[HealerFinding] = []

    if not baseline_exists:
        # First run ever: seed the baseline, don't page on anything --
        # a fresh baseline is not a regression.
        out.append(HealerFinding(
            detector_name=FINDING_DETECTOR_NAME,
            severity="info",
            summary=(
                f"system-truth: baseline seeded with {len(current)} finding(s) "
                "(first run — nothing pages until tomorrow's diff)"
            ),
            details={"keys": current},
        ))
    else:
        for key in new_keys:
            flags = current[key]
            if flags == ["CONSTANT"] and key not in previous_ever_varying:
                # New-field-constant: never observed varying while
                # mature. Filed for triage, does not page the phone.
                out.append(HealerFinding(
                    detector_name=FINDING_DETECTOR_NAME,
                    severity="warning",
                    summary=(
                        f"system-truth: NEW finding {key}: {flags} "
                        "(constant since first observation, no prior "
                        "variance on record — not paging)"
                    ),
                    details={"key": key, "flags": flags},
                ))
            else:
                out.append(HealerFinding(
                    detector_name=FINDING_DETECTOR_NAME,
                    severity="critical",
                    summary=f"system-truth: NEW finding {key}: {flags}",
                    details={"key": key, "flags": flags},
                ))
        if persisting_keys:
            out.append(HealerFinding(
                detector_name=FINDING_DETECTOR_NAME,
                severity="info",
                summary=(
                    f"system-truth: {len(persisting_keys)} known finding(s) "
                    "persisting (not re-paged)"
                ),
                details={"keys": persisting_keys},
            ))
        if resolved_keys:
            out.append(HealerFinding(
                detector_name=FINDING_DETECTOR_NAME,
                severity="info",
                summary=f"system-truth: {len(resolved_keys)} finding(s) resolved since yesterday",
                details={"keys": resolved_keys},
            ))
        if not new_keys and not persisting_keys and not resolved_keys:
            out.append(HealerFinding(
                detector_name=FINDING_DETECTOR_NAME,
                severity="info",
                summary="system-truth: clean sweep, 0 findings",
                details=None,
            ))

    # Always persist today's full set (+ the cumulative ever-varying
    # tracking set) as tomorrow's baseline.
    out.append(HealerFinding(
        detector_name=BASELINE_DETECTOR_NAME,
        severity="info",
        summary=f"system-truth baseline: {len(current)} finding(s) recorded",
        details={"keys": current, "ever_varying": sorted(ever_varying)},
    ))

    return out


__all__ = ["SYSTEM_TRUTH_INTERVAL_HOURS", "detect_system_truth"]
