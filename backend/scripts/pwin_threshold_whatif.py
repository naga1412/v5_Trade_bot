"""p_win threshold what-if grid — ops-debug probe 'pwin-threshold-whatif'.

Fits the same isotonic models used by pwin_calibration_report (oldest 80% of
closed shadow_trades). On the validation window (newest 20%), evaluates how
different lower-bound thresholds on predicted p_win change trade count, WR,
avg_pnl%, sum_pnl%, and expectancy.

Variants per direction:
  1. Lower-bound-only: keep pred_p_win >= T  for T in {0.24, 0.26, 0.28, 0.30}
  2. Window (top-decile excluded): keep T <= pred_p_win <= 0.35
  3. Top-decile-excluded only: keep pred_p_win < 90th-percentile of val preds

Output: raw tables only — no advice, no threshold recommendations.

Usage (inside backend container via ops-debug.yml probe):
    docker compose exec -T backend python /app/scripts/pwin_threshold_whatif.py
"""
from __future__ import annotations

import asyncio
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from sqlalchemy import text

from app.core.scoring.p_win_calibrator import (
    P_WIN_MODEL_PATH_LONG,
    P_WIN_MODEL_PATH_SHORT,
    fit_p_win_models,
)
from app.core.scoring.types import Direction
from app.db.session import get_session_factory


def _stats(pnls: list[float]) -> tuple[float, float, float, float, float, float]:
    """Return (n, wr, avg_pnl, sum_pnl, avg_win, avg_loss)."""
    n = len(pnls)
    if n == 0:
        nan = float("nan")
        return 0, nan, nan, nan, nan, nan
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr = 100.0 * len(wins) / n
    avg_pnl = sum(pnls) / n
    sum_pnl = sum(pnls)
    avg_win = sum(wins) / len(wins) if wins else float("nan")
    avg_loss = sum(losses) / len(losses) if losses else float("nan")
    return n, wr, avg_pnl, sum_pnl, avg_win, avg_loss


def _expectancy(wr_pct: float, avg_win: float, avg_loss: float) -> float:
    if any(v != v for v in (wr_pct, avg_win, avg_loss)):  # nan check
        return float("nan")
    wr = wr_pct / 100.0
    return wr * avg_win + (1.0 - wr) * avg_loss


def _print_row(label: str, n_total: int, pnls: list[float]) -> None:
    n, wr, avg_pnl, sum_pnl, avg_win, avg_loss = _stats(pnls)
    vol_pct = 100.0 * n / n_total if n_total > 0 else float("nan")
    exp = _expectancy(wr, avg_win, avg_loss)
    print(
        f"  {label:<30}  {n:>6}  {vol_pct:>8.1f}%  {wr:>7.1f}%"
        f"  {avg_pnl:>9.3f}  {sum_pnl:>10.3f}  {exp:>11.4f}"
    )


HEADER = (
    f"  {'Scenario':<30}  {'Kept':>6}  {'Vol%':>9}  {'WR%':>8}"
    f"  {'AvgPnl%':>9}  {'SumPnl%':>10}  {'Expectancy':>11}"
)
SEP = "  " + "-" * 30 + "  " + "  ".join(["-" * w for w in [6, 9, 8, 9, 10, 11]])


async def main() -> None:
    sf = get_session_factory()
    async with sf() as session:
        print("Fitting p_win models (train = oldest 80% of closed shadow_trades)...")
        await fit_p_win_models(session)

        rows = (
            await session.execute(
                text(
                    "SELECT entry_score, direction, pnl_pct"
                    " FROM shadow_trades"
                    " WHERE closed_at IS NOT NULL"
                    " ORDER BY closed_at ASC"
                )
            )
        ).fetchall()

    if not rows:
        print("ERROR: no closed shadow_trades found")
        return

    split = int(len(rows) * 0.8)
    val_rows = rows[split:]
    print(
        f"\nTotal closed trades: {len(rows)}"
        f"  Train: {split}  Val: {len(val_rows)}\n"
    )

    for direction, path in [
        (Direction.LONG, P_WIN_MODEL_PATH_LONG),
        (Direction.SHORT, P_WIN_MODEL_PATH_SHORT),
    ]:
        if not path.exists():
            print(f"{direction}: model not fitted — skipping\n")
            continue
        with open(path, "rb") as f:
            model = pickle.load(f)

        d_rows = [
            (r.entry_score, float(r.pnl_pct or 0.0))
            for r in val_rows
            if r.direction == direction
        ]
        if len(d_rows) < 5:
            print(f"{direction}: only {len(d_rows)} val rows — skipping\n")
            continue

        scores, pnls_raw = zip(*d_rows)
        x = np.array([abs(s) for s in scores])
        p_preds = model.predict(x)
        pnls_arr = list(pnls_raw)
        n_total = len(d_rows)

        p90 = float(np.percentile(p_preds, 90))

        print("=" * 76)
        print(f"WHAT-IF GRID  —  {direction}  (val n={n_total})")
        print(f"  p_win pred range: [{p_preds.min():.3f}, {p_preds.max():.3f}]"
              f"  90th-pct: {p90:.3f}")
        print("=" * 76)
        print(HEADER)
        print(SEP)

        _print_row("baseline (all val)", n_total, pnls_arr)

        for T in (0.24, 0.26, 0.28, 0.30):
            mask = p_preds >= T
            kept = [pnls_arr[i] for i, m in enumerate(mask) if m]
            _print_row(f"lower-bound >= {T:.2f}", n_total, kept)

        print(SEP)
        for T in (0.24, 0.26, 0.28, 0.30):
            mask = (p_preds >= T) & (p_preds <= 0.35)
            kept = [pnls_arr[i] for i, m in enumerate(mask) if m]
            _print_row(f"window [{T:.2f}, 0.35]", n_total, kept)

        print(SEP)
        mask_excl = p_preds < p90
        kept_excl = [pnls_arr[i] for i, m in enumerate(mask_excl) if m]
        _print_row(f"top-decile excl (<{p90:.3f})", n_total, kept_excl)

        for T in (0.24, 0.26, 0.28, 0.30):
            mask = (p_preds >= T) & (p_preds < p90)
            kept = [pnls_arr[i] for i, m in enumerate(mask) if m]
            _print_row(f"  + lower-bound >= {T:.2f}", n_total, kept)

        print()

    print("Done.")


asyncio.run(main())
