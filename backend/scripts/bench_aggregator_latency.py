"""Aggregator hook latency benchmark — V-7 gate (PR1 Phase 7 + PR2 Phase 8).

PR1 modes (predictor-side aggregator):

  --mtf-disabled   Baseline: MTF skipped (returns None immediately).
                   p_win, vol_normalization, funding_directional still run.

  --mtf-recording  Full PR1 path with MTF computation enabled.
                   Cache is pre-populated so every call hits the cache.
                   No real Binance fetch.

PR2 modes (dispatcher-side gate microbench):

  --mtf-gate-disabled  Baseline: proposal carries ``mtf_agreement=None`` so
                       ``_apply_mtf_gate`` short-circuits at the first
                       check (fail-open) — represents the PR1 fallback /
                       MTF compute failure path AND the rollback path
                       (MTF_MIN_AGREEMENT_1H=0).

  --mtf-gate-enabled   Active gate: proposal carries
                       ``mtf_agreement=4`` plus a full mtf_directions
                       dict; gate evaluates min-agreement AND the
                       higher-TF veto, then passes.

The PR2 gate is 3 boolean checks + 1 dict-get-with-default + 1 sign
comparison — the delta should be sub-millisecond. If it isn't, the
spec §6.5 V-7 budget (≤50ms p50, ≤200ms p99) is wildly violated and
something is fundamentally wrong.

No flag: runs the PR1 modes sequentially and emits the PR1 gate verdict
(unchanged from pre-PR2). The PR2 modes are explicit; the operator
runs them alongside as part of the PR2 review.

Usage
-----
  python scripts/bench_aggregator_latency.py               # PR1 both modes + gate
  python scripts/bench_aggregator_latency.py --mtf-disabled
  python scripts/bench_aggregator_latency.py --mtf-recording
  python scripts/bench_aggregator_latency.py --mtf-gate-disabled   # PR2
  python scripts/bench_aggregator_latency.py --mtf-gate-enabled    # PR2
  python scripts/bench_aggregator_latency.py --n 10        # custom sample count
"""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path resolution — script can be run from any cwd
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
_FIXTURE_PATH = _BACKEND_DIR / "tests" / "fixtures" / "bench_btcusdt_1h_500bars.json"

# Insert backend/ into sys.path so `app.*` imports work when running directly.
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# ---------------------------------------------------------------------------
# Default benchmark parameters
# ---------------------------------------------------------------------------
DEFAULT_N_WARMUP = 50
DEFAULT_N_SAMPLES = 500

# Gate budgets (milliseconds)
P50_BUDGET_MS = 50.0
P99_BUDGET_MS = 200.0

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

def _load_fixture() -> pd.DataFrame:
    """Load the canned 500-bar BTCUSDT 1h fixture into a pd.DataFrame.

    Returns a DataFrame with DatetimeIndex (UTC) and columns:
    open, high, low, close, volume.
    """
    with open(_FIXTURE_PATH) as fh:
        raw = json.load(fh)

    rows = [
        SimpleNamespace(
            ts=datetime.fromtimestamp(item["ts"], tz=timezone.utc),
            open=item["open"],
            high=item["high"],
            low=item["low"],
            close=item["close"],
            volume=item["volume"],
        )
        for item in raw
    ]

    idx = pd.DatetimeIndex([r.ts for r in rows])
    df = pd.DataFrame(
        {
            "open": [r.open for r in rows],
            "high": [r.high for r in rows],
            "low": [r.low for r in rows],
            "close": [r.close for r in rows],
            "volume": [r.volume for r in rows],
        },
        index=idx,
    )
    return df


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _make_final() -> Any:
    """Synthesize a FinalScore for BTCUSDT LONG signal."""
    from app.core.scoring.types import Direction, FinalScore

    return FinalScore(
        score=0.42,
        direction=Direction.LONG,
        confidence=0.71,
        layer_results={i: None for i in range(1, 11)},
        contributing_layers=(1, 3),
    )


def _make_mock_session() -> Any:
    """Return a mock AsyncSession whose execute() returns a fixed funding_rate."""
    mock_row = MagicMock()
    mock_row.funding_rate = 0.0008

    mock_result = MagicMock()
    mock_result.first.return_value = mock_row

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)
    # Satisfy session.bind.dialect.name check in predictor helpers
    session.bind = MagicMock()
    session.bind.dialect.name = "postgresql"
    return session


def _make_deterministic_kline_row(close: float = 80000.0) -> list[Any]:
    """Single Binance-format kline row: [open_time, o, h, l, c, vol, ...]."""
    return [
        1_700_000_000_000,   # open_time (ms)
        str(close * 0.999),  # open
        str(close * 1.002),  # high (index 2)
        str(close * 0.998),  # low  (index 3)
        str(close),          # close (index 4)
        "500.0",             # volume
        1_700_003_600_000,   # close_time
        "40000000.0",        # quote asset volume
        100,                 # trades
        "250.0",             # taker buy base
        "20000000.0",        # taker buy quote
        "0",                 # ignore
    ]


def _build_deterministic_klines(n: int = 200, base_close: float = 80000.0) -> list[list[Any]]:
    """200 deterministic klines that produce a stable EMA20/50 cross."""
    rng = np.random.default_rng(1234)
    prices = base_close + np.cumsum(rng.normal(0, 50, n))
    rows: list[list[Any]] = []
    for i, p in enumerate(prices):
        ts_ms = 1_700_000_000_000 + i * 3_600_000
        rows.append([
            ts_ms,
            str(p * 0.999),
            str(p * 1.002),
            str(p * 0.998),
            str(p),
            "500.0",
            ts_ms + 3_599_999,
            "40000000.0",
            100,
            "250.0",
            "20000000.0",
            "0",
        ])
    return rows


def _prepopulate_mtf_cache() -> None:
    """Pre-populate _KLINE_CACHE for BTCUSDT × 6 TFs with fresh timestamps.

    Uses a far-future fetched_at so TTL never expires during the bench run.
    No httpx calls will be made — every fetch will hit the cache.
    """
    from app.core.scoring.mtf_confluence import (
        TIMEFRAMES,
        _cache_set,
    )

    klines = _build_deterministic_klines(200)
    # Use time.time() + 1 hour as fetched_at so cache stays hot for the whole run.
    future_ts = time.time() + 3600.0

    for tf in TIMEFRAMES:
        _cache_set("BTCUSDT", tf, klines, fetched_at=future_ts)


# ---------------------------------------------------------------------------
# PR2 dispatcher-gate microbench fixtures
# ---------------------------------------------------------------------------


def _make_pr2_proposal(*, with_mtf: bool) -> Any:
    """Construct a SignalProposal for the PR2 gate microbench.

    ``with_mtf=False`` → mtf_agreement=None → gate short-circuits at the
    first guard (fail-open). Represents the PR1-fallback path AND the
    rollback path (MTF_MIN_AGREEMENT_1H=0 yields the same fast-pass).

    ``with_mtf=True`` → mtf_agreement=4, mtf_directions populated; gate
    evaluates both the min-agreement check and the higher-TF veto (the
    1d/1w direction lookup), then passes.
    """
    from app.trading.execution.dispatcher import SignalProposal

    base = dict(
        symbol="BTC/USDT", timeframe="1h",
        direction="LONG",
        entry_price=80_000.0, stop_loss_price=78_400.0,
        take_profit_price=83_280.0,
        confidence_pct=72.0,
        layer_summary={"L1": {}, "L2": {}, "L3": {}},
        inputs_hash="a" * 64,
    )
    if with_mtf:
        return SignalProposal(
            **base,  # type: ignore[arg-type]
            mtf_agreement=4,
            mtf_dominant_tf="1h",
            mtf_directions={
                "5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": 1, "1w": 1,
            },
        )
    return SignalProposal(**base)  # type: ignore[arg-type]


def _make_pr2_settings() -> Any:
    from app.config import Settings
    return Settings(
        database_url="postgresql://x", redis_url="redis://x",
        MTF_MIN_AGREEMENT_1H=3, MTF_HIGHER_TF_VETO=True,
    )


# ---------------------------------------------------------------------------
# Core measurement loop
# ---------------------------------------------------------------------------

async def _run_one(
    bars: pd.DataFrame,
    final: Any,
    session: Any,
) -> float:
    """Run one PR1 aggregator-hook computation, return elapsed nanoseconds."""
    from app.core.predictor import _compute_aggregator_hook_fields

    t0 = time.perf_counter_ns()
    await _compute_aggregator_hook_fields(
        symbol="BTCUSDT",
        timeframe="1h",
        bars=bars,
        final=final,
        session=session,
    )
    t1 = time.perf_counter_ns()
    return float(t1 - t0)


def _run_one_pr2_gate(proposal: Any, settings: Any) -> float:
    """Run one PR2 _apply_mtf_gate call, return elapsed nanoseconds.

    Synchronous — the gate function is sync (pure, no I/O). The bench
    just measures the call cost.
    """
    from app.trading.execution.dispatcher import _apply_mtf_gate

    t0 = time.perf_counter_ns()
    _apply_mtf_gate(proposal, settings)
    t1 = time.perf_counter_ns()
    return float(t1 - t0)


async def _bench_mode(
    mode: str,
    n_warmup: int,
    n_samples: int,
    bars: pd.DataFrame,
) -> dict[str, Any]:
    """Run the benchmark for one mode. Returns the result dict."""
    final = _make_final()
    session = _make_mock_session()

    if mode == "mtf-disabled":
        # Patch compute_mtf_confluence to return None immediately (skip all MTF work).
        # This is the baseline: "PR1 deployed but MTF cache cold + universe empty".
        # The function is imported inside _compute_aggregator_hook_fields via
        # `from app.core.scoring.mtf_confluence import compute_mtf_confluence`,
        # so we patch at the source module where it's defined.
        async def _null_mtf(*args: Any, **kwargs: Any) -> None:
            return None

        ctx_managers = [
            patch(
                "app.core.scoring.mtf_confluence.compute_mtf_confluence",
                new=_null_mtf,
            ),
        ]

    elif mode == "mtf-gate-disabled":
        # PR2 microbench: gate fast-pass (mtf_agreement=None).
        proposal = _make_pr2_proposal(with_mtf=False)
        settings = _make_pr2_settings()

        async def _run_bench_pr2() -> list[float]:
            samples_: list[float] = []
            for _ in range(n_warmup):
                _run_one_pr2_gate(proposal, settings)
            for _ in range(n_samples):
                samples_.append(_run_one_pr2_gate(proposal, settings))
            return samples_

        samples = await _run_bench_pr2()
        return _summarize(mode, n_warmup, n_samples, samples)

    elif mode == "mtf-gate-enabled":
        # PR2 microbench: gate evaluates both min-agreement + higher-TF veto.
        proposal = _make_pr2_proposal(with_mtf=True)
        settings = _make_pr2_settings()

        async def _run_bench_pr2() -> list[float]:
            samples_: list[float] = []
            for _ in range(n_warmup):
                _run_one_pr2_gate(proposal, settings)
            for _ in range(n_samples):
                samples_.append(_run_one_pr2_gate(proposal, settings))
            return samples_

        samples = await _run_bench_pr2()
        return _summarize(mode, n_warmup, n_samples, samples)

    elif mode == "mtf-recording":
        # Pre-populate cache for all 6 TFs at fresh timestamps so every _fetch_one_tf
        # call hits the cache (returns immediately, no HTTP I/O).
        # Also mock httpx.AsyncClient so SSL context creation (which is expensive even
        # when the network is never used) doesn't dominate the measurement. We're
        # measuring OUR code's overhead, not TLS certificate loading time.
        _prepopulate_mtf_cache()

        class _MockHttpxClient:
            """Minimal stub: async context manager + .get() that should never be called."""

            async def aclose(self) -> None:
                pass

            async def get(self, *args: Any, **kwargs: Any) -> Any:
                raise AssertionError("httpx.get should not be called with a warm cache")

        ctx_managers = [
            patch("httpx.AsyncClient", new=lambda **kw: _MockHttpxClient()),
        ]

    else:
        raise ValueError(f"Unknown mode: {mode!r}")

    async def _run_bench() -> list[float]:
        samples: list[float] = []

        # Warmup runs — flush cold-cache / JIT effects
        for _ in range(n_warmup):
            await _run_one(bars, final, session)

        # Measurement runs
        for _ in range(n_samples):
            ns = await _run_one(bars, final, session)
            samples.append(ns)

        return samples

    # Enter all patches (if any) then run the bench
    if ctx_managers:
        # Apply patches sequentially using ExitStack
        import contextlib
        with contextlib.ExitStack() as stack:
            for cm in ctx_managers:
                stack.enter_context(cm)
            samples = await _run_bench()
    else:
        samples = await _run_bench()

    return _summarize(mode, n_warmup, n_samples, samples)


def _summarize(
    mode: str, n_warmup: int, n_samples: int, samples_ns: list[float],
) -> dict[str, Any]:
    """Convert ns samples to ms percentile summary dict.

    Shared by both the PR1 async aggregator-hook bench and the PR2 sync
    `_apply_mtf_gate` microbench. Single source of truth for the JSON
    output shape so both bench modes are directly comparable.
    """
    samples_ms = [s / 1_000_000.0 for s in samples_ns]
    sorted_samples = sorted(samples_ms)
    n = len(sorted_samples)

    def _percentile(p: float) -> float:
        idx = p / 100.0 * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return sorted_samples[lo] * (1 - frac) + sorted_samples[hi] * frac

    return {
        "mode": mode,
        "n_warmup": n_warmup,
        "n_samples": n_samples,
        "p50_ms": round(_percentile(50), 4),
        "p95_ms": round(_percentile(95), 4),
        "p99_ms": round(_percentile(99), 4),
        "p999_ms": round(_percentile(99.9), 4),
        "mean_ms": round(statistics.mean(samples_ms), 4),
        "stdev_ms": round(statistics.stdev(samples_ms) if n > 1 else 0.0, 4),
        "min_ms": round(min(samples_ms), 4),
        "max_ms": round(max(samples_ms), 4),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


def _compute_gate(
    disabled: dict[str, Any],
    recording: dict[str, Any],
) -> dict[str, Any]:
    """Compute the gate verdict from two mode results."""
    delta_p50 = recording["p50_ms"] - disabled["p50_ms"]
    delta_p99 = recording["p99_ms"] - disabled["p99_ms"]

    failed: list[str] = []
    if delta_p50 > P50_BUDGET_MS:
        failed.append("p50")
    if delta_p99 > P99_BUDGET_MS:
        failed.append("p99")

    verdict = "PASS" if not failed else "FAIL"

    return {
        "gate": {
            "delta_p50_ms": round(delta_p50, 4),
            "delta_p99_ms": round(delta_p99, 4),
            "p50_budget_ms": P50_BUDGET_MS,
            "p99_budget_ms": P99_BUDGET_MS,
            "verdict": verdict,
            "failed_budgets": failed,
        }
    }


# ---------------------------------------------------------------------------
# Public entry point (importable for tests)
# ---------------------------------------------------------------------------

async def _async_main(args: list[str] | None = None) -> int:
    """Run the benchmark. Returns exit code (0=PASS, 1=FAIL or single mode)."""
    parser = argparse.ArgumentParser(
        description="Aggregator hook latency benchmark — V-7 gate",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--mtf-disabled",
        action="store_true",
        dest="mtf_disabled",
        help="Baseline mode: MTF call skipped entirely",
    )
    mode_group.add_argument(
        "--mtf-recording",
        action="store_true",
        dest="mtf_recording",
        help="Full PR1 path: MTF recording-only with warm cache",
    )
    mode_group.add_argument(
        "--mtf-gate-disabled",
        action="store_true",
        dest="mtf_gate_disabled",
        help="PR2 microbench: gate fast-pass (proposal.mtf_agreement=None)",
    )
    mode_group.add_argument(
        "--mtf-gate-enabled",
        action="store_true",
        dest="mtf_gate_enabled",
        help="PR2 microbench: gate evaluates min-agreement + higher-TF veto",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=DEFAULT_N_SAMPLES,
        dest="n_samples",
        help=f"Number of measurement samples (default: {DEFAULT_N_SAMPLES})",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_N_WARMUP,
        dest="n_warmup",
        help=f"Number of warmup runs (default: {DEFAULT_N_WARMUP})",
    )

    ns = parser.parse_args(args)
    bars = _load_fixture()

    if ns.mtf_disabled:
        result = await _bench_mode("mtf-disabled", ns.n_warmup, ns.n_samples, bars)
        print(json.dumps(result, indent=2))
        return 0

    if ns.mtf_recording:
        result = await _bench_mode("mtf-recording", ns.n_warmup, ns.n_samples, bars)
        print(json.dumps(result, indent=2))
        return 0

    if ns.mtf_gate_disabled:
        result = await _bench_mode(
            "mtf-gate-disabled", ns.n_warmup, ns.n_samples, bars,
        )
        print(json.dumps(result, indent=2))
        return 0

    if ns.mtf_gate_enabled:
        result = await _bench_mode(
            "mtf-gate-enabled", ns.n_warmup, ns.n_samples, bars,
        )
        print(json.dumps(result, indent=2))
        return 0

    # No flag: run both modes sequentially, emit gate verdict
    disabled_result = await _bench_mode("mtf-disabled", ns.n_warmup, ns.n_samples, bars)
    print(json.dumps(disabled_result, indent=2))

    recording_result = await _bench_mode("mtf-recording", ns.n_warmup, ns.n_samples, bars)
    print(json.dumps(recording_result, indent=2))

    gate = _compute_gate(disabled_result, recording_result)
    print(json.dumps(gate, indent=2))

    return 0 if gate["gate"]["verdict"] == "PASS" else 1


def main(args: list[str] | None = None) -> int:
    """Synchronous entry point."""
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
