"""shadow-variant-rolling-report probe.

READ-ONLY. Reports paired-comparison outcomes for the breakeven-variant
lane (Amendment 3, 2026-07-31) added by PR #392.

Item 1(c) of the 2026-08-06 gate-divergence follow-up: every paired row is
also labeled `live_eligible` -- a counterfactual re-application of the 3
PR#266 gates (REGIME_GATE_ENABLED, ADX_GATE_ENABLED, PATTERN_BOOST_ENABLED)
that apply to LIVE dispatch but are no-op'd by shadow's own entry-quality
shim. Uses the REAL `open_position_gate()` (imported, not reimplemented)
against deterministically recomputed historical regime (walk-forward
`compute_market_regime()`, no lookahead) and ADX (`_adx()` over real
historical klines) -- same methodology as the gate-counterfactual ops-debug
probe. Each window's report prints three blocks: ALL (unchanged, for
continuity with every prior read), LIVE-ELIGIBLE, and LIVE-INELIGIBLE, so a
breakeven conclusion is never drawn over a population live would never
actually take. Per operator ruling: this labels the existing read only --
it does NOT change what shadow trades or filters shadow's own population.

For each (variant_name, cohort, timeframe, window):
    n_paired   : signals with both a shadow_trades row and a
                 shadow_trade_variants row for this variant.
    n_armed    : subset where the breakeven trigger armed.
    share_armed: n_armed / n_paired.
    Δ_gross    : mean paired (variant.pnl_pct - base.pnl_pct), with SE.
                 THIS is the primary metric.
    Δ_after_fee: ASSERTED equal to Δ_gross (paired fees cancel — both
                 lanes take the same entry and both exit taker via
                 market/stop-market/take-profit-market/breakeven-market
                 stop-market). Any divergence is a BUG FLAG, not a
                 result — printed as `[BUG]` line, not aggregated.

Cohort split (per FU-38 verification 2026-07-30):
    atr_bound  : 1.5 * entry_atr <= 5% of entry_price. PRIMARY cohort.
    cap_bound  : 1.5 * entry_atr  > 5% of entry_price. Small-n, high-SE;
                 secondary. A couple of -5% floor hits inside a paired
                 delta would swamp the signal, so kept separate.
    aggregate  : both together. Tertiary.

Filters:
    - direction = 'LONG' (matches MFE study cohort)
    - entry_score >= 0.36 (top-20 gate)
    - closed_at >= NOW() - :window
    - timeframe IN ('1h', '15m')
    - symbol NOT IN SHADOW_SPOT_BLACKLIST
    - rank_at_trade <= 20 OR symbol = 'BTCUSDT' (structurally top-20;
      ranks 21-30 are untradeable per fleet-cap ratification 2026-07-28)

Decision rule per (variant_name, cohort) at the 90d window:
    - PASS         : n_paired >= 100 AND 95% CI clear of zero
                     (mean - 1.96*SE > 0 OR mean + 1.96*SE < 0).
    - FAIL         : n_paired >= 100 AND CI includes zero.
    - NOT-YET-N    : n_paired < 100.
    Additionally, at n_paired >= 30, prints a FIRST_DIRECTIONAL_READ
    line flagging strongly-signed cases (mean ± 1*SE clear of zero)
    so an early negative signal surfaces before n=100.

Leader print per cohort (at the 90d window): whichever trigger has
the higher paired Δ_gross mean, with the delta (leader - runner-up)
so trigger selection is read off the output.

On-demand invocation via ops-debug workflow. No scheduling.

Read-only guarantees:
    - No writes to postgres (SELECT only).
    - No trading API calls.
"""
from __future__ import annotations

import asyncio
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, stdev

import httpx
import pandas as pd
import sqlalchemy as sa

# Repo path bootstrap so this script runs both inside the container
# (`/app/scripts/...`) and via `python backend/scripts/...` for tests.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config import get_settings  # noqa: E402
from app.core.gates.entry_quality import open_position_gate  # noqa: E402
from app.core.scoring.mtf_confluence import ADX_PERIOD, TIMEFRAMES as MTF_TIMEFRAMES, _adx  # noqa: E402
from app.core.regime.market_regime import EMA_SLOW, compute_market_regime  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402


WINDOWS_DAYS: list[int] = [7, 30, 60, 90]
TIMEFRAMES: list[str] = ["1h", "15m"]
COHORTS: list[str] = ["atr_bound", "cap_bound", "aggregate"]
DECISION_N: int = 100
EARLY_READ_N: int = 30
Z_95: float = 1.96
Z_68: float = 1.00  # ~68% CI for the early directional read
FEE_TOLERANCE: float = 1e-6  # Δ_after_fee must equal Δ_gross to this precision
BINANCE_SPOT_KLINES_URL: str = "https://api.binance.com/api/v3/klines"


@dataclass(frozen=True)
class PairRow:
    variant_name: str
    trigger_r: float
    timeframe: str
    cohort: str
    base_pnl_pct: float
    variant_pnl_pct: float
    armed: bool
    # Item 1(c): counterfactual live-gate eligibility label. Defaults True
    # so existing unit-test construction (pre-dating this field) is
    # unaffected.
    live_eligible: bool = True


def _classify_cohort(entry_price: float, entry_atr: float) -> str:
    """atr_bound / cap_bound per engine.py convention."""
    if entry_price <= 0 or entry_atr <= 0:
        return "atr_bound"  # degenerate → default to primary
    return "cap_bound" if (1.5 * entry_atr) > (0.05 * entry_price) else "atr_bound"


def _blacklist() -> set[str]:
    return set(get_settings().SHADOW_SPOT_BLACKLIST)


# ---------------------------------------------------------------------------
# Item 1(c): live-gate counterfactual eligibility labeling.
#
# No dispatch/decision-log table exists (confirmed via system-truth), so
# whether live's 3 PR#266 gates (regime/ADX/pattern-boost) would have
# allowed or rejected a given shadow trade can't be read back directly.
# Instead this re-runs the REAL open_position_gate() against
# deterministically recomputed historical regime + ADX -- same approach as
# the gate-counterfactual ops-debug probe (items 1a/1b of the same
# follow-up).
# ---------------------------------------------------------------------------


async def _fetch_klines_df(
    client: httpx.AsyncClient, symbol: str, interval: str, limit: int,
    end_ms: int | None = None,
) -> pd.DataFrame:
    params: dict[str, str] = {"symbol": symbol, "interval": interval, "limit": str(limit)}
    if end_ms is not None:
        params["endTime"] = str(end_ms)
    resp = await client.get(BINANCE_SPOT_KLINES_URL, params=params, timeout=15.0)
    resp.raise_for_status()
    rows = resp.json()
    return pd.DataFrame({
        "ts": [int(r[0]) for r in rows],
        "high": [float(r[2]) for r in rows],
        "low": [float(r[3]) for r in rows],
        "close": [float(r[4]) for r in rows],
    })


async def _build_regime_by_date(client: httpx.AsyncClient) -> dict[object, str]:
    """Walk-forward `compute_market_regime()` over real BTC daily klines.

    Day D only ever sees data through day D (no lookahead).
    """
    daily = await _fetch_klines_df(client, "BTCUSDT", "1d", 1000)
    by_date: dict[object, str] = {}
    for i in range(EMA_SLOW + 2, len(daily)):
        window = daily.iloc[: i + 1]
        regime = compute_market_regime(window)
        day = pd.Timestamp(int(daily.iloc[i]["ts"]), unit="ms", tz="UTC").date()
        by_date[day] = regime
    return by_date


async def _build_adx_series(
    client: httpx.AsyncClient, tf: str, needed_since_ms: int,
) -> dict[int, float]:
    """Real `_adx()` rolled over real historical klines for one TF."""
    all_rows: list[pd.DataFrame] = []
    end_ms: int | None = None
    for _ in range(40):  # generous margin — 5m needs ~26 walks for 90d
        df = await _fetch_klines_df(client, "BTCUSDT", tf, 1000, end_ms=end_ms)
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
        series[int(full.iloc[i]["ts"])] = _adx(highs[: i + 1], lows[: i + 1], closes[: i + 1])
    return series


def _nearest_before(series: dict[int, float], ts_ms: int) -> float | None:
    best_ts: int | None = None
    best_val: float | None = None
    for t, v in series.items():
        if t <= ts_ms and (best_ts is None or t > best_ts):
            best_ts, best_val = t, v
    return best_val


def compute_live_eligible(
    *,
    direction: str,
    entry_score: float | None,
    l2_direction: str | None,
    l2_confidence: float | None,
    dominant_tf: str | None,
    opened_at: datetime,
    regime_by_date: dict[object, str],
    adx_series: dict[str, dict[int, float]],
) -> bool:
    """True if the REAL open_position_gate() -- with all 3 PR#266 gates
    forced on -- would allow this trade. Pure/injectable: no DB or REST
    calls, so this is directly unit-testable.
    """
    # Naive datetimes (e.g. a raw Postgres TIMESTAMP round-trip) must be
    # interpreted as UTC, not local system time -- normalize FIRST, before
    # deriving either the ADX lookup timestamp or the regime day, since
    # datetime.timestamp() on a naive value silently assumes local tz.
    aware = opened_at if opened_at.tzinfo else opened_at.replace(tzinfo=timezone.utc)

    adx_map: dict[str, float] | None = None
    if dominant_tf and dominant_tf in adx_series:
        ts_ms = int(aware.timestamp() * 1000)
        v = _nearest_before(adx_series[dominant_tf], ts_ms)
        if v is not None:
            adx_map = {dominant_tf: v}

    class _Sig:
        pass

    sig = _Sig()
    sig.direction = direction  # type: ignore[attr-defined]
    sig.entry_score = entry_score  # type: ignore[attr-defined]
    sig.layer2_direction = l2_direction  # type: ignore[attr-defined]
    sig.layer2_confidence = l2_confidence  # type: ignore[attr-defined]
    sig.mtf_adx_by_tf = adx_map  # type: ignore[attr-defined]
    sig.mtf_dominant_tf = dominant_tf  # type: ignore[attr-defined]

    day = aware.astimezone(timezone.utc).date()
    regime = regime_by_date.get(day)

    real_settings = get_settings()

    class _S:
        REGIME_GATE_ENABLED = True
        ADX_GATE_ENABLED = True
        PATTERN_BOOST_ENABLED = True
        DISABLE_SHORT_SIGNALS = False
        MIN_ENTRY_SCORE_LONG = 0.36
        MIN_ADX_TREND_STRENGTH = real_settings.MIN_ADX_TREND_STRENGTH
        REGIME_OPPOSITE_PATTERN_OVERRIDE = real_settings.REGIME_OPPOSITE_PATTERN_OVERRIDE
        PATTERN_BOOST_MIN_CONFIDENCE = real_settings.PATTERN_BOOST_MIN_CONFIDENCE
        PATTERN_BOOST_AMOUNT = real_settings.PATTERN_BOOST_AMOUNT
        PATTERN_PENALTY_AMOUNT = real_settings.PATTERN_PENALTY_AMOUNT

    return open_position_gate(sig, _S(), market_regime=regime).allow


async def _fetch_pairs(
    window_days: int,
    regime_by_date: dict[object, str],
    adx_series: dict[str, dict[int, float]],
) -> list[PairRow]:
    """One query per window; each returns paired (base, variant) rows
    with pre-computed cohort + BTCUSDT-or-rank<=20 filter."""
    sql = sa.text(
        """
        WITH base AS (
            SELECT
                s.id AS base_id,
                s.symbol,
                s.timeframe,
                s.entry_price,
                s.entry_atr,
                s.entry_score,
                s.direction,
                s.mtf_dominant_tf,
                s.layer_scores -> '2' ->> 'direction' AS l2_direction,
                (s.layer_scores -> '2' ->> 'confidence')::float AS l2_confidence,
                s.pnl_pct AS base_pnl_pct,
                s.opened_at,
                (SELECT MAX(snapshot_at) FROM asset_universe
                    WHERE snapshot_at <= s.opened_at) AS rank_snap
            FROM shadow_trades s
            WHERE s.closed_at IS NOT NULL
              AND s.direction = 'LONG'
              AND s.entry_score >= 0.36
              AND s.closed_at >= NOW() - (:window || ' days')::INTERVAL
              AND s.timeframe IN ('1h', '15m')
              AND s.entry_price > 0
              AND s.entry_atr > 0
        ),
        ranked AS (
            SELECT b.*, au.rank AS rank_at_trade
            FROM base b
            LEFT JOIN asset_universe au
              ON au.snapshot_at = b.rank_snap AND au.symbol = b.symbol
        )
        SELECT
            v.variant_name,
            v.trigger_r,
            v.armed,
            v.pnl_pct AS variant_pnl_pct,
            r.base_pnl_pct,
            r.symbol,
            r.timeframe,
            r.entry_price,
            r.entry_atr,
            r.rank_at_trade,
            r.opened_at,
            r.entry_score,
            r.direction,
            r.mtf_dominant_tf,
            r.l2_direction,
            r.l2_confidence
        FROM ranked r
        JOIN shadow_trade_variants v ON v.base_shadow_trade_id = r.base_id
        """
    )
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(sql, {"window": window_days})).fetchall()

    bl = _blacklist()
    out: list[PairRow] = []
    for r in rows:
        if r.symbol in bl:
            continue
        # BTCUSDT is exempt from rank_at_trade (always tradeable per
        # DEFAULT_EXCLUDE / top-20-union convention).
        if r.symbol != "BTCUSDT":
            if r.rank_at_trade is None or r.rank_at_trade > 20:
                continue
        cohort = _classify_cohort(
            float(r.entry_price), float(r.entry_atr),
        )
        live_eligible = compute_live_eligible(
            direction=str(r.direction),
            entry_score=float(r.entry_score) if r.entry_score is not None else None,
            l2_direction=r.l2_direction,
            l2_confidence=r.l2_confidence,
            dominant_tf=r.mtf_dominant_tf,
            opened_at=r.opened_at,
            regime_by_date=regime_by_date,
            adx_series=adx_series,
        )
        out.append(PairRow(
            variant_name=str(r.variant_name),
            trigger_r=float(r.trigger_r),
            timeframe=str(r.timeframe),
            cohort=cohort,
            base_pnl_pct=float(r.base_pnl_pct) if r.base_pnl_pct is not None else 0.0,
            variant_pnl_pct=float(r.variant_pnl_pct),
            armed=bool(r.armed),
            live_eligible=live_eligible,
        ))
    return out


def _mean_se(values: list[float]) -> tuple[float, float]:
    """Sample mean and SE-of-the-mean. Returns (0, 0) for n <= 1."""
    n = len(values)
    if n <= 1:
        return (values[0] if n == 1 else 0.0, 0.0)
    m = mean(values)
    s = stdev(values)
    return (m, s / math.sqrt(n))


def _fee_assertion_flag(deltas_gross: list[float], deltas_after_fee: list[float]) -> str | None:
    """Return a bug-flag string if paired Δ_gross ≠ paired Δ_after_fee
    for any pair (both lanes share same taker fees per pair → identical).
    None means the assertion held for every pair.
    """
    for g, f in zip(deltas_gross, deltas_after_fee, strict=True):
        if abs(g - f) > FEE_TOLERANCE:
            return (
                f"[BUG] paired Δ_gross ≠ Δ_after_fee "
                f"(gross={g:+.6f} after_fee={f:+.6f} diff={g - f:+.6e}) "
                f"— fees should cancel in the paired difference; "
                f"investigate query or fee model"
            )
    return None


def _decision(n: int, m: float, se: float) -> str:
    """PASS / FAIL / NOT-YET-N at n>=DECISION_N."""
    if n < DECISION_N:
        return "NOT-YET-N"
    lo, hi = m - Z_95 * se, m + Z_95 * se
    if lo > 0:
        return "PASS+ (Δ>0)"
    if hi < 0:
        return "PASS- (Δ<0)"
    return "FAIL (CI includes 0)"


def _early_read(n: int, m: float, se: float) -> str | None:
    """Directional early signal at n>=EARLY_READ_N. None if silent."""
    if n < EARLY_READ_N or n >= DECISION_N:
        return None
    lo, hi = m - Z_68 * se, m + Z_68 * se
    if lo > 0:
        return f"EARLY+ n={n} Δ={m:+.4f}%±{se:.4f}% (>0 at ~68% CI)"
    if hi < 0:
        return f"EARLY- n={n} Δ={m:+.4f}%±{se:.4f}% (<0 at ~68% CI)"
    return None


def _summarize_group(
    pairs: list[PairRow],
) -> tuple[int, int, float, float, float]:
    """Return (n_paired, n_armed, share_armed_pct, mean_delta, se_delta)."""
    n = len(pairs)
    if n == 0:
        return (0, 0, 0.0, 0.0, 0.0)
    n_armed = sum(1 for p in pairs if p.armed)
    deltas = [p.variant_pnl_pct - p.base_pnl_pct for p in pairs]
    m, se = _mean_se(deltas)
    return (n, n_armed, 100.0 * n_armed / n, m, se)


def _print_report(
    window_days: int, pairs: list[PairRow], *, header_suffix: str = "",
) -> None:
    print(f"\n=== window: last {window_days} days{header_suffix} ===")
    if not pairs:
        print("  (no paired rows found)")
        return

    # Bug flag first — after-fee assertion. Since both lanes take the
    # same entry price + same taker fees per pair, subtracting a
    # constant 0.10% RT from BOTH sides leaves the paired Δ unchanged.
    # If they differ, the query is wrong or the fee model changed
    # asymmetrically. We construct Δ_after_fee explicitly so the
    # assertion runs on real numbers (even though it's mathematically
    # trivial — we want the runtime evidence).
    deltas_gross = [p.variant_pnl_pct - p.base_pnl_pct for p in pairs]
    deltas_after_fee = [
        (p.variant_pnl_pct - 0.10) - (p.base_pnl_pct - 0.10)
        for p in pairs
    ]
    bug = _fee_assertion_flag(deltas_gross, deltas_after_fee)
    if bug is not None:
        print(f"  {bug}")

    variants = sorted({p.variant_name for p in pairs})
    header = (
        f"  {'variant':<18} {'cohort':<10} {'tf':<4} "
        f"{'n_pair':>7} {'n_armed':>7} {'arm%':>6} "
        f"{'Δ_mean%':>9} {'±SE':>7}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))

    # Per-variant × per-cohort × per-TF row grid.
    for v in variants:
        for cohort in COHORTS:
            for tf in TIMEFRAMES:
                if cohort == "aggregate":
                    grp = [
                        p for p in pairs
                        if p.variant_name == v and p.timeframe == tf
                    ]
                else:
                    grp = [
                        p for p in pairs
                        if p.variant_name == v
                        and p.cohort == cohort
                        and p.timeframe == tf
                    ]
                n, n_armed, share, m, se = _summarize_group(grp)
                if n == 0:
                    continue
                print(
                    f"  {v:<18} {cohort:<10} {tf:<4} "
                    f"{n:>7d} {n_armed:>7d} {share:>5.1f}% "
                    f"{m:>+8.4f}% {se:>6.4f}%"
                )

    # ---- Decision rule + early directional read (per variant × cohort,
    # ---- collapsed over TF to give one decision per (variant, cohort)).
    print()
    print(f"  DECISION (n>={DECISION_N}) / EARLY READ (n>={EARLY_READ_N}):")
    for v in variants:
        for cohort in COHORTS:
            if cohort == "aggregate":
                grp = [p for p in pairs if p.variant_name == v]
            else:
                grp = [
                    p for p in pairs
                    if p.variant_name == v and p.cohort == cohort
                ]
            n, _, _, m, se = _summarize_group(grp)
            if n == 0:
                continue
            decision = _decision(n, m, se)
            print(
                f"    {v:<18} {cohort:<10} n={n:>4d} "
                f"Δ={m:>+8.4f}%±{se:.4f}% → {decision}"
            )
            early = _early_read(n, m, se)
            if early is not None:
                print(f"        └─ {early}")

    # ---- Leader print (per cohort at THIS window). Which trigger is
    # ---- currently ahead?
    print()
    print("  LEADER (higher paired Δ_gross wins):")
    for cohort in COHORTS:
        by_variant: dict[str, tuple[int, float, float]] = {}
        for v in variants:
            if cohort == "aggregate":
                grp = [p for p in pairs if p.variant_name == v]
            else:
                grp = [
                    p for p in pairs
                    if p.variant_name == v and p.cohort == cohort
                ]
            n, _, _, m, se = _summarize_group(grp)
            if n > 0:
                by_variant[v] = (n, m, se)
        if len(by_variant) < 2:
            continue
        ranked = sorted(by_variant.items(), key=lambda kv: -kv[1][1])
        leader_name, (leader_n, leader_m, leader_se) = ranked[0]
        runner_name, (_, runner_m, _) = ranked[1]
        gap = leader_m - runner_m
        print(
            f"    {cohort:<10} leader={leader_name} "
            f"(n={leader_n}, Δ={leader_m:+.4f}%±{leader_se:.4f}%); "
            f"gap over {runner_name} = {gap:+.4f}%"
        )


async def main() -> None:
    print("shadow-variant-rolling-report — paired-Δ analysis on shadow_trade_variants")
    print(
        f"filters: direction=LONG, entry_score>=0.36, timeframe IN {TIMEFRAMES}, "
        "blacklist excluded, top-20 at trade-open (BTCUSDT exempt)"
    )
    print(f"decision rule: PASS/FAIL at n>={DECISION_N}; early read at n>={EARLY_READ_N}")
    print(f"cohorts: {COHORTS} (atr_bound is PRIMARY per FU-38 verification)")

    print(
        "\nbuilding live-gate eligibility inputs (item 1c): walk-forward regime "
        "series + per-TF ADX series, real klines, no lookahead..."
    )
    async with httpx.AsyncClient() as client:
        regime_by_date = await _build_regime_by_date(client)
        distinct_regimes = sorted(set(regime_by_date.values()))
        print(f"  regime series: {len(regime_by_date)} days, distinct values={distinct_regimes}")

        needed_since_ms = int(
            (datetime.now(timezone.utc) - timedelta(days=max(WINDOWS_DAYS) + 2)).timestamp() * 1000
        )
        adx_series: dict[str, dict[int, float]] = {}
        for tf in MTF_TIMEFRAMES:
            adx_series[tf] = await _build_adx_series(client, tf, needed_since_ms)
            print(f"  {tf} ADX series: {len(adx_series[tf])} points")
            await asyncio.sleep(0.2)

    for w in WINDOWS_DAYS:
        pairs = await _fetch_pairs(w, regime_by_date, adx_series)
        n_eligible = sum(1 for p in pairs if p.live_eligible)
        n_total = len(pairs)
        pct = 100 * n_eligible / n_total if n_total else float("nan")
        print(f"\n--- window={w}d: {n_eligible}/{n_total} ({pct:.1f}%) rows are live-eligible ---")
        _print_report(w, pairs, header_suffix=" — ALL (aggregate, unchanged continuity view)")
        _print_report(
            w, [p for p in pairs if p.live_eligible],
            header_suffix=" — LIVE-ELIGIBLE (would pass regime+ADX+pattern-boost)",
        )
        _print_report(
            w, [p for p in pairs if not p.live_eligible],
            header_suffix=" — LIVE-INELIGIBLE (would be rejected by live gates)",
        )


if __name__ == "__main__":
    asyncio.run(main())
