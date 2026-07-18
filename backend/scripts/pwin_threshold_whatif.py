"""p_win threshold what-if grid — ops-debug probe 'pwin-threshold-whatif'.

Fits isotonic models on oldest 80% of closed shadow_trades (EXCLUDING
SHADOW_SPOT_BLACKLIST symbols). On the clean validation window (newest 20%),
evaluates how different lower-bound thresholds on predicted p_win change trade
count, WR, avg_pnl%, sum_pnl%, and expectancy.

Models are fitted IN-MEMORY only — production .pkl files are NOT overwritten.

Variants per direction:
  1. Lower-bound-only: keep pred_p_win >= T  for T in {0.24, 0.26, 0.28, 0.30}
  2. Window (top-decile excluded): keep T <= pred_p_win <= 0.35
  3. Top-decile-excluded only: keep pred_p_win < 90th-percentile of val preds
  4. MODEL INVERSION: min abs(entry_score) where isotonic pred_p_win >= 0.26
     (candidate MIN_ENTRY_SCORE_LONG on the CLEAN fit)

Usage (inside backend container via ops-debug.yml probe):
    docker compose exec -T backend python /app/scripts/pwin_threshold_whatif.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from sqlalchemy import text

from app.config import get_settings
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
    if any(v != v for v in (wr_pct, avg_win, avg_loss)):
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


def _fit_isotonic(train_rows: list, direction: str):
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        return None
    d_rows = [
        (abs(r.entry_score), 1 if r.won else 0)
        for r in train_rows
        if r.direction == direction
    ]
    if len(d_rows) < 20:
        return None
    x, y = zip(*d_rows)
    ir = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
    ir.fit(list(x), list(y))
    return ir


HEADER = (
    f"  {'Scenario':<30}  {'Kept':>6}  {'Vol%':>9}  {'WR%':>8}"
    f"  {'AvgPnl%':>9}  {'SumPnl%':>10}  {'Expectancy':>11}"
)
SEP = "  " + "-" * 30 + "  " + "  ".join(["-" * w for w in [6, 9, 8, 9, 10, 11]])


async def main() -> None:
    settings = get_settings()
    blacklist: set[str] = set(settings.SHADOW_SPOT_BLACKLIST)
    print(f"SHADOW_SPOT_BLACKLIST ({len(blacklist)} entries): {sorted(blacklist)}")

    sf = get_session_factory()
    async with sf() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT symbol, entry_score, direction, pnl_pct,"
                    " CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END AS won"
                    " FROM shadow_trades"
                    " WHERE closed_at IS NOT NULL"
                    " ORDER BY closed_at ASC"
                )
            )
        ).fetchall()

    if not rows:
        print("ERROR: no closed shadow_trades found")
        return

    excluded_n = sum(1 for r in rows if r.symbol in blacklist)
    rows = [r for r in rows if r.symbol not in blacklist]
    print(f"Excluded {excluded_n} rows from blacklisted symbols. Clean total: {len(rows)}\n")

    if len(rows) < 50:
        print(f"ERROR: only {len(rows)} clean rows (need >=50)")
        return

    split = int(len(rows) * 0.8)
    train_rows = rows[:split]
    val_rows = rows[split:]
    print(
        f"Total clean closed trades: {len(rows)}"
        f"  Train: {split}  Val: {len(val_rows)}\n"
    )

    # Fit in-memory models (no prod .pkl overwrite)
    models = {}
    for direction in (Direction.LONG, Direction.SHORT):
        m = _fit_isotonic(train_rows, direction)
        if m is not None:
            models[direction] = m
            d_count = sum(1 for r in train_rows if r.direction == direction)
            print(f"Fitted {direction} model on {d_count} clean train rows")
        else:
            print(f"{direction}: insufficient train data — skipped")
    print()

    for direction in (Direction.LONG, Direction.SHORT):
        model = models.get(direction)
        if model is None:
            print(f"{direction}: no model — skipping\n")
            continue

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
        print(f"WHAT-IF GRID (CLEAN)  —  {direction}  (val n={n_total})")
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

        # ── Model inversion: find min abs(entry_score) where pred_p_win >= 0.26 ─
        # Fine grid scan over [0.0, 1.0] in 0.001 steps
        if direction == Direction.LONG:
            print(f"MODEL INVERSION  —  {direction}  (clean fit)")
            grid = np.arange(0.0, 1.001, 0.001)
            grid_preds = model.predict(grid)
            candidates = [float(g) for g, p in zip(grid, grid_preds) if p >= 0.26]
            if candidates:
                min_score = min(candidates)
                pred_at_min = float(model.predict([min_score])[0])
                print(
                    f"  Min abs(entry_score) where pred_p_win >= 0.26: {min_score:.3f}"
                    f"  (model predicts {pred_at_min:.4f} at that score)"
                )
                print(f"  -> Candidate MIN_ENTRY_SCORE_LONG = {min_score:.3f}")
            else:
                print("  WARNING: no grid point predicts p_win >= 0.26 (model plateau too low?)")

            # Also check >= 0.28 and >= 0.30
            for tgt in (0.28, 0.30):
                cands = [float(g) for g, p in zip(grid, grid_preds) if p >= tgt]
                if cands:
                    ms = min(cands)
                    pp = float(model.predict([ms])[0])
                    print(f"  Min abs(entry_score) where pred_p_win >= {tgt:.2f}: {ms:.3f}  (predicts {pp:.4f})")
                else:
                    print(f"  WARNING: no grid point predicts p_win >= {tgt:.2f}")
            print()

    print("Done.")


asyncio.run(main())
