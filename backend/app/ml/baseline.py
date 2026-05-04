"""Random-walk baseline: predicts next-bar OHLC = current close (0% change).

Used as a sanity floor — the trained Conv-LSTM must beat this MAE on every
regime window or the model is not adding value over a "do nothing" predictor
(spec sec 10 risk row "Model can't beat random walk").

The baseline is intentionally a `nn.Module` so it plugs straight into the same
`evaluate_on_regime` harness used for the Conv-LSTM (B2 design note: the eval
harness is model-agnostic).
"""
from __future__ import annotations

import torch
from torch import nn


class RandomWalkBaseline(nn.Module):
    """Predicts zero % change on all 4 outputs (open/high/low/close)."""

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # noqa: D401
        # x shape: (batch, 256, 5); output (batch, 4)
        batch = x.shape[0]
        return torch.zeros(batch, 4, dtype=x.dtype, device=x.device)
