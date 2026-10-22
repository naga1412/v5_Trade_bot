import asyncio
import glob
import os
import random
import sys

sys.path.insert(0, "/app")

import numpy as np
import torch
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.rl.adapter import AssetEmbeddingTable
from app.rl.policy import PolicyNetwork
from app.rl.ppo import TrainConfig, train_ppo
from app.rl.replay_buffer import load_from_shadow_trades
from app.trading.symbol_allowlist import is_stablecoin_pair


def find_warm_start_path() -> str | None:
    candidates = sorted(
        glob.glob("/app/data/rl-cache/ppo_policy_v*.pt"),
        key=os.path.getmtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


async def main() -> None:
    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        transitions = await load_from_shadow_trades(session, window_days=365)
    print(f"loaded {len(transitions)} transitions total", flush=True)

    settings = get_settings()
    clean = [t for t in transitions if not is_stablecoin_pair(t.symbol, settings)]
    print(f"clean transitions (stablecoin excluded): {len(clean)}", flush=True)

    warm_path = find_warm_start_path()
    print(f"warm-start candidate (same file the real cron would pick): {warm_path}", flush=True)
    if warm_path:
        st = os.stat(warm_path)
        print(f"  mtime={st.st_mtime} size={st.st_size}", flush=True)

    def run_one(label: str, trans: list, *, warm_start: bool) -> None:
        torch.manual_seed(42)
        np.random.seed(42)
        random.seed(42)
        asset_table = AssetEmbeddingTable()
        policy = PolicyNetwork()
        if warm_start and warm_path:
            state = torch.load(warm_path, map_location="cpu", weights_only=False)
            policy.load_state_dict(state["policy"])
            asset_table.load_state_dict(state["asset_table"])
            print(f"  [{label}] warm-started from {warm_path}", flush=True)
        else:
            print(f"  [{label}] cold-start (fresh weights)", flush=True)
        for t in trans:
            asset_table.register_asset(t.symbol)
        cfg = TrainConfig(epochs=30, batch_size=256, seed=42)
        print(
            f"--- [{label}] starting train_ppo on {len(trans)} transitions ---",
            flush=True,
        )
        try:
            result = train_ppo(
                policy=policy, transitions=trans,
                asset_table=asset_table, config=cfg,
                device=torch.device("cpu"),
            )
            print(
                f"[{label}] SUCCESS -- epochs_completed="
                f"{result.epochs_completed}/{cfg.epochs}",
                flush=True,
            )
        except Exception as e:  # noqa: BLE001 -- this IS the diagnostic
            print(f"[{label}] FAILED: {type(e).__name__}: {e}", flush=True)

    run_one("WARM_START + ALL_DATA (matches real cron exactly)", transitions, warm_start=True)
    run_one("WARM_START + STABLECOIN_FILTERED", clean, warm_start=True)


asyncio.run(main())
