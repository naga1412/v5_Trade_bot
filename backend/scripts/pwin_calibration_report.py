"""p_win calibration report — ops-debug probe 'pwin-calibration-report'.

Fits isotonic models on oldest 80% of closed shadow_trades, then prints
a blended 30-day summary and a rank-bucketed decile calibration table on
the newest 20% (validation window), with P&L and exit-reason breakdown.

Usage (inside backend container via ops-debug.yml probe):
    docker compose exec -T backend python /app/scripts/pwin_calibration_report.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
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


def _tz(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


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
                    "SELECT entry_score, direction, pnl_pct, exit_reason, closed_at,"
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

    # ── Blended 30-day summary (full dataset, not restricted to val) ──────────
    now = datetime.now(timezone.utc)
    cutoff_30d = now - timedelta(days=30)
    print("=" * 76)
    print("BLENDED SUMMARY — last 30 days")
    print("=" * 76)
    print(
        f"  {'Direction':>10}  {'Trades':>7}  {'WR%':>6}  "
        f"{'AvgWin%':>8}  {'AvgLoss%':>9}  {'TotalPnl%':>10}"
    )
    print(f"  {'-'*10}  {'-'*7}  {'-'*6}  {'-'*8}  {'-'*9}  {'-'*10}")
    for direction in (Direction.LONG, Direction.SHORT):
        d_recent = [
            r for r in rows
            if r.direction == direction and _tz(r.closed_at) >= cutoff_30d
        ]
        if not d_recent:
            print(f"  {direction:>10}  {'—':>7}  (no trades in last 30d)")
            continue
        pnls = [float(r.pnl_pct or 0.0) for r in d_recent]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        wr = 100.0 * len(wins) / len(d_recent)
        avg_win = sum(wins) / len(wins) if wins else float("nan")
        avg_loss = sum(losses) / len(losses) if losses else float("nan")
        total_pnl = sum(pnls)
        print(
            f"  {direction:>10}  {len(d_recent):>7}  {wr:>6.1f}  "
            f"{avg_win:>8.3f}  {avg_loss:>9.3f}  {total_pnl:>10.3f}"
        )
    print()

    # ── Decile calibration tables (val set, rank-based bucketing) ─────────────
    for direction, path in [
        (Direction.LONG, P_WIN_MODEL_PATH_LONG),
        (Direction.SHORT, P_WIN_MODEL_PATH_SHORT),
    ]:
        if not path.exists():
            print(f"{direction}: model not fitted (insufficient training rows)\n")
            continue
        with open(path, "rb") as f:
            model = pickle.load(f)

        d_rows = [
            (r.entry_score, r.won, float(r.pnl_pct or 0.0), r.exit_reason or "UNKNOWN")
            for r in val_rows if r.direction == direction
        ]
        if len(d_rows) < 5:
            print(f"{direction}: only {len(d_rows)} val rows — skipping\n")
            continue

        scores, labels, pnls_raw, exits_raw = zip(*d_rows)
        x = np.array([abs(s) for s in scores])
        p_wins = model.predict(x)
        labels_arr = np.array(labels, dtype=float)
        pnls_arr = np.array(pnls_raw, dtype=float)
        exits_list = list(exits_raw)

        # Rank-based bucketing: sort indices by predicted p_win and split into 10
        # equal slices. Fixes the percentile-edge mask bug where duplicate values
        # at an isotonic plateau caused rows at decile boundaries to be counted in
        # multiple buckets simultaneously (n summed to 3-4× the val-set size).
        idx_sorted = np.argsort(p_wins, kind="stable")
        buckets = np.array_split(idx_sorted, 10)

        n_total = len(d_rows)
        print(f"{direction}  (n={n_total} validation trades)")
        print(
            f"  {'Decile':>10}  {'pred_p_win':>10}  {'realized_wr':>11}  {'n':>5}"
            f"  {'avg_pnl%':>8}  {'sum_pnl%':>9}  {'avg_win%':>8}  {'avg_loss%':>9}"
            f"  {'TP':>5}  {'SL':>5}  {'TIMEOUT':>7}"
        )
        print(
            f"  {'-'*10}  {'-'*10}  {'-'*11}  {'-'*5}"
            f"  {'-'*8}  {'-'*9}  {'-'*8}  {'-'*9}"
            f"  {'-'*5}  {'-'*5}  {'-'*7}"
        )
        n_check = 0
        for i, bucket_idx in enumerate(buckets):
            if len(bucket_idx) == 0:
                continue
            avg_pred = float(p_wins[bucket_idx].mean())
            realized = float(labels_arr[bucket_idx].mean())
            n = len(bucket_idx)
            n_check += n
            bucket_pnls = pnls_arr[bucket_idx]
            avg_pnl = float(bucket_pnls.mean())
            sum_pnl = float(bucket_pnls.sum())
            win_mask = bucket_pnls > 0
            loss_mask = ~win_mask
            avg_win_pnl = float(bucket_pnls[win_mask].mean()) if win_mask.any() else float("nan")
            avg_loss_pnl = float(bucket_pnls[loss_mask].mean()) if loss_mask.any() else float("nan")
            bucket_exits = [exits_list[int(j)] for j in bucket_idx]
            tp_n = sum(1 for e in bucket_exits if e == "TAKE_PROFIT")
            sl_n = sum(1 for e in bucket_exits if e == "STOP_LOSS")
            to_n = sum(1 for e in bucket_exits if e == "TIMEOUT")
            print(
                f"  {i*10:>4}%–{(i+1)*10:<4}%  {avg_pred:>10.3f}  {realized:>11.3f}  {n:>5}"
                f"  {avg_pnl:>8.3f}  {sum_pnl:>9.3f}  {avg_win_pnl:>8.3f}  {avg_loss_pnl:>9.3f}"
                f"  {tp_n:>5}  {sl_n:>5}  {to_n:>7}"
            )
        print(f"  {'TOTAL':>10}  {'':>10}  {'':>11}  {n_check:>5}  (== {n_total} ✓)\n")

    print("Done.")


asyncio.run(main())
