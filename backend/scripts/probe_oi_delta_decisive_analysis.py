import asyncio
import statistics as st
import sys

sys.path.insert(0, "/app")

import httpx

# DATA PROVENANCE: every fetch below hits Binance Futures REST directly
# (fapi.binance.com). No DB/DATABASE_URL is touched anywhere in this
# script. Our own OI collector (intermarket_snapshots / flow_feature_
# snapshots) only started working 2026-08-05 and has far too little
# history for a 31-day retrospective window -- this deliberately bypasses
# it entirely.
FUTURES_BASE = "https://fapi.binance.com"
FETCH_DELAY_S = 0.15
WINDOW_BARS = 186        # ~31 days of 4h bars
FORWARD_BARS = 6         # 6*4h = 24h forward move (mover/non-mover label)
PRE_BARS_FULL = 6        # 24h pre-window, ending AT the decision bar t
PRE_BARS_HALF = 3        # 12h split point
MOVER_MULT = 2.0         # |fwd_ret| >= 2x own 30d median -> "mover"
TOP_N_SYMBOLS = 40
OI_THRESHOLDS = [0.02, 0.03, 0.05, 0.08, 0.12]


async def get_json(client, url, params=None, retries=3):
    for attempt in range(retries):
        try:
            resp = await client.get(url, params=params, timeout=15.0)
            if resp.status_code == 429:
                await asyncio.sleep(1.0 + attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception:
            if attempt == retries - 1:
                return None
            await asyncio.sleep(0.5)
    return None


def pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


async def pick_universe(client):
    from app.config import get_settings
    from app.trading.symbol_allowlist import is_stablecoin_pair
    settings = get_settings()

    tickers = await get_json(client, f"{FUTURES_BASE}/fapi/v1/ticker/24hr")
    if not tickers:
        return []
    usdt = [t for t in tickers if t["symbol"].endswith("USDT")]
    usdt.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
    out = []
    for t in usdt:
        sym = t["symbol"]
        if is_stablecoin_pair(sym, settings):
            continue
        out.append(sym)
        if len(out) >= TOP_N_SYMBOLS:
            break
    return out


async def fetch_klines(client, symbol):
    rows = await get_json(
        client, f"{FUTURES_BASE}/fapi/v1/klines",
        params={"symbol": symbol, "interval": "4h", "limit": WINDOW_BARS},
    )
    if not rows:
        return []
    return [(int(r[0]), float(r[4])) for r in rows]  # (open_time, close)


async def fetch_oi_hist(client, symbol):
    # DIRECT Binance REST -- NOT our own DB table. Confirms item 1(d).
    rows = await get_json(
        client, f"{FUTURES_BASE}/futures/data/openInterestHist",
        params={"symbol": symbol, "period": "4h", "limit": 500},
    )
    if not rows:
        return []
    return [(int(r["timestamp"]), float(r["sumOpenInterest"])) for r in rows]


def nearest_before(series, ts_ms, max_gap_ms=6 * 3600_000):
    best = None
    for t, v in series:
        if t <= ts_ms:
            best = (t, v)
        else:
            break
    if best is None or ts_ms - best[0] > max_gap_ms:
        return None
    return best[1]


async def analyze_symbol(client, symbol):
    klines = await fetch_klines(client, symbol)
    if len(klines) < WINDOW_BARS - 5:
        return []
    oi = await fetch_oi_hist(client, symbol)
    await asyncio.sleep(FETCH_DELAY_S)

    closes = [c for _, c in klines]
    fwd_rets = []
    for i in range(len(klines) - FORWARD_BARS):
        c0, c1 = closes[i], closes[i + FORWARD_BARS]
        if c0 > 0:
            fwd_rets.append(abs((c1 - c0) / c0))
    if not fwd_rets:
        return []
    own_median = st.median(fwd_rets)
    if own_median <= 0:
        return []

    results = []
    for i in range(PRE_BARS_FULL, len(klines) - FORWARD_BARS):
        t_ms = klines[i][0]
        c0, c1 = closes[i], closes[i + FORWARD_BARS]
        if c0 <= 0:
            continue
        fwd_ret = abs((c1 - c0) / c0)
        ratio = fwd_ret / own_median
        is_mover = ratio >= MOVER_MULT

        t_minus_24 = klines[i - PRE_BARS_FULL][0]
        t_minus_12 = klines[i - PRE_BARS_HALF][0]
        oi_now = nearest_before(oi, t_ms)
        oi_m12 = nearest_before(oi, t_minus_12)
        oi_m24 = nearest_before(oi, t_minus_24)

        delta_0_24 = None
        delta_0_12 = None
        delta_12_24 = None
        if oi_now is not None and oi_m24 is not None and oi_m24 > 0:
            delta_0_24 = (oi_now - oi_m24) / oi_m24
        if oi_now is not None and oi_m12 is not None and oi_m12 > 0:
            delta_0_12 = (oi_now - oi_m12) / oi_m12
        if oi_m12 is not None and oi_m24 is not None and oi_m24 > 0:
            delta_12_24 = (oi_m12 - oi_m24) / oi_m24

        results.append({
            "symbol": symbol, "is_mover": is_mover,
            "delta_0_24": delta_0_24,
            "delta_0_12": delta_0_12,
            "delta_12_24": delta_12_24,
        })
    return results


def sweep(mv, nm, prevalence, thresholds):
    for T in thresholds:
        if not mv or not nm:
            continue
        recall = sum(1 for v in mv if v > T) / len(mv)
        fpr = sum(1 for v in nm if v > T) / len(nm)
        num = recall * prevalence
        den = num + fpr * (1 - prevalence)
        precision = num / den if den > 0 else float("nan")
        print(
            f"    T=oi_delta>{T:+.2f}: recall={100*recall:5.1f}%  "
            f"FPR={100*fpr:5.1f}%  precision={100*precision:5.1f}%",
            flush=True,
        )


async def main():
    async with httpx.AsyncClient() as client:
        universe = await pick_universe(client)
        print(f"universe: n={len(universe)} symbols (top-{TOP_N_SYMBOLS} Binance "
              f"Futures USDT perps by 24h quoteVolume, stablecoins excluded)",
              flush=True)
        if not universe:
            print("ABORT: could not fetch universe", flush=True)
            return

        all_results = []
        skipped = []
        for idx, sym in enumerate(universe):
            rows = await analyze_symbol(client, sym)
            if not rows:
                skipped.append(sym)
            else:
                all_results.extend(rows)
            await asyncio.sleep(FETCH_DELAY_S)
            if (idx + 1) % 10 == 0:
                print(f"  processed {idx + 1}/{len(universe)} symbols, "
                      f"{len(all_results)} bars so far", flush=True)

        print(f"symbols skipped (insufficient data): {len(skipped)} -> {skipped}",
              flush=True)

        n_total = len(all_results)
        movers = [r for r in all_results if r["is_mover"]]
        non_movers = [r for r in all_results if not r["is_mover"]]
        prevalence = len(movers) / n_total if n_total else float("nan")
        print(
            f"\nTRUE population (ALL bars, no exclusion): n={n_total}  "
            f"movers={len(movers)} ({100*prevalence:.2f}%)  "
            f"non-movers={len(non_movers)} ({100*(1-prevalence):.2f}%)",
            flush=True,
        )
        print(
            "mover definition: |24h fwd return| >= 2.0x that symbol's own "
            "30d median |24h fwd return|. This prevalence is defined "
            "relative to THIS threshold choice, not an independent "
            "ground-truth of 'big move' -- stated explicitly per the "
            "operator's request for the real base rate this test uses.",
            flush=True,
        )

        windows = [
            ("delta_0_24  (FULL 24h pre-window, ending AT decision bar t)", "delta_0_24"),
            ("delta_0_12  (LATE 12h: t-12h -> t, closest to decision point)", "delta_0_12"),
            ("delta_12_24 (EARLY 12h: t-24h -> t-12h, true lead-time window)", "delta_12_24"),
        ]

        for label, key in windows:
            mv = [r[key] for r in movers if r[key] is not None]
            nm = [r[key] for r in non_movers if r[key] is not None]
            print(f"\n=== {label} ===", flush=True)
            print(
                f"  movers      n={len(mv)}  p10={pct(mv,.10):+.4f} p25={pct(mv,.25):+.4f} "
                f"median={pct(mv,.50):+.4f} p75={pct(mv,.75):+.4f} p90={pct(mv,.90):+.4f}",
                flush=True,
            )
            print(
                f"  non-movers  n={len(nm)}  p10={pct(nm,.10):+.4f} p25={pct(nm,.25):+.4f} "
                f"median={pct(nm,.50):+.4f} p75={pct(nm,.75):+.4f} p90={pct(nm,.90):+.4f}",
                flush=True,
            )
            if mv and nm:
                # overlap: % of movers below the non-movers' median (crude
                # but intuitive overlap indicator -- 50% = no separation)
                nm_median = pct(nm, .50)
                frac_mv_below_nm_median = sum(1 for v in mv if v <= nm_median) / len(mv)
                print(
                    f"  overlap: {100*frac_mv_below_nm_median:.1f}% of movers fall AT OR "
                    f"BELOW the non-movers' own median ({nm_median:+.4f}) "
                    f"-- 50% would mean zero separation",
                    flush=True,
                )
            print("  THRESHOLD SWEEP (recall / false-positive-rate / precision "
                  f"@ prevalence={100*prevalence:.2f}%):", flush=True)
            sweep(mv, nm, prevalence, OI_THRESHOLDS)


asyncio.run(main())
