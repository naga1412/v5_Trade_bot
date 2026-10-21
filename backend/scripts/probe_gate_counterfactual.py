import asyncio
import statistics as st
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")

import httpx
import numpy as np
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# ============================================================================
# Gate-divergence counterfactual, items 1(a)+1(b).
#
# No decision log exists (confirmed by system-truth), and docker's log
# retention is truncated by every deploy (confirmed: container up only
# since this morning's deploy) -- so historical gate outcomes cannot be
# read back. Instead this re-runs the REAL open_position_gate() function
# (imported directly, not reimplemented) against deterministically
# recomputed historical inputs:
#   - regime: real compute_market_regime(), walk-forward day-by-day over
#     real BTC daily klines (no lookahead -- day D uses only data
#     through day D).
#   - ADX per dominant_tf: real _adx(), rolling over real historical
#     klines for whichever TFs actually appear as mtf_dominant_tf in the
#     data.
#   - L2 direction/confidence: already persisted per-row, no recompute
#     needed.
# TIMING NOTE: predictions.ts is the candle's OPEN time, not close --
# the actual decision happens ~1 bar later, at close. shadow_trades.
# opened_at more plausibly records true decision time already. Both
# regime (daily cadence) and ADX (hourly) lookups use nearest-value-
# at-or-before the stored timestamp, so a ~1-bar timing offset only
# matters if a gate-relevant value flips inside that single bar --
# immaterial for daily regime, negligible for hourly ADX. Noted for
# rigor, not expected to change any conclusion here.
# 1(a) population: BTC/USDT 1h predictions, last 30d (confirmed via
# main.py grep: run_live_prediction() is called with zero args, i.e.
# BTC/USDT only -- that IS the entire live-dispatch-eligible population).
# 1(b) population: 60d shadow_trades at entry_score>=0.36 LONG (same
# filter as the established Part-3 methodology).
# ============================================================================

BINANCE_SPOT = "https://api.binance.com/api/v3/klines"


async def fetch_klines_df(client, symbol, interval, limit, end_ms=None):
    params = {"symbol": symbol, "interval": interval, "limit": str(limit)}
    if end_ms is not None:
        params["endTime"] = str(end_ms)
    resp = await client.get(BINANCE_SPOT, params=params, timeout=15.0)
    resp.raise_for_status()
    rows = resp.json()
    return pd.DataFrame({
        "ts": [int(r[0]) for r in rows],
        "open": [float(r[1]) for r in rows],
        "high": [float(r[2]) for r in rows],
        "low": [float(r[3]) for r in rows],
        "close": [float(r[4]) for r in rows],
    })


async def build_regime_by_date(client):
    from app.core.regime.market_regime import compute_market_regime, EMA_SLOW
    daily = await fetch_klines_df(client, "BTCUSDT", "1d", 1000)
    print(f"  regime series: fetched {len(daily)} daily BTC bars", flush=True)
    by_date: dict[object, str] = {}
    for i in range(EMA_SLOW + 2, len(daily)):
        window = daily.iloc[: i + 1]
        regime = compute_market_regime(window)
        day = pd.Timestamp(int(daily.iloc[i]["ts"]), unit="ms", tz="UTC").date()
        by_date[day] = regime
    distinct = sorted(set(by_date.values()))
    print(f"  regime series: {len(by_date)} days computed, distinct values={distinct}", flush=True)
    return by_date


async def build_adx_series(client, tf, needed_since_ms):
    from app.core.scoring.mtf_confluence import _adx, ADX_PERIOD
    # 1000 bars is the max single-call limit; walk back with endTime until
    # we cover needed_since_ms.
    all_rows: list[pd.DataFrame] = []
    end_ms = None
    for _ in range(6):  # up to 6*1000 bars of history -- generous margin
        df = await fetch_klines_df(client, "BTCUSDT", tf, 1000, end_ms=end_ms)
        if df.empty:
            break
        all_rows.append(df)
        oldest = int(df.iloc[0]["ts"])
        if oldest <= needed_since_ms:
            break
        end_ms = oldest - 1
        await asyncio.sleep(0.1)
    if not all_rows:
        return {}
    full = pd.concat(all_rows).drop_duplicates(subset="ts").sort_values("ts").reset_index(drop=True)
    highs = full["high"].to_numpy()
    lows = full["low"].to_numpy()
    closes = full["close"].to_numpy()
    series: dict[int, float] = {}
    for i in range(ADX_PERIOD + 2, len(full)):
        val = _adx(highs[: i + 1], lows[: i + 1], closes[: i + 1])
        series[int(full.iloc[i]["ts"])] = val
    return series


def nearest_before(series: dict[int, float], ts_ms: int) -> float | None:
    best_ts, best_val = None, None
    for t, v in series.items():
        if t <= ts_ms and (best_ts is None or t > best_ts):
            best_ts, best_val = t, v
    return best_val


def pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


async def main():
    from app.config import get_settings
    from app.core.gates.entry_quality import AllowDecision, open_position_gate

    real_settings = get_settings()
    print("=== deployed gate flags (real prod settings) ===", flush=True)
    print(f"  REGIME_GATE_ENABLED={real_settings.REGIME_GATE_ENABLED} "
          f"ADX_GATE_ENABLED={real_settings.ADX_GATE_ENABLED} "
          f"PATTERN_BOOST_ENABLED={real_settings.PATTERN_BOOST_ENABLED} "
          f"MIN_ENTRY_SCORE_LONG={real_settings.MIN_ENTRY_SCORE_LONG} "
          f"MIN_ADX_TREND_STRENGTH={real_settings.MIN_ADX_TREND_STRENGTH} "
          f"DISABLE_SHORT_SIGNALS={real_settings.DISABLE_SHORT_SIGNALS}", flush=True)

    db_url = __import__("os").environ["DATABASE_URL"]
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with httpx.AsyncClient() as client:
        print("\n=== building regime series (walk-forward, no lookahead) ===", flush=True)
        regime_by_date = await build_regime_by_date(client)

        # ---- 1(a): live population (BTC/USDT, 1h, last 30d) ------------
        async with session_factory() as session:
            rows_1a = (await session.execute(sa.text(
                """
                SELECT ts, final_score, direction, mtf_dominant_tf,
                       layer_scores -> '2' ->> 'direction' AS l2_dir,
                       (layer_scores -> '2' ->> 'confidence')::float AS l2_conf
                FROM predictions
                WHERE symbol = 'BTC/USDT' AND timeframe = '1h'
                  AND ts >= now() - interval '30 days'
                ORDER BY ts
                """
            ))).all()
        print(f"\n1(a) population: BTC/USDT 1h predictions, 30d -> n={len(rows_1a)}", flush=True)

        needed_tfs_1a = sorted({r.mtf_dominant_tf for r in rows_1a if r.mtf_dominant_tf})
        print(f"  distinct mtf_dominant_tf values present: {needed_tfs_1a}", flush=True)

        # ---- 1(b): shadow population (60d, LONG, entry_score>=0.36) ----
        async with session_factory() as session:
            rows_1b = (await session.execute(sa.text(
                """
                SELECT opened_at, entry_score, direction, mtf_dominant_tf,
                       pnl_usdt, pnl_pct,
                       layer_scores -> '2' ->> 'direction' AS l2_dir,
                       (layer_scores -> '2' ->> 'confidence')::float AS l2_conf
                FROM shadow_trades
                WHERE direction = 'LONG' AND entry_score >= 0.36
                  AND closed_at IS NOT NULL
                  AND opened_at >= now() - interval '60 days'
                ORDER BY opened_at
                """
            ))).all()
        print(f"\n1(b) population: shadow LONG entry_score>=0.36, 60d -> n={len(rows_1b)}", flush=True)

        needed_tfs_1b = sorted({r.mtf_dominant_tf for r in rows_1b if r.mtf_dominant_tf})
        print(f"  distinct mtf_dominant_tf values present: {needed_tfs_1b}", flush=True)

        all_needed_tfs = sorted(set(needed_tfs_1a) | set(needed_tfs_1b))
        min_ts_1a = min((int(r.ts.timestamp() * 1000) for r in rows_1a), default=None)
        min_ts_1b = min((int(r.opened_at.timestamp() * 1000) for r in rows_1b), default=None)
        needed_since_ms = min(x for x in (min_ts_1a, min_ts_1b) if x is not None)

        print(f"\n=== building ADX series for TFs: {all_needed_tfs} ===", flush=True)
        adx_series: dict[str, dict[int, float]] = {}
        for tf in all_needed_tfs:
            adx_series[tf] = await build_adx_series(client, tf, needed_since_ms)
            print(f"  {tf}: {len(adx_series[tf])} points computed", flush=True)
            await asyncio.sleep(0.2)

    class _Sig:
        def __init__(self, direction, entry_score, l2_dir, l2_conf, adx_map, dominant_tf):
            self.direction = direction
            self.entry_score = entry_score
            self.layer2_direction = l2_dir
            self.layer2_confidence = l2_conf
            self.mtf_adx_by_tf = adx_map
            self.mtf_dominant_tf = dominant_tf

    def eval_row(direction, entry_score, ts_dt, l2_dir, l2_conf, dominant_tf, *, regime_on, adx_on, pattern_on):
        adx_map = None
        if dominant_tf and dominant_tf in adx_series:
            ts_ms = int(ts_dt.timestamp() * 1000)
            v = nearest_before(adx_series[dominant_tf], ts_ms)
            if v is not None:
                adx_map = {dominant_tf: v}
        sig = _Sig(direction, entry_score, l2_dir, l2_conf, adx_map, dominant_tf)
        day = ts_dt.astimezone(timezone.utc).date()
        regime = regime_by_date.get(day) if regime_on else None

        class _S:
            REGIME_GATE_ENABLED = regime_on
            ADX_GATE_ENABLED = adx_on
            PATTERN_BOOST_ENABLED = pattern_on
            DISABLE_SHORT_SIGNALS = False  # isolate the 3 gates under test
            MIN_ENTRY_SCORE_LONG = 0.36
            MIN_ADX_TREND_STRENGTH = real_settings.MIN_ADX_TREND_STRENGTH
            REGIME_OPPOSITE_PATTERN_OVERRIDE = real_settings.REGIME_OPPOSITE_PATTERN_OVERRIDE
            PATTERN_BOOST_MIN_CONFIDENCE = real_settings.PATTERN_BOOST_MIN_CONFIDENCE
            PATTERN_BOOST_AMOUNT = real_settings.PATTERN_BOOST_AMOUNT
            PATTERN_PENALTY_AMOUNT = real_settings.PATTERN_PENALTY_AMOUNT

        return open_position_gate(sig, _S(), market_regime=regime)

    # ---------------- 1(a): size each gate + combined on LIVE pop -------
    print("\n" + "=" * 90, flush=True)
    print("1(a) — LIVE (BTC/USDT 1h, 30d) gate-block sizing, each gate isolated + combined", flush=True)
    print("=" * 90, flush=True)
    configs = [
        ("regime_only", True, False, False),
        ("adx_only", False, True, False),
        ("pattern_boost_only", False, False, True),
        ("all_three_combined", True, True, True),
    ]
    n_1a = len(rows_1a)
    for label, r_on, a_on, p_on in configs:
        blocked = 0
        reasons: dict[str, int] = {}
        for row in rows_1a:
            d = eval_row(
                row.direction, row.final_score, row.ts, row.l2_dir, row.l2_conf,
                row.mtf_dominant_tf, regime_on=r_on, adx_on=a_on, pattern_on=p_on,
            )
            if not d.allow:
                blocked += 1
                key = d.reason.split(":")[0] if d.reason else "unknown"
                reasons[key] = reasons.get(key, 0) + 1
        pct_blocked = 100 * blocked / n_1a if n_1a else float("nan")
        print(f"  {label:<22} blocked={blocked}/{n_1a} ({pct_blocked:.1f}%)  reasons={reasons}", flush=True)

    # ---------------- 1(b): counterfactual on SHADOW pop -----------------
    print("\n" + "=" * 90, flush=True)
    print("1(b) — SHADOW (LONG, entry_score>=0.36, 60d) counterfactual: would the live gates reject it?", flush=True)
    print("=" * 90, flush=True)
    would_reject = []
    would_pass = []
    for row in rows_1b:
        d = eval_row(
            row.direction, row.entry_score, row.opened_at, row.l2_dir, row.l2_conf,
            row.mtf_dominant_tf, regime_on=True, adx_on=True, pattern_on=True,
        )
        rec = {"pnl_usdt": row.pnl_usdt, "pnl_pct": row.pnl_pct, "reason": d.reason}
        (would_reject if not d.allow else would_pass).append(rec)

    n_1b = len(rows_1b)
    n_reject = len(would_reject)
    n_pass = len(would_pass)
    print(f"  n={n_1b}: would_reject={n_reject} ({100*n_reject/n_1b:.1f}%)  "
          f"would_pass={n_pass} ({100*n_pass/n_1b:.1f}%)", flush=True)

    reject_reasons: dict[str, int] = {}
    for r in would_reject:
        key = (r["reason"] or "unknown").split(":")[0]
        reject_reasons[key] = reject_reasons.get(key, 0) + 1
    print(f"  would_reject reason breakdown: {reject_reasons}", flush=True)

    for label, subset in (("would_reject", would_reject), ("would_pass", would_pass)):
        pnls = [r["pnl_usdt"] for r in subset if r["pnl_usdt"] is not None]
        pcts = [r["pnl_pct"] for r in subset if r["pnl_pct"] is not None]
        wr = 100 * sum(1 for p in pnls if p > 0) / len(pnls) if pnls else float("nan")
        avg_pnl_pct = st.mean(pcts) if pcts else float("nan")
        print(f"  {label:<14} n={len(subset):<5} WR={wr:.1f}%  avg_pnl_pct={avg_pnl_pct:+.4f}  "
              f"median_pnl_usdt={pct(pnls,.5):+.4f}", flush=True)


asyncio.run(main())
