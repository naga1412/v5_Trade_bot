import asyncio
import json
import statistics
import sys

sys.path.insert(0, "/app")

import sqlalchemy as sa

from app.config import get_settings
from app.db.session import get_session_factory

# Follow-up to layer-value-isolation (card review's pattern-layer study).
# Three retrospective, no-code-change analyses on the SAME population
# (LONG, entry_score>=0.36, blacklist excluded, top-20 at trade-open,
# atr_bound; 90d window unless noted). No scoring/weighting changes.

LIVE_LAYERS = ["1", "2", "3", "4", "5", "9"]


def signed_score(layer: dict | None) -> float | None:
    if layer is None:
        return None
    direction = layer.get("direction")
    strength = layer.get("strength")
    if strength is None:
        return None
    if direction == "LONG":
        return float(strength)
    if direction == "SHORT":
        return -float(strength)
    return 0.0


def stats_for(pnls: list[float]) -> tuple[int, float, float, float]:
    n = len(pnls)
    if n == 0:
        return 0, float("nan"), float("nan"), float("nan")
    wr = sum(1 for p in pnls if p > 0) / n
    avg = statistics.mean(pnls)
    se = (statistics.pstdev(pnls) / (n ** 0.5)) if n > 1 else float("nan")
    return n, wr, avg, se


async def main() -> None:
    settings = get_settings()
    blacklist = set(settings.SHADOW_SPOT_BLACKLIST)

    sql = sa.text(
        """
        WITH trades AS (
            SELECT
                s.id, s.symbol, s.opened_at, s.closed_at,
                s.entry_price, s.entry_atr, s.pnl_pct, s.entry_score,
                s.layer_scores,
                (SELECT MAX(snapshot_at) FROM asset_universe
                    WHERE snapshot_at <= s.opened_at) AS rank_snap
            FROM shadow_trades s
            WHERE s.closed_at IS NOT NULL
              AND s.direction = 'LONG'
              AND s.entry_score >= 0.36
              AND s.closed_at >= NOW() - INTERVAL '90 days'
              AND s.entry_price > 0
              AND s.entry_atr > 0
        )
        SELECT t.*, au.rank AS rank_at_trade
        FROM trades t
        LEFT JOIN asset_universe au
          ON au.snapshot_at = t.rank_snap AND au.symbol = t.symbol
        ORDER BY t.opened_at
        """
    )
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(sql)).fetchall()

    trades = []
    for r in rows:
        if r.symbol in blacklist:
            continue
        rank = r.rank_at_trade
        is_top20 = r.symbol == "BTCUSDT" or (rank is not None and rank <= 20)
        if not is_top20:
            continue
        entry_atr = float(r.entry_atr)
        entry_price = float(r.entry_price)
        if (1.5 * entry_atr) > (0.05 * entry_price):
            continue  # cap_bound, excluded
        layer_scores = r.layer_scores if isinstance(r.layer_scores, dict) else json.loads(r.layer_scores)
        trades.append({
            "id": r.id, "pnl_pct": float(r.pnl_pct),
            "entry_score": float(r.entry_score), "layer_scores": layer_scores,
        })
    print(f"population n=90d={len(trades)}", flush=True)

    # ======================================================================
    # ITEM 3 -- the shared-abstain-gate trades: what do they actually run on?
    # ======================================================================
    print("\n" + "=" * 70 + "\nITEM 3 -- shared-abstain-gate subset (L1-L5 all None)\n" + "=" * 70, flush=True)
    all_abstain = [
        t for t in trades
        if all(t["layer_scores"].get(lid) is None for lid in ["1", "2", "3", "4", "5"])
    ]
    print(f"n = {len(all_abstain)} / {len(trades)} ({len(all_abstain)/len(trades)*100:.1f}%)", flush=True)
    print("entry_score distribution for this subset:", flush=True)
    scores = sorted(t["entry_score"] for t in all_abstain)
    if scores:
        print(f"  min={scores[0]:.3f} median={statistics.median(scores):.3f} max={scores[-1]:.3f}", flush=True)
    print("\nwhat's actually non-None for these trades (L6/L7/L8/L9/L10):", flush=True)
    for lid in ["6", "7", "8", "9", "10"]:
        present = sum(1 for t in all_abstain if t["layer_scores"].get(lid) is not None)
        print(f"  L{lid}: present in {present}/{len(all_abstain)}", flush=True)
    both_bucket_pnls = [t["pnl_pct"] for t in all_abstain]
    n, wr, avg, se = stats_for(both_bucket_pnls)
    print(f"\noutcome for this subset: n={n} WR={wr*100:.1f}% avg_pnl%={avg:.3f} SE={se:.3f}", flush=True)

    # ======================================================================
    # ITEM 2 -- layer-contribution variant simulation (static score only,
    # pre-brain_adjust/trap/news -- those multipliers are IDENTICAL across
    # variants for a given trade, so they don't affect the relative
    # comparison; real entry_score is used as-is for the CONTROL row since
    # it's the actual persisted value, not recomputed).
    # IN-SAMPLE -- variants chosen after seeing which layers "worked" in
    # the same population. Not a shipping decision on its own.
    # ======================================================================
    print("\n" + "=" * 70 + "\nITEM 2 -- layer-contribution variants (IN-SAMPLE, static-only)\n" + "=" * 70, flush=True)

    def variant_static(t: dict, layer_ids: list[str]) -> float | None:
        present = [
            (signed_score(t["layer_scores"].get(lid)), t["layer_scores"][lid].get("confidence", 0.0))
            for lid in layer_ids
            if t["layer_scores"].get(lid) is not None
        ]
        if not present:
            return None
        vals = [s * c for s, c in present if s is not None]
        if not vals:
            return None
        static = sum(vals) / len(present)
        return max(-1.0, min(1.0, static))

    variants = {
        "L2-only": ["2"],
        "L2+L1": ["1", "2"],
        "all-current (control, uses REAL entry_score)": None,  # special-cased below
        "minus-noise (drop L3/L4/L5)": ["1", "2", "9"],
    }
    GATE = 0.36  # proxy -- see caveat printed below
    print(f"{'variant':>45} {'n_signals':>10} {'WR':>7} {'avg_pnl%':>9} {'SE':>7}", flush=True)
    for name, layer_ids in variants.items():
        if layer_ids is None:
            gated = trades  # already gated at entry_score>=0.36 by the population query itself
        else:
            gated = []
            for t in trades:
                s = variant_static(t, layer_ids)
                if s is not None and s >= GATE:
                    gated.append(t)
        pnls = [t["pnl_pct"] for t in gated]
        n, wr, avg, se = stats_for(pnls)
        print(
            f"{name:>45} {n:>10} {wr*100:>6.1f}% {avg:>9.3f} {se:>7.3f}"
            if n > 0 else f"{name:>45} {n:>10} {'--':>7} {'--':>9} {'--':>7}",
            flush=True,
        )
    print(
        "\nCAVEAT: variant static scores are pre-brain_adjust/trap/news, "
        "gated at the SAME 0.36 threshold real entry_score uses. This is a "
        "reasonable proxy ONLY because brain_adjust~=1.0 for this whole "
        "window (brain has been running neutral in prod since 2026-08-05, "
        "confirmed separately), trap fires are rare, and L9/news is 100% "
        "dead -- so static approx= entry_score for these real trades. Not "
        "exact. IN-SAMPLE selection -- these variants were picked after "
        "seeing L2's signal in the SAME data. Needs out-of-sample "
        "confirmation before this informs any shipping decision.",
        flush=True,
    )

    # ======================================================================
    # ITEM 1(c) -- retrospective pattern_stats simulation using the REAL
    # writer's exact population (predictions x shadow_trades on signal_id=
    # inputs_hash, ALL closed trades regardless of direction/score/cohort)
    # and exact correctness definition (exit_reason='TAKE_PROFIT'), for the
    # same top-10 pattern_ids already reported. This does NOT fix or write
    # to pattern_stats -- read-only simulation of what it WOULD show.
    # ======================================================================
    print("\n" + "=" * 70 + "\nITEM 1(c) -- what pattern_stats WOULD show if the write path were fixed\n" + "=" * 70, flush=True)
    real_writer_sql = sa.text(
        "SELECT p.layer_scores AS layer_scores, t.exit_reason AS exit_reason "
        "FROM predictions p "
        "INNER JOIN shadow_trades t ON t.signal_id = p.inputs_hash "
        "WHERE t.exit_reason IS NOT NULL"
    )
    async with sf() as session2:
        real_rows = (await session2.execute(real_writer_sql)).all()
    print(f"real writer's population (ALL closed shadow_trades, any direction/score/cohort): n={len(real_rows)}", flush=True)

    pattern_counts: dict[str, list[int]] = {}  # pattern_id -> [n_samples, n_correct]
    for r in real_rows:
        ls = r.layer_scores if isinstance(r.layer_scores, dict) else (json.loads(r.layer_scores) if r.layer_scores else {})
        l2 = ls.get("2")
        if l2 is None:
            continue
        notes = l2.get("notes", "")
        try:
            parsed = json.loads(notes)
        except (json.JSONDecodeError, TypeError):
            continue
        for p in parsed.get("patterns", []):
            pid = p.get("id")
            if not pid:
                continue
            counts = pattern_counts.setdefault(pid, [0, 0])
            counts[0] += 1
            if r.exit_reason == "TAKE_PROFIT":
                counts[1] += 1

    # Same top-10 pattern_ids as the earlier report, by fire count in the
    # narrower LONG/atr_bound/top-20 population (for direct comparability).
    top10_ids = [
        "opening_range_breakout", "mean_reversion_to_ma", "multi_tf_confluence_long",
        "belt_hold", "channel_up", "regression_channel_up", "long_line",
        "rectangle_continuation", "breakout_pullback_long", "weekly_pivot_break",
    ]
    print(f"{'pattern_id':>28} {'n_all':>7} {'sim_accuracy':>13} {'vs_0.5_prior':>13}", flush=True)
    for pid in top10_ids:
        n, correct = pattern_counts.get(pid, [0, 0])
        if n == 0:
            print(f"{pid:>28} {0:>7} {'no data':>13} {'--':>13}", flush=True)
            continue
        acc = correct / n
        print(f"{pid:>28} {n:>7} {acc*100:>12.1f}% {(acc-0.5)*100:>+12.1f}pp", flush=True)


asyncio.run(main())
