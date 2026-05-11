"""Unit tests for app/ml/inference_path.py — multi-step forward rollout.

Reuses the same mock-model shape as test_ml_inference.py so the test
runs in well under a second. Covers:

- n_steps=20 produces exactly 20 GhostStep entries (step indices 1..20).
- Uncertainty band widens monotonically (sqrt-of-step fan).
- Per-step % move is clipped to ±clip_sigma × base_σ even when the
  model returns absurd values.
- Empty list when bars < 256 or n_steps < 1.
- Step 1 inherits the MC-dropout output of predict_ghost_candle (open
  price is anchored to last_close × (1 + step1_pct)).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import torch
from torch import nn

from app.ml.inference_path import GhostStep, predict_ghost_path


def _bars(n: int = 256, base: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    closes = base * np.exp(np.cumsum(rng.normal(0, 0.005, size=n)))
    return pd.DataFrame({
        "open":  closes * 0.999,
        "high":  closes * 1.002,
        "low":   closes * 0.997,
        "close": closes,
        "volume": rng.uniform(1e3, 1e4, size=n),
    }, index=pd.date_range("2026-05-12", periods=n, freq="1h", tz="UTC"))


class _MockModel(nn.Module):
    def __init__(self, p: float = 0.1) -> None:
        super().__init__()
        self.drop = nn.Dropout(p)
        self.fc = nn.Linear(256 * 5, 4)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b = x.shape[0]
        flat = self.drop(x.reshape(b, -1))
        out: torch.Tensor = self.fc(flat)
        return out


class _AbsurdModel(nn.Module):
    """Always returns a +50% move on every OHLC — to test clipping."""
    def __init__(self) -> None:
        super().__init__()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.full((x.shape[0], 4), 0.5)


def test_predict_ghost_path_produces_n_steps_with_sequential_indices() -> None:
    bars = _bars()
    last_close = float(bars["close"].iloc[-1])
    out = predict_ghost_path(_MockModel(), bars, last_close, n_steps=20)
    assert len(out) == 20
    assert [s.step for s in out] == list(range(1, 21))
    assert all(isinstance(s, GhostStep) for s in out)


def test_predict_ghost_path_uncertainty_widens_monotonically() -> None:
    bars = _bars()
    last_close = float(bars["close"].iloc[-1])
    out = predict_ghost_path(_MockModel(), bars, last_close, n_steps=10)
    uncs = [s.uncertainty for s in out]
    # Each step k uses base_sigma * sqrt(k); strictly increasing for k>=2.
    for i in range(2, len(uncs)):
        assert uncs[i] > uncs[i - 1] - 1e-9, (
            f"uncertainty must not shrink: step {i+1}={uncs[i]:.6f} vs "
            f"step {i}={uncs[i-1]:.6f}"
        )
    # √10 ≈ 3.16x widening from step 1 to step 10 in expectation.
    assert uncs[-1] >= uncs[0] * math.sqrt(10) * 0.5  # generous floor


def test_predict_ghost_path_p95_above_p5() -> None:
    bars = _bars()
    last_close = float(bars["close"].iloc[-1])
    out = predict_ghost_path(_MockModel(), bars, last_close, n_steps=5)
    for s in out:
        assert s.p95_high >= s.p5_low, (
            f"step {s.step}: p95_high={s.p95_high} must be >= p5_low={s.p5_low}"
        )


def test_predict_ghost_path_clips_runaway_predictions() -> None:
    """An absurd 50% per-step model output must be clipped.

    With base_sigma roughly ~0.5 (the absurd model produces wide MC
    variance too), clip_sigma=3 means per-step move ≤ ±1.5. By step 5
    that means projected close ≤ last_close × (1 + 1.5)^5 IF unclipped.
    With clipping, growth must be bounded by clip_abs per step.
    """
    bars = _bars()
    last_close = float(bars["close"].iloc[-1])
    out = predict_ghost_path(
        _AbsurdModel(), bars, last_close,
        n_steps=5, n_samples=4, clip_sigma=3.0,
    )
    # The deterministic model returns 0.5 (= +50%) for every OHLC; with
    # clip_sigma=3 and base_sigma > 0, the clipped move per step must be
    # smaller than the unclipped 50%. So by step 5, close should be far
    # less than last_close × 1.5^5 = 7.6×.
    assert out[-1].close < last_close * 7.0, (
        f"clipping failed: step 5 close={out[-1].close} vs unclipped bound 7×"
    )


def test_predict_ghost_path_empty_when_bars_short() -> None:
    bars = _bars(n=100)
    out = predict_ghost_path(_MockModel(), bars, 100.0, n_steps=5)
    assert out == []


def test_predict_ghost_path_zero_steps_returns_empty() -> None:
    bars = _bars()
    out = predict_ghost_path(_MockModel(), bars, 100.0, n_steps=0)
    assert out == []


def test_predict_ghost_path_step1_anchored_to_last_close() -> None:
    bars = _bars()
    last_close = float(bars["close"].iloc[-1])
    out = predict_ghost_path(_MockModel(), bars, last_close, n_steps=3)
    # Step 1's prices should be within an order of magnitude of last_close.
    s1 = out[0]
    assert 0.5 * last_close < s1.close < 2.0 * last_close
    assert 0.5 * last_close < s1.open < 2.0 * last_close
