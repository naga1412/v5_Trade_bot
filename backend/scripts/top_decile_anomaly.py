"""Item 3 probe — top-decile anomaly investigation.

Compares two score bands in clean LONG shadow_trades (last 30 days, blacklist excluded):
  - EXTREME band:  abs(entry_score) >= 0.445  (maps to pred_p_win >= 0.30 on clean fit)
  - HIGH band:     abs(entry_score) in [0.36, 0.445)  (maps to pred_p_win in [0.28, 0.30))

For each band reports: WR, avg_pnl, total_pnl, top symbols, entry_hour distribution,
and dominant layers from layer_scores JSONB.

Hypothesis: extreme scores fire at momentum exhaustion — checking if specific symbols,
hours, or layers dominate the extreme band.

Read-only. No code changes.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.config import get_settings

settings = get_settings()
blacklist: set[str] = set(settings.SHADOW_SPOT_BLACKLIST)


async def run(session: AsyncSession) -> None:
    rows = await session.execute(
        sa.text(
            "SELECT symbol, entry_score, pnl_pct, exit_reason, "
            "       date_part('hour', opened_at AT TIME ZONE 'UTC') AS hour_utc, "
            "       layer_scores "
            "FROM shadow_trades "
            "WHERE closed_at >= NOW() - INTERVAL '30 days' "
            "  AND closed_at IS NOT NULL "
            "  AND direction = 'LONG' "
            "  AND entry_score IS NOT NULL "
            "ORDER BY entry_score DESC"
        )
    )
    all_rows = rows.fetchall()

    # Exclude blacklisted symbols
    excluded = sum(1 for r in all_rows if r.symbol in blacklist)
    clean = [r for r in all_rows if r.symbol not in blacklist]
    print(f"SHADOW_SPOT_BLACKLIST: {len(blacklist)} entries — excluded {excluded} rows.")
    print(f"Clean LONG rows (30d): {len(clean)}")

    # Split into bands — using abs(entry_score) for LONG (all positive)
    extreme = [r for r in clean if abs(r.entry_score) >= 0.445]
    high = [r for r in clean if 0.36 <= abs(r.entry_score) < 0.445]
    below = [r for r in clean if abs(r.entry_score) < 0.36]

    def band_stats(label: str, band: list) -> None:
        n = len(band)
        if n == 0:
            print(f"\n{label}: n=0 (no data)")
            return
        wins = sum(1 for r in band if r.pnl_pct > 0)
        wr = 100 * wins / n
        pnls = [r.pnl_pct for r in band]
        avg_pnl = statistics.mean(pnls)
        total_pnl = sum(pnls)
        avg_win = statistics.mean(p for p in pnls if p > 0) if wins else float("nan")
        avg_loss = statistics.mean(p for p in pnls if p <= 0) if n - wins else float("nan")

        tp = sum(1 for r in band if r.exit_reason == "TAKE_PROFIT")
        sl = sum(1 for r in band if r.exit_reason == "STOP_LOSS")
        to = sum(1 for r in band if r.exit_reason == "TIMEOUT")

        print(f"\n{'='*60}")
        print(f"{label}  (n={n})")
        print(f"{'='*60}")
        print(f"  WR:         {wr:.1f}%")
        print(f"  AvgPnl:     {avg_pnl:.4f}%")
        print(f"  TotalPnl:   {total_pnl:.3f}%")
        print(f"  AvgWin:     {avg_win:.4f}%    AvgLoss: {avg_loss:.4f}%")
        print(f"  Exit:       TP={tp} ({100*tp/n:.1f}%)  SL={sl} ({100*sl/n:.1f}%)  TO={to} ({100*to/n:.1f}%)")

        # Symbol distribution
        sym_counts: dict[str, list[float]] = defaultdict(list)
        for r in band:
            sym_counts[r.symbol].append(r.pnl_pct)
        top_syms = sorted(sym_counts.items(), key=lambda x: -len(x[1]))[:8]
        print("\n  Top symbols (by trade count):")
        print(f"  {'Symbol':<16} {'N':>4}  {'WR%':>6}  {'AvgPnl%':>8}")
        for sym, pnls_s in top_syms:
            w = sum(1 for p in pnls_s if p > 0)
            print(f"  {sym:<16} {len(pnls_s):>4}  {100*w/len(pnls_s):>6.1f}  {statistics.mean(pnls_s):>8.4f}")

        # Hour distribution (UTC)
        hour_counts: dict[int, list[float]] = defaultdict(list)
        for r in band:
            hour_counts[int(r.hour_utc)].append(r.pnl_pct)
        top_hours = sorted(hour_counts.items(), key=lambda x: -len(x[1]))[:5]
        print("\n  Top entry hours UTC (by trade count):")
        print(f"  {'Hour':>5} {'N':>4}  {'WR%':>6}")
        for hr, pnls_h in top_hours:
            w = sum(1 for p in pnls_h if p > 0)
            print(f"  {hr:>5}  {len(pnls_h):>4}  {100*w/len(pnls_h):>6.1f}%")

        # Layer scores — parse JSONB and tally dominant layers by magnitude
        layer_totals: dict[str, float] = defaultdict(float)
        layer_counts: dict[str, int] = defaultdict(int)
        for r in band:
            ls = r.layer_scores
            if ls is None:
                continue
            if isinstance(ls, str):
                try:
                    ls = json.loads(ls)
                except Exception:
                    continue
            if not isinstance(ls, dict):
                continue
            for layer_key, val in ls.items():
                try:
                    layer_totals[layer_key] += abs(float(val))
                    layer_counts[layer_key] += 1
                except (TypeError, ValueError):
                    pass
        if layer_counts:
            sorted_layers = sorted(layer_totals.items(), key=lambda x: -x[1])[:8]
            print("\n  Layer contribution (avg abs score × count = total magnitude):")
            print(f"  {'Layer':<20} {'N':>4}  {'AvgAbs':>8}  {'TotalMag':>10}")
            for lk, total_mag in sorted_layers:
                cnt = layer_counts[lk]
                print(f"  {lk:<20} {cnt:>4}  {total_mag/cnt:>8.4f}  {total_mag:>10.3f}")
        else:
            print("\n  layer_scores: no data available")

    band_stats("EXTREME band  abs(entry_score) >= 0.445  [pred_p_win >= 0.30 territory]", extreme)
    band_stats("HIGH band     abs(entry_score) in [0.36, 0.445)  [pred_p_win in [0.28, 0.30)]", high)
    band_stats("BELOW band    abs(entry_score) < 0.36  [current + proposed gate territory]", below)

    # Score/pnl correlation for high+extreme combined
    combined = extreme + high
    if len(combined) >= 3:
        scores = [abs(r.entry_score) for r in combined]
        pnls = [r.pnl_pct for r in combined]
        n = len(combined)
        sx = sum(scores)
        sy = sum(pnls)
        sx2 = sum(x**2 for x in scores)
        sxy = sum(x * y for x, y in zip(scores, pnls))
        denom = (n * sx2 - sx**2)
        if denom != 0:
            corr_num = n * sxy - sx * sy
            corr = corr_num / (denom**0.5 * (n * sum(y**2 for y in pnls) - sy**2)**0.5) if (n * sum(y**2 for y in pnls) - sy**2) > 0 else float("nan")
            print(f"\nPearson correlation (score vs pnl, combined high+extreme, n={n}): {corr:.4f}")
            if corr < -0.1:
                print("  -> NEGATIVE correlation: higher score associated with worse pnl (momentum exhaustion consistent)")
            elif corr > 0.1:
                print("  -> POSITIVE correlation: higher score associated with better pnl")
            else:
                print("  -> Near-zero correlation: score magnitude does not predict pnl in this range")

    print("\nDone.")


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL.replace("+asyncpg", "+asyncpg"), echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await run(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
