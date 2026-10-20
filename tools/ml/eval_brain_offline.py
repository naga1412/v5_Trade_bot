"""Offline predictive evaluation of the RL brain on strictly held-out data.

PRE-REGISTERED in docs/superpowers/decisions/2026-09-17-brain-offline-eval-
preregistration.md. Every bar, split, criterion and label below was fixed
BEFORE this script first ran. Read that document first.

What this is NOT: it is not backtest_brain.py. That evaluates Sharpe on a
SYNTHETIC reward series (reward * brain_adjust) and has two documented
defects -- win_rate is invariant to the brain, and Sharpe of a ratio
metric misleads when std collapses. This script answers a different and
simpler question: does the brain's per-trade output SEPARATE real
pnl_pct on trades it has never seen?

Method (the same one that audited L1-L6):
  1. Strict TEMPORAL split on opened_at: TRAIN < 2026-08-20,
     PRIMARY [2026-08-20, 2026-09-05), REPLICATION >= 2026-09-05.
     No shuffling. No k-fold across time.
  2. Train PPO from RANDOM INIT on the TRAIN window only. No warm start.
     No checkpoint whose lineage touches rl_checkpoints.id=70 (poisoned
     NaN run) or id=62 (58-dim, incompatible with OBS_DIM 53).
  3. Score every held-out trade: deterministic action -> brain_adjust
     (what live would act on) and V(s) (continuous). Bucket, compare
     avg_pnl_pct with n and SE.
  4. NEUTRAL BASELINE: an untrained, randomly-initialised policy scored
     on the identical harness. If trained ~ untrained, the model learned
     nothing.
  5. TRAIN-set performance reported next to held-out, every time -- the
     overfitting signature is train >> held-out.
  6. New symbols (no TRAIN row) reported SEPARATELY, never dropped.
  7. oi_delta_24h MISSINGNESS INDICATOR, not zero-fill. The collector
     started 2026-08-05; zero-fill would let the model read
     "pre-August" off one dim -- a temporal leak in feature clothing.
     The indicator is written into the L8 slot (obs index 38), which is
     a structural constant 0.0 (flag off -> None -> 0.0) and therefore
     carries no information to displace. Applied identically to TRAIN
     and held-out. NOTE: this is an evaluation-harness convention, not
     a production obs.py change; if Phase 2 separates, Phase 3 must
     formalise the indicator properly.

Writes NOTHING: no checkpoint, no rl_checkpoints row, no DB write.
Prints a human table and a JSON block.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import sqlalchemy as sa
import torch
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.rl.adapter import AssetEmbeddingTable  # noqa: E402
from app.rl.backtest_brain import _infer_proposed_direction  # noqa: E402
from app.rl.inference import action_to_brain_adjust  # noqa: E402
from app.rl.obs import EMB_DIM, OBS_DIM  # noqa: E402
from app.rl.policy import PolicyNetwork  # noqa: E402
from app.rl.ppo import TrainConfig, train_ppo  # noqa: E402
from app.rl.replay_buffer import ALL_ACTIONS, Transition, load_from_shadow_trades  # noqa: E402

log = logging.getLogger("eval_brain_offline")

# --- pre-registered constants (do not edit after the first run) --------
TRAIN_END = datetime(2026, 8, 20, tzinfo=timezone.utc)
PRIMARY_END = datetime(2026, 9, 5, tzinfo=timezone.utc)
OI_MISSING_SLOT = EMB_DIM + 6          # L8's slot: index 38, structural constant 0.0
MIN_ENTRY_SCORE_LONG = 0.36
SECONDARY_BAR_PCT = 3.2                # pre-registered; 1h/LONG is UNINFORMATIVE
SECONDARY_LABEL = "directional only in the sense that it has a sign"


@dataclasses.dataclass
class Side:
    pnl_pct: float
    timeframe: str
    direction: str
    entry_score: float
    oi_missing: bool


async def _load_side_table(db_url: str) -> dict[tuple[str, str], Side]:
    """(symbol, opened_at_iso) -> real outcome + the raw OI missingness."""
    engine = create_async_engine(db_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    out: dict[tuple[str, str], Side] = {}
    async with sm() as s:
        rows = (await s.execute(sa.text(
            "SELECT st.symbol, st.opened_at, st.pnl_pct, st.timeframe, st.direction, "
            "       st.entry_score, "
            "       (so.components->'market'->>'oi_delta_24h') IS NULL AS oi_missing "
            "FROM shadow_trades st "
            "LEFT JOIN shadow_observations so ON so.signal_id = st.signal_id "
            "WHERE st.closed_at IS NOT NULL AND st.pnl_usdt IS NOT NULL "
            "  AND st.pnl_pct IS NOT NULL"
        ))).all()
    await engine.dispose()
    for r in rows:
        key = (str(r.symbol), r.opened_at.isoformat())
        out[key] = Side(float(r.pnl_pct), str(r.timeframe), str(r.direction),
                        float(r.entry_score or 0.0), bool(r.oi_missing))
    return out


def _bucket_stats(vals: list[float]) -> dict:
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": None, "se": None}
    a = np.asarray(vals, dtype=np.float64)
    sd = float(a.std(ddof=1)) if n > 1 else 0.0
    return {"n": n, "mean": float(a.mean()), "se": (sd / math.sqrt(n)) if n > 1 else None}


def _gap(hi: dict, lo: dict) -> dict:
    if not hi["n"] or not lo["n"] or hi["se"] is None or lo["se"] is None:
        return {"gap": None, "se_diff": None, "sigma": None}
    gap = hi["mean"] - lo["mean"]
    se = math.sqrt(hi["se"] ** 2 + lo["se"] ** 2)
    return {"gap": gap, "se_diff": se, "sigma": (gap / se) if se > 0 else None}


def _score(policy: PolicyNetwork, table: AssetEmbeddingTable,
           trs: list[Transition], side: dict, dev: torch.device) -> list[dict]:
    """Mirror backtest_brain's loop exactly: embedding hot-swap, deterministic act."""
    policy.eval()
    out: list[dict] = []
    with torch.no_grad():
        for tr in trs:
            key = (tr.symbol, tr.opened_at_iso)
            sd = side.get(key)
            if sd is None:
                continue
            obs_t = torch.from_numpy(tr.obs).unsqueeze(0).to(dev)
            aid = torch.tensor([tr.asset_id], dtype=torch.long).to(dev)
            live_emb = table.module(aid)
            obs_live = torch.cat([live_emb, obs_t[:, EMB_DIM:]], dim=1)
            action_t, _lp, value_t = policy.act(obs_live, deterministic=True)
            idx = int(action_t.squeeze(0).item())
            if not 0 <= idx < len(ALL_ACTIONS):
                continue
            proposed = _infer_proposed_direction(tr.action)
            adj = float(action_to_brain_adjust(ALL_ACTIONS[idx], proposed_direction=proposed))
            out.append({
                "symbol": tr.symbol, "opened_at": tr.opened_at_iso,
                "pnl_pct": sd.pnl_pct, "tf": sd.timeframe, "dir": sd.direction,
                "entry_score": sd.entry_score, "brain_adjust": adj,
                "value": float(value_t.squeeze(0).item()),
            })
    return out


def _report_block(name: str, rows: list[dict], bar_pct: float | None = None) -> dict:
    boost = [r["pnl_pct"] for r in rows if r["brain_adjust"] > 1.0]
    neut = [r["pnl_pct"] for r in rows if abs(r["brain_adjust"] - 1.0) < 1e-9]
    supp = [r["pnl_pct"] for r in rows if r["brain_adjust"] < 1.0]
    b, nn_, s = _bucket_stats(boost), _bucket_stats(neut), _bucket_stats(supp)
    g_adj = _gap(b, s)
    vals = [r["value"] for r in rows]
    if len(vals) >= 4:
        med = float(np.median(vals))
        top = [r["pnl_pct"] for r in rows if r["value"] >= med]
        bot = [r["pnl_pct"] for r in rows if r["value"] < med]
        t, bo = _bucket_stats(top), _bucket_stats(bot)
        g_val = _gap(t, bo)
    else:
        t = bo = {"n": 0, "mean": None, "se": None}
        g_val = _gap(t, bo)
    blk = {"name": name, "n": len(rows),
           "brain_adjust": {"BOOST": b, "NEUTRAL": nn_, "SUPPRESS": s,
                            "gap_boost_minus_suppress": g_adj},
           "value_head": {"TOP_HALF": t, "BOTTOM_HALF": bo,
                          "gap_top_minus_bottom": g_val}}
    if bar_pct is not None:
        blk["pre_registered_bar_pct"] = bar_pct
        blk["label"] = SECONDARY_LABEL
    return blk


def _fmt(b: dict) -> str:
    if not b["n"]:
        return "n=0"
    se = f"{b['se']:.3f}" if b["se"] is not None else "n/a"
    return f"n={b['n']:<5d} mean={b['mean']:+.3f}%  SE={se}"


def _print_block(blk: dict) -> None:
    print(f"\n=== {blk['name']}  (n={blk['n']}) ===")
    ba = blk["brain_adjust"]
    for k in ("BOOST", "NEUTRAL", "SUPPRESS"):
        print(f"  brain_adjust {k:<9} {_fmt(ba[k])}")
    g = ba["gap_boost_minus_suppress"]
    if g["gap"] is not None:
        print(f"  BOOST - SUPPRESS gap = {g['gap']:+.3f}%  SE_diff={g['se_diff']:.3f}%"
              f"  -> {g['sigma']:+.2f} sigma")
    vh = blk["value_head"]
    for k in ("TOP_HALF", "BOTTOM_HALF"):
        print(f"  V(s) {k:<12} {_fmt(vh[k])}")
    g = vh["gap_top_minus_bottom"]
    if g["gap"] is not None:
        print(f"  TOP - BOTTOM gap     = {g['gap']:+.3f}%  SE_diff={g['se_diff']:.3f}%"
              f"  -> {g['sigma']:+.2f} sigma")
    if "pre_registered_bar_pct" in blk:
        print(f"  PRE-REGISTERED BAR: {blk['pre_registered_bar_pct']}% per-trade gap. "
              f"This read is UNINFORMATIVE -- {blk['label']}.")


def _with_oi_flag(trs: list[Transition], side: dict) -> list[Transition]:
    out = []
    for tr in trs:
        sd = side.get((tr.symbol, tr.opened_at_iso))
        obs = tr.obs.copy()
        if sd is not None:
            obs[OI_MISSING_SLOT] = 1.0 if sd.oi_missing else 0.0
        out.append(dataclasses.replace(tr, obs=obs))
    return out


def _ts(tr: Transition) -> datetime:
    return (datetime.fromisoformat(tr.opened_at_iso.replace("Z", "+00:00"))
            .astimezone(timezone.utc))


async def _main(a: argparse.Namespace) -> int:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    dev = torch.device("cpu")
    torch.manual_seed(a.seed)
    np.random.seed(a.seed)

    side = await _load_side_table(a.db_url)
    log.info("side table: %d closed trades", len(side))

    # Register EVERY symbol so every transition gets a real (frozen, random)
    # embedding; track which were present in TRAIN separately.
    table = AssetEmbeddingTable()
    all_syms = sorted({k[0] for k in side})
    table.bulk_register(all_syms)
    w = table.module.weight.detach().cpu().numpy()
    emb = {aid: w[aid].astype(np.float32).copy() for aid in table.symbol_to_id.values()}

    engine = create_async_engine(a.db_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        trs = await load_from_shadow_trades(
            s, window_days=3650, asset_embeddings=emb,
            asset_id_for_symbol=dict(table.symbol_to_id),
        )
    await engine.dispose()
    trs = _with_oi_flag(trs, side)
    log.info("transitions: %d", len(trs))

    train = [t for t in trs if _ts(t) < TRAIN_END]
    primary = [t for t in trs if TRAIN_END <= _ts(t) < PRIMARY_END]
    repl = [t for t in trs if _ts(t) >= PRIMARY_END]
    train_syms = {t.symbol for t in train}
    log.info("split: train=%d primary=%d repl=%d  train_symbols=%d",
             len(train), len(primary), len(repl), len(train_syms))

    # --- TRAINED policy: random init, TRAIN window only ---
    policy = PolicyNetwork().to(dev)
    init_note = (f"random init, torch.manual_seed({a.seed}), NO warm start, "
                 f"no id=70/id=62 lineage")
    cfg = TrainConfig(epochs=a.epochs, batch_size=a.batch_size, seed=a.seed)
    res = train_ppo(policy=policy, transitions=train, asset_table=table,
                    config=cfg, device=dev)
    log.info("trained %d epochs on %d TRAIN transitions", res.epochs_completed, len(train))

    # --- UNTRAINED baseline: fresh random init, different seed, never trained ---
    torch.manual_seed(a.seed + 1000)
    untrained = PolicyNetwork().to(dev)

    def score_all(pol: PolicyNetwork) -> dict[str, list[dict]]:
        p = _score(pol, table, primary, side, dev)
        r = _score(pol, table, repl, side, dev)
        t = _score(pol, table, train, side, dev)
        return {
            "TRAIN (in-sample)": t,
            "PRIMARY established-symbol (Aug20-Sep05)":
                [x for x in p if x["symbol"] in train_syms],
            "PRIMARY new-symbol (no TRAIN row)":
                [x for x in p if x["symbol"] not in train_syms],
            "REPLICATION established-symbol (Sep05+)":
                [x for x in r if x["symbol"] in train_syms],
            "REPLICATION new-symbol":
                [x for x in r if x["symbol"] not in train_syms],
        }

    tr_sets = score_all(policy)
    un_sets = score_all(untrained)

    print("\n" + "=" * 78)
    print("BRAIN OFFLINE EVAL -- pre-registered 2026-09-17")
    print(f"initial weights: {init_note}")
    print(f"epochs={a.epochs} batch={a.batch_size} OBS_DIM={OBS_DIM} "
          f"oi_missing_slot={OI_MISSING_SLOT}")
    print("=" * 78)
    out: dict = {"init": init_note, "trained": {}, "untrained": {}, "secondary": {}}
    print("\n########## TRAINED ##########")
    for name, rows in tr_sets.items():
        blk = _report_block(name, rows)
        out["trained"][name] = blk
        _print_block(blk)
    # Secondary: 1h/LONG live-eligible across BOTH held-out windows, established only
    sec_keys = ("PRIMARY established-symbol (Aug20-Sep05)",
                "REPLICATION established-symbol (Sep05+)")
    sec = [x for k in sec_keys for x in tr_sets[k]
           if x["tf"] == "1h" and x["dir"] == "LONG"
           and x["entry_score"] >= MIN_ENTRY_SCORE_LONG]
    blk = _report_block("SECONDARY 1h/LONG live-eligible held-out (UNINFORMATIVE)",
                        sec, bar_pct=SECONDARY_BAR_PCT)
    out["secondary"] = blk
    _print_block(blk)
    print("\n########## UNTRAINED NEUTRAL BASELINE (same harness) ##########")
    for name in sec_keys:
        blk = _report_block(name, un_sets[name])
        out["untrained"][name] = blk
        _print_block(blk)

    # --- pre-registered pass criteria ---
    pk = "PRIMARY established-symbol (Aug20-Sep05)"
    rk = "REPLICATION established-symbol (Sep05+)"
    P = out["trained"][pk]["brain_adjust"]["gap_boost_minus_suppress"]
    R = out["trained"][rk]["brain_adjust"]["gap_boost_minus_suppress"]
    T = out["trained"]["TRAIN (in-sample)"]["brain_adjust"]["gap_boost_minus_suppress"]
    U = out["untrained"][pk]["brain_adjust"]["gap_boost_minus_suppress"]
    c1 = P["sigma"] is not None and abs(P["sigma"]) >= 3.0
    c2 = bool(P["gap"] is not None and R["gap"] is not None and R["se_diff"]
              and (P["gap"] > 0) == (R["gap"] > 0)
              and abs(R["gap"] - P["gap"]) <= 2.0 * R["se_diff"])
    print("\n########## PRE-REGISTERED CRITERIA ##########")
    print(f"1. PRIMARY |sigma| >= 3           : {'PASS' if c1 else 'FAIL'}  ({P['sigma']})")
    print(f"2. REPLICATION same sign, within 2 SE_diff of primary : "
          f"{'PASS' if c2 else 'FAIL'}")
    print(f"3. NEUTRAL BASELINE primary gap    : {U['gap']}  (trained {P['gap']})"
          f"  -- if similar, model learned nothing")
    print(f"4. TRAIN vs HELD-OUT gap           : train {T['gap']}  held-out {P['gap']}"
          f"  -- train >> held-out = overfit signature")
    out["criteria"] = {"c1_primary_3sigma": c1, "c2_replication_consistent": c2}
    print("\n### JSON ###")
    print(json.dumps(out, indent=1, default=str))
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db-url", default=os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/trading_radar"))
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--seed", type=int, default=42)
    return p


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
