"""PR10 Phase 9 — V-7 microbench for the symbol allowlist gate.

Measures dispatcher pre-condition latency with allowlist gate
disabled / cache-warm / cache-cold. V-7 budget:
  delta_p50_cache_hit  <= 2ms
  delta_p99_cache_miss <= 10ms
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
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


P50_HIT_BUDGET_MS = 2.0
P99_MISS_BUDGET_MS = 10.0
DEFAULT_N = 500


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


def _settings(enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        SYMBOL_ALLOWLIST_ENABLED=enabled,
        SHADOW_STABLECOIN_EXCLUDE_LIST=["USDC", "FDUSD"],
        SYMBOL_ALLOWLIST_GRACE_TRADES=50,
        SYMBOL_ALLOWLIST_CACHE_TTL_SECONDS=3600,
    )


async def _run_mode(mode: str, n: int) -> dict[str, Any]:
    from app.trading.execution import symbol_allowlist_gate as gate_mod

    # Clear cache between modes
    gate_mod._CACHE.clear()
    gate_mod._LOCKS.clear()

    snap = SimpleNamespace(trades_count=200, sharpe=1.5)
    snaps = {"BTCUSDT": snap}

    proposal = MagicMock(symbol="BTCUSDT")
    session = MagicMock()
    enabled = mode != "baseline"

    times: list[float] = []
    with patch(
        "app.trading.execution.symbol_allowlist_gate.load_latest_snapshots_per_symbol",
        new=AsyncMock(return_value=snaps),
    ):
        for _ in range(n):
            if mode == "cache-cold-every-call":
                gate_mod._CACHE.clear()
            t0 = time.perf_counter()
            await gate_mod._apply_symbol_allowlist_gate(
                proposal=proposal,
                user_id=1,
                session=session,
                settings=_settings(enabled=enabled),
                now_fn=lambda: datetime.now(tz=timezone.utc),
            )
            times.append((time.perf_counter() - t0) * 1000.0)

    return {
        "mode": mode,
        "n": n,
        "p50_ms": _percentile(times, 0.50),
        "p99_ms": _percentile(times, 0.99),
        "mean_ms": statistics.mean(times),
    }


async def _amain(args: argparse.Namespace) -> int:
    baseline = await _run_mode("baseline", args.samples)
    warm = await _run_mode("cache-warm", args.samples)
    cold = await _run_mode("cache-cold-every-call", args.samples)
    delta_p50_hit = warm["p50_ms"] - baseline["p50_ms"]
    delta_p99_miss = cold["p99_ms"] - baseline["p99_ms"]
    result = {
        "platform": platform.platform(),
        "python_version": sys.version.split()[0],
        "baseline": baseline,
        "cache_warm": warm,
        "cache_cold": cold,
        "delta_p50_cache_hit_ms": delta_p50_hit,
        "delta_p99_cache_miss_ms": delta_p99_miss,
        "p50_hit_budget_ms": P50_HIT_BUDGET_MS,
        "p99_miss_budget_ms": P99_MISS_BUDGET_MS,
        "pass": (delta_p50_hit <= P50_HIT_BUDGET_MS)
        and (delta_p99_miss <= P99_MISS_BUDGET_MS),
    }
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


def main() -> int:
    import asyncio

    p = argparse.ArgumentParser()
    p.add_argument("--samples", type=int, default=DEFAULT_N)
    args = p.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(_amain(args))
    return 130


if __name__ == "__main__":
    sys.exit(main())
