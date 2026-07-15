"""Per-direction Bayesian p_win calibrator (PR1 stub).

PR1 contract: predict_p_win ALWAYS returns None (caller treats as
'p_win unavailable — column persists as NULL'). fit_p_win_models is
a callable NOOP that logs the deferred-to-PR5 intent.

PR5 will:
  - Replace fit_p_win_models body with sklearn.isotonic.IsotonicRegression
    fitting against shadow_trades closed-trade outcomes (per direction).
  - Persist fitted models to backend/app/data/p_win_models/{long,short}.pkl
  - Replace predict_p_win body with lazy-load + isotonic transform.
  - Register a nightly worker (every 24h) that re-fits both models.

This stub lets Tasks 3.4 (aggregator hook) + 3.5 (predictor wire-up) ship
their None-handling code paths now, with PR5 swapping the implementation
behind the stable function signatures.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.core.scoring.types import Direction

log = logging.getLogger(__name__)


# PR5 will load + cache fitted models here. Empty in PR1.
_LOADED_MODELS: dict[Direction, Any] = {}

# .pkl file paths — directory created lazily by PR5's fit worker.
P_WIN_MODEL_DIR: Path = Path(__file__).parent.parent.parent / "data" / "p_win_models"
P_WIN_MODEL_PATH_LONG: Path = P_WIN_MODEL_DIR / "long.pkl"
P_WIN_MODEL_PATH_SHORT: Path = P_WIN_MODEL_DIR / "short.pkl"


async def fit_p_win_models(session: Any) -> None:
    """Fit per-direction IsotonicRegression models on closed shadow_trades.

    Walk-forward split: train on oldest 80% so the validation window
    (newest 20%) is a clean holdout for the ops-debug calibration report.
    Models are pickled to P_WIN_MODEL_PATH_{LONG,SHORT} and cached in
    _LOADED_MODELS.

    Args:
        session: AsyncSession — used to SELECT from shadow_trades.
    """
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        log.warning("p_win_calibrator: sklearn unavailable; skipping fit")
        return
    import pickle
    from sqlalchemy import text

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

    if len(rows) < 50:
        log.info(
            "p_win_calibrator: only %d closed trades (need ≥50); skipping fit",
            len(rows),
        )
        return

    split = int(len(rows) * 0.8)
    train_rows = rows[:split]
    log.info(
        "p_win_calibrator: fitting on %d train rows (%d total, %d val holdout)",
        split, len(rows), len(rows) - split,
    )

    P_WIN_MODEL_DIR.mkdir(parents=True, exist_ok=True)

    for direction, path in [
        (Direction.LONG, P_WIN_MODEL_PATH_LONG),
        (Direction.SHORT, P_WIN_MODEL_PATH_SHORT),
    ]:
        d_rows = [
            (r.entry_score, r.won)
            for r in train_rows
            if r.direction == direction
        ]
        if len(d_rows) < 20:
            log.info(
                "p_win_calibrator: only %d %s training rows (need ≥20); skipping",
                len(d_rows), direction,
            )
            continue
        scores, labels = zip(*d_rows)
        # abs(entry_score): both LONG (positive) and SHORT (negative) are
        # monotone-increasing with signal strength after abs — stronger signal
        # should map to higher p_win.
        x = [abs(s) for s in scores]
        ir = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
        ir.fit(x, labels)
        with open(path, "wb") as f:
            pickle.dump(ir, f)
        _LOADED_MODELS[direction] = ir
        log.info(
            "p_win_calibrator: fitted %s model on %d rows → %s",
            direction, len(d_rows), path,
        )


async def predict_p_win(
    final_score: float,
    direction: Direction,
) -> float | None:
    """PR1: ALWAYS returns None.

    PR5 will lazy-load the per-direction model and apply isotonic
    transform: long.pkl for LONG, short.pkl for SHORT. NEUTRAL has no
    model (always None even post-PR5).

    Args:
        final_score: aggregated signal score in [-1, +1].
        direction: trade direction; NEUTRAL always returns None.

    Returns:
        Calibrated win probability in [0, 1], or None when:
          - PR1 (always)
          - direction is NEUTRAL
          - model file does not exist (PR5)
          - sklearn import failed (PR5)
    """
    # PR5 will lazy-import sklearn inside this function, lazy-load the
    # .pkl, apply isotonic transform. PR1 short-circuits.
    return None


__all__ = [
    "P_WIN_MODEL_DIR",
    "P_WIN_MODEL_PATH_LONG",
    "P_WIN_MODEL_PATH_SHORT",
    "fit_p_win_models",
    "predict_p_win",
]
