"""p_win calibration report — ops-debug probe 'pwin-calibration-report'.

Fits isotonic models on oldest 80% of closed shadow_trades (EXCLUDING the
SHADOW_SPOT_BLACKLIST — stablecoins / pegged tokens). Prints a blended 30-day
summary and a rank-bucketed decile calibration table on the newest 20%
(validation window), with P&L and exit-reason breakdown.

Models are fitted IN-MEMORY only — the production .pkl files on disk are NOT
overwritten. Use the nightly cron to refresh production models.

Usage (inside backend container via ops-debug.yml probe):
    docker compose exec -T backend python /app/scripts/pwin_calibration_report.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.config import get_settings
from app.core.scoring.types import Direction
from app.db.session import get_session_factory


def _tz(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _expectancy(wr_pct: float, avg_win: float, avg_loss: float) -> float:
    if any(v != v for v in (wr_pct, avg_win, avg_loss)):
        return float("nan")
    wr = wr_pct / 100.0
    return wr * avg_win + (1.0 - wr) * avg_loss


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


async def main() -> None:
    try:
        import numpy as np
        from sklearn.isotonic import IsotonicRegression  # noqa: F401
    except ImportError as exc:
        print(f"ERROR: missing dependency — {exc}")
        return

    settings = get_settings()
    blacklist: set[str] = set(settings.SHADOW_SPOT_BLACKLIST)
    print(f"SHADOW_SPOT_BLACKLIST ({len(blacklist)} entries): {sorted(blacklist)}")

    sf = get_session_factory()
    async with sf() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT symbol, entry_score, direction, pnl_pct, exit_reason, closed_at,"
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

    excluded = [r for r in rows if r.symbol in blacklist]
    rows = [r for r in rows if r.symbol not in blacklist]
    print(f"\nExcluded {len(excluded)} rows from {len({r.symbol for r in excluded})} blacklisted symbols.")

    if len(rows) < 50:
        print(f"ERROR: only {len(rows)} clean rows (need >=50)")
        return

    split = int(len(rows) * 0.8)
    train_rows = rows[:split]
    val_rows = rows[split:]
    print(
        f"Clean trades total: {len(rows)}"
        f"  Train (oldest 80%): {split}"
        f"  Val (newest 20%): {len(val_rows)}\n"
    )

    # Fit in-memory models (no prod .pkl overwrite)
    print("Fitting clean isotonic models (in-memory, no disk write)...")
    models = {}
    for direction in (Direction.LONG, Direction.SHORT):
        m = _fit_isotonic(train_rows, direction)
        if m is not None:
            models[direction] = m
            d_count = sum(1 for r in train_rows if r.direction == direction)
            print(f"  {direction}: fitted on {d_count} train rows")
        else:
            print(f"  {direction}: insufficient data — skipped")
    print()

    # ── Blended 30-day summary (clean data, full dataset) ─────────────────────
    now = datetime.now(timezone.utc)
    cutoff_30d = now - timedelta(days=30)
    print("=" * 84)
    print("BLENDED SUMMARY — last 30 days (CLEAN: blacklist excluded)")
    print("=" * 84)
    print(
        f"  {'Direction':>10}  {'Trades':>7}  {'WR%':>6}  "
        f"{'AvgWin%':>8}  {'AvgLoss%':>9}  {'Expectancy':>11}  {'TotalPnl%':>10}"
    )
    print(f"  {'-'*10}  {'-'*7}  {'-'*6}  {'-'*8}  {'-'*9}  {'-'*11}  {'-'*10}")
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
        exp = _expectancy(wr, avg_win, avg_loss)
        total_pnl = sum(pnls)
        print(
            f"  {direction:>10}  {len(d_recent):>7}  {wr:>6.1f}  "
            f"{avg_win:>8.3f}  {avg_loss:>9.3f}  {exp:>11.4f}  {total_pnl:>10.3f}"
        )
    print()

    # ── Decile calibration tables (val set, rank-based bucketing) ─────────────
    for direction in (Direction.LONG, Direction.SHORT):
        model = models.get(direction)
        if model is None:
            print(f"{direction}: no model — skipping decile table\n")
            continue

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

        idx_sorted = np.argsort(p_wins, kind="stable")
        buckets = np.array_split(idx_sorted, 10)

        n_total = len(d_rows)
        print(f"{direction}  (n={n_total} clean validation trades)")
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
