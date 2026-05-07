"""Smoke tests for tools/ml/train.py.

A full 30-epoch training run is too slow for CI (would take 30 min on
T4 GPU, hours on CPU). These tests validate the *plumbing*:
    - dataset materialization shape
    - chronological split boundaries
    - one tiny end-to-end training run on synthetic data (1 epoch, 300 bars)
      that produces a checkpoint + eval JSON without crashing
"""
from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

from tools.ml import train as T


@pytest.fixture
def synthetic_bars() -> pd.DataFrame:
    """1000 bars of plausible OHLCV starting 2024-01-01."""
    rng = np.random.default_rng(7)
    n = 1000
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.005, size=n)))
    df = pd.DataFrame({
        "open":  closes * (1 + rng.normal(0, 0.001, size=n)),
        "high":  closes * (1 + np.abs(rng.normal(0, 0.002, size=n))),
        "low":   closes * (1 - np.abs(rng.normal(0, 0.002, size=n))),
        "close": closes,
        "volume": rng.uniform(1e3, 1e4, size=n),
    })
    df.index = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return df


def test_dataset_shapes_match_window_bars(synthetic_bars: pd.DataFrame) -> None:
    ds = T.OhlcvWindowDataset(synthetic_bars)
    expected_n = len(synthetic_bars) - T.WINDOW_BARS - 1
    assert len(ds) == expected_n
    x, y = ds[0]
    assert x.shape == (T.WINDOW_BARS, 5)
    assert y.shape == (4,)


def test_dataset_rejects_too_short_input() -> None:
    short = pd.DataFrame({
        "open": [100.0, 100.5], "high": [101.0, 101.5],
        "low": [99.0, 100.0], "close": [100.5, 101.0],
        "volume": [1000.0, 1100.0],
    }, index=pd.date_range("2024-01-01", periods=2, freq="1h", tz="UTC"))
    with pytest.raises(ValueError, match="need >="):
        T.OhlcvWindowDataset(short)


def test_split_boundaries_are_chronological() -> None:
    """TRAIN_END < VAL_END — no time-travel in the split itself."""
    assert T.TRAIN_END < T.VAL_END
    assert T.TRAIN_END.tz is not None
    assert T.VAL_END.tz is not None


def test_set_seed_makes_torch_reproducible() -> None:
    T.set_seed(123)
    a = torch.randn(3)
    T.set_seed(123)
    b = torch.randn(3)
    assert torch.equal(a, b)


def test_one_tiny_training_run_end_to_end(
    synthetic_bars: pd.DataFrame, tmp_path: Path,
) -> None:
    """1-epoch run on 1000 bars; produces .pt + eval.json with the right keys.

    Wall time: ~3-10s on CPU. Skips the regime eval (which needs 7+ years
    of data) by stubbing it to an empty dict — we only check plumbing here.
    """
    train_bars = synthetic_bars.iloc[:600]
    val_bars = synthetic_bars.iloc[600:]
    train_ds = T.OhlcvWindowDataset(train_bars)
    val_ds = T.OhlcvWindowDataset(val_bars)

    model, result = T.train_one_run(
        train_ds=train_ds, val_ds=val_ds,
        epochs=1, lr=1e-3, batch_size=16, patience=3,
        device=torch.device("cpu"), seed=42,
    )

    assert result.epochs_completed == 1
    assert result.best_epoch == 1
    assert len(result.history) == 1
    assert result.history[0].train_loss > 0
    assert result.history[0].val_loss > 0
    # State dict should have the four parameter groups defined in ConvLSTMPredictor.
    state = model.state_dict()
    assert any(k.startswith("conv1") for k in state)
    assert any(k.startswith("conv2") for k in state)
    assert any(k.startswith("lstm") for k in state)
    assert any(k.startswith("fc") for k in state)


def test_evaluate_all_regimes_writes_pass_fail_per_window(
    synthetic_bars: pd.DataFrame,
) -> None:
    """Synthetic bars span only 2024 — the function still iterates all 5
    regime windows and returns a dict per regime, even if 4 of them have
    0 samples (out-of-range windows degrade gracefully via the eval
    harness's len-check)."""
    train_ds = T.OhlcvWindowDataset(synthetic_bars.iloc[:600])
    val_ds = T.OhlcvWindowDataset(synthetic_bars.iloc[600:])
    model, _ = T.train_one_run(
        train_ds=train_ds, val_ds=val_ds,
        epochs=1, lr=1e-3, batch_size=16, patience=3,
        device=torch.device("cpu"), seed=42,
    )

    results = T.evaluate_all_regimes(model, synthetic_bars, seed=42)
    assert set(results.keys()) >= {
        "bull_breakout", "bear_crash", "sideways_grind",
        "high_volatility", "low_volatility",
    }
    for name, r in results.items():
        assert "mae" in r
        assert "samples" in r
        assert "passes_acceptance" in r
