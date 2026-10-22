import asyncio
import statistics as st
import sys

sys.path.insert(0, "/app")

import httpx

FUTURES_BASE = "https://fapi.binance.com"
FETCH_DELAY_S = 0.15
BAR_MS_4H = 4 * 3600_000
WINDOW_BARS = 186          # ~31 days of 4h bars (covers a clean 30d window)
FORWARD_BARS = 6           # 6 * 4h = 24h forward move
PRE_BARS = 6               # 24h pre-window for signal averaging
MOVER_MULT = 2.0           # |fwd_ret| >= 2x own median -> mover
CONTROL_MULT = 0.5         # |fwd_ret| <= 0.5x own median -> control
TOP_N_SYMBOLS = 40


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


async def fetch_funding(client, symbol, start_ms, end_ms):
    rows = await get_json(
        client, f"{FUTURES_BASE}/fapi/v1/fundingRate",
        params={"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
    )
    if not rows:
        return []
    return [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]


async def fetch_oi_hist(client, symbol):
    rows = await get_json(
        client, f"{FUTURES_BASE}/futures/data/openInterestHist",
        params={"symbol": symbol, "period": "4h", "limit": 500},
    )
    if not rows:
        return []
    return [(int(r["timestamp"]), float(r["sumOpenInterest"])) for r in rows]


async def fetch_taker_ratio(client, symbol):
    # /futures/data/takerbuyvolume + takersellvolume do NOT exist on
    # Binance's API (confirmed via direct curl 2026-08-06 -- both 404).
    # The real endpoint is takerlongshortRatio, a single call returning
    # buyVol + sellVol together. See fix/taker-buy-sell-ratio-endpoint.
    rows = await get_json(
        client, f"{FUTURES_BASE}/futures/data/takerlongshortRatio",
        params={"symbol": symbol, "period": "4h", "limit": 500},
    )
    if not rows:
        return []
    out = []
    for r in rows:
        ts = int(r["timestamp"])
        b = float(r["buyVol"])
        s = float(r["sellVol"])
        total = b + s
        if total > 0:
            out.append((ts, b / total))
    return out


def nearest_before(series, ts_ms, max_gap_ms=6 * 3600_000):
    """Last (ts, val) at or before ts_ms, within max_gap_ms. series sorted."""
    best = None
    for t, v in series:
        if t <= ts_ms:
            best = (t, v)
        else:
            break
    if best is None or ts_ms - best[0] > max_gap_ms:
        return None
    return best[1]


def window_mean(series, start_ms, end_ms):
    vals = [v for t, v in series if start_ms <= t <= end_ms]
    return st.mean(vals) if vals else None


async def analyze_symbol(client, symbol):
    klines = await fetch_klines(client, symbol)
    if len(klines) < WINDOW_BARS - 5:
        return []
    start_ms, end_ms = klines[0][0], klines[-1][0]

    funding = await fetch_funding(client, symbol, start_ms, end_ms)
    await asyncio.sleep(FETCH_DELAY_S)
    oi = await fetch_oi_hist(client, symbol)
    await asyncio.sleep(FETCH_DELAY_S)
    taker = await fetch_taker_ratio(client, symbol)
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
    for i in range(PRE_BARS, len(klines) - FORWARD_BARS):
        t_ms = klines[i][0]
        c0, c1 = closes[i], closes[i + FORWARD_BARS]
        if c0 <= 0:
            continue
        fwd_ret = abs((c1 - c0) / c0)
        ratio = fwd_ret / own_median
        if ratio >= MOVER_MULT:
            group = "mover"
        elif ratio <= CONTROL_MULT:
            group = "control"
        else:
            continue

        pre_start = klines[i - PRE_BARS][0]
        funding_mean = window_mean(funding, pre_start, t_ms)
        oi_now = nearest_before(oi, t_ms)
        oi_pre = nearest_before(oi, pre_start)
        oi_delta = None
        if oi_now is not None and oi_pre is not None and oi_pre > 0:
            oi_delta = (oi_now - oi_pre) / oi_pre
        taker_mean = window_mean(taker, pre_start, t_ms)

        results.append({
            "symbol": symbol, "group": group,
            "funding_mean": funding_mean,
            "oi_delta": oi_delta,
            "taker_ratio": taker_mean,
        })
    return results


async def main():
    async with httpx.AsyncClient() as client:
        universe = await pick_universe(client)
        print(f"universe: n={len(universe)} symbols", flush=True)
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
                      f"{len(all_results)} classified bars so far", flush=True)

        print(f"symbols skipped (insufficient data): {len(skipped)} -> {skipped}", flush=True)
        movers = [r for r in all_results if r["group"] == "mover"]
        controls = [r for r in all_results if r["group"] == "control"]
        print(f"classified bars: movers={len(movers)} controls={len(controls)} "
              f"(from {len(universe) - len(skipped)} usable symbols)", flush=True)

        for signal in ("funding_mean", "oi_delta", "taker_ratio"):
            mv = [r[signal] for r in movers if r[signal] is not None]
            ct = [r[signal] for r in controls if r[signal] is not None]
            print(f"\n=== {signal} ===", flush=True)
            print(f"  movers   n={len(mv)}  p25={pct(mv,.25):+.5f}  "
                  f"median={pct(mv,.50):+.5f}  p75={pct(mv,.75):+.5f}  "
                  f"mean={(st.mean(mv) if mv else float('nan')):+.5f}", flush=True)
            print(f"  control  n={len(ct)}  p25={pct(ct,.25):+.5f}  "
                  f"median={pct(ct,.50):+.5f}  p75={pct(ct,.75):+.5f}  "
                  f"mean={(st.mean(ct) if ct else float('nan')):+.5f}", flush=True)
            if mv and ct:
                # % of mover pre-window values with |signal| exceeding the
                # control group's 75th percentile of |signal| -- a simple,
                # dependency-free separation check (no scipy needed).
                ct_abs_p75 = pct([abs(v) for v in ct], 0.75)
                frac_mv_above = sum(1 for v in mv if abs(v) > ct_abs_p75) / len(mv)
                frac_ct_above = sum(1 for v in ct if abs(v) > ct_abs_p75) / len(ct)
                print(f"  separation check: {100*frac_mv_above:.1f}% of movers vs "
                      f"{100*frac_ct_above:.1f}% of controls exceed the control "
                      f"group's own |{signal}| p75 ({ct_abs_p75:.5f})", flush=True)


asyncio.run(main())
