"""Re-evaluate existing brain checkpoints against the CURRENT buffer state.

PR-BRAIN-CORRECTNESS-METRIC (2026-05-22) added ``brain_correctness`` to
BacktestResult. Existing rl_checkpoints rows (id=1..6) were saved before
this field was tracked — their ``eval_results`` JSONB has no
``brain_correctness`` key.

This script loads each .pt file (typically in ``_predegenerate/`` after
the brain-rl-cache-archive probe runs), rebuilds the replay buffer from
the CURRENT shadow_trades state, runs ``evaluate_brain_on_holdout``, and
prints a comparison table — letting us judge the old checkpoints by the
new metric without retraining.

Usage:
    docker compose exec -T backend python /app/host-tools/ml/reeval_checkpoints.py \
        --pt-glob '/app/data/rl-cache/_predegenerate/ppo_policy_v*.pt'

Notes:
* The buffer is the SAME for all evaluations (current shadow_trades
  snapshot) → comparable across checkpoints
* Loads policy + asset_table state from each .pt; computes backtest with
  the current backtest_brain.py code
* Read-only; does NOT modify rl_checkpoints or any .pt files
"""
from __future__ import annotations

# Same sys.path shim as train_brain.py and register_brain.py.
import sys
sys.path.insert(0, "/app")

import argparse
import asyncio
import glob
import json
import logging
import os
from pathlib import Path

import torch
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# tools/ml is on sys.path via Python's startup; backend's /app/host-tools
# bind-mount makes app.* resolvable.
from app.rl.adapter import AssetEmbeddingTable
from app.rl.backtest_brain import evaluate_brain_on_holdout
from app.rl.policy import PolicyNetwork
from app.rl.replay_buffer import load_from_shadow_trades


log = logging.getLogger(__name__)


async def _build_buffer_async(db_url: str, asset_table: AssetEmbeddingTable):
    """Build the same buffer train_brain would use — for the loaded
    asset_table's symbol_to_id state. Returns list[Transition]."""
    engine = create_async_engine(db_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        # Snapshot embeddings for hot-swap consistency with the new
        # backtest_brain code.
        weight = (
            asset_table.module.weight.detach().cpu().numpy().astype("float32")
        )
        embeddings = {
            aid: weight[aid].copy()
            for aid in asset_table.symbol_to_id.values()
        }
        transitions = await load_from_shadow_trades(
            session,
            window_days=365,
            asset_id_for_symbol=asset_table.symbol_to_id,
            asset_embeddings=embeddings,
        )
    await engine.dispose()
    return transitions


def _reeval_one(pt_path: Path, db_url: str, device: torch.device) -> dict:
    """Load one .pt, build buffer, run backtest. Returns metrics dict."""
    state = torch.load(pt_path, map_location="cpu", weights_only=False)

    policy = PolicyNetwork().to(device)
    policy.load_state_dict(state["policy"])

    asset_table = AssetEmbeddingTable()
    asset_table.load_state_dict(state["asset_table"])

    transitions = asyncio.run(_build_buffer_async(db_url, asset_table))
    if not transitions:
        return {
            "pt": pt_path.name,
            "_status": "no_transitions",
        }

    outcome = evaluate_brain_on_holdout(
        policy=policy, asset_table=asset_table,
        transitions=transitions, device=device,
    )
    if isinstance(outcome, dict):
        return {"pt": pt_path.name, **outcome}
    # BacktestResult
    return {
        "pt": pt_path.name,
        "sharpe": outcome.sharpe,
        "max_dd": outcome.max_dd,
        "win_rate": outcome.win_rate,
        "brain_correctness": outcome.brain_correctness,
        "brain_correctness_n": outcome.brain_correctness_n,
        "cumulative_reward": outcome.cumulative_reward,
        "total_trades": outcome.total_trades,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tools.ml.reeval_checkpoints")
    p.add_argument(
        "--pt-glob",
        default="/app/data/rl-cache/_predegenerate/ppo_policy_v*.pt",
        help="glob for .pt files to re-evaluate",
    )
    p.add_argument(
        "--db-url",
        default=os.environ.get(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/trading_radar",
        ),
    )
    p.add_argument("--device", default="cpu", choices=["cpu", "cuda", "auto"])
    p.add_argument("--log-level", default="WARNING")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)

    pt_paths = sorted(glob.glob(args.pt_glob))
    if not pt_paths:
        log.error("no .pt files matched glob: %s", args.pt_glob)
        return 1

    results = []
    for path_s in pt_paths:
        try:
            r = _reeval_one(Path(path_s), args.db_url, device)
            results.append(r)
        except Exception as e:  # survive per-checkpoint errors
            log.exception("reeval failed for %s", path_s)
            results.append({"pt": Path(path_s).name, "_error": str(e)})

    # Print a comparison table — operator-readable, plus JSON for piping.
    print("\n=== brain checkpoint re-evaluation (current buffer state) ===")
    print(
        f"{'checkpoint':50s} {'sharpe':>8s} {'max_dd':>8s} "
        f"{'win_rate':>9s} {'brain_corr':>11s} {'cum_rwd':>10s} "
        f"{'n_scored':>9s} {'n_trades':>9s}",
    )
    for r in results:
        if "_error" in r:
            print(f"{r['pt']:50s} ERROR: {r['_error'][:80]}")
            continue
        if r.get("_status") == "insufficient_data":
            print(f"{r['pt']:50s} (insufficient_data)")
            continue
        if r.get("_status") == "no_transitions":
            print(f"{r['pt']:50s} (no transitions)")
            continue
        print(
            f"{r['pt']:50s} "
            f"{r.get('sharpe', 0):+8.3f} "
            f"{r.get('max_dd', 0):8.3f} "
            f"{r.get('win_rate', 0):9.4f} "
            f"{r.get('brain_correctness', 0):11.4f} "
            f"{r.get('cumulative_reward', 0):+10.3f} "
            f"{r.get('brain_correctness_n', 0):>9d} "
            f"{r.get('total_trades', 0):>9d}",
        )

    print("\n=== JSON ===")
    print(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
