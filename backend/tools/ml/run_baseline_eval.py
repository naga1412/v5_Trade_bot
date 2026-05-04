"""Run the RandomWalkBaseline against all 5 regime windows and emit JSON.

Manual run — not part of pytest. Output is committed to
backend/data/eval_baseline.json so the Conv-LSTM eval (Phase E) can produce
a direct MAE comparison.

Usage:
    docker compose exec backend python -m tools.ml.run_baseline_eval \\
        --bulk-export /app/data/ml-bulk-export \\
        --out /app/data/eval_baseline.json
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

from app.ml.baseline import RandomWalkBaseline
from app.ml.eval import evaluate_on_regime
from app.ml.regimes import REGIME_WINDOWS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bulk-export", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--symbol", default="BTC_USDT")
    args = parser.parse_args()

    parquet = args.bulk_export / f"ohlcv_1h_{args.symbol}.parquet"
    df = pq.read_table(parquet).to_pandas().set_index("ts").sort_index()

    model = RandomWalkBaseline()
    results = []
    for window in REGIME_WINDOWS:
        r = evaluate_on_regime(model=model, bars=df, window=window, seed=42)
        results.append(asdict(r))
        print(
            f"{window.name:20s}  mae={r.mae:.5f}  n={r.samples}  "
            f"passes={r.passes_acceptance}"
        )

    args.out.write_text(
        json.dumps(
            {
                "model": "random_walk_baseline_v1",
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
