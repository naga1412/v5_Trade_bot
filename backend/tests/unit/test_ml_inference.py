"""Unit tests for app/ml/inference.py — predict_ghost_candle (SP-1 C3).

Spec sec 6.1 — MC-dropout inference: keep dropout layers active and call
forward N times to produce a distribution over the next-bar OHLC. We then
report mean OHLC + p5/p95 band + scalar uncertainty.

We use a tiny mock model (a single Linear layer w/ a Dropout in front) so
each forward pass is microseconds — the full test file runs in well under 2s.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import torch
from torch import nn

from app.ml.inference import GhostCandle, predict_ghost_candle


# ------------------------------------------------------------------ helpers


def _bars(n: int = 256, base: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    closes = base * np.exp(np.cumsum(rng.normal(0, 0.005, size=n)))
    return pd.DataFrame({
        "open":  closes * 0.999,
        "high":  closes * 1.002,
        "low":   closes * 0.997,
        "close": closes,
        "volume": rng.uniform(1e3, 1e4, size=n),
    })


class _MockModel(nn.Module):
    """Cheap stand-in for ConvLSTMPredictor.

    Flattens (B, 256, 5) -> (B, 1280), passes through Dropout (so MC-dropout
    is observable as output variance), then through a Linear -> (B, 4).
    """
    def __init__(self, p: float = 0.5) -> None:
        super().__init__()
        self.drop = nn.Dropout(p)
        self.fc = nn.Linear(256 * 5, 4)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b = x.shape[0]
        flat = self.drop(x.reshape(b, -1))
        out: torch.Tensor = self.fc(flat)
        return out


class _ZeroModel(nn.Module):
    """Always predicts (0, 0, 0, 0) % change — used for round-trip test."""
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.zeros(x.shape[0], 4)


# ------------------------------------------------------------------ tests


def test_returns_ghost_candle_dataclass() -> None:
    torch.manual_seed(0)
    model = _MockModel()
    out = predict_ghost_candle(model, _bars(), last_close=100.0, n_samples=8)
    assert isinstance(out, GhostCandle)


def test_runs_n_samples_forward_passes(monkeypatch: object) -> None:
    """n_samples=8 must invoke the model exactly 8 times."""
    torch.manual_seed(0)
    model = _MockModel()
    counter = {"n": 0}
    real_forward = model.forward

    def counting_forward(x: torch.Tensor) -> torch.Tensor:
        counter["n"] += 1
        return real_forward(x)

    model.forward = counting_forward  # type: ignore[method-assign]
    predict_ghost_candle(model, _bars(), last_close=100.0, n_samples=8)
    assert counter["n"] == 8


def test_uncertainty_positive_with_dropout_active() -> None:
    """With Dropout(p=0.5) on a 1280-wide input, sample variance must be > 0."""
    torch.manual_seed(0)
    model = _MockModel(p=0.5)
    out = predict_ghost_candle(model, _bars(), last_close=100.0, n_samples=16)
    assert out.uncertainty > 0.0


def test_p5_low_le_mean_low_le_mean_high_le_p95_high() -> None:
    torch.manual_seed(0)
    model = _MockModel(p=0.5)
    out = predict_ghost_candle(model, _bars(), last_close=100.0, n_samples=32)
    assert out.p5_low <= out.low + 1e-6
    assert out.high <= out.p95_high + 1e-6


def test_zero_model_round_trip() -> None:
    """A model that predicts all-zero % change should yield a ghost candle
    whose OHLC are exactly the last_close (and the band collapses too).
    """
    model = _ZeroModel()
    last_close = 80_000.0
    out = predict_ghost_candle(model, _bars(), last_close=last_close, n_samples=8)
    assert abs(out.open - last_close) < 1e-3
    assert abs(out.high - last_close) < 1e-3
    assert abs(out.low - last_close) < 1e-3
    assert abs(out.close - last_close) < 1e-3
    assert abs(out.p5_low - last_close) < 1e-3
    assert abs(out.p95_high - last_close) < 1e-3
    assert out.uncertainty == 0.0


def test_default_n_samples_is_32() -> None:
    """Spec sec 6.1 default — verify the keyword default."""
    torch.manual_seed(0)
    model = _MockModel()
    counter = {"n": 0}
    real_forward = model.forward

    def counting_forward(x: torch.Tensor) -> torch.Tensor:
        counter["n"] += 1
        return real_forward(x)

    model.forward = counting_forward  # type: ignore[method-assign]
    predict_ghost_candle(model, _bars(), last_close=100.0)  # no n_samples
    assert counter["n"] == 32
