"""ConvLSTMPredictor — two-stage Conv1d + 2-layer LSTM that consumes a
normalized 256-bar OHLCV window and predicts the next bar's OHLC % change.

Spec sec 3.1 — architecture:
    Conv1d(5, 32, k=3, pad=1) -> ReLU
    Conv1d(32, 64, k=3, pad=1) -> ReLU
    Dropout(0.2)
    LSTM(input=64, hidden=128, layers=2, dropout=0.2)  # MC-dropout source
    Linear(128, 4)

Input:  (batch, 256, 5)  — last axis is [open, high, low, close, volume]
                            already normalized via app.ml.normalize.
Output: (batch, 4)       — [open, high, low, close] predicted % change vs.
                            the window's last close.

The dropout layers stay active under model.train() and are bypassed under
model.eval(). This is what enables MC-dropout uncertainty estimation in
app.ml.inference.predict_ghost_candle (spec sec 6.1).
"""
from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import nn


class ConvLSTMPredictor(nn.Module):
    """Two-stage conv + LSTM predictor — see spec section 3.1."""

    def __init__(self) -> None:
        super().__init__()
        self.conv1 = nn.Conv1d(in_channels=5, out_channels=32, kernel_size=3, padding=1)
        self.conv2 = nn.Conv1d(in_channels=32, out_channels=64, kernel_size=3, padding=1)
        self.conv_drop = nn.Dropout(0.2)
        self.lstm = nn.LSTM(
            input_size=64,
            hidden_size=128,
            num_layers=2,
            dropout=0.2,
            batch_first=True,
        )
        self.fc = nn.Linear(128, 4)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, 256, 5) -> Conv1d wants (batch, channels=5, seq=256).
        x = x.transpose(1, 2)
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = self.conv_drop(x)
        # back to (batch, seq=256, channels=64) for the LSTM.
        x = x.transpose(1, 2)
        h, _ = self.lstm(x)
        # take the LAST timestep's hidden state -> (batch, 128) -> Linear -> (batch, 4)
        out: torch.Tensor = self.fc(h[:, -1, :])
        return out
