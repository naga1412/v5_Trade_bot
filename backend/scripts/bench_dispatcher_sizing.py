"""PR9 Phase 9 — V-7 microbench for dispatcher dynamic-sizing path.

Measures the marginal cost of compute_dynamic_size + split_entries
on the dispatcher hot path. Default-OFF means production sees no
behavior change; this bench validates that the math itself doesn't
blow the V-7 budget when enabled.

Two modes:

  --mode=baseline    DYNAMIC_SIZING_ENABLED=False — compute_dynamic_size
                     short-circuits to None.
  --mode=dynamic-on  DYNAMIC_SIZING_ENABLED=True — full Kelly + tier +
                     split-entries pipeline runs.

V-7 budget:
  delta_p50 = dynamic-on.p50 - baseline.p50  ≤ 2ms
  delta_p99 = dynamic-on.p99 - baseline.p99  ≤ 10ms

Tighter than PR8's 5ms/20ms because PR9's path is pure-CPU (no DB
read). Compute is small constant — a few multiplies + dict lookups.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any


_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


DEFAULT_N_WARMUP = 50
DEFAULT_N_SAMPLES = 500
P50_BUDGET_MS = 2.0
P99_BUDGET_MS = 10.0


def _percentile(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _build_settings(enabled: bool):
    """Build a minimal settings-shaped object."""
    return SimpleNamespace(
        DYNAMIC_SIZING_ENABLED=enabled,
        SIZING_USE_P_WIN_WHEN_AVAILABLE=True,
        SIZING_FRACTIONAL_KELLY=0.25,
        SIZING_TIER_MAX_FRACTION={
            "small": 0.01, "medium": 0.02, "large": 0.05, "whale": 0.10,
        },
        SIZING_TIER_BOUNDARIES={
            "small_max": 1_000.0, "medium_max": 10_000.0,
            "large_max": 100_000.0,
        },
        SIZING_MULTI_ENTRY_THRESHOLD=0.75,
        SIZING_MULTI_ENTRY_RATIOS=[0.6, 0.4],
        SIZING_MULTI_ENTRY_DCA_BAND_PCT=0.5,
    )


def _run_one(mode: str, n_warmup: int, n_samples: int) -> dict[str, Any]:
    """Time the sizing path N times; return p50/p99 in ms."""
    from app.trading.dynamic_sizing import compute_dynamic_size, split_entries

    settings = _build_settings(enabled=(mode == "dynamic-on"))

    # Warmup
    for _ in range(n_warmup):
        margin = compute_dynamic_size(
            balance_usdt=10_000.0, confidence_pct=70.0, settings=settings,
        )
        if margin is not None:
            split_entries(margin, 70.0, settings)

    # Measure
    times_ms: list[float] = []
    for _ in range(n_samples):
        start = time.perf_counter()
        margin = compute_dynamic_size(
            balance_usdt=10_000.0, confidence_pct=70.0, settings=settings,
        )
        if margin is not None:
            split_entries(margin, 70.0, settings)
        times_ms.append((time.perf_counter() - start) * 1000.0)

    return {
        "mode": mode,
        "n_samples": n_samples,
        "p50_ms": _percentile(times_ms, 0.50),
        "p99_ms": _percentile(times_ms, 0.99),
        "mean_ms": statistics.mean(times_ms),
    }


def _amain(args: argparse.Namespace) -> int:
    if args.mode == "compare":
        baseline = _run_one("baseline", args.warmup, args.samples)
        dynamic_on = _run_one("dynamic-on", args.warmup, args.samples)
        delta_p50 = dynamic_on["p50_ms"] - baseline["p50_ms"]
        delta_p99 = dynamic_on["p99_ms"] - baseline["p99_ms"]
        result = {
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
            "baseline": baseline, "dynamic_on": dynamic_on,
            "delta_p50_ms": delta_p50, "delta_p99_ms": delta_p99,
            "p50_budget_ms": P50_BUDGET_MS, "p99_budget_ms": P99_BUDGET_MS,
            "pass": delta_p50 <= P50_BUDGET_MS and delta_p99 <= P99_BUDGET_MS,
        }
        print(json.dumps(result, indent=2))
        return 0 if result["pass"] else 1

    res = _run_one(args.mode, args.warmup, args.samples)
    print(json.dumps(res, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--mode", choices=["baseline", "dynamic-on", "compare"],
        default="compare",
    )
    p.add_argument("--warmup", type=int, default=DEFAULT_N_WARMUP)
    p.add_argument("--samples", type=int, default=DEFAULT_N_SAMPLES)
    args = p.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        return _amain(args)
    return 130


if __name__ == "__main__":
    sys.exit(main())
