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


def fit_p_win_models(session: Any) -> None:
    """PR1: NOOP. PR5 will populate this.

    Args:
        session: AsyncSession or similar (unused in PR1; accepted so the
            PR5 worker can call without a signature change).
    """
    log.info(
        "p_win_calibrator: fit_p_win_models called; "
        "fit not implemented until PR5 (returning NOOP)",
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
