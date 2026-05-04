"""MC-dropout inference for the next-bar ghost candle.

Spec sec 6.1 — to estimate uncertainty we keep the model's dropout layers
ACTIVE at inference time (model.train()) and run the forward pass N times.
Each forward yields a slightly different prediction; the empirical sample
mean is the point estimate, the 5/95 percentiles bound a confidence band,
and the per-feature std collapsed to a scalar is a single-number
uncertainty signal that downstream consumers (frontend chart, scoring) can
threshold on.

The function is model-agnostic: it accepts any nn.Module whose forward takes
(batch, 256, 5) and returns (batch, 4) of OHLC % change. In production it
gets a ConvLSTMPredictor; in tests we substitute a tiny mock model.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import torch
from torch import nn

from app.ml.normalize import normalize_window


@dataclass(frozen=True)
class GhostCandle:
    """Predicted next bar — point estimate (OHLC) plus uncertainty band.

    open/high/low/close are the sample-mean predictions converted back to
    absolute price via last_close * (1 + pct).
    p5_low is the 5th percentile of predicted lows; p95_high is the 95th
    percentile of predicted highs — together they bound a ~90% credible
    band that the frontend renders as the ghost band.
    uncertainty is the mean of the per-OHLC sample std, a single scalar.
    """
    open: float
    high: float
    low: float
    close: float
    p5_low: float
    p95_high: float
    uncertainty: float


def predict_ghost_candle(
    model: nn.Module,
    bars: pd.DataFrame,
    last_close: float,
    n_samples: int = 32,
) -> GhostCandle:
    """Run MC-dropout inference and return a GhostCandle.

    bars must contain at least 256 rows of OHLCV — only the last 256 are used.
    last_close anchors the % change predictions back to absolute price.
    n_samples controls how many forward passes we average; spec default is 32.
    """
    window = bars.iloc[-256:]
    x = normalize_window(window).unsqueeze(0)  # (1, 256, 5)

    # Activate dropout — this is the MC-dropout trick.
    model.train()
    with torch.no_grad():
        samples = torch.stack(
            [model(x) for _ in range(n_samples)]
        )  # (n_samples, 1, 4)

    # Collapse the singleton batch dim -> (4,) per statistic.
    mean = samples.mean(dim=0).squeeze(0)
    p5 = samples.quantile(0.05, dim=0).squeeze(0)
    p95 = samples.quantile(0.95, dim=0).squeeze(0)
    std = samples.std(dim=0).squeeze(0)

    return GhostCandle(
        open=last_close * (1.0 + float(mean[0].item())),
        high=last_close * (1.0 + float(mean[1].item())),
        low=last_close * (1.0 + float(mean[2].item())),
        close=last_close * (1.0 + float(mean[3].item())),
        p5_low=last_close * (1.0 + float(p5[2].item())),
        p95_high=last_close * (1.0 + float(p95[1].item())),
        uncertainty=float(std.mean().item()),
    )
