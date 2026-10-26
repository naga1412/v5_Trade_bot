"""A0 full-range escalation -- does entry_score predict the PATH?

READ-ONLY. Implements
`docs/superpowers/decisions/2026-10-22-a0-escalation-amendment.md`
(#599), which amends `2026-10-21-phase-a-preregistration.md` (#589/#590).
All three were merged to main BEFORE this file existed, which is the
point: the nominated horizon, the clustering requirement, the population
ruling and the bar were fixed before any code could see a result.

WHY THIS EXISTS. A0's in-band test was a null measured across a
0.1368-unit decile contrast inside a band only 0.235 wide. #590
pre-committed that such a null TRIGGERS the full-range test rather than
closing the question. The `predictions` table carries a score for every
candle, traded or not: 44,606 LONG rows over 279 symbols in 60 days,
with 92.4% of them below the 0.30 entry bar.

THE WINDOW IS FIXED BY DESIGN, and this measurement gets that for free:
hypothetical entries have no realised exit, so there is no
outcome-dependent window to contaminate the target. That is the defect
which downgraded A1a (`2026-10-22-measurement-window-rule.md`), and it
cannot arise here.

THE DEPENDENCE TRAP, which is what the clustering is for. Hourly
`predictions` rows on the same symbol OVERLAP: a 40-bar MFE at hour t
and at hour t+1 share 39 of their 40 bars. n=44,606 overlapping windows
do not carry 44,606 windows' worth of information, and the naive SE
would hand back a tidy +8 sigma. SEs are therefore clustered by symbol,
the naive SE is printed beside them so the inflation factor is visible,
and A RESULT SIGNIFICANT ONLY ON THE NAIVE SE IS A PRE-DECLARED FAILURE.

GEOMETRY, read from app/shadow/engine.py rather than guessed:
    entry = the signal bar's CLOSE (the engine's `last_close`; #590 said
            "next bar's open", and matching the engine keeps MFE_R
            comparable with A1a's -- deviation declared in the amendment)
    R     = min(1.5 * atr, 0.05 * entry)      <- the 5% SL cap included
    atr   = simple mean of the last 14 true ranges (NOT Wilder)
"""
from __future__ import annotations

import asyncio
import math
import os
import statistics as st
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

sys.path.insert(0, "/app")

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.bar_fetch import UnresolvedMarket
from scripts.mfe_mae_curve import Bar, _fetch_bars_at_tf
from scripts.phase_a_scan import cell, decile_of, deciles

# --- pre-registered constants, none of them chosen after a result ------

WINDOW_DAYS: int = 60
TIMEFRAME: str = "1h"
BAR_MS: int = 60 * 60 * 1000

HORIZONS: tuple[int, ...] = (5, 10, 20, 40)
NOMINATED_HORIZON: int = 10          # the real population's mean hold is 9.60/13.04 bars

N_COMPARISONS: int = 14              # 12 from #589 + MFE_R and MAE_R here
BAR_SIGMA: float = 3.0
BAR_SIGMA_CORRECTED: float = 3.729   # Bonferroni off 3 sigma at 14 comparisons

SL_ATR_MULT: float = 1.5             # app/shadow/engine.py
SL_CAP_FRAC: float = 0.05            # the 5% cap, PR #286
ATR_PERIOD: int = 14
MIN_CONFIDENCE: float = 0.50         # engine's gate; secondary only

FETCH_CONCURRENCY: int = 5
PROGRESS_EVERY: int = 20

# Stated in the output header per the amendment's explicit instruction,
# so a null is read as "tested at the power actually available".
EXPECTED_CONTRAST_GAIN: str = "~2.5x the in-band contrast (NOT the ~7x the naive SE claims)"
EXPECTED_DETECTABLE_SLOPE: str = "~1.5 R per score unit clustered (in-band was ~3.8)"


_SQL = f"""
SELECT p.symbol,
       p.symbol_source,
       (EXTRACT(EPOCH FROM p.ts) * 1000)::bigint AS ts_ms,
       p.ts,
       p.final_score,
       p.confidence,
       EXISTS (
           SELECT 1 FROM shadow_trades s
           WHERE s.symbol = REPLACE(p.symbol, '/', '')
             AND s.opened_at >= p.ts - INTERVAL '1 hour'
             AND s.opened_at <  p.ts + INTERVAL '1 hour'
       ) AS became_trade
FROM predictions p
WHERE p.timeframe = '{TIMEFRAME}'
  AND p.direction = 'LONG'
  AND p.final_score IS NOT NULL
  AND p.ts >= NOW() - INTERVAL '{WINDOW_DAYS} days'
ORDER BY p.symbol, p.ts
"""


@dataclass
class Row:
    symbol: str
    symbol_source: str      # FU-53: picks the klines endpoint
    ts_ms: int
    day: str
    score: float
    confidence: float
    became_trade: bool
    # filled once bars are in
    entry: float = 0.0
    atr: float = 0.0
    r_unit: float = 0.0
    mfe: dict[int, float] = field(default_factory=dict)
    mae: dict[int, float] = field(default_factory=dict)
    resolved: bool = False


# ---------------------------------------------------------------------------
# Pure functions. No DB, no network -- these decide what the probe reports.
# ---------------------------------------------------------------------------

def true_ranges(bars: list[Bar]) -> list[float]:
    """TR_i = max(h-l, |h-prev_close|, |l-prev_close|), TR_0 using its own close.

    Mirrors app/core/predictor.py `_atr`'s own prev_close construction
    (which prepends c[0]), so the warm-up bar is handled identically.
    """
    if not bars:
        return []
    out: list[float] = []
    for i, b in enumerate(bars):
        prev_close = bars[i - 1].close if i > 0 else bars[0].close
        out.append(max(
            b.high - b.low,
            abs(b.high - prev_close),
            abs(b.low - prev_close),
        ))
    return out


def atr_at(trs: list[float], idx: int, period: int = ATR_PERIOD) -> float | None:
    """Simple mean of the `period` true ranges ENDING AT idx inclusive.

    None when there is not enough history, which must propagate as a
    dropped row rather than a zero -- a zero ATR would make R zero and
    MFE_R infinite.
    """
    if idx + 1 < period:
        return None
    window = trs[idx + 1 - period: idx + 1]
    if len(window) < period:
        return None
    return sum(window) / period


def r_unit(entry: float, atr: float) -> float:
    """R = entry - stop, with the engine's 5% cap. See engine.py evaluate()."""
    return min(SL_ATR_MULT * atr, SL_CAP_FRAC * entry)


def excursions_at_horizons(
    bars: list[Bar], entry_idx: int, entry: float, r: float,
    horizons: tuple[int, ...] = HORIZONS,
) -> tuple[dict[int, float], dict[int, float]]:
    """MFE_R / MAE_R over windows FIXED BY DESIGN, h bars from entry.

    Returns only horizons with complete data -- a truncated window would
    understate MFE for late rows and bias the newest data downward.
    MAE_R is reported POSITIVE (magnitude of adverse excursion).
    """
    mfe: dict[int, float] = {}
    mae: dict[int, float] = {}
    if r <= 0:
        return mfe, mae
    for h in horizons:
        last = entry_idx + h
        if last >= len(bars):
            continue
        hi = max(b.high for b in bars[entry_idx + 1: last + 1])
        lo = min(b.low for b in bars[entry_idx + 1: last + 1])
        mfe[h] = (hi - entry) / r
        mae[h] = (entry - lo) / r
    return mfe, mae


def naive_contrast(lo: list[float], hi: list[float]) -> dict:
    """Difference in means with the ordinary independent-rows SE.

    Printed for comparison ONLY. On overlapping windows it is wrong, and
    the amendment pre-declares that a result significant only on this is
    a failure of the test.
    """
    if len(lo) < 2 or len(hi) < 2:
        return {"ok": False}
    c0, c1 = cell(lo), cell(hi)
    se = math.sqrt(c0["se"] ** 2 + c1["se"] ** 2)
    diff = c1["mean"] - c0["mean"]
    return {
        "ok": True, "diff": diff, "se": se,
        "sigma": diff / se if se > 0 else float("nan"),
        "n_lo": c0["n"], "n_hi": c1["n"],
        "mean_lo": c0["mean"], "mean_hi": c1["mean"],
    }


def cluster_robust_contrast(rows: list[tuple[str, float, bool]]) -> dict:
    """Difference in means (treated - control) with a CR1 cluster-robust SE.

    `rows` is (cluster_id, value, is_treated).

    This is the exact OLS sandwich for the saturated two-group model
    y = a + b*d, written out rather than fitted:

        (X'X)^-1 row for b  =  [-1/N0,  N/(N1*N0)]
        meat                =  sum_g [[S_g^2, S_g*T_g], [S_g*T_g, T_g^2]]

    where S_g is the sum of residuals in cluster g and T_g the sum over
    its TREATED rows only. CR1 applies the usual
    (G/(G-1)) * ((N-1)/(N-K)) finite-sample factor with K=2.

    It reduces EXACTLY to the naive SE when every row is its own
    cluster -- derived, not assumed, and pinned by a test. That identity
    is what makes the inflation factor interpretable: it is entirely the
    within-symbol correlation, not an artefact of a different estimator.
    """
    treated = [v for _, v, t in rows if t]
    control = [v for _, v, t in rows if not t]
    n1, n0 = len(treated), len(control)
    if n1 < 2 or n0 < 2:
        return {"ok": False}
    clusters = {c for c, _, _ in rows}
    g = len(clusters)
    if g < 2:
        return {"ok": False}
    n = n0 + n1
    if n <= 2:
        return {"ok": False}

    m1 = sum(treated) / n1
    m0 = sum(control) / n0
    b = m1 - m0

    s_g: dict[str, float] = defaultdict(float)
    t_g: dict[str, float] = defaultdict(float)
    for c, v, t in rows:
        e = v - (m1 if t else m0)
        s_g[c] += e
        if t:
            t_g[c] += e

    m00 = sum(v * v for v in s_g.values())
    m01 = sum(s_g[c] * t_g[c] for c in s_g)
    m11 = sum(v * v for v in t_g.values())

    a = -1.0 / n0
    bb = n / (n1 * n0)
    var = a * a * m00 + 2.0 * a * bb * m01 + bb * bb * m11
    var *= (g / (g - 1.0)) * ((n - 1.0) / (n - 2.0))
    if var <= 0:
        return {"ok": False}
    se = math.sqrt(var)
    return {
        "ok": True, "diff": b, "se": se, "sigma": b / se,
        "n_lo": n0, "n_hi": n1, "mean_lo": m0, "mean_hi": m1,
        "clusters": g,
    }


def verdict(sigma: float) -> str:
    """PASS requires the CORRECTED bar. 3-to-3.729 is SUGGESTIVE and must
    NOT be announced."""
    if not isinstance(sigma, float) or sigma != sigma:
        return "INSUFFICIENT"
    a = abs(sigma)
    if a >= BAR_SIGMA_CORRECTED:
        return f"PASS (corrected {BAR_SIGMA_CORRECTED}sigma)"
    if a >= BAR_SIGMA:
        return "SUGGESTIVE -- re-test, do not announce"
    return "FAIL"


def thin_non_overlapping(rows: list[Row], horizon: int) -> list[Row]:
    """At most one row per symbol per `horizon` bars -- strictly
    non-overlapping windows, for the replication check.

    Greedy earliest-first within each symbol, which is deterministic and
    does not select on the outcome.
    """
    by_symbol: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_symbol[r.symbol].append(r)
    kept: list[Row] = []
    span = horizon * BAR_MS
    for sym in sorted(by_symbol):
        last = None
        for r in sorted(by_symbol[sym], key=lambda x: x.ts_ms):
            if last is None or r.ts_ms - last >= span:
                kept.append(r)
                last = r.ts_ms
    return kept


def design_effect(cluster_se: float, naive_se: float) -> float:
    """(cluster SE / naive SE)^2 -- how many rows' worth of information
    each row actually carries. 1.0 means the clustering was unnecessary."""
    if naive_se <= 0:
        return float("nan")
    return (cluster_se / naive_se) ** 2


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

async def _load_rows(session_factory) -> list[Row]:
    async with session_factory() as session:
        res = (await session.execute(sa.text(_SQL))).all()
    out: list[Row] = []
    for r in res:
        out.append(Row(
            symbol=r.symbol.replace("/", ""),
            symbol_source=r.symbol_source,
            ts_ms=int(r.ts_ms),
            day=r.ts.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            score=float(r.final_score),
            confidence=float(r.confidence) if r.confidence is not None else 0.0,
            became_trade=bool(r.became_trade),
        ))
    return out


async def _resolve_symbol(
    client: httpx.AsyncClient, symbol: str, rows: list[Row],
) -> int:
    """Fetch ONE contiguous series for the symbol and resolve every row
    from it.

    This is the whole feasibility argument. mfe_mae_curve's per-trade
    fetch would be 44,606 requests (~22h, and the ssh channel dies long
    before that -- see phase_a_scan's own failure). One series per symbol
    is ~2 paginated requests x 279 symbols.
    """
    lo = min(r.ts_ms for r in rows) - (ATR_PERIOD + 2) * BAR_MS
    hi = max(r.ts_ms for r in rows) + (max(HORIZONS) + 2) * BAR_MS
    bars = await _fetch_bars_at_tf(
        client, symbol, lo, hi, timeframe=TIMEFRAME,
        # Every row here shares one symbol, so one cohort.
        symbol_source=rows[0].symbol_source,
    )
    if len(bars) < ATR_PERIOD + 2:
        return 0
    trs = true_ranges(bars)
    idx_of = {b.ts_ms: i for i, b in enumerate(bars)}
    resolved = 0
    for r in rows:
        i = idx_of.get(r.ts_ms)
        if i is None:
            continue
        atr = atr_at(trs, i)
        if atr is None or atr <= 0:
            continue
        entry = bars[i].close
        if entry <= 0:
            continue
        ru = r_unit(entry, atr)
        if ru <= 0:
            continue
        mfe, mae = excursions_at_horizons(bars, i, entry, ru)
        if NOMINATED_HORIZON not in mfe:
            continue
        r.entry, r.atr, r.r_unit = entry, atr, ru
        r.mfe, r.mae = mfe, mae
        r.resolved = True
        resolved += 1
    return resolved


# ---------------------------------------------------------------------------
# Reporting. Order is fixed: HEADER -> INVARIANTS -> BAR -> ESTIMATES ->
# REPLICATION. A reader cannot reach an estimate before its bar.
# ---------------------------------------------------------------------------

def _print_header(rows: list[Row]) -> None:
    print("=" * 78)
    print("A0 FULL-RANGE ESCALATION -- entry_score vs the PATH")
    print("=" * 78)
    print("Pre-registered: 2026-10-22-a0-escalation-amendment.md (#599),")
    print("amending 2026-10-21-phase-a-preregistration.md (#589/#590).")
    print("All merged to main BEFORE this file existed.")
    print()
    print("POWER, STATED BEFORE ANY ESTIMATE (amendment's explicit instruction):")
    print(f"  contrast gain vs in-band : {EXPECTED_CONTRAST_GAIN}")
    print(f"  detectable slope         : {EXPECTED_DETECTABLE_SLOPE}")
    print("  => READ A NULL AS 'tested at the power actually available',")
    print("     NOT as a strong closure. Sub-bar estimates are reported")
    print("     with their SEs and are NOT discarded.")
    print()
    print("DEPENDENCE: hourly rows on one symbol overlap (a 40-bar window")
    print("shares 39 bars with the next). PRIMARY SEs are CLUSTERED BY")
    print("SYMBOL; the naive SE is printed beside them. A RESULT")
    print("SIGNIFICANT ONLY ON THE NAIVE SE IS A PRE-DECLARED FAILURE.")
    print()
    print("WINDOW: fixed by design (5/10/20/40 bars from entry). Hypothetical")
    print("entries have no realised exit, so the collider that downgraded")
    print("A1a cannot arise here. Nested horizons are ONE observation")
    print("measured four ways, never four results.")
    print(f"NOMINATED CRITERION-BEARING HORIZON: {NOMINATED_HORIZON} bars.")
    print(f"  The other {len(HORIZONS) - 1} are DESCRIPTIVE.")
    print()
    print(f"BAR: {N_COMPARISONS} comparisons -> {BAR_SIGMA_CORRECTED}sigma corrected")
    print(f"     (uncorrected {BAR_SIGMA}sigma also printed; "
          f"{BAR_SIGMA}-{BAR_SIGMA_CORRECTED} = SUGGESTIVE, do not announce)")
    print()
    print(f"POPULATION: {TIMEFRAME} predictions, direction=LONG, {WINDOW_DAYS}d, "
          f"n={len(rows)}, symbols={len({r.symbol for r in rows})}")
    print("  PRIMARY is ALL rows, traded or not. #590's literal 'never became")
    print("  trades' anti-join would remove exactly the highest-scoring rows")
    print("  and re-truncate the range this test exists to restore. MFE/MAE")
    print("  over a fixed window are descriptive path properties, so a row")
    print("  having also been a real trade introduces nothing.")
    print()


def _print_invariants(rows: list[Row], cuts: list[float]) -> None:
    print("-" * 78)
    print("INVARIANTS, printed BEFORE any estimate (counterfactual rule)")
    print("-" * 78)
    buckets: dict[int, list[Row]] = defaultdict(list)
    for r in rows:
        buckets[decile_of(r.score, cuts)].append(r)
    print(f"{'dec':>4} {'n':>7} {'score_mean':>11} {'syms':>6} "
          f"{'sd(mfe10)':>10} {'atr/close':>10}")
    for d in sorted(buckets):
        b = buckets[d]
        scores = [r.score for r in b]
        mfes = [r.mfe[NOMINATED_HORIZON] for r in b if NOMINATED_HORIZON in r.mfe]
        atrp = [r.atr / r.entry for r in b if r.entry > 0]
        print(f"{d:>4} {len(b):>7} {st.mean(scores):>11.4f} "
              f"{len({r.symbol for r in b}):>6} "
              f"{(st.stdev(mfes) if len(mfes) > 1 else float('nan')):>10.4f} "
              f"{(st.mean(atrp) if atrp else float('nan')):>10.5f}")
    print()
    print("  n per decile should be ~equal by construction; unequal means the")
    print("  cuts hit ties. A MONOTONE trend in atr/close means the score is")
    print("  partly a volatility proxy -- that is the A1a mechanism, and it")
    print("  would deflate MFE_R for high-score rows (R is larger), so it can")
    print("  produce a WRONG-SIGN result. Read it before the estimates.")
    print()


def _print_contrast(label: str, lo: list[Row], hi: list[Row],
                    pick, *, cluster_by) -> dict:
    rows = [(cluster_by(r), pick(r), False) for r in lo if pick(r) is not None]
    rows += [(cluster_by(r), pick(r), True) for r in hi if pick(r) is not None]
    cr = cluster_robust_contrast(rows)
    nv = naive_contrast([pick(r) for r in lo if pick(r) is not None],
                        [pick(r) for r in hi if pick(r) is not None])
    if not cr.get("ok") or not nv.get("ok"):
        print(f"  {label:<26} INSUFFICIENT")
        return cr
    de = design_effect(cr["se"], nv["se"])
    print(f"  {label:<26} diff={cr['diff']:+.4f}  "
          f"lo={cr['mean_lo']:+.4f} hi={cr['mean_hi']:+.4f}  "
          f"n={cr['n_lo']}/{cr['n_hi']}")
    print(f"  {'':<26} CLUSTERED se={cr['se']:.4f} "
          f"sigma={cr['sigma']:+.2f} clusters={cr['clusters']}  "
          f"-> {verdict(cr['sigma'])}")
    print(f"  {'':<26} naive     se={nv['se']:.4f} "
          f"sigma={nv['sigma']:+.2f}   design_effect={de:.2f}x")
    if abs(nv["sigma"]) >= BAR_SIGMA > abs(cr["sigma"]):
        print(f"  {'':<26} *** SIGNIFICANT ONLY ON THE NAIVE SE ***")
        print(f"  {'':<26} *** PRE-DECLARED FAILURE OF THIS TEST ***")
    return cr


async def main() -> None:
    t0 = time.time()
    url = os.environ["DATABASE_URL"]
    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    rows = await _load_rows(session_factory)
    print(f"loaded {len(rows)} prediction rows")
    if not rows:
        print("no rows -- nothing to do")
        return

    by_symbol: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_symbol[r.symbol].append(r)
    print(f"fetching ONE contiguous {TIMEFRAME} series per symbol "
          f"({len(by_symbol)} symbols, concurrency {FETCH_CONCURRENCY}) ...",
          flush=True)

    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    done = 0
    total_resolved = 0

    async with httpx.AsyncClient() as client:
        async def one(sym: str) -> int:
            nonlocal done, total_resolved
            async with sem:
                try:
                    n = await _resolve_symbol(client, sym, by_symbol[sym])
                except (UnresolvedMarket, ValueError):
                    # PROPAGATE. This arm exists to get OUT of the broad
                    # handler below, which is there for flaky symbols and
                    # transient HTTP -- the right call for a 279-symbol
                    # sweep that must not die on one bad response. An
                    # endpoint we cannot resolve is a different animal: it
                    # is a bug in the sweep's own cohort data, and
                    # continuing would silently drop symbols from the
                    # denominator. FU-53 cost this very script 60 of its
                    # 279 symbols exactly that way, and the loss was
                    # invisible because the run still printed a total.
                    raise
                except Exception as e:  # noqa: BLE001
                    print(f"  ! {sym}: {e}", flush=True)
                    n = 0
            done += 1
            total_resolved += n
            if done % PROGRESS_EVERY == 0:
                print(f"  ... {done}/{len(by_symbol)} symbols, "
                      f"{total_resolved} rows resolved "
                      f"({time.time() - t0:.0f}s)", flush=True)
            return n

        await asyncio.gather(*(one(s) for s in sorted(by_symbol)))

    usable = [r for r in rows if r.resolved]
    print(f"\nresolved {len(usable)} / {len(rows)} rows "
          f"({100.0 * len(usable) / len(rows):.1f}%) in {time.time() - t0:.0f}s")
    if len(usable) < 200:
        print("too few resolved rows -- refusing to report")
        return

    _print_header(usable)

    cuts = deciles([r.score for r in usable])
    if not cuts:
        print("could not compute deciles -- refusing to report")
        return
    _print_invariants(usable, cuts)

    lo = [r for r in usable if decile_of(r.score, cuts) == 0]
    hi = [r for r in usable if decile_of(r.score, cuts) == len(cuts)]
    span = st.mean([r.score for r in hi]) - st.mean([r.score for r in lo])
    print("-" * 78)
    print("ESTIMATES")
    print("-" * 78)
    print(f"measured extreme-decile score contrast = {span:.4f} units "
          f"(in-band A0 measured 0.1368)")
    print()

    print(f"[PRIMARY] horizon {NOMINATED_HORIZON}, clustered by SYMBOL")
    cr_mfe = _print_contrast(
        f"MFE_R @{NOMINATED_HORIZON}", lo, hi,
        lambda r: r.mfe.get(NOMINATED_HORIZON), cluster_by=lambda r: r.symbol)
    cr_mae = _print_contrast(
        f"MAE_R @{NOMINATED_HORIZON}", lo, hi,
        lambda r: r.mae.get(NOMINATED_HORIZON), cluster_by=lambda r: r.symbol)
    if cr_mfe.get("ok") and span > 0:
        print(f"\n  implied slope = {cr_mfe['diff'] / span:+.3f} R per score unit")
    print()

    print("[DESCRIPTIVE] the other horizons -- NESTED, so this is ONE")
    print("observation measured several ways, NOT several results.")
    for h in HORIZONS:
        if h == NOMINATED_HORIZON:
            continue
        _print_contrast(f"MFE_R @{h}", lo, hi,
                        lambda r, h=h: r.mfe.get(h), cluster_by=lambda r: r.symbol)
    print()

    print("[SENSITIVITY] clustered by CALENDAR DAY instead of symbol")
    print("  279 symbols move together with BTC, so timestamp is a second")
    print("  dependence axis. Reported, never pooled into the primary.")
    _print_contrast(f"MFE_R @{NOMINATED_HORIZON} (day)", lo, hi,
                    lambda r: r.mfe.get(NOMINATED_HORIZON),
                    cluster_by=lambda r: r.day)
    print()

    print("[REPLICATION] strictly NON-OVERLAPPING subset, "
          f"<=1 row per symbol per {NOMINATED_HORIZON} bars")
    thin = thin_non_overlapping(usable, NOMINATED_HORIZON)
    tcuts = deciles([r.score for r in thin])
    if tcuts:
        tlo = [r for r in thin if decile_of(r.score, tcuts) == 0]
        thi = [r for r in thin if decile_of(r.score, tcuts) == len(tcuts)]
        print(f"  thinned n={len(thin)} (from {len(usable)})")
        _print_contrast(f"MFE_R @{NOMINATED_HORIZON} (thinned)", tlo, thi,
                        lambda r: r.mfe.get(NOMINATED_HORIZON),
                        cluster_by=lambda r: r.symbol)
    else:
        print("  thinned subset too small to decile")
    print()

    print("[SECONDARY] the populations #590's literal wording would have used")
    untraded = [r for r in usable if not r.became_trade]
    print(f"  untraded-only  n={len(untraded)} "
          f"({100.0 * len(untraded) / len(usable):.1f}% of resolved)")
    ucuts = deciles([r.score for r in untraded])
    if ucuts:
        ulo = [r for r in untraded if decile_of(r.score, ucuts) == 0]
        uhi = [r for r in untraded if decile_of(r.score, ucuts) == len(ucuts)]
        uspan = st.mean([r.score for r in uhi]) - st.mean([r.score for r in ulo])
        print(f"  its score contrast = {uspan:.4f} units "
              f"(vs {span:.4f} on the primary -- if SMALLER, the anti-join "
              f"re-truncated the range, which is why it is not primary)")
        _print_contrast(f"MFE_R @{NOMINATED_HORIZON} (untraded)", ulo, uhi,
                        lambda r: r.mfe.get(NOMINATED_HORIZON),
                        cluster_by=lambda r: r.symbol)
    conf = [r for r in usable if r.confidence >= MIN_CONFIDENCE]
    print(f"\n  confidence>={MIN_CONFIDENCE} n={len(conf)} "
          f"({100.0 * len(conf) / len(usable):.1f}%)")
    ccuts = deciles([r.score for r in conf])
    if ccuts:
        clo = [r for r in conf if decile_of(r.score, ccuts) == 0]
        chi = [r for r in conf if decile_of(r.score, ccuts) == len(ccuts)]
        _print_contrast(f"MFE_R @{NOMINATED_HORIZON} (conf-gated)", clo, chi,
                        lambda r: r.mfe.get(NOMINATED_HORIZON),
                        cluster_by=lambda r: r.symbol)

    print()
    print("=" * 78)
    print(f"done in {time.time() - t0:.0f}s at "
          f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print("=" * 78)
    _ = cr_mae


if __name__ == "__main__":
    asyncio.run(main())
