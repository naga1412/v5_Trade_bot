"""Window normalization for Conv-LSTM input.

Spec sec 3.2 — OHLC are converted to % change from the window's LAST close (not
mean), so the last row's close column is exactly 0. Volume is z-scored over
the window. This makes the model price-scale-invariant — the same model
generalizes from $30k BTC to $80k BTC with no retraining needed.

Edge: a window where volume std is 0 (constant volume) would produce inf/nan
under naive z-scoring. We fall back to zero in that case.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import torch


def normalize_window(bars: pd.DataFrame) -> torch.Tensor:
    """Normalize a 256-row OHLCV window for Conv-LSTM input.

    bars: DataFrame with columns [open, high, low, close, volume].
    Returns: tensor shape (len(bars), 5) — OHLC as % change from
    bars['close'].iloc[-1], volume z-scored over the window.
    """
    if len(bars) < 2:
        raise ValueError(f"normalize_window needs >= 2 rows, got {len(bars)}")

    last_close = float(bars["close"].iloc[-1])
    if last_close <= 0:
        raise ValueError(f"last close must be positive, got {last_close}")

    pct = (
        bars[["open", "high", "low", "close"]]
        .astype(float)
        .div(last_close)
        .sub(1.0)
        .to_numpy()
    )

    vol = bars["volume"].astype(float).to_numpy()
    vol_std = vol.std()
    if vol_std < 1e-12:
        vol_z = np.zeros_like(vol)
    else:
        vol_z = (vol - vol.mean()) / vol_std

    arr = np.column_stack([pct, vol_z]).astype(np.float32)
    return torch.from_numpy(arr)


def denormalize_prediction(
    pred_pct: torch.Tensor, *, last_close: float
) -> dict[str, float]:
    """Convert a (4,) tensor of % changes back to absolute OHLC prices.

    Returns: {open, high, low, close} in price units.
    """
    if pred_pct.shape != (4,):
        raise ValueError(f"expected shape (4,), got {tuple(pred_pct.shape)}")
    return {
        "open":  last_close * (1.0 + float(pred_pct[0].item())),
        "high":  last_close * (1.0 + float(pred_pct[1].item())),
        "low":   last_close * (1.0 + float(pred_pct[2].item())),
        "close": last_close * (1.0 + float(pred_pct[3].item())),
    }
