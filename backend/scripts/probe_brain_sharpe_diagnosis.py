import glob
import json
import math
import os

candidates = sorted(
    glob.glob("/app/data/rl-cache/eval_brain_*_per_trade.json"),
    key=os.path.getmtime,
    reverse=True,
)
if not candidates:
    print("no per_trade.json files found in rl-cache")
    raise SystemExit(1)
path = candidates[0]
print(f"analyzing: {path}", flush=True)

with open(path) as f:
    records = json.load(f)

n = len(records)
raw = [float(r["raw_reward"]) for r in records]
syn = [float(r["synthetic_reward"]) for r in records]


def sharpe(xs):
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / len(xs)
    std = math.sqrt(var)
    return (m / (std + 1e-8)) * math.sqrt(len(xs))


print(f"n_holdout_trades={n}")
print(f"raw_reward: mean={sum(raw)/n:.6f} sharpe(neutral-brain)={sharpe(raw):.4f}")
print(f"synthetic_reward: mean={sum(syn)/n:.6f} sharpe(actual-checkpoint)={sharpe(syn):.4f}")
print(f"sqrt(n_holdout)={math.sqrt(n):.3f} -- this is the ONLY scaling factor "
      f"applied; NOT calendar-annualized, NOT comparable across checkpoints "
      f"with different holdout sizes")

adjusts = [float(r["brain_adjust"]) for r in records]
agree = sum(1 for a in adjusts if a > 1.0)
disagree = sum(1 for a in adjusts if a < 1.0)
neutral = sum(1 for a in adjusts if a == 1.0)
print(f"brain stance in this holdout: agree={agree} disagree={disagree} neutral={neutral}")
