import asyncio
import sys

sys.path.insert(0, "/app")

import httpx

# Structural guard for item 3(b): a reusable, on-demand check of every
# distinct external REST endpoint this app depends on. Built after
# finding THREE separate instances this week of a wrong Binance path
# 404ing silently for a feature's entire life (both taker-ratio
# endpoints, PR #408; the OI endpoint in flow_features.py, PR #409).
# Each entry below is (name, method, url, params, expected_keys).
# expected_keys are top-level (or first-row) keys the calling code
# actually reads -- a 200 with a missing/renamed key is caught same as
# a straight 404.

ENDPOINTS = [
    ("spot klines", "https://api.binance.com/api/v3/klines",
     {"symbol": "BTCUSDT", "interval": "1h", "limit": "2"}, "[0][4]"),
    ("spot exchangeInfo", "https://api.binance.com/api/v3/exchangeInfo",
     {}, "symbols"),
    ("spot ticker/price (single)", "https://api.binance.com/api/v3/ticker/price",
     {"symbol": "BTCUSDT"}, "price"),
    ("spot ticker/24hr", "https://api.binance.com/api/v3/ticker/24hr",
     {"symbol": "BTCUSDT"}, "quoteVolume"),
    ("futures klines", "https://fapi.binance.com/fapi/v1/klines",
     {"symbol": "BTCUSDT", "interval": "1h", "limit": "2"}, "[0][4]"),
    ("futures exchangeInfo", "https://fapi.binance.com/fapi/v1/exchangeInfo",
     {}, "symbols"),
    ("futures premiumIndex", "https://fapi.binance.com/fapi/v1/premiumIndex",
     {"symbol": "BTCUSDT"}, "lastFundingRate"),
    ("futures ping", "https://fapi.binance.com/fapi/v1/ping", {}, None),
    ("futures OI hist (correct path)",
     "https://fapi.binance.com/futures/data/openInterestHist",
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
     {"category": "linear", "symbol": "BTCUSDT", "interval": "60", "limit": "2"},
     "result.list"),
    ("bybit instruments-info", "https://api.bybit.com/v5/market/instruments-info",
     {"category": "linear", "symbol": "BTCUSDT"}, "result.list"),
    ("fear & greed index", "https://api.alternative.me/fng/", {}, "data[0].value"),
]


async def check_json_endpoint(client, name, url, params, expected):
    try:
        resp = await client.get(url, params=params, timeout=10.0)
    except Exception as e:  # noqa: BLE001
        return (name, "ERROR", str(e)[:120], False)

    status = resp.status_code
    if status != 200:
        return (name, str(status), resp.text[:120].replace("\n", " "), False)

    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return (name, "200", "non-JSON body", False)

    if expected is None:
        return (name, "200", "OK (no shape check)", True)

    try:
        cursor = data
        parts = [p for p in expected.replace("]", "").replace("[", ".").split(".") if p]
        for part in parts:
            if part.isdigit():
                cursor = cursor[int(part)]
            else:
                cursor = cursor[part]
        return (name, "200", f"shape OK ({expected} present)", True)
    except Exception:  # noqa: BLE001
        return (name, "200", f"SHAPE MISMATCH -- {expected} missing", False)


async def check_exchangerate_host(client):
    # Known-broken (item-3 audit, 2026-08-06): 200 with an error BODY,
    # not a 404 -- requires a paid access_key the code never sends.
    # raise_for_status() never fires on this one; only a body check catches it.
    try:
        resp = await client.get(
            "https://api.exchangerate.host/latest",
            params={"base": "USD", "symbols": "INR"}, timeout=10.0,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("success") is False:
            return ("exchangerate.host (INR FX)", "200", (
                f"SILENT DEGRADATION -- 200 OK but body is an error: "
                f"{data.get('error')}"
            ), False)
        if resp.status_code == 200 and "rates" in data and "INR" in data.get("rates", {}):
            return ("exchangerate.host (INR FX)", "200", "shape OK", True)
        return ("exchangerate.host (INR FX)", str(resp.status_code),
                str(data)[:120], False)
    except Exception as e:  # noqa: BLE001
        return ("exchangerate.host (INR FX)", "ERROR", str(e)[:120], False)


async def check_cryptopanic(client):
    # Known-blocked (item-3 audit): Cloudflare 403 to plain httpx regardless
    # of UA/token. Report as an explicit known-limitation, not pass/fail.
    try:
        from app.config import get_settings
        token = getattr(get_settings(), "CRYPTOPANIC_API_KEY", None)
    except Exception:  # noqa: BLE001
        token = None
    try:
        resp = await client.get(
            "https://cryptopanic.com/api/v1/posts/",
            params={"auth_token": token or "unset", "public": "true"},
            timeout=10.0,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp.status_code == 403:
            return ("cryptopanic.com", "403 (Cloudflare)",
                    "KNOWN LIMITATION -- cannot verify via plain httpx from "
                    "outside the app; not counted as a failure", None)
        if resp.status_code == 200:
            return ("cryptopanic.com", "200", "reachable + 200", True)
        return ("cryptopanic.com", str(resp.status_code), resp.text[:120], False)
    except Exception as e:  # noqa: BLE001
        return ("cryptopanic.com", "ERROR", str(e)[:120], False)


async def check_yahoo_rss():
    try:
        import feedparser
    except ImportError:
        return ("Yahoo RSS feeds", "SKIP", "feedparser not importable here", None)
    try:
        from app.news.adapters.yahoo_rss import FEED_URLS  # type: ignore[attr-defined]
    except Exception:
        FEED_URLS = [
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US",
        ]
    ok = True
    detail = []
    for url in FEED_URLS[:2]:
        parsed = feedparser.parse(url)
        good = (not parsed.bozo) and getattr(parsed, "status", 200) == 200 and len(parsed.entries) > 0
        ok = ok and good
        detail.append(f"{url.split('?')[0].split('/')[-1]}={'OK' if good else 'FAIL'}")
    return ("Yahoo RSS feeds", "n/a (feedparser)", ", ".join(detail), ok)


async def main():
    results = []
    async with httpx.AsyncClient() as client:
        for name, url, params, expected in ENDPOINTS:
            results.append(await check_json_endpoint(client, name, url, params, expected))
            await asyncio.sleep(0.1)
        results.append(await check_exchangerate_host(client))
        results.append(await check_cryptopanic(client))
    results.append(await check_yahoo_rss())

    print(f"{'ENDPOINT':<38} {'STATUS':<16} {'RESULT':<50} PASS", flush=True)
    print("-" * 118, flush=True)
    n_fail = 0
    n_unverified = 0
    for name, status, detail, ok in results:
        if ok is True:
            mark = "PASS"
        elif ok is False:
            mark = "FAIL"
            n_fail += 1
        else:
            mark = "UNVERIFIED"
            n_unverified += 1
        print(f"{name:<38} {status:<16} {detail:<50} {mark}", flush=True)

    print(f"\n{len(results)} endpoints checked: "
          f"{len(results) - n_fail - n_unverified} PASS, "
          f"{n_fail} FAIL, {n_unverified} UNVERIFIED (known limitation, not a failure)",
          flush=True)
    if n_fail:
        print(f"\nFAILING LOUDLY: {n_fail} endpoint(s) broken.", flush=True)
        sys.exit(1)


asyncio.run(main())
