"""Forward ghost path — iterative rollout with widening uncertainty.

Extends :func:`app.ml.inference.predict_ghost_candle` from a single
ghost candle to a list of N forward steps. The honest counterpart to
SMC Pro's "20 forward candles" cosmetic trick.

Approach:
1. Step 1 uses full MC-dropout (32 samples) to establish the base
   uncertainty σ. Same math as the existing 1-bar inference.
2. Steps 2..N use deterministic forward passes (model.eval()) on a
   rolling window — the previous step's predicted OHLC is appended,
   the oldest bar drops off. The model has never seen synthetic input
   during training so per-step % moves are CLIPPED to ±3σ of the base
   uncertainty to prevent runaway drift.
3. The P5/P95 band widens analytically by √k: at step k it's
   mean ± 1.65 × σ × √k. The eye reads this correctly as
   "the further out, the less certain".
4. Synthetic volume comes from the trailing 20-bar mean of real volume;
   we don't predict volume and don't try to.

The frontend renders bars 1..3 as full candles, bars 4..7 as a
close-line dot + faded P5/P95 ribbon, bars 8..N as the ribbon alone
with no center line. The visualization carries the uncertainty story
even when one screenshots a single frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch
from torch import nn

from app.ml.inference import predict_ghost_candle
from app.ml.normalize import normalize_window


# 90% credible band → 1.6448... standard deviations either side.
_Z_90: float = 1.6449


@dataclass(frozen=True)
class GhostStep:
    """One step in the forward rollout.

    step is 1-indexed (step 1 is "the next bar"). open/high/low/close
    are absolute prices. p5_low/p95_high bound the 90% credible band
    *for this step*. uncertainty is the scalar widened band radius.
    """
    step: int
    open: float
    high: float
    low: float
    close: float
    p5_low: float
    p95_high: float
    uncertainty: float


def _clip_pct(pct: float, clip_abs: float) -> float:
    """Clamp a single % change to ±clip_abs (a fraction, not a percent)."""
    if pct > clip_abs:
        return clip_abs
    if pct < -clip_abs:
        return -clip_abs
    return pct


def _predict_one_deterministic(
    model: nn.Module, window: pd.DataFrame,
) -> tuple[float, float, float, float]:
    """Single forward pass (no MC) returning (open, high, low, close) % moves."""
    x = normalize_window(window).unsqueeze(0)  # (1, 256, 5)
    model.eval()
    with torch.no_grad():
        out = model(x)  # (1, 4)
    out = out.squeeze(0)  # (4,)
    return (
        float(out[0].item()),
        float(out[1].item()),
        float(out[2].item()),
        float(out[3].item()),
    )


def predict_ghost_path(
    model: nn.Module,
    bars: pd.DataFrame,
    last_close: float,
    n_steps: int = 20,
    n_samples: int = 32,
    clip_sigma: float = 3.0,
) -> list[GhostStep]:
    """Return a list of ``n_steps`` forward predictions.

    Step 1 uses MC-dropout (same as :func:`predict_ghost_candle`). Steps
    2..N are deterministic rollouts with the per-step % move clipped to
    ±``clip_sigma`` × σ (the base uncertainty). The credible band widens
    by √k per step.
    """
    if n_steps < 1:
        return []
    if len(bars) < 256:
        return []

    # --- Step 1: full MC-dropout ---
    step1 = predict_ghost_candle(model, bars, last_close, n_samples=n_samples)
    base_sigma = max(step1.uncertainty, 1e-6)
    out: list[GhostStep] = [
        GhostStep(
            step=1,
            open=step1.open, high=step1.high, low=step1.low, close=step1.close,
            p5_low=step1.p5_low, p95_high=step1.p95_high,
            uncertainty=base_sigma,
        ),
    ]
    if n_steps == 1:
        return out

    # --- Steps 2..N: deterministic rollouts with clipping + √k fan ---
    # Use the trailing-20 mean volume as the synthetic-bar volume placeholder.
    vol_mean = float(bars["volume"].iloc[-20:].mean()) if len(bars) >= 20 else float(
        bars["volume"].iloc[-1]
    )
    clip_abs = clip_sigma * base_sigma
    rolling = bars.iloc[-256:].copy()
    prev_close = step1.close

    for k in range(2, n_steps + 1):
        # Append the previous step's predicted candle to the rolling window.
        # Use a synthetic ts one step ahead of the rolling window's tail —
        # the actual ts value doesn't matter for inference (the model
        # ignores the index) but pandas needs a valid index.
        last_idx = rolling.index[-1]
        if isinstance(last_idx, pd.Timestamp):
            step_seconds = int(
                (rolling.index[-1] - rolling.index[-2]).total_seconds()
            ) if len(rolling) >= 2 else 3600
            new_idx = last_idx + pd.Timedelta(seconds=step_seconds)
        else:
            new_idx = last_idx
        prev = out[-1]
        synthetic = pd.DataFrame(
            [[prev.open, prev.high, prev.low, prev.close, vol_mean]],
            columns=["open", "high", "low", "close", "volume"],
            index=[new_idx],
        )
        rolling = pd.concat([rolling.iloc[1:], synthetic])

        # One deterministic forward pass. Clip the per-OHLC % move so a
        # model output that's far out-of-distribution can't run the
        # projected price off the screen.
        o_pct, h_pct, l_pct, c_pct = _predict_one_deterministic(model, rolling)
        o_pct = _clip_pct(o_pct, clip_abs)
        h_pct = _clip_pct(h_pct, clip_abs)
        l_pct = _clip_pct(l_pct, clip_abs)
        c_pct = _clip_pct(c_pct, clip_abs)

        new_open = prev_close * (1.0 + o_pct)
        new_high = prev_close * (1.0 + h_pct)
        new_low = prev_close * (1.0 + l_pct)
        new_close = prev_close * (1.0 + c_pct)

        # √k fan around the predicted close.
        fan = base_sigma * math.sqrt(k)
        p5_low = new_close * (1.0 - _Z_90 * fan)
        p95_high = new_close * (1.0 + _Z_90 * fan)

        out.append(
            GhostStep(
                step=k,
                open=new_open, high=new_high,
                low=new_low, close=new_close,
                p5_low=p5_low, p95_high=p95_high,
                uncertainty=fan,
            ),
        )
        prev_close = new_close

    return out


def _to_np_array(steps: list[GhostStep]) -> np.ndarray:
    """Compact (n, 7) array for callers that want vectorised access."""
    return np.array([
        [s.open, s.high, s.low, s.close, s.p5_low, s.p95_high, s.uncertainty]
        for s in steps
    ])


__all__ = ["GhostStep", "predict_ghost_path", "_to_np_array"]
