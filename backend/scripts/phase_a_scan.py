"""Phase A scan — the five untested variables, against BOTH targets.

READ-ONLY. Implements
`docs/superpowers/decisions/2026-10-21-phase-a-preregistration.md`
(#589) as amended by #590, both merged to main BEFORE this file existed.

Reporting order is fixed by the pre-registration and enforced by the
print sequence: CELLS -> BAR -> ESTIMATES -> REPLICATION. A reader
cannot see a point estimate before the bar it is judged against.

TARGETS
    pnl_pct   realised, as recorded
    MFE_R     max favourable excursion in R units
    MAE_R     max adverse excursion in R units
  MFE/MAE are DESCRIPTIVE properties of the realised path, not
  counterfactual exits, so they carry no baseline-event requirement.
  The baseline-event agreement rate is still printed before them,
  because any later "what if we exited at MFE" simulation inherits it.

POPULATION
    LONG, entry_score >= 0.30 (the real shadow entry bar), 60d.
  NOT the mfe-mae-curve population, which filters at >= 0.36 -- the top
  third of a band that only spans 0.30-0.51. Reusing it would truncate
  A0's independent variable and would also make the already-recorded
  ladder/stop-width figures non-comparable.
"""
from __future__ import annotations

import asyncio
import os
import statistics as st
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, "/app")

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scripts.mfe_mae_curve import _TF_TO_INTERVAL, Bar, _fetch_bars_at_tf

# Pre-registered constants. Changing any of these invalidates the
# pre-registration -- they are not tuning knobs.
MIN_ENTRY_SCORE = 0.30
WINDOW_DAYS = 60
N_COMPARISONS = 12
BAR_SIGMA = 3.0
BAR_SIGMA_CORRECTED = 3.68  # Bonferroni on alpha=0.0027 over 12 comparisons
N_DECILES = 10
# Staleness bounds for the snapshot joins. An unbounded nearest-before
# join would silently pull a value from weeks earlier and present it as
# the value at entry.
MAX_SNAPSHOT_AGE_H = {"oi": 24.0, "funding": 24.0, "depth": 48.0}

# Bounded concurrency for the kline fetch. Sequential at ~1.8s/trade the
# scan took ~55 min over ~1,800 trades. Each klines call is weight 1-2
# against Binance's 1200/min per-IP budget, so 5 in flight at ~1.8s each
# is ~170 calls/min -- well inside it.
FETCH_CONCURRENCY = 5

# Progress cadence. The first run of this scan died after ~50 min with
# `client_loop: send disconnect: Broken pipe` and ssh exit 255 -- NOT a
# Python error. The fetch loop printed nothing for the whole window, so
# the ssh channel sat silent and the connection was dropped. Concurrency
# alone would not have fixed that; silence was the cause. Anything that
# runs for minutes over `ssh ... docker compose exec` must keep writing.
PROGRESS_EVERY = 100


@dataclass
class Row:
    trade_id: int
    symbol: str
    timeframe: str
    opened_ms: int
    closed_ms: int
    entry_price: float
    stop_loss: float
    take_profit: float
    entry_score: float
    mtf_agreement: int | None
    funding_directional_adj: float | None
    oi_24h_delta: float | None
    oi_age_h: float | None
    funding_rate: float | None
    funding_age_h: float | None
    depth_0_5pct_usdt: float | None
    depth_age_h: float | None
    pnl_pct: float
    exit_reason: str
    closed_at: object
    # filled after bars are fetched
    mfe_r: float = 0.0
    mae_r: float = 0.0
    model_outcome: str = "unknown"
    bars: list[Bar] = field(default_factory=list)

    @property
    def r_unit(self) -> float:
        return self.entry_price - self.stop_loss


_SQL = f"""
SELECT s.id, s.symbol, s.timeframe,
       (EXTRACT(EPOCH FROM s.opened_at) * 1000)::bigint AS opened_ms,
       (EXTRACT(EPOCH FROM s.closed_at) * 1000)::bigint AS closed_ms,
       s.entry_price, s.stop_loss, s.take_profit, s.entry_score,
       s.mtf_agreement, s.funding_directional_adj,
       s.pnl_pct, s.exit_reason, s.closed_at,
       ff.oi_24h_delta,
       EXTRACT(EPOCH FROM (s.opened_at - ff.captured_at)) / 3600.0 AS oi_age_h,
       im.funding_rate,
       EXTRACT(EPOCH FROM (s.opened_at - im.captured_at)) / 3600.0 AS funding_age_h,
       lf.depth_0_5pct_usdt,
       EXTRACT(EPOCH FROM (s.opened_at - lf.snapshot_at)) / 3600.0 AS depth_age_h
FROM shadow_trades s
LEFT JOIN LATERAL (
    SELECT f.oi_24h_delta, f.captured_at FROM flow_feature_snapshots f
    WHERE f.symbol = s.symbol AND f.captured_at <= s.opened_at
    ORDER BY f.captured_at DESC LIMIT 1
) ff ON TRUE
LEFT JOIN LATERAL (
    SELECT i.funding_rate, i.captured_at FROM intermarket_snapshots i
    WHERE i.symbol = s.symbol AND i.captured_at <= s.opened_at
    ORDER BY i.captured_at DESC LIMIT 1
) im ON TRUE
LEFT JOIN LATERAL (
    SELECT l.depth_0_5pct_usdt, l.snapshot_at FROM live_fleet_universe l
    WHERE l.symbol = s.symbol AND l.snapshot_at <= s.opened_at
    ORDER BY l.snapshot_at DESC LIMIT 1
) lf ON TRUE
WHERE s.closed_at IS NOT NULL
  AND s.direction = 'LONG'
  AND s.entry_score >= {MIN_ENTRY_SCORE}
  AND s.pnl_pct IS NOT NULL
  AND s.closed_at >= NOW() - INTERVAL '{WINDOW_DAYS} days'
  AND s.entry_price > 0 AND s.stop_loss > 0 AND s.take_profit > 0
  AND s.stop_loss < s.entry_price AND s.take_profit > s.entry_price
  AND s.timeframe IN ('1h', '15m')
ORDER BY s.closed_at
"""


# ---------------------------------------------------------------------------
# Pure statistics. Separated from I/O so they are unit-testable and
# mutation-checkable without a DB or network.
# ---------------------------------------------------------------------------

def cell(vals: list[float]) -> dict:
    """n, mean, sd, se for one cell. sd/se are nan below n=2."""
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": float("nan"), "sd": float("nan"),
                "se": float("nan")}
    mean = sum(vals) / n
    if n < 2:
        return {"n": n, "mean": mean, "sd": float("nan"), "se": float("nan")}
    sd = st.stdev(vals)
    return {"n": n, "mean": mean, "sd": sd, "se": sd / (n ** 0.5)}


def contrast(lo: list[float], hi: list[float]) -> dict:
    """hi - lo, with the 3-sigma bar from the REAL per-cell sds.

    The bar is computed from the data, never from an assumed pooled
    sigma, so it reflects the cells that actually exist.
    """
    a, b = cell(lo), cell(hi)
    if a["n"] < 2 or b["n"] < 2:
        return {"ok": False, "lo": a, "hi": b}
    se_diff = (a["se"] ** 2 + b["se"] ** 2) ** 0.5
    diff = b["mean"] - a["mean"]
    return {
        "ok": True, "lo": a, "hi": b, "diff": diff, "se_diff": se_diff,
        "bar": BAR_SIGMA * se_diff,
        "bar_corrected": BAR_SIGMA_CORRECTED * se_diff,
        "sigma": diff / se_diff if se_diff > 0 else float("nan"),
    }


def deciles(values: list[float], k: int = N_DECILES) -> list[float]:
    """k-1 interior cut points. Returns [] when there is nothing to cut."""
    xs = sorted(values)
    if len(xs) < k:
        return []
    return [xs[int(len(xs) * i / k)] for i in range(1, k)]


def decile_of(value: float, cuts: list[float]) -> int:
    """0-based decile index for `value` given interior cut points."""
    idx = 0
    for c in cuts:
        if value >= c:
            idx += 1
    return idx


def verdict(c: dict) -> str:
    """PASS only against the CORRECTED bar. 3-to-3.68 sigma is
    SUGGESTIVE and must be re-tested, never announced."""
    if not c.get("ok"):
        return "INSUFFICIENT"
    a = abs(c["diff"])
    if a >= c["bar_corrected"]:
        return "PASS (corrected)"
    if a >= c["bar"]:
        return "SUGGESTIVE (clears 3s, not 3.68s) -- re-test, do not announce"
    return "FAIL"


def replication_flags(primary: dict, half: dict) -> str:
    """Consistency, not re-significance: same sign AND within 2 SE_diff."""
    if not (primary.get("ok") and half.get("ok")):
        return "n/a"
    same_sign = (half["diff"] >= 0) == (primary["diff"] >= 0)
    within = abs(half["diff"] - primary["diff"]) <= 2.0 * primary["se_diff"]
    return f"same_sign={same_sign} within_2SE={within}"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _fmt(x: float, nd: int = 4) -> str:
    return "nan" if x != x else f"{x:+.{nd}f}"


def report_split(
    label: str, target: str, lo: list[float], hi: list[float],
    *, lo_desc: str, hi_desc: str, note: str | None = None,
    halves: list[tuple[str, list[float], list[float]]] | None = None,
) -> None:
    c = contrast(lo, hi)
    print("")
    print(f"  [{label}] target={target}")
    if note:
        print(f"    NOTE  {note}")
    # 1. CELLS (and the invariants worth watching: n and sd per cell)
    a, b = c["lo"], c["hi"]
    print(f"    CELLS {lo_desc}: n={a['n']} sd={_fmt(a['sd'], 3)}   "
          f"{hi_desc}: n={b['n']} sd={_fmt(b['sd'], 3)}")
    if not c.get("ok"):
        print("    INSUFFICIENT CELLS -- no estimate reported")
        return
    # 2. BAR, before any estimate
    print(f"    BAR   3s={c['bar']:.4f}  corrected(3.68s)="
          f"{c['bar_corrected']:.4f}")
    # 3. ESTIMATES
    print(f"    EST   {lo_desc}={_fmt(a['mean'])}  {hi_desc}={_fmt(b['mean'])}"
          f"  diff={_fmt(c['diff'])}  SE={c['se_diff']:.4f}  "
          f"-> {c['sigma']:+.2f}s")
    print(f"    VERDICT {verdict(c)}")
    # 4. REPLICATION
    for hname, hlo, hhi in (halves or []):
        hc = contrast(hlo, hhi)
        if hc.get("ok"):
            print(f"    REPL  {hname}: diff={_fmt(hc['diff'])} "
                  f"({hc['sigma']:+.2f}s)  {replication_flags(c, hc)}")
        else:
            print(f"    REPL  {hname}: insufficient cells")


def _targets(r: Row) -> dict:
    return {"pnl_pct": r.pnl_pct, "MFE_R": r.mfe_r, "MAE_R": r.mae_r}


def _median_split(rows: list[Row], get, target: str):
    """Split at the median, falling back when the median IS the mode.

    A median split empties one cell whenever the median equals the modal
    value -- which is exactly what happens on a near-constant variable.
    funding_directional_adj has TWO distinct values over 60d (0.0 and
    0.1, with 3,572 zeros against 98 non-zeros), so its median is 0 and
    `>= med` captures the entire population. The first run of this scan
    reported "INSUFFICIENT CELLS" for it, which reads as missing data
    rather than as a split that cannot separate.

    Fallback: split at the smallest value STRICTLY above the minimum, so
    a near-constant variable is tested as baseline-vs-elevated instead of
    silently dropping out. The threshold actually used is returned, so
    the report states which split it ran.
    """
    vals = [get(r) for r in rows if get(r) is not None]
    if len(vals) < 4:
        return [], [], float("nan")
    med = st.median(vals)
    cut = med
    if min(vals) >= med or max(vals) < med:
        above = sorted({v for v in vals if v > min(vals)})
        if not above:
            return [], [], float("nan")   # genuinely constant
        cut = above[0]
    lo = [_targets(r)[target] for r in rows
          if get(r) is not None and get(r) < cut]
    hi = [_targets(r)[target] for r in rows
          if get(r) is not None and get(r) >= cut]
    return lo, hi, cut


def _halves(rows: list[Row]) -> tuple[list[Row], list[Row]]:
    # (closed_at, trade_id): sorting on closed_at alone leaves the split
    # input-order dependent whenever two trades share a timestamp,
    # because Python's sort is stable. Under concurrency the input order
    # IS completion order, so this tiebreak is what makes the halves
    # reproducible run to run.
    ordered = sorted(rows, key=lambda r: (r.closed_at, r.trade_id))
    mid = len(ordered) // 2
    return ordered[:mid], ordered[mid:]


def run_median_variable(
    rows: list[Row], *, label: str, get, note: str | None = None,
    targets: tuple[str, ...] = ("pnl_pct", "MFE_R"),
) -> None:
    matched = [r for r in rows if get(r) is not None]
    print("")
    print(f"--- {label} ---")
    print(f"    JOIN/AVAIL  matched={len(matched)}/{len(rows)} "
          f"({100.0 * len(matched) / len(rows) if rows else 0:.1f}%)  "
          f"row loss={len(rows) - len(matched)}")
    if len(matched) < 4:
        print("    INSUFFICIENT -- no estimate reported")
        return
    h1, h2 = _halves(matched)
    for target in targets:
        lo, hi, med = _median_split(matched, get, target)
        halves = []
        for hname, hs in (("FIRST_HALF", h1), ("SECOND_HALF", h2)):
            hlo, hhi, _ = _median_split(hs, get, target)
            halves.append((hname, hlo, hhi))
        report_split(
            label, target, lo, hi,
            lo_desc="below_med", hi_desc="at_or_above_med",
            note=(f"median={med:.6g}" + (f" | {note}" if note else "")),
            halves=halves,
        )


async def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print("PHASE A SCAN -- pre-registered 2026-10-21 (#589, amended #590)")
    print("=" * 78)
    print("POWER NOTE, FIRST: the effect we need is ~0.06-0.1pp and these")
    print("scans resolve ~0.2-0.3pp, so A NULL DOES NOT EXCLUDE A USEFUL")
    print("EFFECT. Every point estimate is reported with its SE even when it")
    print("fails the bar. Sub-bar results are information, not noise.")
    print("")
    print(f"COMPARISONS: {N_COMPARISONS} primary -> Bonferroni-corrected bar")
    print(f"= {BAR_SIGMA_CORRECTED} sigma. PASS requires the corrected bar;")
    print("3-to-3.68 sigma is SUGGESTIVE and re-tested, never announced.")
    print("")
    print(f"POPULATION: LONG, entry_score >= {MIN_ENTRY_SCORE} (the real")
    print(f"shadow entry bar), {WINDOW_DAYS}d. Deliberately NOT")
    print("mfe-mae-curve's >= 0.36 population.")

    engine = create_async_engine(os.environ["DATABASE_URL"])
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as session:
        raw = (await session.execute(sa.text(_SQL))).all()
    await engine.dispose()

    rows: list[Row] = []
    for r in raw:
        def _bounded(val, age, key):
            if val is None or age is None:
                return None, None
            return (val, age) if age <= MAX_SNAPSHOT_AGE_H[key] else (None, age)

        oi, oi_age = _bounded(r.oi_24h_delta, r.oi_age_h, "oi")
        fr, fr_age = _bounded(r.funding_rate, r.funding_age_h, "funding")
        dp, dp_age = _bounded(r.depth_0_5pct_usdt, r.depth_age_h, "depth")
        rows.append(Row(
            trade_id=r.id, symbol=r.symbol, timeframe=str(r.timeframe),
            opened_ms=int(r.opened_ms), closed_ms=int(r.closed_ms),
            entry_price=float(r.entry_price), stop_loss=float(r.stop_loss),
            take_profit=float(r.take_profit),
            entry_score=float(r.entry_score),
            mtf_agreement=r.mtf_agreement,
            funding_directional_adj=(
                float(r.funding_directional_adj)
                if r.funding_directional_adj is not None else None
            ),
            oi_24h_delta=oi, oi_age_h=oi_age,
            funding_rate=fr, funding_age_h=fr_age,
            depth_0_5pct_usdt=dp, depth_age_h=dp_age,
            pnl_pct=float(r.pnl_pct), exit_reason=str(r.exit_reason),
            closed_at=r.closed_at,
        ))
    print(f"\nloaded {len(rows)} trades")
    if not rows:
        print("no rows -- nothing to scan")
        return 1

    lo_s = min(r.entry_score for r in rows)
    hi_s = max(r.entry_score for r in rows)
    print(f"entry_score range in population: {lo_s:.4f} .. {hi_s:.4f} "
          f"(span {hi_s - lo_s:.4f})")

    print(f"\nfetching native-TF bars per trade (~{len(rows)} requests) ...")
    print(f"  concurrency={FETCH_CONCURRENCY} (bounded)")
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    failures: list[tuple[int, str, str]] = []
    no_bars: list[int] = []

    # Defined BEFORE _tick, which closes over both. Python's late
    # binding would have resolved them either way, since _tick is only
    # ever called from inside the gather -- but depending on that across
    # forty lines is what breaks on the next edit.
    done = {"n": 0}
    requested = len(rows)

    def _tick() -> None:
        """Keep the ssh channel alive and make progress visible."""
        done["n"] += 1
        if done["n"] % PROGRESS_EVERY == 0 or done["n"] == requested:
            print(f"    fetched {done['n']}/{requested}", flush=True)

    async def _one(client: httpx.AsyncClient, r: Row) -> Row | None:
        """Fetch + measure one trade.

        EVERY exception is caught HERE, per item. asyncio.gather without
        per-item handling is all-or-nothing: one bad symbol would abort
        the entire scan. And a silently dropped trade would shrink n
        without anyone knowing -- the same class of error as substituting
        a model-resolved event for a recorded one.
        """
        async with sem:
            try:
                _iv, bar_ms = _TF_TO_INTERVAL.get(r.timeframe, ("1h", 3600000))
                bars = await _fetch_bars_at_tf(
                    client, r.symbol, r.opened_ms - bar_ms,
                    r.closed_ms + bar_ms, timeframe=r.timeframe,
                )
            except Exception as e:  # noqa: BLE001
                failures.append(
                    (r.trade_id, r.symbol, f"{type(e).__name__}: {e}"))
                _tick()
                return None
        bars = [b for b in bars if r.opened_ms <= b.ts_ms <= r.closed_ms]
        if not bars or r.r_unit <= 0:
            no_bars.append(r.trade_id)
            _tick()
            return None
        r.bars = bars
        r.mfe_r = max((b.high - r.entry_price) / r.r_unit for b in bars)
        r.mae_r = max((r.entry_price - b.low) / r.r_unit for b in bars)
        sl_i = next((i for i, b in enumerate(bars) if b.low <= r.stop_loss), None)
        tp_i = next((i for i, b in enumerate(bars) if b.high >= r.take_profit), None)
        if tp_i is not None and (sl_i is None or tp_i < sl_i):
            r.model_outcome = "win"
        elif sl_i is not None:
            r.model_outcome = "loss"
        else:
            r.model_outcome = "timeout"
        _tick()
        return r

    async with httpx.AsyncClient() as client:
        settled = await asyncio.gather(
            *(_one(client, r) for r in rows), return_exceptions=True,
        )
    kept: list[Row] = []
    for item in settled:
        if isinstance(item, BaseException):
            # Should be unreachable -- _one catches its own fetch errors --
            # but counted rather than ignored, because "unreachable" is a
            # belief and n shrinking silently is the failure being avoided.
            failures.append(
                (-1, "?", f"escaped: {type(item).__name__}: {item}"))
        elif item is not None:
            kept.append(item)
    rows = kept

    # EXCLUSIONS REPORTED EXPLICITLY. A run that quietly loses rows reports
    # a smaller n as though it were the population.
    print(f"  requested={requested}  usable={len(rows)}  "
          f"fetch_failures={len(failures)}  no_usable_bars={len(no_bars)}")
    if failures:
        print(f"  EXCLUDED on fetch failure: {len(failures)} "
              f"({100.0 * len(failures) / requested:.1f}% of requested)")
        for tid, sym, err in failures[:10]:
            print(f"    trade {tid} {sym}: {err}")
        if len(failures) > 10:
            print(f"    ... and {len(failures) - 10} more")
    if no_bars:
        print(f"  EXCLUDED for no usable bars / non-positive R: {len(no_bars)}")
    if not rows:
        return 1

    # Baseline-event agreement, printed BEFORE anything path-based.
    model_stops = sum(1 for r in rows if r.model_outcome == "loss")
    db_stops = sum(1 for r in rows if r.exit_reason == "STOP_LOSS")
    print("")
    print("BASELINE-EVENT AGREEMENT (stated before the path targets, per the")
    print("counterfactual rule -- any later 'what if we exited at MFE'")
    print("simulation inherits this gap):")
    print(f"  model resolves stop-outs: {model_stops}/{len(rows)} "
          f"({100.0 * model_stops / len(rows):.1f}%)")
    print(f"  DB records STOP_LOSS:     {db_stops}/{len(rows)} "
          f"({100.0 * db_stops / len(rows):.1f}%)")
    print("  MFE_R/MAE_R are DESCRIPTIVE properties of the realised path, so")
    print("  they carry no baseline-event requirement here.")

    h1, h2 = _halves(rows)

    # ---- A0: entry_score deciles vs all three targets -------------------
    print("")
    print("=" * 78)
    print("A0 -- entry_score vs PATH (the headline test; never run before)")
    print("=" * 78)
    print("LOW CONTRAST, pre-registered: the traded band is bounded below by")
    print("the entry bar, so extreme-decile separation spans only a fraction")
    print("of full score range. A NULL HERE TRIGGERS THE FULL-RANGE TEST ON")
    print("predictions ROWS -- it does not close the question (#590).")
    cuts = deciles([r.entry_score for r in rows])
    if not cuts:
        print("  insufficient rows to decile")
    else:
        buckets: dict[int, list[Row]] = {}
        for r in rows:
            buckets.setdefault(decile_of(r.entry_score, cuts), []).append(r)
        print("")
        print("  decile | n    | score_lo..hi      | pnl_pct  |   MFE_R  |   MAE_R")
        for d in sorted(buckets):
            bs = buckets[d]
            ss = [r.entry_score for r in bs]
            print(f"  {d:>6} | {len(bs):<4} | {min(ss):.4f}..{max(ss):.4f} | "
                  f"{cell([r.pnl_pct for r in bs])['mean']:+8.4f} | "
                  f"{cell([r.mfe_r for r in bs])['mean']:+8.4f} | "
                  f"{cell([r.mae_r for r in bs])['mean']:+8.4f}")
        top, bot = max(buckets), min(buckets)

        # ---- WHAT A0 CAN DETECT, computed before the estimates -------
        # The range width was banked by the failed run of 2026-10-21
        # BEFORE any outcome was measured, so this is a POWER FACT, not a
        # result. Printed as a number rather than as "low contrast" in
        # prose, so an in-band null cannot be read as closure.
        sc_lo = cell([r.entry_score for r in buckets[bot]])["mean"]
        sc_hi = cell([r.entry_score for r in buckets[top]])["mean"]
        sc_contrast = sc_hi - sc_lo
        print("")
        print(f"  CONTRAST  mean score d{bot}={sc_lo:.4f} -> d{top}="
              f"{sc_hi:.4f}  =>  {sc_contrast:.4f} score units")
        print("  DETECTABLE SLOPE (bar / contrast) -- the per-unit-score")
        print("  effect A0 would need before it could see anything:")
        for tname in ("pnl_pct", "MFE_R", "MAE_R"):
            cc = contrast(
                [_targets(r)[tname] for r in buckets[bot]],
                [_targets(r)[tname] for r in buckets[top]],
            )
            if not cc.get("ok") or sc_contrast <= 0:
                print(f"    {tname:<8} insufficient cells")
                continue
            print(f"    {tname:<8} bar(3s)={cc['bar']:.4f} -> "
                  f"{cc['bar'] / sc_contrast:.3f} per unit score   |   "
                  f"bar(3.68s)={cc['bar_corrected']:.4f} -> "
                  f"{cc['bar_corrected'] / sc_contrast:.3f} per unit score")
        print("  Read it this way: a slope SMALLER than the figure above is")
        print("  invisible to A0 regardless of whether it is real. If the")
        print("  figure is large relative to any plausible effect, THE")
        print("  IN-BAND NULL CARRIES ALMOST NO INFORMATION and the")
        print("  full-range escalation (#590) is the real test, not a")
        print("  fallback.")
        for target in ("pnl_pct", "MFE_R", "MAE_R"):
            halves = []
            for hname, hs in (("FIRST_HALF", h1), ("SECOND_HALF", h2)):
                hb: dict[int, list[Row]] = {}
                for r in hs:
                    hb.setdefault(decile_of(r.entry_score, cuts), []).append(r)
                halves.append((
                    hname,
                    [_targets(r)[target] for r in hb.get(bot, [])],
                    [_targets(r)[target] for r in hb.get(top, [])],
                ))
            report_split(
                "A0 entry_score top-vs-bottom decile", target,
                [_targets(r)[target] for r in buckets[bot]],
                [_targets(r)[target] for r in buckets[top]],
                lo_desc=f"d{bot}", hi_desc=f"d{top}",
                note="criterion-bearing contrast for A0",
                halves=halves,
            )

    # ---- A1: conclusive tier -------------------------------------------
    print("")
    print("=" * 78)
    print("A1 -- CONCLUSIVE TIER (full-history per-trade, no join)")
    print("=" * 78)
    mtf_vals = [r.mtf_agreement for r in rows if r.mtf_agreement is not None]
    if mtf_vals:
        print(f"    mtf_agreement median={st.median(mtf_vals)} "
              f"(reported so '4 may sit far from the median' is checkable)")
        print("    distribution: " + ", ".join(
            f"{v}:{mtf_vals.count(v)}" for v in sorted(set(mtf_vals))))
    for target in ("pnl_pct", "MFE_R"):
        lo = [_targets(r)[target] for r in rows
              if r.mtf_agreement is not None and r.mtf_agreement < 4]
        hi = [_targets(r)[target] for r in rows
              if r.mtf_agreement is not None and r.mtf_agreement >= 4]
        halves = []
        for hname, hs in (("FIRST_HALF", h1), ("SECOND_HALF", h2)):
            halves.append((
                hname,
                [_targets(r)[target] for r in hs
                 if r.mtf_agreement is not None and r.mtf_agreement < 4],
                [_targets(r)[target] for r in hs
                 if r.mtf_agreement is not None and r.mtf_agreement >= 4],
            ))
        report_split(
            "A1a mtf_agreement>=4", target, lo, hi,
            lo_desc="mtf<4", hi_desc="mtf>=4",
            note="THRESHOLD test. The original +0.481 figure is UNUSABLE "
                 "(its baseline was retracted) and is not carried forward.",
            halves=halves,
        )
    # VALIDITY DIAGNOSTIC, NOT A 13TH HYPOTHESIS. A1a passed on MFE_R,
    # but its high-mtf cell also has the larger sd (1.457 vs 1.060), so
    # the gain could be nothing more than selecting more volatile trades
    # -- which would raise the ADVERSE excursion by a similar amount. If
    # MAE_R moves as much as MFE_R did, mtf>=4 is a volatility proxy and
    # not an edge. This exists to interpret an existing result, cannot
    # itself "pass" anything, and is NOT counted in the 12 pre-registered
    # comparisons.
    print("")
    print("  --- A1a VALIDITY DIAGNOSTIC: is the MFE gain just volatility? ---")
    print("  Not a pre-registered comparison. If MAE_R rises by a similar")
    print("  amount to MFE_R, mtf>=4 selects volatility rather than edge.")
    lo_m = [r.mae_r for r in rows
            if r.mtf_agreement is not None and r.mtf_agreement < 4]
    hi_m = [r.mae_r for r in rows
            if r.mtf_agreement is not None and r.mtf_agreement >= 4]
    report_split(
        "A1a mtf_agreement>=4 [DIAGNOSTIC]", "MAE_R", lo_m, hi_m,
        lo_desc="mtf<4", hi_desc="mtf>=4",
        note="validity check on the MFE_R pass; not one of the 12",
    )
    cm = contrast(lo_m, hi_m)
    cf = contrast(
        [r.mfe_r for r in rows
         if r.mtf_agreement is not None and r.mtf_agreement < 4],
        [r.mfe_r for r in rows
         if r.mtf_agreement is not None and r.mtf_agreement >= 4],
    )
    if cm.get("ok") and cf.get("ok"):
        print(f"    MFE_R gain={cf['diff']:+.4f}   MAE_R gain={cm['diff']:+.4f}"
              f"   net(MFE-MAE)={cf['diff'] - cm['diff']:+.4f} R")
        print("    net ~0 => volatility proxy. net clearly positive => the")
        print("    favourable excursion grew MORE than the adverse one, which")
        print("    is what an edge would look like.")

    run_median_variable(
        rows, label="A1b funding_directional_adj",
        get=lambda r: r.funding_directional_adj,
        note="per-trade column, never among the eleven",
    )

    # ---- A2 / A3 / A4 ---------------------------------------------------
    print("")
    print("=" * 78)
    print("A2 -- CLEAN BUT JOINED")
    print("=" * 78)
    run_median_variable(
        rows, label="A2 oi_delta_24h",
        get=lambda r: r.oi_24h_delta,
        note="post-08-06 is AUTOMATIC (table starts then); NO zero-fill",
    )

    print("")
    print("=" * 78)
    print("A3 -- DESCRIPTIVE ONLY. NO CONCLUSION PERMITTED.")
    print("=" * 78)
    run_median_variable(
        rows, label="A3 funding_rate [UNDERPOWERED]",
        get=lambda r: r.funding_rate,
        note="intermarket_snapshots starts 2026-10-07. Pre-registered as "
             "unable to resolve the ~0.06-0.1pp target. No conclusion, "
             "positive or null, may be drawn.",
    )

    print("")
    print("=" * 78)
    print("A4 -- PROXY ONLY")
    print("=" * 78)
    run_median_variable(
        rows, label="A4 depth_0_5pct_usdt [DAILY PROXY]",
        get=lambda r: r.depth_0_5pct_usdt,
        note="~1 snapshot/symbol/day, so this is the nearest DAILY value, "
             "not depth at entry. A NULL DOES NOT RULE OUT dispatch-time "
             "depth. #555 persists dispatch-time depth -> real test ~late Nov.",
    )

    print(f"\nTotal runtime: {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
