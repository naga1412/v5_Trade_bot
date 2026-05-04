"""Unit tests for app/ml/model.py — ConvLSTMPredictor (SP-1 C1).

Spec sec 3.1 — two-stage Conv1d (5->32->64) + 2-layer LSTM(128) + Linear(4)
that consumes a (batch, 256, 5) normalized OHLCV window and predicts the
next bar's OHLC % change. Total parameter count ~500K.

MC dropout (sec 6.1) requires that dropout layers stay active under
`model.train()` even when we call `model(x)` from inference, and turn off
under `model.eval()`. We assert both code paths.
"""
from __future__ import annotations

import torch

from app.ml.model import ConvLSTMPredictor


def test_forward_pass_single_sample_shape() -> None:
    model = ConvLSTMPredictor()
    x = torch.randn(1, 256, 5)
    out = model(x)
    assert out.shape == (1, 4)


def test_forward_pass_batch_shape() -> None:
    model = ConvLSTMPredictor()
    x = torch.randn(8, 256, 5)
    out = model(x)
    assert out.shape == (8, 4)


def test_parameter_count_in_expected_range() -> None:
    """Sanity bound on model size — the spec'd architecture
    (Conv1d 5->32->64, LSTM 2x128, Linear 128->4) computes to ~238K params:
        conv1:        5 * 32 * 3 + 32       =      512
        conv2:       32 * 64 * 3 + 64       =    6,208
        LSTM layer1: 4 * (64*128 + 128*128 + 128)  =   98,816
        LSTM layer2: 4 * (128*128 + 128*128 + 128) =  131,584
        fc:         128 * 4 + 4              =      516
        ------------------------------------------------
        total                                 ~ 237,636
    Bound at 200K-600K — catches a wildly oversized model (e.g. an LSTM
    width bump) while still allowing the spec arch to pass.
    """
    model = ConvLSTMPredictor()
    n_params = sum(p.numel() for p in model.parameters())
    assert 200_000 <= n_params <= 600_000, f"got {n_params} params"


def test_dropout_active_in_train_mode_inactive_in_eval_mode() -> None:
    """MC dropout sanity — same input twice gives different outputs in train
    mode (dropout is stochastic) and identical outputs in eval mode.
    """
    torch.manual_seed(0)
    model = ConvLSTMPredictor()
    x = torch.randn(2, 256, 5)

    model.eval()
    with torch.no_grad():
        out_eval_a = model(x)
        out_eval_b = model(x)
    assert torch.allclose(out_eval_a, out_eval_b), "eval mode must be deterministic"

    model.train()
    with torch.no_grad():
        out_train_a = model(x)
        out_train_b = model(x)
    assert not torch.allclose(out_train_a, out_train_b), (
        "train mode dropout must produce different outputs across calls"
    )


def test_output_dtype_is_float32() -> None:
    model = ConvLSTMPredictor()
    x = torch.randn(1, 256, 5)
    out = model(x)
    assert out.dtype == torch.float32
