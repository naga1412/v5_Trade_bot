"""Unit tests for app/ml/eval.py — per-regime MAE evaluation harness (SP-1 B2)."""
import numpy as np
import pandas as pd
import torch
from torch import nn

from app.ml.eval import RegimeEvalResult, evaluate_on_regime
from app.ml.regimes import ACCEPTANCE_MAE_THRESHOLD, REGIME_WINDOWS


class _ConstantModel(nn.Module):
    """Always predicts 0% change for all 4 outputs. MAE = avg|actual_pct|."""

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # noqa: D401
        return torch.zeros(x.shape[0], 4, dtype=torch.float32)


def _synthetic_bars(n: int, start_ts: pd.Timestamp, drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.005, size=n)))
    df = pd.DataFrame({
        "open":  closes * (1 + rng.normal(0, 0.001, size=n)),
        "high":  closes * (1 + np.abs(rng.normal(0, 0.002, size=n))),
        "low":   closes * (1 - np.abs(rng.normal(0, 0.002, size=n))),
        "close": closes,
        "volume": rng.uniform(1e3, 1e4, size=n),
    })
    df.index = pd.date_range(start=start_ts, periods=n, freq="1h", tz="UTC")
    return df


def test_evaluate_on_regime_returns_result_dataclass() -> None:
    bars = _synthetic_bars(512, pd.Timestamp("2024-04-01", tz="UTC"))
    model = _ConstantModel()
    window = next(w for w in REGIME_WINDOWS if w.name == "low_volatility")

    result = evaluate_on_regime(model=model, bars=bars, window=window, seed=42)
    assert isinstance(result, RegimeEvalResult)
    assert result.regime_name == "low_volatility"
    assert result.samples > 0
    assert result.mae > 0  # constant predictor never gets exact 0% bars
    assert isinstance(result.passes_acceptance, bool)


def test_evaluate_is_deterministic_for_fixed_seed() -> None:
    bars = _synthetic_bars(512, pd.Timestamp("2024-04-01", tz="UTC"))
    model = _ConstantModel()
    window = next(w for w in REGIME_WINDOWS if w.name == "low_volatility")

    a = evaluate_on_regime(model=model, bars=bars, window=window, seed=42)
    b = evaluate_on_regime(model=model, bars=bars, window=window, seed=42)
    assert a.mae == b.mae
    assert a.samples == b.samples


def test_passes_acceptance_uses_threshold() -> None:
    bars = _synthetic_bars(512, pd.Timestamp("2024-04-01", tz="UTC"))
    window = next(w for w in REGIME_WINDOWS if w.name == "low_volatility")

    class _PerfectModel(nn.Module):
        """Memorizes the true % change for each window-end's actual next bar.

        Trivially achieves MAE ~0; passes_acceptance must be True.
        """

        def __init__(self, bars: pd.DataFrame) -> None:
            super().__init__()
            self.bars = bars
            self._counter = 256

        def forward(self, x: torch.Tensor) -> torch.Tensor:  # noqa: D401
            i = self._counter
            self._counter += 1
            actual = self.bars.iloc[i + 1]
            last_close = self.bars.iloc[i]["close"]
            pct = torch.tensor([
                actual["open"] / last_close - 1,
                actual["high"] / last_close - 1,
                actual["low"]  / last_close - 1,
                actual["close"]/ last_close - 1,
            ], dtype=torch.float32).unsqueeze(0)
            return pct

    model = _PerfectModel(bars)
    result = evaluate_on_regime(model=model, bars=bars, window=window, seed=42)
    assert result.mae < ACCEPTANCE_MAE_THRESHOLD
    assert result.passes_acceptance is True
