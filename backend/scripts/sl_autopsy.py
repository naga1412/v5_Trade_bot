"""SL autopsy — ops-debug probe 'sl-autopsy'.

Diagnoses whether the 70%-SL-exit rate reflects stops placed inside the noise
band for closed LONG shadow_trades in the last 30 days.

Computed fields (all derived from stored columns; no model needed):
  sl_dist_pct  = abs(entry_price - stop_loss) / entry_price * 100
  atr_pct      = entry_atr / entry_price * 100   (entry_atr is raw price units)
  sl_atr_ratio = sl_dist_pct / atr_pct            (design target: 1.5×)

The SL formula in engine.py is:
  sl = max(entry - 1.5 × atr,  entry × 0.95)
So sl_atr_ratio ≤ 1.5 always; values below 1.5 mean the 5%-floor kicked in.

Output: overall summary, ratio distribution by exit_reason, per-symbol table.
Raw tables only — no recommendations.

Usage (inside backend container via ops-debug.yml probe):
    docker compose exec -T backend python /app/scripts/sl_autopsy.py
"""
from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.config import get_settings
from app.db.session import get_session_factory

RATIO_BRACKETS = [
    (0.0, 0.5, "< 0.5"),
    (0.5, 1.0, "0.5 – 1.0"),
    (1.0, 1.3, "1.0 – 1.3"),
    (1.3, 1.45, "1.3 – 1.45"),
    (1.45, 1.55, "1.45 – 1.55  (≈ nominal 1.5×)"),
    (1.55, 999.0, "> 1.55"),
]


def _bracket(ratio: float) -> str:
    for lo, hi, label in RATIO_BRACKETS:
        if lo <= ratio < hi:
            return label
    return "> 1.55"


def _mean(vals: list[float]) -> str:
    if not vals:
        return "  n/a"
    return f"{sum(vals) / len(vals):>6.3f}"


async def main() -> None:
    settings = get_settings()
    blacklist: set[str] = set(settings.SHADOW_SPOT_BLACKLIST)

    sf = get_session_factory()
    async with sf() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT symbol, exit_reason, pnl_pct,"
                    " entry_price, stop_loss, entry_atr"
                    " FROM shadow_trades"
                    " WHERE closed_at IS NOT NULL"
                    "   AND direction = 'LONG'"
                    "   AND closed_at >= NOW() - INTERVAL '30 days'"
                    "   AND entry_price > 0"
                    "   AND entry_atr > 0"
                    " ORDER BY closed_at ASC"
                )
            )
        ).fetchall()

    excluded_n = sum(1 for r in rows if r.symbol in blacklist)
    rows = [r for r in rows if r.symbol not in blacklist]
    print(f"SHADOW_SPOT_BLACKLIST: {len(blacklist)} entries — excluded {excluded_n} rows.")

    if not rows:
        print("ERROR: no closed LONG shadow_trades in the last 30 days (after blacklist filter)")
        return

    # Compute derived fields
    records: list[dict] = []
    for r in rows:
        ep = float(r.entry_price)
        sl = float(r.stop_loss)
        atr = float(r.entry_atr)
        sl_dist_pct = abs(ep - sl) / ep * 100.0
        atr_pct = atr / ep * 100.0
        sl_atr_ratio = sl_dist_pct / atr_pct if atr_pct > 0 else float("nan")
        records.append(
            {
                "symbol": r.symbol,
                "exit_reason": r.exit_reason or "UNKNOWN",
                "pnl_pct": float(r.pnl_pct or 0.0),
                "sl_dist_pct": sl_dist_pct,
                "atr_pct": atr_pct,
                "sl_atr_ratio": sl_atr_ratio,
            }
        )

    n = len(records)
    by_exit: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        by_exit[rec["exit_reason"]].append(rec)

    # ── Overall summary ───────────────────────────────────────────────────────
    print("=" * 72)
    print(f"SL AUTOPSY  —  LONG shadow_trades, last 30 days  (n={n})")
    print("=" * 72)
    print(f"\n{'Exit reason':<14}  {'N':>5}  {'%':>5}  "
          f"{'AvgSL%':>7}  {'AvgATR%':>8}  {'AvgRatio':>9}  {'AvgPnl%':>8}")
    print(f"  {'-'*14}  {'-'*5}  {'-'*5}  {'-'*7}  {'-'*8}  {'-'*9}  {'-'*8}")
    for reason in ("STOP_LOSS", "TAKE_PROFIT", "TIMEOUT", "UNKNOWN"):
        recs = by_exit.get(reason, [])
        if not recs:
            continue
        avg_sl = sum(r["sl_dist_pct"] for r in recs) / len(recs)
        avg_atr = sum(r["atr_pct"] for r in recs) / len(recs)
        avg_ratio = sum(r["sl_atr_ratio"] for r in recs) / len(recs)
        avg_pnl = sum(r["pnl_pct"] for r in recs) / len(recs)
        pct = 100.0 * len(recs) / n
        print(f"  {reason:<14}  {len(recs):>5}  {pct:>5.1f}  "
              f"{avg_sl:>7.3f}  {avg_atr:>8.3f}  {avg_ratio:>9.3f}  {avg_pnl:>8.3f}")

    # ── Ratio distribution by exit reason ────────────────────────────────────
    print("\n\nsl_atr_ratio DISTRIBUTION  (design target = 1.5x; floor=5% -> ratio < 1.5)")
    print(f"{'Bracket':<28}  {'SL exits':>9}  {'TP exits':>9}  {'TIMEOUT':>8}  {'Total':>6}")
    print(f"  {'-'*28}  {'-'*9}  {'-'*9}  {'-'*8}  {'-'*6}")

    sl_recs = by_exit.get("STOP_LOSS", [])
    tp_recs = by_exit.get("TAKE_PROFIT", [])
    to_recs = by_exit.get("TIMEOUT", [])

    for _, _, label in RATIO_BRACKETS:
        sl_n = sum(1 for r in sl_recs if _bracket(r["sl_atr_ratio"]) == label)
        tp_n = sum(1 for r in tp_recs if _bracket(r["sl_atr_ratio"]) == label)
        to_n = sum(1 for r in to_recs if _bracket(r["sl_atr_ratio"]) == label)
        tot = sl_n + tp_n + to_n
        if tot == 0:
            continue
        print(f"  {label:<28}  {sl_n:>9}  {tp_n:>9}  {to_n:>8}  {tot:>6}")

    # ── Per-symbol summary ────────────────────────────────────────────────────
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        by_symbol[rec["symbol"]].append(rec)

    print("\n\nPER-SYMBOL  (sorted by trade count desc)")
    print(
        f"  {'Symbol':<12}  {'N':>4}  {'SL%':>5}  {'TP%':>5}  {'TO%':>5}"
        f"  {'AvgSLdist%':>10}  {'AvgATR%':>8}  {'AvgRatio':>9}"
        f"  {'SL AvgPnl%':>10}  {'TP AvgPnl%':>10}"
    )
    print(
        f"  {'-'*12}  {'-'*4}  {'-'*5}  {'-'*5}  {'-'*5}"
        f"  {'-'*10}  {'-'*8}  {'-'*9}"
        f"  {'-'*10}  {'-'*10}"
    )

    for sym, recs in sorted(by_symbol.items(), key=lambda kv: -len(kv[1])):
        sn = len(recs)
        sl_n = sum(1 for r in recs if r["exit_reason"] == "STOP_LOSS")
        tp_n = sum(1 for r in recs if r["exit_reason"] == "TAKE_PROFIT")
        to_n = sum(1 for r in recs if r["exit_reason"] == "TIMEOUT")
        avg_sl_dist = sum(r["sl_dist_pct"] for r in recs) / sn
        avg_atr = sum(r["atr_pct"] for r in recs) / sn
        avg_ratio = sum(r["sl_atr_ratio"] for r in recs) / sn
        sl_pnl_vals = [r["pnl_pct"] for r in recs if r["exit_reason"] == "STOP_LOSS"]
        tp_pnl_vals = [r["pnl_pct"] for r in recs if r["exit_reason"] == "TAKE_PROFIT"]
        sl_pnl_str = _mean(sl_pnl_vals)
        tp_pnl_str = _mean(tp_pnl_vals)
        sl_pct = 100.0 * sl_n / sn
        tp_pct = 100.0 * tp_n / sn
        to_pct = 100.0 * to_n / sn
        print(
            f"  {sym:<12}  {sn:>4}  {sl_pct:>5.1f}  {tp_pct:>5.1f}  {to_pct:>5.1f}"
            f"  {avg_sl_dist:>10.3f}  {avg_atr:>8.3f}  {avg_ratio:>9.3f}"
            f"  {sl_pnl_str:>10}  {tp_pnl_str:>10}"
        )

    print("\nDone.")


asyncio.run(main())
