"""Offline brain (Layer 10) RL training driver — SP-4 Phase G.

Driver script invoked nightly by ``backend/scripts/hetzner_brain_cron.sh``:

    python /app/host-tools/ml/train_brain.py \
      --window-days 365 \
      --out-dir /app/data/rl-cache \
      --device cpu \
      --version-tag v1-20260514-031500

What it does:
  1. Open a DB session and call ``load_from_shadow_trades`` to build a
     list of ``Transition`` (obs, action, reward, next_obs, done) from
     historical closed shadow trades.
  2. If no transitions are available (< MIN_TRANSITIONS_TO_TRAIN), exit
     cleanly with a message — the nightly cron handles that as a soft
     failure (it just means we don't have enough data yet to train).
  3. Build a fresh PolicyNetwork (or load --warm-start state_dict on top).
  4. Run ``train_ppo`` with the supplied TrainConfig.
  5. Save final state_dict to ``<out-dir>/ppo_policy_<version>.pt``.
  6. Write an eval JSON to ``<out-dir>/eval_brain_<version>.json`` with
     training metrics + dataset stats — the cron consumes this to decide
     if the challenger beats the champion.

This driver does NOT activate the resulting checkpoint in
``rl_checkpoints``; that's the job of ``register_brain.py`` which the
cron calls separately after a Telegram-approved promotion.

Why this is a "scaffold":
  Production currently has 12 lifetime shadow trades (7 closed + 5 open),
  vastly fewer than the ~10000 typically needed for PPO to produce a
  policy meaningfully better than random. The cron will keep running
  every night; once enough trades accumulate, this driver will start
  producing real candidate policies that improve over the no-op
  ``brain_adjust=1.0`` baseline.

  In the meantime, the scaffold gives the operator:
    - a working training pipeline they can poke at on a laptop or in
      tests with synthetic data;
    - a clean exit-0-with-message path when data is insufficient;
    - the symmetric LONG/SHORT learning we want (PPO rewards both sides
      equally based on the realized R-multiple of each trade).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch

from app.db.session import get_session_factory
from app.rl.policy import PolicyNetwork
from app.rl.ppo import TrainConfig, train_ppo
from app.rl.replay_buffer import load_from_shadow_trades


log = logging.getLogger("train_brain")

# Below this many closed-trade transitions we don't even attempt to
# train — too noisy for PPO to converge on anything but garbage.
# Set low enough that the operator can dry-run with synthetic data,
# but real production training waits for orders-of-magnitude more.
MIN_TRANSITIONS_TO_TRAIN: int = 50


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--window-days", type=int, default=365,
        help="How many days of shadow_trades history to feed the PPO buffer.",
    )
    p.add_argument(
        "--out-dir", type=Path, required=True,
        help="Where to write ppo_policy_<version>.pt + eval_brain_<version>.json.",
    )
    p.add_argument(
        "--device", default="cpu",
        choices=["cpu", "cuda"],
        help="Torch device for training. CPU is fine for the current dataset size.",
    )
    p.add_argument(
        "--version-tag", required=True,
        help="Version string for output filenames + rl_checkpoints.version.",
    )
    p.add_argument(
        "--warm-start", type=Path, default=None,
        help="Optional .pt state_dict to load before training (champion-warm-start).",
    )
    p.add_argument(
        "--epochs", type=int, default=None,
        help="Override TrainConfig.epochs (default 30 from ppo.py).",
    )
    p.add_argument(
        "--seed", type=int, default=0,
        help="Random seed for reproducibility.",
    )
    p.add_argument(
        "--min-transitions", type=int, default=MIN_TRANSITIONS_TO_TRAIN,
        help="Refuse to train below this many transitions (soft-exit 0).",
    )
    return p


async def _load_transitions_async(window_days: int) -> list[Any]:
    """Open a DB session and call load_from_shadow_trades."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        return await load_from_shadow_trades(
            session, window_days=window_days,
        )


def _build_train_config(args: argparse.Namespace) -> TrainConfig:
    # Use TrainConfig defaults except where the operator overrode them.
    kwargs: dict[str, Any] = {"seed": args.seed}
    if args.epochs is not None:
        kwargs["epochs"] = args.epochs
    return TrainConfig(**kwargs)


def _maybe_load_warm_start(
    policy: PolicyNetwork, path: Path | None, device: torch.device,
) -> None:
    if path is None:
        return
    if not path.exists():
        log.warning(
            "warm-start path %s does not exist; training from random init", path
        )
        return
    state = torch.load(path, map_location=device)
    policy.load_state_dict(state)
    log.info("loaded warm-start state_dict from %s", path)


def _write_eval_json(
    eval_path: Path,
    *,
    version_tag: str,
    transitions_count: int,
    window_days: int,
    result: Any,
    sha256: str,
    checkpoint_path: Path,
) -> None:
    """Write the training metrics JSON the nightly cron consumes."""
    history = []
    for ep in result.history:
        history.append({
            "epoch": ep.epoch,
            "policy_loss": ep.policy_loss,
            "value_loss": ep.value_loss,
            "entropy": ep.entropy,
            "total_loss": ep.total_loss,
        })
    payload = {
        "version_tag": version_tag,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_transitions": transitions_count,
        "window_days": window_days,
        "epochs_completed": result.epochs_completed,
        "final_policy_loss": history[-1]["policy_loss"] if history else None,
        "final_value_loss": history[-1]["value_loss"] if history else None,
        "final_entropy": history[-1]["entropy"] if history else None,
        "history": history,
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": sha256,
    }
    eval_path.write_text(json.dumps(payload, indent=2))


async def _async_main(args: argparse.Namespace) -> int:
    """Async core. Returns process exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = args.out_dir / f"ppo_policy_{args.version_tag}.pt"
    eval_path = args.out_dir / f"eval_brain_{args.version_tag}.json"

    log.info("loading transitions (window_days=%d)...", args.window_days)
    transitions = await _load_transitions_async(args.window_days)
    log.info("loaded %d transitions from shadow_trades", len(transitions))

    if len(transitions) < args.min_transitions:
        log.warning(
            "not enough transitions to train (have %d, need >= %d). "
            "Exiting 0 — try again when more shadow trades have accumulated.",
            len(transitions), args.min_transitions,
        )
        return 0

    device = torch.device(args.device)
    policy = PolicyNetwork().to(device)
    _maybe_load_warm_start(policy, args.warm_start, device)

    config = _build_train_config(args)
    log.info("starting PPO train_ppo (epochs=%d, seed=%d)", config.epochs, config.seed)
    result = train_ppo(
        policy=policy, transitions=transitions, config=config, device=device,
    )
    log.info(
        "training done: %d/%d epochs, final policy_loss=%.4f",
        result.epochs_completed, config.epochs,
        result.history[-1].policy_loss if result.history else float("nan"),
    )

    if result.final_state_dict is None:
        log.error("train_ppo returned no state_dict — refusing to write checkpoint")
        return 1

    torch.save(result.final_state_dict, checkpoint_path)
    sha = _sha256_file(checkpoint_path)
    log.info("wrote checkpoint to %s (sha256=%s)", checkpoint_path, sha[:12])

    _write_eval_json(
        eval_path,
        version_tag=args.version_tag,
        transitions_count=len(transitions),
        window_days=args.window_days,
        result=result,
        sha256=sha,
        checkpoint_path=checkpoint_path,
    )
    log.info("wrote eval JSON to %s", eval_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Sync entry point — wraps the async core in asyncio.run."""
    args = _build_arg_parser().parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
