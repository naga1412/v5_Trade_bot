"""Per-regime MAE evaluation harness.

Spec sec 5.3 — for each window:
  1. Slice `bars` to [window.start, window.end]
  2. Build a sliding-window prediction loop: at each i (>= 256), feed last 256
     bars into the model, compare predicted next-bar O/H/L/C against actual.
  3. Aggregate to MAE (mean over all 4 outputs across all samples).
  4. `passes_acceptance = mae <= ACCEPTANCE_MAE_THRESHOLD` (1.5% per spec sec 2 row 13).

Determinism: callers pass a `seed`. Torch RNG + NumPy RNG are seeded inside
this function. `model.eval()` is called (we want point estimates here, not
MC dropout — uncertainty is reported separately by `predict_ghost_candle`).

Model-agnostic: any callable / nn.Module that takes a (batch, 256, 5) tensor
and returns a (batch, 4) tensor of % changes works — Conv-LSTM (Phase C),
RandomWalkBaseline (B3), or future ensembles.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch
from torch import nn

from app.ml.normalize import normalize_window
from app.ml.regimes import ACCEPTANCE_MAE_THRESHOLD, RegimeWindow


WINDOW_BARS: int = 256


@dataclass(frozen=True)
class RegimeEvalResult:
    """Outcome of evaluating one model on one regime window."""

    regime_name: str
    mae: float
    samples: int
    passes_acceptance: bool
    per_output_mae: tuple[float, float, float, float]  # open / high / low / close


def evaluate_on_regime(
    *,
    model: nn.Module,
    bars: pd.DataFrame,
    window: RegimeWindow,
    seed: int = 42,
    threshold: float = ACCEPTANCE_MAE_THRESHOLD,
) -> RegimeEvalResult:
    """Evaluate `model` on the slice of `bars` inside `window`.

    Returns a frozen `RegimeEvalResult`. Deterministic for fixed `seed`.
    Bars must have a tz-aware DatetimeIndex covering `window.start..window.end`.
    """
    torch.manual_seed(seed)
    np.random.seed(seed)
    model.eval()

    sl = bars.loc[window.start:window.end]  # type: ignore[misc]
    if len(sl) < WINDOW_BARS + 2:
        return RegimeEvalResult(
            regime_name=window.name,
            mae=float("inf"),
            samples=0,
            passes_acceptance=False,
            per_output_mae=(0.0, 0.0, 0.0, 0.0),
        )

    abs_errs: list[np.ndarray] = []  # list of (4,) arrays
    with torch.no_grad():
        for i in range(WINDOW_BARS, len(sl) - 1):
            window_bars = sl.iloc[i - WINDOW_BARS:i]
            x = normalize_window(window_bars).unsqueeze(0)  # (1, 256, 5)
            pred_pct = model(x).squeeze(0).numpy()  # (4,) predicted % change
            last_close = float(sl.iloc[i]["close"])
            actual_next = sl.iloc[i + 1]
            actual_pct = np.array([
                actual_next["open"]  / last_close - 1.0,
                actual_next["high"]  / last_close - 1.0,
                actual_next["low"]   / last_close - 1.0,
                actual_next["close"] / last_close - 1.0,
            ])
            abs_errs.append(np.abs(pred_pct - actual_pct))

    arr = np.array(abs_errs)  # (samples, 4)
    per_output_mae = (
        float(arr[:, 0].mean()),
        float(arr[:, 1].mean()),
        float(arr[:, 2].mean()),
        float(arr[:, 3].mean()),
    )
    mae = float(arr.mean())
    return RegimeEvalResult(
        regime_name=window.name,
        mae=mae,
        samples=len(abs_errs),
        passes_acceptance=mae <= threshold,
        per_output_mae=per_output_mae,
    )
