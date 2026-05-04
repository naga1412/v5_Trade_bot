"""Unit tests for app/ml/baseline.py — random-walk MAE floor (SP-1 B3)."""
import numpy as np
import pandas as pd
import torch

from app.ml.baseline import RandomWalkBaseline
from app.ml.eval import evaluate_on_regime
from app.ml.regimes import REGIME_WINDOWS


def _synthetic_bars(n: int, start_ts: pd.Timestamp, sigma: float = 0.005) -> pd.DataFrame:
    rng = np.random.default_rng(123)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, sigma, size=n)))
    df = pd.DataFrame({
        "open":  closes,
        "high":  closes * 1.001,
        "low":   closes * 0.999,
        "close": closes,
        "volume": np.full(n, 1000.0),
    })
    df.index = pd.date_range(start=start_ts, periods=n, freq="1h", tz="UTC")
    return df


def test_random_walk_baseline_predicts_zero_pct_change() -> None:
    model = RandomWalkBaseline()
    x = torch.zeros(8, 256, 5)
    out = model(x)
    assert out.shape == (8, 4)
    assert torch.all(out == 0)


def test_baseline_mae_in_low_vol_under_one_percent() -> None:
    """On a low-vol synthetic series, the baseline's MAE should be < 1%."""
    model = RandomWalkBaseline()
    bars = _synthetic_bars(512, pd.Timestamp("2024-04-01", tz="UTC"), sigma=0.003)
    window = next(w for w in REGIME_WINDOWS if w.name == "low_volatility")
    result = evaluate_on_regime(model=model, bars=bars, window=window, seed=42)
    assert result.mae < 0.01


def test_baseline_mae_in_high_vol_higher() -> None:
    """In high vol, baseline MAE should be > low vol."""
    bars_low = _synthetic_bars(512, pd.Timestamp("2024-04-01", tz="UTC"), sigma=0.003)
    bars_high = _synthetic_bars(512, pd.Timestamp("2020-03-01", tz="UTC"), sigma=0.02)
    window_low = next(w for w in REGIME_WINDOWS if w.name == "low_volatility")
    window_high = next(w for w in REGIME_WINDOWS if w.name == "high_volatility")

    model = RandomWalkBaseline()
    r_low = evaluate_on_regime(model=model, bars=bars_low, window=window_low, seed=42)
    r_high = evaluate_on_regime(model=model, bars=bars_high, window=window_high, seed=42)
    assert r_high.mae > r_low.mae
