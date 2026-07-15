"""p_win calibration report — ops-debug probe 'pwin-calibration-report'.

Fits isotonic models on oldest 80% of closed shadow_trades, then prints
a decile calibration table on the newest 20% (validation window) showing
predicted p_win vs realized win rate per direction.

Usage (inside backend container via ops-debug.yml probe):
    docker compose exec -T backend python /app/scripts/pwin_calibration_report.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running from /app (Docker) or from the repo root in tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.core.scoring.p_win_calibrator import (
    P_WIN_MODEL_PATH_LONG,
    P_WIN_MODEL_PATH_SHORT,
    fit_p_win_models,
)
from app.core.scoring.types import Direction
from app.db.session import get_session_factory


async def main() -> None:
    try:
        import pickle

        import numpy as np
        from sklearn.isotonic import IsotonicRegression  # noqa: F401 — validate install
    except ImportError as exc:
        print(f"ERROR: missing dependency — {exc}")
        return

    sf = get_session_factory()
    async with sf() as session:
        print("Fitting p_win models (train = oldest 80% of closed shadow_trades)...")
        await fit_p_win_models(session)

        rows = (
            await session.execute(
                text(
                    "SELECT entry_score, direction,"
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

    split = int(len(rows) * 0.8)
    val_rows = rows[split:]
    print(
        f"\nClosed trades total: {len(rows)}"
        f"  Train (oldest 80%): {split}"
        f"  Val (newest 20%): {len(val_rows)}\n"
    )

    for direction, path in [
        (Direction.LONG, P_WIN_MODEL_PATH_LONG),
        (Direction.SHORT, P_WIN_MODEL_PATH_SHORT),
    ]:
        if not path.exists():
            print(f"{direction}: model not fitted (insufficient training rows)\n")
            continue
        with open(path, "rb") as f:
            model = pickle.load(f)

        d_rows = [(r.entry_score, r.won) for r in val_rows if r.direction == direction]
        if len(d_rows) < 5:
            print(f"{direction}: only {len(d_rows)} val rows — skipping\n")
            continue

        scores, labels = zip(*d_rows)
        x = np.array([abs(s) for s in scores])
        p_wins = model.predict(x)
        labels_arr = np.array(labels, dtype=float)

        # Decile edges by predicted p_win rank
        edges = np.percentile(p_wins, np.arange(0, 110, 10))

        print(f"{direction}  (n={len(d_rows)} validation trades)")
        print(f"  {'Decile':>10}  {'pred_p_win':>10}  {'realized_wr':>11}  {'n':>5}")
        print(f"  {'-'*10}  {'-'*10}  {'-'*11}  {'-'*5}")
        for i in range(10):
            lo, hi = edges[i], edges[i + 1]
            mask = (p_wins >= lo) & (p_wins <= hi)
            if not mask.any():
                continue
            avg_pred = float(p_wins[mask].mean())
            realized = float(labels_arr[mask].mean())
            n = int(mask.sum())
            print(f"  {i*10:>4}%–{(i+1)*10:<4}%  {avg_pred:>10.3f}  {realized:>11.3f}  {n:>5}")
        print()

    print("Done.")


asyncio.run(main())
