import asyncio
import statistics
import sys

sys.path.insert(0, "/app")

import httpx

from app.data.ratelimit import RateLimitedClient, TokenBucket

# Replicates backend/app/data/futures_liquidity.py's check_liquidity +
# backend/app/shadow/live_fleet_universe.py's candidate-selection logic
# standalone, sweeping the DEPTH threshold only (qvol/spread thresholds
# held fixed, per operator instruction). Methodology matches
# docs/superpowers/decisions/2026-08-15-liquidity-floor-selector-supersedes-top20.md
# exactly: 3 samples per candidate ~4s apart, classify stable-pass (3/3)
# vs borderline (flips) vs stable-fail (0/3), so this run's $50k-tier
# numbers are directly comparable to that decision's reference table.

_FUTURES_BASE = "https://fapi.binance.com"
_SPOT_BASE = "https://api.binance.com"

QVOL_FLOOR_USDT = 20_000_000.0
SPREAD_MAX_BPS = 5.0
DEPTH_BAND = 0.005  # 0.5% of mid, both sides -- NOT swept, matches production
DEPTH_TIERS = [50_000.0, 25_000.0, 10_000.0, 5_000.0]
N_SAMPLES = 3
SAMPLE_GAP_SECONDS = 4.0
ORDER_SIZE_USDT = 500.0  # the operator's stated real trade size


def _rate_client(http: httpx.AsyncClient) -> RateLimitedClient:
    # Real Binance Futures weights for the two endpoints used here (per
    # Binance's own docs): ticker/24hr single-symbol=1, depth limit=100=5.
    # Matches the exact bucket config app.data.adapters.binance_futures_
    # intermarket._default_rate_client uses for this same exchange.
    return RateLimitedClient(
        exchange="binance_futures",
        http=http,
        buckets={"default": TokenBucket(capacity=2400, refill_per_sec=40.0)},
        endpoint_weights={"ticker24hr_bulk": 40, "depth": 5},
        sync_header="X-MBX-USED-WEIGHT-1M",
        sync_capacity=2400.0,
    )


def _walk_book(levels: list[tuple[float, float]], order_usdt: float) -> float | None:
    """Average fill price for a market order of size order_usdt, walking
    real order-book levels from best price outward. Returns None if the
    order exceeds all fetched levels (order too large for this snapshot's
    depth -- did not happen in practice at $500 against any candidate,
    but handled honestly rather than silently)."""
    remaining = order_usdt
    cost_usdt = 0.0
    qty_taken = 0.0
    for price, qty in levels:
        if remaining <= 0:
            break
        level_usdt = price * qty
        take_usdt = min(remaining, level_usdt)
        qty_taken += take_usdt / price
        cost_usdt += take_usdt
        remaining -= take_usdt
    if qty_taken <= 0 or remaining > 0:
        return None
    return cost_usdt / qty_taken


async def sample_one(symbol: str, rate_client: RateLimitedClient) -> dict | None:
    """One real sample: order book only (qvol comes from the bulk ticker
    fetched once up front -- 24h rolling volume does not flicker second
    to second the way spread/depth do, so refetching it 3x per candidate
    would triple the cost for no real signal; this deviates slightly from
    check_liquidity's per-call refetch but is the right tradeoff for a
    stability-sampling run, not a correctness issue)."""
    try:
        resp = await rate_client.request(
            "GET", f"{_FUTURES_BASE}/fapi/v1/depth",
            endpoint_key="depth", params={"symbol": symbol, "limit": "100"}, timeout=10.0,
        )
        resp.raise_for_status()
        book = resp.json()
        bids = [(float(p), float(q)) for p, q in book["bids"]]
        asks = [(float(p), float(q)) for p, q in book["asks"]]
        if not bids or not asks:
            return None
        best_bid, best_ask = bids[0][0], asks[0][0]
        mid = (best_bid + best_ask) / 2
        spread_bps = (best_ask - best_bid) / mid * 10_000

        lo, hi = mid * (1 - DEPTH_BAND), mid * (1 + DEPTH_BAND)
        bid_depth = sum(p * q for p, q in bids if p >= lo)
        ask_depth = sum(p * q for p, q in asks if p <= hi)
        depth_0_5pct_usdt = bid_depth + ask_depth

        buy_avg = _walk_book(asks, ORDER_SIZE_USDT)
        sell_avg = _walk_book(bids, ORDER_SIZE_USDT)
        buy_impact_bps = (
            (buy_avg - mid) / mid * 10_000 if buy_avg is not None else None
        )
        sell_impact_bps = (
            (mid - sell_avg) / mid * 10_000 if sell_avg is not None else None
        )
        return {
            "spread_bps": spread_bps,
            "depth_0_5pct_usdt": depth_0_5pct_usdt,
            "buy_impact_bps": buy_impact_bps,
            "sell_impact_bps": sell_impact_bps,
        }
    except Exception as e:  # noqa: BLE001
        print(f"  sample failed for {symbol}: {e}", flush=True)
        return None


async def sample_candidate(symbol: str, rate_client: RateLimitedClient) -> list[dict]:
    samples = []
    for i in range(N_SAMPLES):
        s = await sample_one(symbol, rate_client)
        if s is not None:
            samples.append(s)
        if i < N_SAMPLES - 1:
            await asyncio.sleep(SAMPLE_GAP_SECONDS)
    return samples


async def main() -> None:
    async with httpx.AsyncClient(timeout=15.0) as http:
        rate_client = _rate_client(http)

        fut_resp = await http.get(f"{_FUTURES_BASE}/fapi/v1/exchangeInfo")
        fut_resp.raise_for_status()
        fut_symbols = {
            s["symbol"] for s in fut_resp.json()["symbols"]
            if s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
        }

        spot_resp = await http.get(f"{_SPOT_BASE}/api/v3/exchangeInfo")
        spot_resp.raise_for_status()
        spot_symbols = {
            s["symbol"] for s in spot_resp.json()["symbols"]
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
        }
        futures_only_set = fut_symbols - spot_symbols
        spot_backed_set = fut_symbols & spot_symbols

        print(
            f"Full USDT-M perpetual market: {len(fut_symbols)} symbols -> "
            f"{len(spot_backed_set)} spot-backed, {len(futures_only_set)} futures-only "
            f"(2026-08-15 reference: 527 -> 358 / 169)",
            flush=True,
        )

        ticker_resp = await rate_client.request(
            "GET", f"{_FUTURES_BASE}/fapi/v1/ticker/24hr",
            endpoint_key="ticker24hr_bulk", timeout=15.0,
        )
        ticker_resp.raise_for_status()
        qvol_by_symbol = {t["symbol"]: float(t["quoteVolume"]) for t in ticker_resp.json()}

        candidates = sorted(
            s for s in fut_symbols if qvol_by_symbol.get(s, 0.0) >= QVOL_FLOOR_USDT
        )
        print(
            f"Clear the ${QVOL_FLOOR_USDT:,.0f} volume pre-filter: {len(candidates)} "
            f"(2026-08-15 reference: 73)",
            flush=True,
        )

        print(f"Sampling {len(candidates)} candidates, {N_SAMPLES}x each, "
              f"~{SAMPLE_GAP_SECONDS:.0f}s apart -- this takes a few minutes...", flush=True)

        results = await asyncio.gather(
            *(sample_candidate(sym, rate_client) for sym in candidates)
        )
        by_symbol = dict(zip(candidates, results))

        # ---- Depth-tier sweep (holding qvol + spread fixed) -------------
        print("\n=== DEPTH THRESHOLD SWEEP (qvol >= $20M, spread <= 5bps fixed) ===", flush=True)
        for tier in DEPTH_TIERS:
            counts = {
                "total": {"stable_pass": 0, "borderline": 0, "stable_fail": 0, "no_data": 0},
                "spot_backed": {"stable_pass": 0, "borderline": 0, "stable_fail": 0, "no_data": 0},
                "futures_only": {"stable_pass": 0, "borderline": 0, "stable_fail": 0, "no_data": 0},
            }
            for sym in candidates:
                samples = by_symbol[sym]
                bucket = "spot_backed" if sym in spot_backed_set else "futures_only"
                if not samples:
                    counts["total"]["no_data"] += 1
                    counts[bucket]["no_data"] += 1
                    continue
                pass_count = sum(
                    1 for s in samples
                    if s["spread_bps"] <= SPREAD_MAX_BPS and s["depth_0_5pct_usdt"] >= tier
                )
                if pass_count == len(samples):
                    klass = "stable_pass"
                elif pass_count == 0:
                    klass = "stable_fail"
                else:
                    klass = "borderline"
                counts["total"][klass] += 1
                counts[bucket][klass] += 1
            print(f"\n-- depth >= ${tier:,.0f} --", flush=True)
            for scope in ("total", "spot_backed", "futures_only"):
                c = counts[scope]
                print(
                    f"  {scope:14s} stable_pass={c['stable_pass']:3d}  "
                    f"borderline={c['borderline']:3d}  stable_fail={c['stable_fail']:3d}  "
                    f"no_data={c['no_data']:3d}",
                    flush=True,
                )

        # ---- $500 order calm-book price impact, bucketed by real depth --
        print(f"\n=== ${ORDER_SIZE_USDT:.0f} MARKET ORDER PRICE IMPACT (real order-book walk, calm conditions) ===", flush=True)
        buckets = [
            (">=50k", lambda d: d >= 50_000),
            ("25k-50k", lambda d: 25_000 <= d < 50_000),
            ("10k-25k", lambda d: 10_000 <= d < 25_000),
            ("5k-10k", lambda d: 5_000 <= d < 10_000),
            ("<5k", lambda d: d < 5_000),
        ]
        all_samples = [s for samples in by_symbol.values() for s in samples]
        for label, pred in buckets:
            bucket_samples = [s for s in all_samples if pred(s["depth_0_5pct_usdt"])]
            buys = [s["buy_impact_bps"] for s in bucket_samples if s["buy_impact_bps"] is not None]
            sells = [s["sell_impact_bps"] for s in bucket_samples if s["sell_impact_bps"] is not None]
            if not buys and not sells:
                print(f"  depth {label:10s} n=0 (no samples in this range)", flush=True)
                continue
            buy_med = statistics.median(buys) if buys else float("nan")
            sell_med = statistics.median(sells) if sells else float("nan")
            buy_max = max(buys) if buys else float("nan")
            sell_max = max(sells) if sells else float("nan")
            print(
                f"  depth {label:10s} n={len(bucket_samples):4d}  "
                f"buy: median={buy_med:.1f}bps max={buy_max:.1f}bps  "
                f"sell: median={sell_med:.1f}bps max={sell_max:.1f}bps",
                flush=True,
            )

        # ---- Simple formula cross-check, at the tier thresholds themselves
        print(f"\n=== Back-of-envelope cross-check (order_usdt / depth_usdt, at the exact tier value) ===", flush=True)
        for tier in DEPTH_TIERS:
            frac = ORDER_SIZE_USDT / tier
            print(
                f"  depth=${tier:,.0f}: ${ORDER_SIZE_USDT:.0f} order consumes "
                f"{frac*100:.2f}% of within-0.5%-band depth", flush=True,
            )

        print(
            "\n=== WHAT THIS CANNOT MEASURE ===\n"
            "All numbers above are RESTING, CALM-BOOK depth -- captured whatever "
            "market conditions existed at the moment each sample ran. Depth during "
            "a fast move (news, liquidation cascade, flash crash) is NOT this "
            "measurement, and does not scale down proportionally with order size "
            "the way calm-book impact does: market makers widen spreads and pull "
            "resting orders precisely when volatility spikes, so a thin book gets "
            "thinner exactly when it matters most. A $500 order that walks 2bps "
            "against a calm $50k-depth book could walk far more than 2bps against "
            "the SAME symbol's book mid-cascade. This risk cannot be captured by "
            "resampling more, sampling for longer, or any variant of this "
            "methodology -- it requires either live capture during an actual fast "
            "move (opportunistic, not repeatable on demand) or an entirely "
            "different data source (e.g. historical order-book snapshots around "
            "known volatility events, if such a dataset were built separately).",
            flush=True,
        )


asyncio.run(main())
