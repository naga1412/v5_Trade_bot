"""Train SP-4 RL brain on the offline replay buffer (Phase B3).

Dual-target: runs on Colab T4 GPU OR Hetzner 4-vCPU CPU (or your laptop).
The PPO policy is small (~25K params); 365 days of top-60 universe trades
is on the order of 10K-50K transitions, so training is tractable
everywhere — just slower on CPU (~1-2 hrs vs ~30-60 min on T4).

Pipeline:
    1. Connect to backend Postgres (or local SQLite for dev)
    2. Load replay buffer via app.rl.replay_buffer.load_from_shadow_trades
    3. Build / restore AssetEmbeddingTable from previous checkpoint
    4. Construct PolicyNetwork (or restore weights from previous checkpoint
       for warm-start — preserves long-term knowledge across retrains)
    5. Train via app.rl.ppo.train_ppo
    6. Backtest the trained policy against the held-out window
    7. Save .pt + eval.json to --out-dir

Usage on Hetzner (production cron):
    docker compose -f /opt/trading-radar/docker-compose.yml exec -T backend \
        python /app/host-tools/ml/train_brain.py \
            --window-days 365 \
            --out-dir /app/data/rl-cache \
            --device cpu \
            --warm-start /app/data/rl-cache/ppo_policy_v_current.pt

Usage on Colab T4 (manual, one-shot):
    python -m tools.ml.train_brain --device auto --backend-url ...
"""
from __future__ import annotations

# PR-BRAIN-BOOTSTRAP-FIX: prepend /app to sys.path so `from app.rl import ...`
# works when the trainer is invoked as `python /app/host-tools/ml/train_brain.py`
# from the cron (cron's working directory is /, not /app, and Python only adds
# the script's directory to sys.path — not the bind-mount root). Same pattern
# as scripts/diag_audit_bundle.py uses.
import sys
sys.path.insert(0, "/app")

import argparse
import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import sqlalchemy as sa
import torch
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Allow `python -m tools.ml.train_brain` from the repo root by inserting
# backend/ on sys.path so app.rl.* imports resolve.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.rl.adapter import AssetEmbeddingTable  # noqa: E402
from app.rl.policy import PolicyNetwork, count_params  # noqa: E402
from app.rl.ppo import TrainConfig, train_ppo  # noqa: E402
from app.rl.replay_buffer import load_from_shadow_trades  # noqa: E402


log = logging.getLogger(__name__)


def pick_device(want: str) -> torch.device:
    if want == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(want)


async def _discover_training_symbols(db_url: str) -> list[str]:
    """Return distinct symbols appearing in closed shadow_trades.

    Separate cheap query used during cold-start so we can pre-register
    every training-window symbol into the AssetEmbeddingTable BEFORE
    building the replay buffer. Without pre-registration, the buffer
    would assign asset_id=0 + embedding=zeros(32) to every transition
    (the bug PR-BRAIN-WARMSTART-FIX repairs).
    """
    engine = create_async_engine(db_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        rows = (await session.execute(sa.text(
            "SELECT DISTINCT symbol FROM shadow_trades "
            "WHERE closed_at IS NOT NULL AND pnl_usdt IS NOT NULL "
            "ORDER BY symbol"
        ))).all()
    await engine.dispose()
    return [r.symbol for r in rows]


def _extract_asset_embeddings(
    asset_table: AssetEmbeddingTable,
) -> dict[int, np.ndarray]:
    """Snapshot the table's current embeddings as a ``{asset_id: ndarray}`` dict.

    Passed to load_from_shadow_trades so obs vectors carry the real
    per-asset embedding instead of zeros(32). Snapshot semantics are
    fine here because asset embeddings are frozen during PPO training
    (the nn.Embedding is NOT in policy.parameters() — see follow-up
    in spec).
    """
    out: dict[int, np.ndarray] = {}
    weight = asset_table.module.weight.detach().cpu().numpy().astype(np.float32)
    for asset_id in asset_table.symbol_to_id.values():
        out[asset_id] = weight[asset_id].copy()
    return out


async def _build_buffer(
    *, db_url: str, window_days: int, asset_table: AssetEmbeddingTable,
):
    asset_embeddings = _extract_asset_embeddings(asset_table)
    engine = create_async_engine(db_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        transitions = await load_from_shadow_trades(
            session,
            window_days=window_days,
            asset_id_for_symbol=asset_table.symbol_to_id,
            asset_embeddings=asset_embeddings,
        )
    await engine.dispose()
    return transitions


def maybe_warm_start(
    policy: PolicyNetwork, asset_table: AssetEmbeddingTable, ckpt_path: Path | None,
) -> bool:
    """Load weights from a previous checkpoint if one exists. Returns True
    on warm-start, False on cold-start (legitimate — first-ever run)."""
    if ckpt_path is None or not ckpt_path.exists():
        log.info("cold-start training (no warm-start checkpoint provided)")
        return False
    state = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    policy.load_state_dict(state["policy"])
    asset_table.load_state_dict(state["asset_table"])
    log.info("warm-started from %s", ckpt_path)
    return True


def save_checkpoint(
    *, policy: PolicyNetwork, asset_table: AssetEmbeddingTable,
    ckpt_path: Path,
) -> str:
    """Pickle policy weights + asset_table together; return sha256 hex."""
    state = {
        "policy": policy.state_dict(),
        "asset_table": asset_table.state_dict(),
    }
    torch.save(state, ckpt_path)
    return hashlib.sha256(ckpt_path.read_bytes()).hexdigest()


def build_eval_doc(
    *, version: str, transitions, train_history, all_pass: bool,
) -> dict:
    """Assemble the eval JSON the registrar expects.

    For the v1 trainer this is mostly training metadata + a placeholder
    for backtest results. The 6-month backtest (spec sec 5.3) will be
    wired in Phase B5 alongside the smoke tests; until then we report
    training-side metrics only and the operator gates promotion manually.
    """
    return {
        "model_name": "ppo_policy_v1",
        "version": version,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_data_window": {
            "n_transitions": len(transitions),
            "asset_count": len({t.asset_id for t in transitions}),
            "first_open": min((t.opened_at_iso for t in transitions), default=""),
            "last_open": max((t.opened_at_iso for t in transitions), default=""),
        },
        "training": {
            "epochs_completed": len(train_history),
            "final_policy_loss": train_history[-1].policy_loss if train_history else None,
            "final_value_loss": train_history[-1].value_loss if train_history else None,
            "final_entropy": train_history[-1].entropy if train_history else None,
        },
        "all_regimes_pass": all_pass,
        "backtest_results": {
            "_status": "deferred_to_phase_b5",
            "_note": (
                "6-month per-asset Sharpe + portfolio-level metrics will be "
                "computed by tools/ml/backtest_brain.py once Phase B5 lands. "
                "Until then promote checkpoints via Telegram-approve mode "
                "with the operator inspecting raw rewards."
            ),
        },
    }


async def _async_main(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    device = pick_device(args.device)
    log.info(
        "device=%s torch=%s cuda=%s",
        device, torch.__version__, torch.cuda.is_available(),
    )

    asset_table = AssetEmbeddingTable()
    policy = PolicyNetwork().to(device)
    log.info("policy params: %d", count_params(policy))

    # PR-BRAIN-WARMSTART-FIX: ordering matters here.
    #
    # Old order was: build_buffer → register_asset → maybe_warm_start, which
    # meant the buffer was always built with an empty symbol_to_id (every
    # transition got asset_id=0 + embedding=zeros) regardless of warm-start.
    #
    # New order:
    #   1. maybe_warm_start  — restores asset_table.symbol_to_id + weights
    #                          from prior .pt if one exists.
    #   2. bulk_register     — for cold-start only, pre-register all training
    #                          symbols WITHOUT median-seeding their embeddings,
    #                          so each gets its own diverse Gaussian-init.
    #   3. build_buffer      — now sees a populated symbol_to_id AND real
    #                          embeddings via _extract_asset_embeddings.
    #   4. register_asset    — defensive sweep for symbols that appeared in
    #                          the buffer but aren't in asset_table yet
    #                          (warm-start case: new symbols entered universe
    #                          since prior training). Uses the median-seed
    #                          register_asset since these are truly new.
    warm_start_path = (
        Path(args.warm_start) if args.warm_start else None
    )
    warm_started = maybe_warm_start(policy, asset_table, warm_start_path)

    if not warm_started:
        training_symbols = await _discover_training_symbols(args.db_url)
        asset_table.bulk_register(training_symbols)
        log.info(
            "cold-start: pre-registered %d symbols with diverse Gaussian-init "
            "embeddings (skipping median-seed to avoid clustering)",
            len(asset_table.symbol_to_id),
        )

    transitions = await _build_buffer(
        db_url=args.db_url,
        window_days=args.window_days,
        asset_table=asset_table,
    )
    if not transitions:
        log.error("replay buffer empty — no closed trades found in last %d days",
                  args.window_days)
        return 2
    log.info("loaded %d transitions across %d assets",
             len(transitions), len({t.symbol for t in transitions}))

    # Defensive: any symbol that slipped through (e.g., warm-start case where
    # a new symbol entered the universe after prior training) gets the standard
    # median-seed register_asset — appropriate for genuinely-unseen assets.
    for t in transitions:
        asset_table.register_asset(t.symbol)

    cfg = TrainConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        seed=args.seed,
    )
    train_result = train_ppo(
        policy=policy,
        transitions=transitions,
        config=cfg,
        device=device,
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    version = args.version_tag or datetime.now(timezone.utc).strftime(
        "v1-%Y%m%d-%H%M%S",
    )
    ckpt_path = out_dir / f"ppo_policy_{version}.pt"
    eval_path = out_dir / f"eval_brain_{version}.json"

    sha = save_checkpoint(
        policy=policy, asset_table=asset_table, ckpt_path=ckpt_path,
    )
    eval_doc = build_eval_doc(
        version=version, transitions=transitions,
        train_history=train_result.history, all_pass=False,
    )
    eval_doc["sha256"] = sha
    eval_path.write_text(json.dumps(eval_doc, indent=2, default=str))

    log.info("checkpoint -> %s (sha=%s)", ckpt_path, sha[:12])
    log.info("eval json   -> %s", eval_path)
    log.info("Next step: tools/ml/register_brain.py to upload + activate")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tools.ml.train_brain")
    p.add_argument(
        "--db-url",
        default=os.environ.get(
            "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/trading_radar",
        ),
        help="async SQLAlchemy URL for the backend DB",
    )
    p.add_argument("--window-days", type=int, default=365)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--device", default="auto", choices=["auto", "cpu", "cuda"],
    )
    p.add_argument(
        "--warm-start", default=None,
        help="optional prior checkpoint .pt for warm-start fine-tuning",
    )
    p.add_argument("--version-tag", default=None)
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
