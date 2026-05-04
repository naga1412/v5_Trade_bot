"""Unit tests for app/ml/normalize.py — window normalization + denormalization (SP-1 C2)."""
import numpy as np
import pandas as pd
import torch

from app.ml.normalize import denormalize_prediction, normalize_window


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


def test_normalize_window_shape_and_dtype() -> None:
    bars = _bars()
    out = normalize_window(bars)
    assert out.shape == (256, 5)
    assert out.dtype == torch.float32


def test_normalize_window_last_close_is_zero_pct() -> None:
    bars = _bars()
    out = normalize_window(bars)
    # Last row's close column (idx 3) should be ~0 (last close vs last close).
    assert abs(out[-1, 3].item()) < 1e-6


def test_normalize_window_volume_z_scored() -> None:
    bars = _bars()
    out = normalize_window(bars)
    vol_col = out[:, 4].numpy()
    assert abs(vol_col.mean()) < 1e-3
    assert abs(vol_col.std() - 1.0) < 1e-2


def test_denormalize_round_trip_close() -> None:
    last_close = 80000.0
    pred_pct = torch.tensor([-0.001, 0.005, -0.003, 0.002], dtype=torch.float32)
    out = denormalize_prediction(pred_pct, last_close=last_close)
    assert abs(out["close"] - last_close * (1.0 + 0.002)) < 1e-3
    assert abs(out["open"] - last_close * (1.0 - 0.001)) < 1e-3
    assert abs(out["high"] - last_close * (1.0 + 0.005)) < 1e-3
    assert abs(out["low"] - last_close * (1.0 - 0.003)) < 1e-3


def test_denormalize_returns_dict_with_four_keys() -> None:
    out = denormalize_prediction(torch.zeros(4), last_close=100.0)
    assert set(out.keys()) == {"open", "high", "low", "close"}
    for k in out:
        assert out[k] == 100.0


def test_normalize_volume_zero_std_handled() -> None:
    """If volume is constant, std=0 — must not produce NaN/inf."""
    bars = _bars()
    bars["volume"] = 1000.0
    out = normalize_window(bars)
    assert torch.isfinite(out).all()
