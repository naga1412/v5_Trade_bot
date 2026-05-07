"""Train Conv-LSTM predictor on BTC/USDT 1h history; evaluate on 5 regime windows.

SP-1.1 — produces the first checkpoint that satisfies the SP-1 acceptance gate
(MAE <= 1.5% on ALL 5 regimes). Gates pass = checkpoint can be activated; gates
fail = checkpoint is saved anyway with the eval JSON for diagnosis.

Pipeline (matches SP-1 spec sec 5.2):
    1. Load Parquet from --data
    2. Chronological split: train (2017..2022), val (2023), test (2024+)
    3. Sliding 256-bar windows -> ConvLSTM -> next-bar OHLC % change
    4. Train ~30 epochs, Adam lr=3e-4, MSE loss, early stopping patience=5
    5. Evaluate on val + 5 regime windows from app.ml.regimes
    6. Write .pt + eval.json to --out-dir

Determinism: torch / numpy / random all seeded from --seed (default 42).

Usage (local CPU):
    python -m tools.ml.train \\
        --data data/ml/btcusdt_1h.parquet \\
        --out-dir data/ml/runs/v1-2026-05-07

Usage (Colab GPU): see tools/ml/colab/train_conv_lstm.ipynb
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

# Add backend to path so we can reuse the production model + eval modules.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.ml.eval import evaluate_on_regime  # noqa: E402
from app.ml.model import ConvLSTMPredictor  # noqa: E402
from app.ml.normalize import normalize_window  # noqa: E402
from app.ml.regimes import (  # noqa: E402
    ACCEPTANCE_MAE_THRESHOLD,
    REGIME_WINDOWS,
)


log = logging.getLogger(__name__)

WINDOW_BARS = 256
DEFAULT_BATCH_SIZE = 64
DEFAULT_LR = 3e-4
DEFAULT_EPOCHS = 30
DEFAULT_PATIENCE = 5

# Chronological split boundaries — leak-free per spec sec 8 cross-cut policy.
TRAIN_END = pd.Timestamp("2022-12-31 23:59:59", tz="UTC")
VAL_END = pd.Timestamp("2023-12-31 23:59:59", tz="UTC")


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


class OhlcvWindowDataset(Dataset):
    """Materializes (256-bar normalized window, next-bar % change) pairs.

    On a 50k-bar slice this is ~50k samples * (256*5 + 4) floats * 4 bytes
    = ~250 MB in RAM. Fits comfortably; avoids per-batch normalize cost.
    """

    def __init__(self, bars: pd.DataFrame) -> None:
        if len(bars) < WINDOW_BARS + 2:
            raise ValueError(
                f"need >= {WINDOW_BARS + 2} bars, got {len(bars)}"
            )
        xs: list[torch.Tensor] = []
        ys: list[torch.Tensor] = []
        for i in range(WINDOW_BARS, len(bars) - 1):
            window = bars.iloc[i - WINDOW_BARS:i]
            x = normalize_window(window)
            last_close = float(bars.iloc[i]["close"])
            nxt = bars.iloc[i + 1]
            y = torch.tensor([
                nxt["open"] / last_close - 1.0,
                nxt["high"] / last_close - 1.0,
                nxt["low"] / last_close - 1.0,
                nxt["close"] / last_close - 1.0,
            ], dtype=torch.float32)
            xs.append(x)
            ys.append(y)
        self.x = torch.stack(xs)  # (N, 256, 5)
        self.y = torch.stack(ys)  # (N, 4)

    def __len__(self) -> int:
        return self.x.shape[0]

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self.x[idx], self.y[idx]


@dataclass
class EpochMetrics:
    epoch: int
    train_loss: float
    val_loss: float
    val_mae: float


@dataclass
class TrainingResult:
    epochs_completed: int
    best_epoch: int
    best_val_mae: float
    history: list[EpochMetrics]


def train_one_run(
    *, train_ds: OhlcvWindowDataset, val_ds: OhlcvWindowDataset,
    epochs: int, lr: float, batch_size: int, patience: int,
    device: torch.device, seed: int,
) -> tuple[ConvLSTMPredictor, TrainingResult]:
    set_seed(seed)
    model = ConvLSTMPredictor().to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()

    train_loader = DataLoader(
        train_ds, batch_size=batch_size, shuffle=True, drop_last=True,
    )
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)

    best_state: dict[str, torch.Tensor] | None = None
    best_val_mae = float("inf")
    best_epoch = -1
    history: list[EpochMetrics] = []
    bad_epochs = 0

    for epoch in range(1, epochs + 1):
        model.train()
        losses: list[float] = []
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            opt.zero_grad()
            pred = model(xb)
            loss = loss_fn(pred, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            opt.step()
            losses.append(loss.item())
        train_loss = float(np.mean(losses))

        model.eval()
        v_losses: list[float] = []
        v_abs_errs: list[float] = []
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(device), yb.to(device)
                pred = model(xb)
                v_losses.append(loss_fn(pred, yb).item())
                v_abs_errs.append(float((pred - yb).abs().mean().item()))
        val_loss = float(np.mean(v_losses))
        val_mae = float(np.mean(v_abs_errs))

        history.append(EpochMetrics(epoch, train_loss, val_loss, val_mae))
        log.info(
            "epoch %02d train_loss=%.6f val_loss=%.6f val_mae=%.4f",
            epoch, train_loss, val_loss, val_mae,
        )

        if val_mae < best_val_mae:
            best_val_mae = val_mae
            best_epoch = epoch
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            bad_epochs = 0
        else:
            bad_epochs += 1
            if bad_epochs >= patience:
                log.info(
                    "early stop at epoch %d — no improvement for %d epochs",
                    epoch, patience,
                )
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, TrainingResult(
        epochs_completed=epoch,
        best_epoch=best_epoch,
        best_val_mae=best_val_mae,
        history=history,
    )


def evaluate_all_regimes(
    model: ConvLSTMPredictor, bars: pd.DataFrame, *, seed: int,
) -> dict[str, dict]:
    """Run the 5-regime acceptance gate. Returns dict of regime -> result dict."""
    out: dict[str, dict] = {}
    cpu_model = model.to(torch.device("cpu"))
    for window in REGIME_WINDOWS:
        result = evaluate_on_regime(
            model=cpu_model, bars=bars, window=window, seed=seed,
        )
        out[window.name] = {
            "mae": result.mae,
            "samples": result.samples,
            "passes_acceptance": result.passes_acceptance,
            "per_output_mae": list(result.per_output_mae),
        }
        marker = "PASS" if result.passes_acceptance else "FAIL"
        log.info(
            "regime %-18s mae=%.4f samples=%d %s",
            window.name, result.mae, result.samples, marker,
        )
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tools.ml.train")
    p.add_argument("--data", required=True, help="Parquet from fetch_ohlcv.py")
    p.add_argument("--out-dir", required=True, help="dir for checkpoint + eval.json")
    p.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    p.add_argument("--lr", type=float, default=DEFAULT_LR)
    p.add_argument("--patience", type=int, default=DEFAULT_PATIENCE)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--device", default="auto", choices=["auto", "cpu", "cuda"],
    )
    p.add_argument(
        "--version-tag",
        default=datetime.now(timezone.utc).strftime("v1-%Y%m%d-%H%M%S"),
        help="checkpoint version label, e.g. v1-20260507-180000",
    )
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)
    log.info("device=%s torch=%s", device, torch.__version__)

    bars = pd.read_parquet(args.data)
    if not isinstance(bars.index, pd.DatetimeIndex):
        bars.index = pd.to_datetime(bars.index, utc=True)
    if bars.index.tz is None:
        bars.index = bars.index.tz_localize("UTC")
    bars = bars.sort_index()
    log.info("loaded %d bars from %s..%s", len(bars), bars.index[0], bars.index[-1])

    train_bars = bars.loc[:TRAIN_END]
    val_bars = bars.loc[TRAIN_END:VAL_END]
    test_bars = bars.loc[VAL_END:]
    log.info(
        "split sizes — train=%d val=%d test=%d",
        len(train_bars), len(val_bars), len(test_bars),
    )

    log.info("building train/val datasets (sliding windows, materialized)")
    train_ds = OhlcvWindowDataset(train_bars)
    val_ds = OhlcvWindowDataset(val_bars)
    log.info("train_ds=%d samples val_ds=%d samples", len(train_ds), len(val_ds))

    model, train_result = train_one_run(
        train_ds=train_ds, val_ds=val_ds,
        epochs=args.epochs, lr=args.lr, batch_size=args.batch_size,
        patience=args.patience, device=device, seed=args.seed,
    )

    log.info("evaluating on 5 regime windows ...")
    regime_results = evaluate_all_regimes(model, bars, seed=args.seed)
    all_pass = all(r["passes_acceptance"] for r in regime_results.values())

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = out_dir / f"conv_lstm_{args.version_tag}.pt"
    eval_path = out_dir / f"eval_{args.version_tag}.json"

    torch.save(model.state_dict(), ckpt_path)

    eval_doc = {
        "model_name": "conv_lstm_predictor",
        "version": args.version_tag,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_data_window": {
            "train_start": str(train_bars.index[0]),
            "train_end": str(train_bars.index[-1]),
            "val_start": str(val_bars.index[0]),
            "val_end": str(val_bars.index[-1]),
            "n_train_bars": len(train_bars),
            "n_val_bars": len(val_bars),
        },
        "training": {
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "lr": args.lr,
            "patience": args.patience,
            "seed": args.seed,
            "epochs_completed": train_result.epochs_completed,
            "best_epoch": train_result.best_epoch,
            "best_val_mae": train_result.best_val_mae,
            "history": [asdict(m) for m in train_result.history],
        },
        "regime_results": regime_results,
        "acceptance_threshold": ACCEPTANCE_MAE_THRESHOLD,
        "all_regimes_pass": all_pass,
    }
    eval_path.write_text(json.dumps(eval_doc, indent=2, default=str))

    log.info("checkpoint -> %s", ckpt_path)
    log.info("eval json   -> %s", eval_path)
    if all_pass:
        log.info("✓ ALL 5 regimes pass acceptance gate (MAE <= %.4f)",
                 ACCEPTANCE_MAE_THRESHOLD)
        log.info("Next: tools/ml/register.py to upload + activate")
        return 0
    log.warning("✗ acceptance gate FAILED on at least one regime — see eval.json")
    return 2


if __name__ == "__main__":
    sys.exit(main())
