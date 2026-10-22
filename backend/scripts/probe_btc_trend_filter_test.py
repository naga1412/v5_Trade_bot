import asyncio
import os
import statistics as st
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# =====================================================================
# BTC trend filter test — pre-registered 2026-10-20 in
# docs/superpowers/decisions/2026-10-20-btc-trend-filter-preregistration.md
# (committed to main BEFORE this probe existed).
#
# VARIABLE: BTCUSDT above/below its own trailing 20-day SMA at each
# trade's ENTRY, from daily closes whose CLOSE TIME is strictly before
# the entry. No lookahead. Monthly BTC drift is inadmissible (only
# knowable after the month ends, so untradeable) and is not used.
#
# REPORTING ORDER IS FIXED by the pre-registration: cell sizes, then
# the computed bar, then the point estimates.
# =====================================================================

BINANCE = "https://api.binance.com/api/v3/klines"
MA_LEN = 20
START = "2026-05-01"
# asyncpg binds parameters by type and will NOT coerce a date string.
# Pass a real datetime (same class of bug as the shadow-variant
# rolling-report :window defect).
START_DT = datetime(2026, 5, 1, tzinfo=timezone.utc)


async def fetch_daily(client):
    r = await client.get(
        BINANCE,
        params={"symbol": "BTCUSDT", "interval": "1d", "limit": "1000"},
        timeout=20.0,
    )
    r.raise_for_status()
    # index 6 is CLOSE time; index 4 is close price
    return [(int(x[6]), float(x[4])) for x in r.json()]


def build_trend_series(daily):
    out = []
    for i in range(MA_LEN - 1, len(daily)):
        window = [c for _, c in daily[i - MA_LEN + 1: i + 1]]
        ma = sum(window) / MA_LEN
        ct, close = daily[i]
        out.append((ct, close, ma, "uptrend" if close > ma else "downtrend"))
    return out


def state_at(series, ts_ms):
    """State from the latest daily bar CLOSED strictly before ts_ms."""
    res = None
    for ct, _close, _ma, state in series:
        if ct < ts_ms:
            res = state
        else:
            break
    return res


def cell(vals):
    n = len(vals)
    if n < 2:
        return n, float("nan"), float("nan"), float("nan")
    m = st.mean(vals)
    sd = st.stdev(vals)
    return n, m, sd, sd / (n ** 0.5)


def report(label, up, dn, *, primary_diff=None, primary_se=None):
    n_up, m_up, sd_up, se_up = cell(up)
    n_dn, m_dn, sd_dn, se_dn = cell(dn)
    if n_up < 2 or n_dn < 2:
        print(f"  {label}: insufficient cells (up={n_up} down={n_dn})", flush=True)
        return None, None
    se_diff = (se_up ** 2 + se_dn ** 2) ** 0.5
    bar = 3.0 * se_diff

    # --- 1. CELL SIZES ---
    print(f"  {label}  CELLS      n_uptrend={n_up}  n_downtrend={n_dn}", flush=True)
    print(f"  {label}  SPREAD     sd_up={sd_up:.3f}%  sd_down={sd_dn:.3f}%  "
          f"SE_diff={se_diff:.4f}%", flush=True)
    # --- 2. THE BAR, before any point estimate ---
    print(f"  {label}  BAR        3-sigma on the difference = {bar:.4f}%", flush=True)
    # --- 3. POINT ESTIMATES ---
    diff = m_up - m_dn
    sigma = diff / se_diff
    verdict = "PASS" if abs(diff) >= bar else "FAIL"
    print(f"  {label}  ESTIMATE   mean_uptrend={m_up:+.4f}%  "
          f"mean_downtrend={m_dn:+.4f}%", flush=True)
    print(f"  {label}  RESULT     diff={diff:+.4f}%  -> {sigma:+.2f} sigma  "
          f"vs bar {bar:.4f}%  ==> {verdict}", flush=True)
    if primary_diff is not None:
        same_sign = (diff >= 0) == (primary_diff >= 0)
        within = abs(diff - primary_diff) <= 2.0 * primary_se
        print(f"  {label}  REPLICATION same_sign={same_sign} "
              f"within_2_SE_diff_of_primary={within}", flush=True)
    return diff, se_diff


async def main():
    async with httpx.AsyncClient() as client:
        daily = await fetch_daily(client)
    series = build_trend_series(daily)
    d0 = datetime.fromtimestamp(series[0][0] / 1000, timezone.utc).date()
    d1 = datetime.fromtimestamp(series[-1][0] / 1000, timezone.utc).date()
    n_up_days = sum(1 for _, _, _, s in series if s == "uptrend")
    print("=== BTC TREND FILTER TEST (pre-registered 2026-10-20) ===", flush=True)
    print(f"BTC daily bars fetched={len(daily)}  MA{MA_LEN} series points={len(series)}  "
          f"span {d0} .. {d1}", flush=True)
    print(f"trend-day balance: uptrend={n_up_days} downtrend={len(series) - n_up_days}",
          flush=True)

    eng = create_async_engine(os.environ["DATABASE_URL"])
    sf = async_sessionmaker(eng, expire_on_commit=False)
    async with sf() as s:
        rows = (await s.execute(sa.text(
            "SELECT direction, pnl_pct, opened_at, closed_at FROM shadow_trades "
            "WHERE closed_at IS NOT NULL AND pnl_pct IS NOT NULL "
            "AND opened_at >= :start ORDER BY closed_at"
        ), {"start": START_DT})).all()
    await eng.dispose()
    print(f"closed trades from {START}: n={len(rows)}", flush=True)

    tagged = []
    unlabelled = 0
    for r in rows:
        ts_ms = int(r.opened_at.timestamp() * 1000)
        stt = state_at(series, ts_ms)
        if stt is None:
            unlabelled += 1
            continue
        tagged.append((r.direction, float(r.pnl_pct), stt, r.closed_at, r.opened_at))
    print(f"labelled={len(tagged)}  unlabelled(no 20 prior closes)={unlabelled}", flush=True)

    # ---- PROXY FAITHFULNESS DIAGNOSTIC (added 2026-10-21) ----
    # Does the MA20 label actually track monthly drift? If trades in
    # up-drift months are predominantly labelled uptrend, the proxy is
    # faithful and the trade-level test genuinely tested the monthly
    # hypothesis. If labels scramble across months, the test measured
    # something adjacent. Monthly drift here is POST-HOC and is used only
    # to characterise the proxy -- it is never a test variable.
    from collections import defaultdict

    month_days = defaultdict(lambda: {"up": 0, "dn": 0, "flips": 0,
                                      "first": None, "last": None})
    prev_state = None
    for ct, close, _ma, state in series:
        dd = datetime.fromtimestamp(ct / 1000, timezone.utc)
        key = f"{dd.year:04d}-{dd.month:02d}"
        md = month_days[key]
        md["up" if state == "uptrend" else "dn"] += 1
        if md["first"] is None:
            md["first"] = close
        md["last"] = close
        if prev_state is not None and state != prev_state:
            md["flips"] += 1
        prev_state = state

    month_trades = defaultdict(lambda: {"up": 0, "dn": 0})
    for _d, _p, stt2, _c, opened in tagged:
        key = f"{opened.year:04d}-{opened.month:02d}"
        month_trades[key]["up" if stt2 == "uptrend" else "dn"] += 1

    print("\n" + "=" * 78, flush=True)
    print("PROXY FAITHFULNESS -- MA20 label vs calendar month", flush=True)
    print("=" * 78, flush=True)
    hdr = ("month".ljust(9) + "btc_drift".rjust(11) + "ma_flips".rjust(9)
           + "days_up/tot".rjust(13) + "trades_up".rjust(11)
           + "trades_dn".rjust(11) + "share_up".rjust(10))
    print(hdr, flush=True)
    for key in sorted(month_trades):
        md = month_days.get(key, {})
        first, last = md.get("first"), md.get("last")
        drift = ((last - first) / first * 100) if first else float("nan")
        du, dnn = md.get("up", 0), md.get("dn", 0)
        tu, td = month_trades[key]["up"], month_trades[key]["dn"]
        share = 100.0 * tu / (tu + td) if (tu + td) else float("nan")
        print(key.ljust(9) + (f"{drift:+.2f}%").rjust(11)
              + str(md.get("flips", 0)).rjust(9)
              + (str(du) + "/" + str(du + dnn)).rjust(13)
              + str(tu).rjust(11) + str(td).rjust(11)
              + (f"{share:.1f}%").rjust(10), flush=True)

    print("\n" + "=" * 78, flush=True)
    print("PRIMARY — full history, LONG and SHORT tested SEPARATELY", flush=True)
    print("=" * 78, flush=True)
    primary = {}
    for direction in ("LONG", "SHORT"):
        up = [p for d, p, s, _, _o in tagged if d == direction and s == "uptrend"]
        dn = [p for d, p, s, _, _o in tagged if d == direction and s == "downtrend"]
        diff, se = report(direction, up, dn)
        primary[direction] = (diff, se)
        print("", flush=True)

    # Temporal replication: halves by closed_at
    ordered = sorted(tagged, key=lambda t: t[3])
    mid = len(ordered) // 2
    halves = (("FIRST_HALF", ordered[:mid]), ("SECOND_HALF", ordered[mid:]))
    print("=" * 78, flush=True)
    print("REPLICATION — temporal halves by closed_at (consistency, not "
          "re-significance)", flush=True)
    print("=" * 78, flush=True)
    for hname, subset in halves:
        b0 = subset[0][3].date()
        b1 = subset[-1][3].date()
        print(f"\n--- {hname}  n={len(subset)}  {b0} .. {b1} ---", flush=True)
        for direction in ("LONG", "SHORT"):
            up = [p for d, p, s, _, _o in subset if d == direction and s == "uptrend"]
            dn = [p for d, p, s, _, _o in subset if d == direction and s == "downtrend"]
            pd_, pse = primary[direction]
            report(f"{hname}/{direction}", up, dn,
                   primary_diff=pd_, primary_se=pse)


asyncio.run(main())
