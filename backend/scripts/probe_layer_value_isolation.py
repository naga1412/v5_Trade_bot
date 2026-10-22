import asyncio
import json
import statistics
import sys

sys.path.insert(0, "/app")

import sqlalchemy as sa

from app.config import get_settings
from app.db.session import get_session_factory

# Population matches the established convention from
# backend/scripts/mfe_mae_curve.py exactly (direction=LONG,
# entry_score>=0.36, blacklist excluded, top-20 rank at trade-open
# including BTCUSDT exemption, atr_bound cohort per engine.py's
# 1.5*ATR > 5%*entry_price convention) -- reported for both 60d and 90d
# windows since the operator asked for "60-90d".
LIVE_LAYERS = ["1", "2", "3", "4", "5", "9"]
# L6/L8 abstain heavily (excluded per operator instruction -- would just
# be near-all-abstain rows). L7/L10 are permanent stubs (score() always
# returns None in production -- confirmed by reading both files directly
# this session) and are excluded entirely, not even shown as
# all-abstain, since including them could misleadingly imply they
# SOMETIMES fire.
STRONG_THRESHOLD = 0.5  # |signed_score| >= this = "fired strongly"; explicit, not data-derived, so every layer is compared on the same absolute scale


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
    return 0.0  # NEUTRAL


def bucket_for(score: float | None) -> str:
    if score is None:
        return "abstained"
    return "strong" if abs(score) >= STRONG_THRESHOLD else "weak"


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
                s.entry_price, s.entry_atr, s.pnl_pct, s.layer_scores,
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

    trades_90d = []
    for r in rows:
        if r.symbol in blacklist:
            continue
        rank = r.rank_at_trade
        is_top20 = r.symbol == "BTCUSDT" or (rank is not None and rank <= 20)
        if not is_top20:
            continue
        entry_atr = float(r.entry_atr)
        entry_price = float(r.entry_price)
        is_atr_bound = (1.5 * entry_atr) <= (0.05 * entry_price)
        if not is_atr_bound:
            continue
        layer_scores = r.layer_scores if isinstance(r.layer_scores, dict) else json.loads(r.layer_scores)
        trades_90d.append({
            "id": r.id, "closed_at": r.closed_at, "pnl_pct": float(r.pnl_pct),
            "layer_scores": layer_scores,
        })

    print("population: LONG, entry_score>=0.36, blacklist-excluded, top-20-at-trade, atr_bound", flush=True)
    print(f"n (90d) = {len(trades_90d)}", flush=True)

    for window_days in (90, 60):
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        trades = [t for t in trades_90d if t["closed_at"] >= cutoff]
        print(f"\n{'='*70}\nWINDOW: {window_days}d -- n={len(trades)}\n{'='*70}", flush=True)

        # ---- (a)+(b): every live layer, strong/weak/abstain buckets ----
        print("\n-- per-layer bucket table (STRONG_THRESHOLD=|score|>=0.5) --", flush=True)
        print(f"{'layer':>6} {'bucket':>9} {'n':>5} {'WR':>7} {'avg_pnl%':>9} {'SE':>7}", flush=True)
        abstain_rates: dict[str, float] = {}
        for layer_id in LIVE_LAYERS:
            buckets: dict[str, list[float]] = {"strong": [], "weak": [], "abstained": []}
            for t in trades:
                ls = signed_score(t["layer_scores"].get(layer_id))
                buckets[bucket_for(ls)].append(t["pnl_pct"])
            total_n = sum(len(v) for v in buckets.values())
            abstain_rates[layer_id] = (
                len(buckets["abstained"]) / total_n if total_n else float("nan")
            )
            for b in ("strong", "weak", "abstained"):
                n, wr, avg, se = stats_for(buckets[b])
                print(
                    f"{('L'+layer_id):>6} {b:>9} {n:>5} {wr*100:>6.1f}% {avg:>9.3f} {se:>7.3f}"
                    if n > 0 else
                    f"{('L'+layer_id):>6} {b:>9} {n:>5} {'--':>7} {'--':>9} {'--':>7}",
                    flush=True,
                )

        # ---- (c): abstain rate summary ----
        print("\n-- abstain rate per layer --", flush=True)
        for layer_id in LIVE_LAYERS:
            print(f"  L{layer_id}: {abstain_rates[layer_id]*100:.1f}%", flush=True)

        if window_days != 90:
            continue  # pattern hit-rate comparison (d) only needs the larger window

        # ---- (d): pattern_stats assumed vs observed hit rate ----
        print("\n-- (d) top L2 patterns: assumed (pattern_stats) vs observed hit rate --", flush=True)
        pattern_trades: dict[str, list[float]] = {}
        for t in trades:
            l2 = t["layer_scores"].get("2")
            if l2 is None:
                continue
            notes = l2.get("notes", "")
            try:
                parsed = json.loads(notes)
            except (json.JSONDecodeError, TypeError):
                continue
            for p in parsed.get("patterns", []):
                pid = p.get("id")
                if pid:
                    pattern_trades.setdefault(pid, []).append(t["pnl_pct"])

        top_patterns = sorted(pattern_trades.items(), key=lambda kv: -len(kv[1]))[:10]
        pattern_ids = [pid for pid, _ in top_patterns]
        stats_rows = []
        if pattern_ids:
            async with sf() as session2:
                stats_rows = (await session2.execute(
                    sa.text(
                        "SELECT pattern_id, SUM(n_samples) AS n_samples, SUM(n_correct) AS n_correct "
                        "FROM pattern_stats WHERE pattern_id = ANY(:ids) GROUP BY pattern_id"
                    ),
                    {"ids": pattern_ids},
                )).all()
        stored_by_pattern = {
            r.pattern_id: (float(r.n_correct) / float(r.n_samples) if r.n_samples else None)
            for r in stats_rows
        }
        print(f"{'pattern_id':>28} {'n_fires':>8} {'observed_WR':>12} {'stored_acc':>11} {'delta':>7}", flush=True)
        for pid, pnls in top_patterns:
            n, wr, avg, se = stats_for(pnls)
            stored = stored_by_pattern.get(pid)
            stored_str = f"{stored*100:.1f}%" if stored is not None else "no row"
            delta_str = f"{(wr - stored)*100:+.1f}pp" if stored is not None else "--"
            print(f"{pid:>28} {n:>8} {wr*100:>11.1f}% {stored_str:>11} {delta_str:>7}", flush=True)


asyncio.run(main())
