"""PR3 Phase 9 — V-7 microbench for shadow worker per-candle handling.

Two modes:

  --mode=baseline   ShadowWorker with timeframes=["1h"] (pre-PR3 lane).
                    Drives `_handle_candle` N times with a synthetic 1h
                    candle, asserts `_handle_candle` returns. The bench
                    times JUST the `_handle_candle` call — the DB and
                    Binance calls inside are mocked / replaced with
                    in-memory fakes so the measurement isolates the
                    new per-(symbol, tf) keying overhead.

  --mode=multi-tf   ShadowWorker with timeframes=["1h", "15m"].
                    Drives `_handle_candle` N times alternating between
                    1h and 15m candles. Measures the cost of the
                    enlarged bars dict + per-TF cooldown filtering +
                    per-TF PositionGate.is_blocked construction.

V-7 budget (spec §6.5, same as PR1):
  delta_p50 = multi-tf.p50 - baseline.p50  ≤ 50ms
  delta_p99 = multi-tf.p99 - baseline.p99  ≤ 200ms

Expected: sub-millisecond delta. The TF-aware refactor adds ~2 tuple
comprehensions per candle (PositionGate filter + cooldowns filter).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

# ---------------------------------------------------------------------------
# Path resolution — script can be run from any cwd
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# DB env var defaults so bare runs don't fail Pydantic Settings validation.
import os  # noqa: E402
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


DEFAULT_N_WARMUP = 50
DEFAULT_N_SAMPLES = 500
P50_BUDGET_MS = 50.0
P99_BUDGET_MS = 200.0


# ---------------------------------------------------------------------------
# Fixture: ShadowWorker + fake candle + mocked dependencies
# ---------------------------------------------------------------------------


def _make_candle(*, symbol: str = "BTCUSDT", timeframe: str = "1h",
                 close: float = 100.0) -> Any:
    from app.shadow.multi_stream import MultiStreamCandle
    return MultiStreamCandle(
        symbol=symbol,
        timeframe=timeframe,
        ts=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        open=close * 0.999, high=close * 1.001,
        low=close * 0.998, close=close, volume=1000.0,
    )


def _make_bars_seed() -> pd.DataFrame:
    """200-bar synthetic OHLCV — large enough for build_prediction to
    not bail on insufficient history but cheap to construct."""
    idx = pd.date_range(
        end=datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc),
        periods=200, freq="1h",
    )
    return pd.DataFrame(
        {
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.5, "volume": 1000.0,
        },
        index=idx,
    )


def _build_worker(*, timeframes: list[str]) -> Any:
    from app.shadow.worker import ShadowWorker

    class _NoopReader:
        async def stream(self):
            return
            yield  # unreachable; satisfies async-generator protocol

    # MagicMock session_factory: callable that returns an async-cm-able mock.
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=session)

    worker = ShadowWorker(
        symbols=["BTCUSDT", "ETHUSDT"],
        session_factory=factory,
        reader=_NoopReader(),
        timeframes=timeframes,
    )
    # Pre-seed bars buffers so build_prediction doesn't bail.
    seed = _make_bars_seed()
    for tf in timeframes:
        for sym in worker.symbols:
            worker.bars[(sym, tf)] = seed.copy()
    return worker


@contextlib.contextmanager
def _patched_handle_deps():
    """Mock the heavy I/O inside _handle_candle so we measure only the
    per-candle bookkeeping cost (the new bit PR3 added)."""
    # pause_state returns False → skip the paused branch
    pause_module = MagicMock()
    pause_module.is_paused = AsyncMock(return_value=False)

    # build_prediction returns a NEUTRAL prediction so _maybe_open_position
    # bails early at the evaluator (no DB write).
    fake_layer = MagicMock()
    fake_layer.model_dump = MagicMock(return_value={"score": 0.0})
    fake_final = MagicMock()
    fake_final.score = 0.0
    fake_final.confidence = 0.0
    fake_pred = MagicMock()
    fake_pred.layer_scores = {str(i): fake_layer for i in range(1, 10)}
    fake_pred.final = fake_final
    fake_pred.mtf_agreement = None

    # pattern_stats_cache returns None
    fake_cache = MagicMock()
    fake_cache.get_or_load = AsyncMock(return_value=None)

    # record_heartbeat is a no-op (best-effort wrapper)
    fake_heartbeat = AsyncMock(return_value=None)

    # _atr returns a positive value so we don't bail
    fake_atr = MagicMock(return_value=1.5)

    with patch.dict(sys.modules, {"app.ops.pause_state": pause_module}), \
         patch("app.shadow.worker.build_prediction",
               new=AsyncMock(return_value=fake_pred)), \
         patch("app.shadow.worker.pattern_stats_cache", new=fake_cache), \
         patch("app.shadow.worker.record_heartbeat", new=fake_heartbeat), \
         patch("app.shadow.worker._atr", new=fake_atr):
        yield


# ---------------------------------------------------------------------------
# Measurement loop
# ---------------------------------------------------------------------------


async def _run_one(worker: Any, candle: Any, tf: str) -> float:
    t0 = time.perf_counter_ns()
    await worker._handle_candle(candle, tf=tf)  # noqa: SLF001
    t1 = time.perf_counter_ns()
    return float(t1 - t0)


async def _bench_mode(
    mode: str, n_warmup: int, n_samples: int,
) -> dict[str, Any]:
    if mode == "baseline":
        worker = _build_worker(timeframes=["1h"])
        candles_cycle = [_make_candle(timeframe="1h", close=100.0)]
    elif mode == "multi-tf":
        worker = _build_worker(timeframes=["1h", "15m"])
        candles_cycle = [
            _make_candle(timeframe="1h", close=100.0),
            _make_candle(timeframe="15m", close=200.0),
        ]
    else:
        raise ValueError(f"Unknown mode: {mode!r}")

    samples_ns: list[float] = []
    with _patched_handle_deps():
        # Warmup
        for i in range(n_warmup):
            c = candles_cycle[i % len(candles_cycle)]
            await worker._handle_candle(c, tf=c.timeframe)  # noqa: SLF001
        # Measurement
        for i in range(n_samples):
            c = candles_cycle[i % len(candles_cycle)]
            ns = await _run_one(worker, c, c.timeframe)
            samples_ns.append(ns)

    return _summarize(mode, n_warmup, n_samples, samples_ns)


def _summarize(
    mode: str, n_warmup: int, n_samples: int, samples_ns: list[float],
) -> dict[str, Any]:
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
    baseline: dict[str, Any], multi_tf: dict[str, Any],
) -> dict[str, Any]:
    delta_p50 = multi_tf["p50_ms"] - baseline["p50_ms"]
    delta_p99 = multi_tf["p99_ms"] - baseline["p99_ms"]
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


async def _async_main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Shadow worker _handle_candle latency bench — PR3 V-7 gate. "
            "Without --mode runs both modes and emits the gate verdict."
        ),
    )
    parser.add_argument(
        "--mode", choices=["baseline", "multi-tf"], default=None,
        help="Run a single mode; omit to run both and emit verdict.",
    )
    parser.add_argument(
        "--n", type=int, default=DEFAULT_N_SAMPLES, dest="n_samples",
    )
    parser.add_argument(
        "--warmup", type=int, default=DEFAULT_N_WARMUP, dest="n_warmup",
    )
    ns = parser.parse_args(args)

    if ns.mode is not None:
        result = await _bench_mode(ns.mode, ns.n_warmup, ns.n_samples)
        print(json.dumps(result, indent=2))
        return 0

    baseline = await _bench_mode("baseline", ns.n_warmup, ns.n_samples)
    print(json.dumps(baseline, indent=2))
    multi_tf = await _bench_mode("multi-tf", ns.n_warmup, ns.n_samples)
    print(json.dumps(multi_tf, indent=2))
    gate = _compute_gate(baseline, multi_tf)
    print(json.dumps(gate, indent=2))
    return 0 if gate["gate"]["verdict"] == "PASS" else 1


def main(args: list[str] | None = None) -> int:
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
