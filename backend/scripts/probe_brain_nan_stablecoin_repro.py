import asyncio
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


async def main() -> None:
    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        transitions = await load_from_shadow_trades(session, window_days=365)
    print(f"loaded {len(transitions)} transitions total", flush=True)

    settings = get_settings()
    stable = [t for t in transitions if is_stablecoin_pair(t.symbol, settings)]
    clean = [t for t in transitions if not is_stablecoin_pair(t.symbol, settings)]
    print(
        "stablecoin-pair transitions excluded: "
        f"{len(stable)} symbols={sorted({t.symbol for t in stable})}",
        flush=True,
    )
    print(f"clean transitions remaining: {len(clean)}", flush=True)

    def run_one(label: str, trans: list) -> None:
        torch.manual_seed(42)
        np.random.seed(42)
        random.seed(42)
        asset_table = AssetEmbeddingTable()
        for t in trans:
            asset_table.register_asset(t.symbol)
        policy = PolicyNetwork()
        cfg = TrainConfig(epochs=30, batch_size=256, seed=42)
        print(
            f"--- [{label}] starting train_ppo on {len(trans)} "
            "transitions ---",
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

    run_one("WITHOUT_FILTER (reproduces the real cron scenario)", transitions)
    run_one("WITH_STABLECOIN_FILTER", clean)


asyncio.run(main())
