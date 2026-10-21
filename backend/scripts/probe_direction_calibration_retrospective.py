"""direction_calibration retrospective measurement (2026-08-20 layer audit item 1).

READ-ONLY, no code change. Answers the operator's 3-part question about
whether wiring app.core.scoring.calibration into the live aggregator would
help before touching any call site, plus the operator's PRE-COMMITTED
decision rule (set before this probe's results were seen, to avoid
post-hoc rationalization):

  WIRE IT if: avg_pnl_pct improves materially on SURVIVES vs today's full
  population, AND the surviving count stays usable (this lane already
  runs ~0.43 live trades/day -- a big volume cut needs a real gain).

  DO NOT WIRE IT if: expectancy is roughly unchanged and only volume
  falls (gate change wearing a calibration costume -- MIN_ENTRY_SCORE_LONG
  already does that with one env var, no new scoring-path code).

  DO NOT WIRE IT if: the multiplier is near-static across historical
  recompute points (part d) -- a constant is not a calibration, a
  permanent fixed multiplier is just a rescaled threshold.

  n and SE reported on both sides for every comparison -- an apparent
  improvement inside the error bar is not a result.

  (a) What multiplier would compute_calibration_for_predictor() return
      RIGHT NOW for LONG/SHORT/NEUTRAL? Calls the REAL production function
      directly (no formula re-transcription) against the real DB.
  (b) Apply that multiplier retrospectively to the last 90d of LONG+SHORT
      shadow_trades signals: score distribution shift, how many still clear
      the +-0.36 gate, WR/avg_pnl_pct (with n and SE) for the surviving set
      vs today's full population.
  (c) THE DECIDING QUESTION: compare the DROPPED subset (passes the gate
      today, would NOT survive after calibration) against the SURVIVING
      subset, with n and SE on both. If DROPPED has meaningfully worse
      WR/avg_pnl than SURVIVING (outside the SE band), calibration
      discriminates. If they're statistically similar, this is a gate
      change wearing a calibration costume.
  (d) STABILITY CHECK: recompute the multiplier compute_calibration_for_
      predictor() would have returned at several past points across the
      90d window (same 50-row-per-direction lookback logic, bounded by
      validated_at <= that checkpoint). Reports the trajectory and its
      range/stdev per direction -- near-static means it's not tracking
      anything, just a fixed rescale.

Population mirrors this project's existing mfe_mae_curve.py filter (same
blacklist/price-sanity guards) but covers BOTH directions instead of
LONG-only, since direction_calibration is a per-direction multiplier and
SHORT's behavior is exactly what's in question.

The aggregator applies calibration to the FINAL score (after direction_
penalty), not to individual layer strengths -- see
app/core/scoring/aggregator.py:99 `final = raw_final * direction_penalty
* calibration_multiplier`. Since calibration_multiplier was 1.0 for every
row ever persisted (never wired to any call site), each historical
shadow_trades.entry_score IS raw_final*direction_penalty already -- so
simulating "what if calibration had been on" is exactly
`new_score = clip(entry_score * multiplier[direction], -1, 1)`, no need
to reverse-engineer raw_final.

Usage (inside backend container via ops-debug probe, base64-embedded --
this is a one-off measurement script, not a permanent probe):
    docker compose exec -T backend python3 -
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from statistics import mean, stdev

sys.path.insert(0, "/app")

import sqlalchemy as sa  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.core.scoring.calibration import (  # noqa: E402
    DEFAULT_LOOKBACK_N,
    MIN_SAMPLE_PER_DIRECTION,
    ValidationSample,
    compute_calibration_for_predictor,
    compute_direction_multipliers,
    load_recent_validations,
)

ENTRY_THRESHOLD: float = 0.36
LOOKBACK_DAYS: int = 90
N_CHECKPOINTS: int = 6


def _blacklist() -> set[str]:
    return set(get_settings().SHADOW_SPOT_BLACKLIST)


async def _part_a(session_factory) -> dict[str, float]:
    print("=" * 78)
    print("PART (a) -- what compute_calibration_for_predictor() returns RIGHT NOW")
    print("=" * 78)

    async with session_factory() as session:
        total_counts = (await session.execute(text(
            "SELECT direction, COUNT(*) AS n FROM prediction_validations "
            "WHERE validated_at IS NOT NULL GROUP BY direction"
        ))).all()
    print("\nFull prediction_validations population (validated rows), by direction:")
    total = 0
    for r in total_counts:
        print(f"  {r.direction:<8} n={r.n}")
        total += r.n
    print(f"  TOTAL validated rows = {total}")

    # The REAL function -- default lookback_n=50 per direction, min_n=20.
    samples = await load_recent_validations(session_factory, lookback_n=DEFAULT_LOOKBACK_N)
    by_dir: dict[str, list[float]] = {}
    for s in samples:
        by_dir.setdefault(s.direction, []).append(s.pnl_pct)

    multipliers = await compute_calibration_for_predictor(session_factory)

    print(
        f"\ncompute_calibration_for_predictor() reads the most recent "
        f"{DEFAULT_LOOKBACK_N} validated rows PER DIRECTION (not the full "
        f"{total}-row table) -- that lookback window, not the table size, "
        f"is what determines the multiplier below. min_n={MIN_SAMPLE_PER_DIRECTION} "
        f"required per direction or the multiplier stays 1.0 (no-op)."
    )
    print(f"\n{'direction':<10} {'n_in_window':>12} {'avg_pnl_pct':>13} {'multiplier':>11}")
    for d in ("LONG", "SHORT", "NEUTRAL"):
        pnls = by_dir.get(d, [])
        avg = mean(pnls) if pnls else float("nan")
        print(f"{d:<10} {len(pnls):>12} {avg:>13.4f} {multipliers.get(d, 1.0):>11.4f}")

    print(f"\nRESULT: {multipliers}")
    return multipliers


class Row:
    __slots__ = ("direction", "entry_score", "pnl_pct", "win")

    def __init__(self, direction: str, entry_score: float, pnl_pct: float) -> None:
        self.direction = direction
        self.entry_score = entry_score
        self.pnl_pct = pnl_pct
        self.win = pnl_pct > 0


async def _load_population(session_factory, bl: set[str]) -> list[Row]:
    sql = text(
        """
        SELECT s.direction, s.entry_score, s.pnl_pct, s.symbol
        FROM shadow_trades s
        WHERE s.closed_at IS NOT NULL
          AND s.closed_at >= NOW() - INTERVAL '{days} days'
          AND s.entry_price > 0 AND s.stop_loss > 0 AND s.take_profit > 0
          AND s.entry_atr > 0
          AND s.direction IN ('LONG', 'SHORT')
          AND (
                (s.direction = 'LONG'  AND s.entry_score >=  0.36)
             OR (s.direction = 'SHORT' AND s.entry_score <= -0.36)
          )
        """.format(days=LOOKBACK_DAYS)
    )
    async with session_factory() as session:
        rows = (await session.execute(sql)).fetchall()
    out: list[Row] = []
    for r in rows:
        if r.symbol in bl:
            continue
        out.append(Row(str(r.direction), float(r.entry_score), float(r.pnl_pct)))
    return out


def _stats(rows: list[Row]) -> tuple[int, float, float, float, float]:
    """Returns (n, win_rate_pct, wr_se_pp, avg_pnl_pct, avg_se_pct).

    wr_se_pp: standard error of the win-rate proportion, in percentage
    points (sqrt(p*(1-p)/n) * 100). avg_se_pct: standard error of the
    mean pnl_pct (stdev/sqrt(n)). Both NaN when n < 2 -- an SE on a
    single point is meaningless, not zero.
    """
    n = len(rows)
    if n == 0:
        return 0, float("nan"), float("nan"), float("nan"), float("nan")
    p = sum(1 for r in rows if r.win) / n
    wr = 100.0 * p
    wr_se = 100.0 * (p * (1 - p) / n) ** 0.5 if n >= 2 else float("nan")
    avg = mean(r.pnl_pct for r in rows)
    avg_se = (stdev(r.pnl_pct for r in rows) / (n ** 0.5)) if n >= 2 else float("nan")
    return n, wr, wr_se, avg, avg_se


def _fmt_stat(label: str, n: int, wr: float, wr_se: float, avg: float, avg_se: float) -> str:
    return (
        f"{label} n={n}  WR={wr:.1f}%+-{wr_se:.1f}pp  "
        f"avg_pnl_pct={avg:+.4f}%+-{avg_se:.4f}%"
    )


async def _part_b_and_c(session_factory, multipliers: dict[str, float]) -> None:
    print("\n" + "=" * 78)
    print(f"PART (b)/(c) -- retrospective application to last {LOOKBACK_DAYS}d shadow_trades")
    print("=" * 78)

    bl = _blacklist()
    rows = await _load_population(session_factory, bl)
    n_long = sum(1 for r in rows if r.direction == "LONG")
    n_short = sum(1 for r in rows if r.direction == "SHORT")
    print(
        f"\nToday's population (entry_score clears +-{ENTRY_THRESHOLD} today, "
        f"blacklist excluded, {LOOKBACK_DAYS}d): n={len(rows)} "
        f"(LONG={n_long}, SHORT={n_short})"
    )
    if not rows:
        print("No rows -- nothing further to compute.")
        return

    stats0 = _stats(rows)
    print("\nTODAY'S FULL POPULATION (calibration OFF, as currently live):")
    print("  " + _fmt_stat("", *stats0))
    for d in ("LONG", "SHORT"):
        sub = [r for r in rows if r.direction == d]
        print(f"  [{d}] " + _fmt_stat("", *_stats(sub)))

    # Score distribution shift.
    before_scores = [r.entry_score for r in rows]
    after_scores = []
    for r in rows:
        mult = multipliers.get(r.direction, 1.0)
        new_score = max(-1.0, min(1.0, r.entry_score * mult))
        after_scores.append(new_score)

    print(f"\nSCORE DISTRIBUTION SHIFT (n={len(rows)}):")
    print(
        f"  BEFORE: mean={mean(before_scores):+.4f} "
        f"stdev={stdev(before_scores) if len(before_scores) > 1 else 0.0:.4f} "
        f"min={min(before_scores):+.4f} max={max(before_scores):+.4f}"
    )
    print(
        f"  AFTER:  mean={mean(after_scores):+.4f} "
        f"stdev={stdev(after_scores) if len(after_scores) > 1 else 0.0:.4f} "
        f"min={min(after_scores):+.4f} max={max(after_scores):+.4f}"
    )

    survives: list[Row] = []
    dropped: list[Row] = []
    for r, new_score in zip(rows, after_scores):
        clears = (
            (r.direction == "LONG" and new_score >= ENTRY_THRESHOLD)
            or (r.direction == "SHORT" and new_score <= -ENTRY_THRESHOLD)
        )
        if clears:
            survives.append(r)
        else:
            dropped.append(r)

    stats_s = _stats(survives)
    stats_d = _stats(dropped)
    n_s, wr_s, wr_se_s, avg_s, avg_se_s = stats_s
    n_d, wr_d, wr_se_d, avg_d, avg_se_d = stats_d
    print("\nAFTER CALIBRATION -- population split:")
    print(f"  SURVIVES (still clears +-{ENTRY_THRESHOLD}): " + _fmt_stat("", *stats_s))
    print(f"  DROPPED  (no longer clears):        " + _fmt_stat("", *stats_d))
    for d in ("LONG", "SHORT"):
        sub_s = [r for r in survives if r.direction == d]
        sub_d = [r for r in dropped if r.direction == d]
        print(f"    [{d}] SURVIVES " + _fmt_stat("", *_stats(sub_s)) + "  |  DROPPED " + _fmt_stat("", *_stats(sub_d)))

    print(f"\nVOLUME CHANGE: {len(rows)} -> {n_s}  ({100.0 * n_s / len(rows) - 100.0:+.1f}%)")

    print("\n" + "=" * 78)
    print("PART (c) -- THE DECIDING QUESTION: discriminates, or just scales?")
    print("=" * 78)
    if n_s > 0 and n_d > 0:
        wr_gap = wr_s - wr_d
        pnl_gap = avg_s - avg_d
        # Combined SE of the gap (independent samples): sqrt(se1^2 + se2^2).
        wr_gap_se = (wr_se_s ** 2 + wr_se_d ** 2) ** 0.5 if n_s >= 2 and n_d >= 2 else float("nan")
        pnl_gap_se = (avg_se_s ** 2 + avg_se_d ** 2) ** 0.5 if n_s >= 2 and n_d >= 2 else float("nan")
        print(
            f"\nSURVIVES vs DROPPED gap: WR {wr_gap:+.1f}pp (SE {wr_gap_se:.1f}pp), "
            f"avg_pnl_pct {pnl_gap:+.4f}% (SE {pnl_gap_se:.4f}%)"
        )
        gap_outside_se = (
            not (wr_gap_se != wr_gap_se) and abs(wr_gap) > 2 * wr_gap_se
        ) or (
            not (pnl_gap_se != pnl_gap_se) and abs(pnl_gap) > 2 * pnl_gap_se
        )
        if not gap_outside_se:
            verdict = (
                "GAP IS INSIDE THE ERROR BAR (< 2 SE) -- SURVIVES and DROPPED "
                "are not distinguishable at this sample size. Per the "
                "pre-committed rule: an apparent improvement inside the error "
                "bar is not a result. Default to GATE CHANGE WEARING A "
                "CALIBRATION COSTUME unless a larger sample later shows "
                "otherwise."
            )
        elif abs(wr_gap) < 5.0 and abs(pnl_gap) < 0.05:
            verdict = (
                "GATE CHANGE WEARING A CALIBRATION COSTUME -- gap clears 2 SE "
                "but is small in absolute terms. SURVIVES and DROPPED look "
                "similar enough that this reads as volume-cutting, not "
                "discrimination."
            )
        else:
            verdict = (
                "LOOKS DISCRIMINATING -- gap clears 2 SE AND is large in "
                "absolute terms. Consistent with calibration down-weighting "
                "the specifically bad trades on that side, not just cutting "
                "the marginal ones uniformly."
            )
        print(f"\nVERDICT (mechanical, not a recommendation): {verdict}")
    else:
        print("\nOne of SURVIVES/DROPPED is empty -- cannot compare, report as inconclusive.")

    # Newly-qualifying: below threshold TODAY but would clear it after
    # calibration (only possible for a direction being amplified, mult>1).
    print("\nChecking for NEWLY-QUALIFYING signals (below today's gate, would clear after calibration):")
    for d in ("LONG", "SHORT"):
        mult = multipliers.get(d, 1.0)
        if mult <= 1.0:
            print(f"  [{d}] multiplier={mult:.4f} <= 1.0 -- no amplification, cannot newly qualify.")
            continue
        sql = text(
            f"""
            SELECT s.entry_score, s.pnl_pct, s.symbol
            FROM shadow_trades s
            WHERE s.closed_at IS NOT NULL
              AND s.closed_at >= NOW() - INTERVAL '{LOOKBACK_DAYS} days'
              AND s.direction = :d
              AND s.entry_price > 0 AND s.stop_loss > 0 AND s.take_profit > 0
              AND ((:d = 'LONG'  AND s.entry_score <  0.36 AND s.entry_score > 0)
                OR (:d = 'SHORT' AND s.entry_score > -0.36 AND s.entry_score < 0))
            """
        )
        async with session_factory() as session:
            below_rows = (await session.execute(sql, {"d": d})).fetchall()
        below_rows = [r for r in below_rows if r.symbol not in bl]
        newly = []
        for r in below_rows:
            new_score = max(-1.0, min(1.0, float(r.entry_score) * mult))
            clears = (
                (d == "LONG" and new_score >= ENTRY_THRESHOLD)
                or (d == "SHORT" and new_score <= -ENTRY_THRESHOLD)
            )
            if clears:
                newly.append(Row(d, float(r.entry_score), float(r.pnl_pct)))
        if newly:
            print(f"  [{d}] " + _fmt_stat("newly-qualifying:", *_stats(newly)))
        else:
            print(f"  [{d}] 0 newly-qualifying trades in the sub-threshold pool.")


async def _load_samples_as_of(
    session_factory, as_of: datetime, lookback_n: int,
) -> list[ValidationSample]:
    """Same logic as calibration.load_recent_validations, but bounded to
    rows validated at or before `as_of` -- reconstructs what the real
    function would have returned if run AT that past moment."""
    samples: list[ValidationSample] = []
    async with session_factory() as session:
        for direction in ("LONG", "SHORT", "NEUTRAL"):
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT direction, was_correct, pnl_pct "
                        "FROM prediction_validations "
                        "WHERE validated_at IS NOT NULL AND validated_at <= :asof "
                        "AND direction = :d "
                        "ORDER BY validated_at DESC LIMIT :n"
                    ),
                    {"d": direction, "n": lookback_n, "asof": as_of},
                )
            ).all()
            for r in rows:
                samples.append(ValidationSample(
                    direction=str(r.direction),
                    was_correct=bool(r.was_correct),
                    pnl_pct=float(r.pnl_pct) if r.pnl_pct is not None else 0.0,
                ))
    return samples


async def _part_d(session_factory) -> None:
    print("\n" + "=" * 78)
    print(f"PART (d) -- STABILITY CHECK: multiplier at {N_CHECKPOINTS} historical points")
    print("=" * 78)
    print(
        "\nRecomputes what compute_calibration_for_predictor()'s exact logic "
        "would have returned AS OF each checkpoint below (same 50-row-per-"
        "direction lookback, bounded to validated_at <= checkpoint). A "
        "near-static series means this isn't tracking anything real -- "
        "per the pre-committed rule, DO NOT WIRE in that case."
    )

    now = datetime.now(timezone.utc)
    checkpoints = [
        now - timedelta(days=LOOKBACK_DAYS * (N_CHECKPOINTS - 1 - i) / (N_CHECKPOINTS - 1))
        for i in range(N_CHECKPOINTS)
    ]

    series: dict[str, list[float]] = {"LONG": [], "SHORT": []}
    print(f"\n{'checkpoint (UTC)':<22} {'LONG mult':>10} {'LONG n':>8} {'SHORT mult':>11} {'SHORT n':>8}")
    for cp in checkpoints:
        samples = await _load_samples_as_of(session_factory, cp, DEFAULT_LOOKBACK_N)
        mults = compute_direction_multipliers(samples, min_n=MIN_SAMPLE_PER_DIRECTION)
        n_long = sum(1 for s in samples if s.direction == "LONG")
        n_short = sum(1 for s in samples if s.direction == "SHORT")
        series["LONG"].append(mults["LONG"])
        series["SHORT"].append(mults["SHORT"])
        print(
            f"{cp.strftime('%Y-%m-%d %H:%M'):<22} {mults['LONG']:>10.4f} {n_long:>8} "
            f"{mults['SHORT']:>11.4f} {n_short:>8}"
        )

    print("\nStability summary:")
    for d in ("LONG", "SHORT"):
        vals = series[d]
        rng = max(vals) - min(vals)
        sd = stdev(vals) if len(vals) > 1 else 0.0
        near_static = rng < 0.05
        print(
            f"  [{d}] range={rng:.4f}  stdev={sd:.4f}  "
            f"{'NEAR-STATIC (<0.05 range) -- DO NOT WIRE per pre-committed rule' if near_static else 'varies meaningfully across checkpoints'}"
        )


async def main() -> None:
    print("direction_calibration retrospective measurement -- READ-ONLY, no writes.")
    session_factory = get_session_factory()
    multipliers = await _part_a(session_factory)
    await _part_b_and_c(session_factory, multipliers)
    await _part_d(session_factory)
    print("\nDone. Measurement only -- no calibration wired into any live call site.")


if __name__ == "__main__":
    asyncio.run(main())
