"""PR8 Phase 9 — V-7 microbench for dispatcher pre-conditions block.

Measures the marginal cost of the PR8 cooldown gate. The gate adds
one PK SELECT against `live_cooldowns` to the dispatcher's
pre-conditions block, BEFORE the MTF gate. Default-OFF means
production sees no behavior change; this bench validates that the
DB read itself doesn't blow the V-7 budget when enabled.

Two modes:

  --mode=baseline    Cooldown gate disabled (LIVE_COOLDOWN_ENABLED=False).
                     Bench measures the existing pre-conditions block.

  --mode=gate-on     Cooldown gate enabled (LIVE_COOLDOWN_ENABLED=True).
                     Bench measures pre-conditions with the cooldown
                     PK lookup inserted.

V-7 budget:
  delta_p50 = gate-on.p50 - baseline.p50  ≤ 5ms
  delta_p99 = gate-on.p99 - baseline.p99  ≤ 20ms

5ms / 20ms are tighter than PR3's 50/200ms because the cooldown gate
is fundamentally a single SELECT, not a per-candle compute. A gate
that costs more than 5ms p50 indicates either a missing PK index, a
chatty session factory, or a slow `is_cooldown_blocked` path — all
of which should be investigated.
"""
from __future__ import annotations

import argparse
import asyncio
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
from unittest.mock import MagicMock

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


DEFAULT_N_WARMUP = 50
DEFAULT_N_SAMPLES = 500
P50_BUDGET_MS = 5.0
P99_BUDGET_MS = 20.0


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


def _build_settings(gate_on: bool):
    """Build a minimal settings-shaped object for is_cooldown_blocked."""
    return SimpleNamespace(
        LIVE_COOLDOWN_ENABLED=gate_on,
        LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF=True,
        LIVE_COOLDOWN_HOURS_BY_OUTCOME={
            "stop_loss": 8.0, "take_profit": 1.0, "timeout": 4.0,
            "manual_close": 0.0, "external_close": 0.0,
            "liquidation_buffer_breach": 24.0,
        },
    )


async def _mk_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE live_cooldowns ("
            "user_id INTEGER NOT NULL, symbol TEXT NOT NULL, "
            "cooldown_until TEXT NOT NULL, last_exit_reason TEXT NOT NULL, "
            "last_mtf_agreement INTEGER, updated_at TEXT NOT NULL, "
            "PRIMARY KEY (user_id, symbol))"
        ))
        # Pre-seed one row so the gate-on path actually reads data
        # (worst case for the SELECT — index hit + row hydration).
        await conn.execute(sa.text(
            "INSERT INTO live_cooldowns VALUES "
            "(1, 'BTCUSDT', '2026-05-19T00:00:00+00:00', "
            " 'take_profit', 5, '2026-05-18T01:00:00+00:00')"
        ))
    return async_sessionmaker(engine, expire_on_commit=False)


async def _run_one(mode: str, n_warmup: int, n_samples: int) -> dict[str, Any]:
    """Time the cooldown gate N times; return p50/p99 in ms."""
    from app.trading.execution.cooldown_gate import _apply_cooldown_gate

    factory = await _mk_session_factory()
    settings = _build_settings(gate_on=(mode == "gate-on"))
    proposal = MagicMock(symbol="BTCUSDT", mtf_agreement=5)

    # Warmup
    async with factory() as session:
        for _ in range(n_warmup):
            await _apply_cooldown_gate(
                proposal=proposal, user_id=1, session=session,
                settings=settings,
            )

    # Measure
    times_ms: list[float] = []
    async with factory() as session:
        for _ in range(n_samples):
            start = time.perf_counter()
            await _apply_cooldown_gate(
                proposal=proposal, user_id=1, session=session,
                settings=settings,
            )
            times_ms.append((time.perf_counter() - start) * 1000.0)

    return {
        "mode": mode,
        "n_samples": n_samples,
        "p50_ms": _percentile(times_ms, 0.50),
        "p99_ms": _percentile(times_ms, 0.99),
        "mean_ms": statistics.mean(times_ms),
    }


async def _amain(args: argparse.Namespace) -> int:
    if args.mode == "compare":
        baseline = await _run_one("baseline", args.warmup, args.samples)
        gate_on = await _run_one("gate-on", args.warmup, args.samples)
        delta_p50 = gate_on["p50_ms"] - baseline["p50_ms"]
        delta_p99 = gate_on["p99_ms"] - baseline["p99_ms"]
        result = {
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
            "baseline": baseline, "gate_on": gate_on,
            "delta_p50_ms": delta_p50, "delta_p99_ms": delta_p99,
            "p50_budget_ms": P50_BUDGET_MS, "p99_budget_ms": P99_BUDGET_MS,
            "pass": delta_p50 <= P50_BUDGET_MS and delta_p99 <= P99_BUDGET_MS,
        }
        print(json.dumps(result, indent=2))
        return 0 if result["pass"] else 1

    # Single-mode run
    res = await _run_one(args.mode, args.warmup, args.samples)
    print(json.dumps(res, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--mode", choices=["baseline", "gate-on", "compare"],
        default="compare",
    )
    p.add_argument("--warmup", type=int, default=DEFAULT_N_WARMUP)
    p.add_argument("--samples", type=int, default=DEFAULT_N_SAMPLES)
    args = p.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(_amain(args))
    return 130


if __name__ == "__main__":
    sys.exit(main())
