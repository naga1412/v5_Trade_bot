import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# ============================================================================
# system-truth: single-probe observability sweep.
#
# The insight: ~15 defects found this project, all the same shape -- a
# field that was always NULL, always CONSTANT, or a code path that never
# fired, invisible because nothing checked for it. This checks for exactly
# that shape, everywhere, in one run.
#
# Four sections: (1) field liveness on the 5 core tables, (2) layer /
# feature-computer / gate execution + consumption, (3) trap fire health,
# (4) external endpoint health. Read-only throughout.
# ============================================================================

# ---------------------------------------------------------------------------
# Section 1 config: table -> (timestamp_column, [(col, kind, plausible_range)])
# kind: "num" (min/max/mean + distinct), "cat" (distinct only), "json" (null-
# rate + distinct-of-top-level-presence only). plausible_range: (lo, hi) or
# None. Deliberately excludes pure bookkeeping columns (id, hash-chain,
# foreign keys) -- those are supposed to be unique/monotonic, a liveness
# check on them is meaningless by construction.
# ---------------------------------------------------------------------------

TABLES: dict[str, dict] = {
    "predictions": {
        "ts_col": "ts",
        "columns": [
            ("symbol", "cat", None),
            ("timeframe", "cat", None),
            ("final_score", "num", (-1.0, 1.0)),
            ("direction", "cat", None),
            ("confidence", "num", (0.0, 1.0)),
            ("cold_start", "cat", None),
            ("ghost_close", "num", None),
            ("ghost_uncertainty", "num", (0.0, None)),
            ("model_checkpoint_id", "cat", None),
            ("mtf_agreement", "cat", None),
            ("mtf_dominant_tf", "cat", None),
            ("p_win", "num", (0.0, 1.0)),
            ("effective_score", "num", (-1.0, 1.0)),
            ("realized_vol_20d", "num", (0.0, None)),
            ("funding_directional_adj", "num", None),
            ("mtf_directions_json", "json", None),
            ("layer_scores", "json", None),
        ],
    },
    "shadow_observations": {
        "ts_col": "opened_at",
        "columns": [
            ("symbol", "cat", None),
            ("components", "json", None),
        ],
    },
    "shadow_trades": {
        "ts_col": "opened_at",
        "columns": [
            ("symbol", "cat", None),
            ("timeframe", "cat", None),
            ("direction", "cat", None),
            ("entry_price", "num", (0.0, None)),
            ("entry_score", "num", (-1.0, 1.0)),
            ("entry_confidence", "num", (0.0, 1.0)),
            ("entry_atr", "num", (0.0, None)),
            ("exit_price", "num", (0.0, None)),
            ("exit_reason", "cat", None),
            ("pnl_pct", "num", None),
            ("pnl_usdt", "num", None),
            ("bars_held", "num", (0, None)),
            ("model_version", "cat", None),
            ("mtf_agreement", "cat", None),
            ("mtf_dominant_tf", "cat", None),
            ("p_win", "num", (0.0, 1.0)),
            ("effective_score", "num", (-1.0, 1.0)),
            ("realized_vol_20d", "num", (0.0, None)),
            ("funding_directional_adj", "num", None),
            ("hold_scaling_factor", "num", None),
            ("hold_timeout_bars", "num", (0, None)),
            ("layer_scores", "json", None),
        ],
    },
    "live_trades": {
        "ts_col": "opened_at",
        "columns": [
            ("symbol", "cat", None),
            ("direction", "cat", None),
            ("margin_usdt", "num", (0.0, None)),
            ("leverage", "num", (1, None)),
            ("entry_price", "num", (0.0, None)),
            ("exit_price", "num", (0.0, None)),
            ("stop_loss", "num", (0.0, None)),
            ("take_profit", "num", (0.0, None)),
            ("pnl_usdt", "num", None),
            ("pnl_pct", "num", None),
            ("fees_paid_usdt", "num", (0.0, None)),
            ("exit_reason", "cat", None),
            ("mode_at_open", "cat", None),
            ("approved_via", "cat", None),
            ("mtf_agreement", "cat", None),
            ("mtf_dominant_tf", "cat", None),
            ("p_win", "num", (0.0, 1.0)),
            ("effective_score", "num", (-1.0, 1.0)),
            ("realized_vol_20d", "num", (0.0, None)),
            ("status", "cat", None),
            ("failure_reason", "cat", None),
        ],
    },
    "flow_feature_snapshots": {
        "ts_col": "captured_at",
        "columns": [
            ("symbol", "cat", None),
            ("ls_account_ratio", "num", (0.0, 1.0)),
            ("taker_buy_sell_ratio", "num", (0.0, 1.0)),
            ("oi_4h_delta", "num", None),
            ("oi_24h_delta", "num", None),
            ("source", "cat", None),
        ],
    },
}

CONSTANT_MIN_N = 100


def fmt(v):
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return f"{v:.5g}"
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


async def section1(session) -> list[dict]:
    print("\n" + "=" * 100, flush=True)
    print("SECTION 1 — FIELD LIVENESS", flush=True)
    print("=" * 100, flush=True)

    findings = []
    for table, cfg in TABLES.items():
        ts_col = cfg["ts_col"]
        print(f"\n--- {table} (ts_col={ts_col}) ---", flush=True)
        for col, kind, plausible in cfg["columns"]:
            is_num = kind == "num"
            extra = (
                f"MIN({col}) FILTER (WHERE {ts_col} >= now() - interval '7 days') AS min_v, "
                f"MAX({col}) FILTER (WHERE {ts_col} >= now() - interval '7 days') AS max_v, "
                f"AVG({col}::double precision) FILTER (WHERE {ts_col} >= now() - interval '7 days') AS mean_v"
                if is_num else "NULL AS min_v, NULL AS max_v, NULL AS mean_v"
            )
            sql = f"""
                SELECT
                    COUNT(*) FILTER (WHERE {ts_col} >= now() - interval '24 hours') AS n_24h,
                    COUNT({col}) FILTER (WHERE {ts_col} >= now() - interval '24 hours') AS nn_24h,
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
                print(f"  {col:<28} QUERY ERROR: {e}", flush=True)
                continue

            null_rate_24h = (
                100 * (1 - row.nn_24h / row.n_24h) if row.n_24h else None
            )
            null_rate_7d = (
                100 * (1 - row.nn_7d / row.n_7d) if row.n_7d else None
            )
            flags = []
            if null_rate_7d is not None and null_rate_7d > 50:
                flags.append("BROKEN")
            if row.distinct_7d == 1 and row.nn_7d > CONSTANT_MIN_N:
                flags.append("CONSTANT")
            if row.last_non_null_at is not None:
                age_h = (
                    datetime.now(timezone.utc) - row.last_non_null_at
                ).total_seconds() / 3600
                if age_h > 24:
                    flags.append(f"STALLED({age_h:.0f}h)")
            elif row.n_7d and row.n_7d > 0:
                flags.append("STALLED(never-non-null-in-7d)")
            if plausible is not None and is_num:
                lo, hi = plausible
                if row.min_v is not None and lo is not None and float(row.min_v) < lo:
                    flags.append(f"SUSPECT(min={fmt(row.min_v)}<{lo})")
                if row.max_v is not None and hi is not None and float(row.max_v) > hi:
                    flags.append(f"SUSPECT(max={fmt(row.max_v)}>{hi})")

            flag_str = ",".join(flags) if flags else "ok"
            print(
                f"  {col:<28} n24h={row.n_24h:<6} null%24h={fmt(null_rate_24h):<8} "
                f"n7d={row.n_7d:<6} null%7d={fmt(null_rate_7d):<8} "
                f"distinct7d={row.distinct_7d:<5} "
                f"min={fmt(row.min_v):<12} max={fmt(row.max_v):<12} mean={fmt(row.mean_v):<12} "
                f"last_non_null={fmt(row.last_non_null_at):<26} [{flag_str}]",
                flush=True,
            )
            if flags:
                findings.append({"table": table, "column": col, "flags": flags})

    print(f"\nSECTION 1 VERDICT: {'FAIL' if findings else 'PASS'} "
          f"({len(findings)} column(s) flagged)", flush=True)
    return findings


LAYER_NAMES = {
    "1": "L1 macro", "2": "L2 patterns", "3": "L3 momentum", "4": "L4 SMC",
    "5": "L5 volume", "6": "L6 micro", "7": "L7 xgboost(stub)",
    "8": "L8 convlstm", "9": "L9 news", "10": "L10 brain",
}


async def section2(session) -> list[dict]:
    print("\n" + "=" * 100, flush=True)
    print("SECTION 2 — LAYER / FEATURE-COMPUTER / GATE HEALTH + CONSUMPTION", flush=True)
    print("=" * 100, flush=True)

    findings = []

    print("\n--- L1-L10 (predictions.layer_scores, 7d) ---", flush=True)
    for key, name in LAYER_NAMES.items():
        sql = f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE layer_scores -> '{key}' IS NOT NULL
                                  AND layer_scores -> '{key}' != 'null') AS evaluated,
                COUNT(DISTINCT layer_scores -> '{key}' ->> 'direction')
                    FILTER (WHERE layer_scores -> '{key}' IS NOT NULL
                             AND layer_scores -> '{key}' != 'null') AS distinct_direction
            FROM predictions
            WHERE ts >= now() - interval '7 days'
        """
        row = (await session.execute(sa.text(sql))).one()
        abstain_rate = 100 * (1 - row.evaluated / row.total) if row.total else None
        flags = []
        if abstain_rate is not None and abstain_rate > 90:
            flags.append(f"ABSTAIN_RATE={abstain_rate:.1f}%>90%")
        if row.evaluated > CONSTANT_MIN_N and row.distinct_direction == 1:
            flags.append("SINGLE_VALUE")
        flag_str = ",".join(flags) if flags else "ok"
        print(
            f"  {key:>2} {name:<20} total={row.total:<6} evaluated={row.evaluated:<6} "
            f"abstain%={fmt(abstain_rate):<8} distinct_direction={row.distinct_direction} [{flag_str}]",
            flush=True,
        )
        if flags:
            findings.append({"item": f"layer_{key}_{name}", "flags": flags})

    print("\n--- W-module feature computers (predictions.layer_scores->'features', 7d) ---", flush=True)
    print("  W1 mean_reversion / W2 volatility_state / W3 btc_spread / W5 structure_location: "
          "NOT ON MAIN (dev-only, confirmed 2026-08-06 inventory) — not checked here, nothing to check.",
          flush=True)
    for feat_key in ("ls_account_ratio", "taker_buy_sell_ratio", "oi_4h_delta", "oi_24h_delta"):
        sql = f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE layer_scores -> 'features' ->> '{feat_key}' IS NOT NULL) AS non_null,
                COUNT(DISTINCT layer_scores -> 'features' ->> '{feat_key}') AS distinct_v
            FROM predictions
            WHERE ts >= now() - interval '7 days'
        """
        row = (await session.execute(sa.text(sql))).one()
        null_rate = 100 * (1 - row.non_null / row.total) if row.total else None
        flags = []
        if null_rate is not None and null_rate > 50:
            flags.append("BROKEN")
        if row.non_null > CONSTANT_MIN_N and row.distinct_v == 1:
            flags.append("CONSTANT")
        flag_str = ",".join(flags) if flags else "ok"
        print(
            f"  W4.{feat_key:<22} total={row.total:<6} non_null={row.non_null:<6} "
            f"null%={fmt(null_rate):<8} distinct={row.distinct_v} "
            f"[reaches: extras['features'] ONLY, verified NOT in scoring] [{flag_str}]",
            flush=True,
        )
        if flags:
            findings.append({"item": f"W4.{feat_key}", "flags": flags})

    print("\n--- PR#266 entry-quality gate extensions (input-field population, "
          "7d; decision OUTCOMES are NOT persisted anywhere -- see note) ---", flush=True)
    gate_checks = [
        ("PATTERN_BOOST input (L2 direction present)",
         "layer_scores -> '2' IS NOT NULL AND layer_scores -> '2' != 'null'"),
        ("MTF confluence reaches persistence (mtf_dominant_tf)",
         "mtf_dominant_tf IS NOT NULL"),
    ]
    for label, cond in gate_checks:
        sql = f"""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE {cond}) AS populated
            FROM predictions WHERE ts >= now() - interval '7 days'
        """
        row = (await session.execute(sa.text(sql))).one()
        pct = 100 * row.populated / row.total if row.total else None
        print(f"  {label:<45} total={row.total:<6} populated={row.populated:<6} pct={fmt(pct)}%", flush=True)
    print(
        "  ADX_GATE_ENABLED input (mtf_adx_by_tf_json): NOT CHECKABLE FROM "
        "DB -- predictor.py computes this on every prediction (threaded "
        "onto LivePredictionOut, reaches the WS payload) but it is NOT a "
        "column in `predictions` and is never passed to persist_prediction() "
        "(confirmed via grep of persistence.py + information_schema). The "
        "live ADX gate consumes it in-memory at decision time (same "
        "request), but no historical record survives -- there is no audit "
        "trail for what ADX values drove any past gate decision.",
        flush=True,
    )
    print(
        "  NOTE: no dispatch/decision-log table exists (confirmed via "
        "information_schema.tables) -- gate ALLOW/DENY outcomes are logged "
        "to stdout only, not queryable. This is itself a system-truth gap: "
        "cannot verify from data alone whether REGIME_GATE_ENABLED / "
        "ADX_GATE_ENABLED / PATTERN_BOOST_ENABLED (confirmed True in prod "
        "via regime-live-check probe) actually block anything in practice.",
        flush=True,
    )
    findings.append({
        "item": "mtf_adx_by_tf_json_not_persisted",
        "flags": ["COMPUTED_BUT_NOT_PERSISTED — no audit trail for ADX gate inputs"],
    })
    print(
        "  CONFIRMED SEPARATELY (2026-08-06 feature inventory, code "
        "inspection not this probe): shadow_worker's entry-quality call "
        "site uses a 2-field shim missing layer2_direction/layer2_confidence/"
        "mtf_adx_by_tf/mtf_dominant_tf and never passes market_regime= -- "
        "so regime/pattern-boost/ADX gates silently no-op on the shadow "
        "path specifically, regardless of the flags above.",
        flush=True,
    )
    findings.append({
        "item": "PR266_gate_decision_log",
        "flags": ["NO_DECISION_LOG — gate outcomes unauditable from data"],
    })

    return findings


async def section3(session) -> list[dict]:
    print("\n" + "=" * 100, flush=True)
    print("SECTION 3 — TRAP HEALTH (30d)", flush=True)
    print("=" * 100, flush=True)

    sql = """
        SELECT jsonb_array_elements(layer_scores -> 'traps_fired') ->> 'trap_id' AS trap_id,
               p.direction, p.final_score
        FROM predictions p
        WHERE p.ts >= now() - interval '30 days'
          AND jsonb_typeof(layer_scores -> 'traps_fired') = 'array'
    """
    rows = (await session.execute(sa.text(sql))).all()
    fire_counts: dict[str, int] = {}
    for r in rows:
        fire_counts[r.trap_id] = fire_counts.get(r.trap_id, 0) + 1

    known_trap_sql = """
        SELECT DISTINCT jsonb_array_elements(layer_scores -> 'traps_fired') ->> 'trap_id' AS tid
        FROM predictions WHERE jsonb_typeof(layer_scores -> 'traps_fired') = 'array'
    """
    findings = []
    if not fire_counts:
        print("  No trap fires recorded in the last 30 days at all (or "
              "traps_fired key absent/empty in every row this window).", flush=True)
        findings.append({"item": "all_traps", "flags": ["ZERO_FIRES_30D"]})
    else:
        for trap_id, n in sorted(fire_counts.items(), key=lambda x: -x[1]):
            print(f"  {trap_id:<28} fires_30d={n}", flush=True)
        print(
            f"\n  {len(fire_counts)} distinct trap_ids fired in 30d. Full "
            "per-trap WR/avg_pnl comparison + the ~7/17 confirmed-dead "
            "traps list already exists in memory (traps_inventory_2026_08_05) "
            "and is unchanged (trap files identical main/dev, re-verified "
            "2026-08-06) -- not re-derived here to keep this run fast; "
            "flagging ZERO_FIRES for any trap_id absent from the 30d set "
            "would require the full known-trap-id registry, which lives in "
            "app.core.scoring.traps.ALL_TRAPS (Python-side, not DB-queryable) "
            "-- cross-referenced separately, see report.",
            flush=True,
        )
    return findings


async def section4() -> list[dict]:
    print("\n" + "=" * 100, flush=True)
    print("SECTION 4 — EXTERNAL ENDPOINT HEALTH", flush=True)
    print("=" * 100, flush=True)
    print("  (reuses the external-endpoint-healthcheck probe logic verbatim)", flush=True)

    ENDPOINTS = [
        ("spot klines", "https://api.binance.com/api/v3/klines",
         {"symbol": "BTCUSDT", "interval": "1h", "limit": "2"}, "[0][4]"),
        ("spot exchangeInfo", "https://api.binance.com/api/v3/exchangeInfo", {}, "symbols"),
        ("spot ticker/price", "https://api.binance.com/api/v3/ticker/price",
         {"symbol": "BTCUSDT"}, "price"),
        ("spot ticker/24hr", "https://api.binance.com/api/v3/ticker/24hr",
         {"symbol": "BTCUSDT"}, "quoteVolume"),
        ("futures klines", "https://fapi.binance.com/fapi/v1/klines",
         {"symbol": "BTCUSDT", "interval": "1h", "limit": "2"}, "[0][4]"),
        ("futures exchangeInfo", "https://fapi.binance.com/fapi/v1/exchangeInfo", {}, "symbols"),
        ("futures premiumIndex", "https://fapi.binance.com/fapi/v1/premiumIndex",
         {"symbol": "BTCUSDT"}, "lastFundingRate"),
        ("futures ping", "https://fapi.binance.com/fapi/v1/ping", {}, None),
        ("futures OI hist", "https://fapi.binance.com/futures/data/openInterestHist",
         {"symbol": "BTCUSDT", "period": "1h", "limit": "5"}, "[0].sumOpenInterest"),
        ("futures long/short account ratio",
         "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
         {"symbol": "BTCUSDT", "period": "5m", "limit": "1"}, "[0].longAccount"),
        ("futures taker long/short ratio",
         "https://fapi.binance.com/futures/data/takerlongshortRatio",
         {"symbol": "BTCUSDT", "period": "5m", "limit": "1"}, "[0].buyVol"),
        ("testnet futures exchangeInfo",
         "https://testnet.binancefuture.com/fapi/v1/exchangeInfo", {}, "symbols"),
        ("testnet futures premiumIndex",
         "https://testnet.binancefuture.com/fapi/v1/premiumIndex",
         {"symbol": "BTCUSDT"}, "markPrice"),
        ("bybit kline", "https://api.bybit.com/v5/market/kline",
         {"category": "linear", "symbol": "BTCUSDT", "interval": "60", "limit": "2"}, "result.list"),
        ("bybit instruments-info", "https://api.bybit.com/v5/market/instruments-info",
         {"category": "linear", "symbol": "BTCUSDT"}, "result.list"),
        ("fear & greed index", "https://api.alternative.me/fng/", {}, "data[0].value"),
    ]

    async def check(client, name, url, params, expected):
        try:
            resp = await client.get(url, params=params, timeout=10.0)
        except Exception as e:  # noqa: BLE001
            return (name, "ERROR", str(e)[:100], False)
        if resp.status_code != 200:
            return (name, str(resp.status_code), resp.text[:100].replace("\n", " "), False)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            return (name, "200", "non-JSON body", False)
        if expected is None:
            return (name, "200", "OK", True)
        try:
            cursor = data
            for part in [p for p in expected.replace("]", "").replace("[", ".").split(".") if p]:
                cursor = cursor[int(part)] if part.isdigit() else cursor[part]
            return (name, "200", f"shape OK ({expected})", True)
        except Exception:  # noqa: BLE001
            return (name, "200", f"SHAPE MISMATCH -- {expected} missing", False)

    findings = []
    async with httpx.AsyncClient() as client:
        for name, url, params, expected in ENDPOINTS:
            name2, status, detail, ok = await check(client, name, url, params, expected)
            mark = "PASS" if ok else "FAIL"
            print(f"  {name2:<38} {status:<12} {detail:<45} {mark}", flush=True)
            if not ok:
                findings.append({"item": name2, "flags": [f"{status}: {detail}"]})
            await asyncio.sleep(0.1)

    print(f"\nSECTION 4 VERDICT: {'FAIL' if findings else 'PASS'} "
          f"({len(findings)} endpoint(s) broken)", flush=True)
    return findings


async def main():
    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    all_findings = {}
    async with session_factory() as session:
        all_findings["section1_field_liveness"] = await section1(session)
        all_findings["section2_layer_feature_gate_health"] = await section2(session)
        all_findings["section3_trap_health"] = await section3(session)
    all_findings["section4_endpoint_health"] = await section4()

    print("\n" + "=" * 100, flush=True)
    print("MACHINE-READABLE SUMMARY (for healer baseline diffing)", flush=True)
    print("=" * 100, flush=True)
    print(json.dumps(all_findings, default=str), flush=True)

    total_findings = sum(len(v) for v in all_findings.values())
    print(f"\nOVERALL: {total_findings} total finding(s) across all 4 sections.", flush=True)


asyncio.run(main())
