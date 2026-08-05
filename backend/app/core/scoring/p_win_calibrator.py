"""Per-direction Bayesian p_win calibrator (PR5).

fit_p_win_models fits sklearn.isotonic.IsotonicRegression against closed
shadow_trades outcomes (per direction) and persists to
backend/app/data/p_win_models/{long,short}.pkl. predict_p_win lazy-loads
the fitted model and applies the isotonic transform.

2026-08-05 scope note: this ships the RECORDING path only — predict_p_win
feeding predictions.p_win (a documented "PR1 record-only analytics field"
per predictor.py's _compute_aggregator_hook_fields docstring: never
modifies final_score/confidence/direction/layer_scores). It does NOT wire
p_win into live-money position sizing. `app/trading/dynamic_sizing.py`'s
`_resolve_p_win` still uses the confidence_pct proxy — its docstring
already documents that flipping SIZING_USE_P_WIN_WHEN_AVAILABLE requires
making the sizing call chain async, cascading up to the dispatcher, and
is deliberately deferred as its own follow-up. That follow-up is
PR9-class (live-money sizing) and needs its own soak + explicit operator
sign-off — out of scope here.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.core.scoring.types import Direction

log = logging.getLogger(__name__)


# Per-process model cache, populated either by fit_p_win_models() (same
# process ran the nightly refit) or lazily by predict_p_win() on first
# call (a prior process's fit already wrote the .pkl to disk).
_LOADED_MODELS: dict[Direction, Any] = {}

# Sentinel distinguishing "checked, no model available" from "never
# checked" in _LOADED_MODELS — avoids re-stat'ing a missing .pkl on
# every single prediction.
_NO_MODEL: Any = object()

# .pkl file paths — directory created lazily by the fit worker.
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


_MODEL_PATH_FOR_DIRECTION: dict[Direction, Path] = {
    Direction.LONG: P_WIN_MODEL_PATH_LONG,
    Direction.SHORT: P_WIN_MODEL_PATH_SHORT,
}


def _load_model(direction: Direction) -> Any | None:
    """Return the cached model for ``direction``, lazy-loading from disk
    on first use in this process. Caches a explicit sentinel (not just
    "absent from dict") for "no model available" so a missing .pkl
    doesn't re-stat the filesystem on every single prediction — the
    nightly refit worker is what invalidates this, by writing directly
    into _LOADED_MODELS itself (see fit_p_win_models)."""
    if direction in _LOADED_MODELS:
        model = _LOADED_MODELS[direction]
        return model if model is not _NO_MODEL else None

    path = _MODEL_PATH_FOR_DIRECTION.get(direction)
    if path is None or not path.exists():
        _LOADED_MODELS[direction] = _NO_MODEL
        return None

    try:
        import pickle
        with open(path, "rb") as f:
            model = pickle.load(f)
    except Exception:  # noqa: BLE001 — corrupt/unreadable pkl must not break predictions
        log.warning(
            "p_win_calibrator: failed to load %s; treating as unavailable",
            path, exc_info=True,
        )
        _LOADED_MODELS[direction] = _NO_MODEL
        return None

    _LOADED_MODELS[direction] = model
    return model


async def predict_p_win(
    final_score: float,
    direction: Direction,
) -> float | None:
    """Calibrated win probability from the per-direction isotonic model.

    Lazy-loads long.pkl for LONG / short.pkl for SHORT (cached per
    process after first load — see _load_model). NEUTRAL has no model
    and always returns None. Same abs(score) transform as the fit side
    (see fit_p_win_models) — both LONG and SHORT are monotone-increasing
    in signal strength after abs().

    Args:
        final_score: aggregated signal score in [-1, +1].
        direction: trade direction; NEUTRAL always returns None.

    Returns:
        Calibrated win probability in [0, 1], or None when:
          - direction is NEUTRAL
          - no model file has been fitted yet
          - the model file is unreadable/corrupt (including: sklearn is
            unavailable, so the pickled IsotonicRegression can't be
            unpickled — surfaces through _load_model's own except, not
            a separate check here, since an already-cached model (e.g.
            this process ran fit_p_win_models itself) needs no sklearn
            re-verification on every call)
    """
    if direction not in (Direction.LONG, Direction.SHORT):
        return None

    model = _load_model(direction)
    if model is None:
        return None

    try:
        pred = model.predict([abs(final_score)])
        return float(max(0.0, min(1.0, pred[0])))
    except Exception:  # noqa: BLE001 — never break the predictor over a calibration failure
        log.warning(
            "p_win_calibrator: predict failed for direction=%s score=%s",
            direction, final_score, exc_info=True,
        )
        return None


__all__ = [
    "P_WIN_MODEL_DIR",
    "P_WIN_MODEL_PATH_LONG",
    "P_WIN_MODEL_PATH_SHORT",
    "fit_p_win_models",
    "predict_p_win",
]
