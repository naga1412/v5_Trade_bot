# backend/scripts

Operator and developer scripts for the trading backend.

---

## bench_aggregator_latency.py — V-7 latency gate (PR1 Phase 7)

Benchmarks `_compute_aggregator_hook_fields` from `app.core.predictor` in two
modes to measure the overhead introduced by PR1's MTF confluence recording path.

### Modes

| Mode | Description |
|------|-------------|
| `--mtf-disabled` | Baseline: MTF call skipped entirely (`compute_mtf_confluence` returns `None` immediately). `p_win`, `vol_normalization`, and `funding_directional` helpers still run. |
| `--mtf-recording` | Full PR1 path with MTF recording enabled. Cache is pre-populated for all 6 TFs so every call hits the cache (no real Binance fetch). |
| _(no flag)_ | Runs both modes sequentially, then emits a gate verdict comparing the deltas against the budgets. |

### Running the benchmark

```bash
cd backend

# Both modes + gate verdict (default N=500, warmup=50)
python scripts/bench_aggregator_latency.py

# Single mode
python scripts/bench_aggregator_latency.py --mtf-disabled
python scripts/bench_aggregator_latency.py --mtf-recording

# Custom sample count (e.g., quick check)
python scripts/bench_aggregator_latency.py --n 50 --warmup 10
```

### Gate budgets

| Metric | Budget |
|--------|--------|
| `delta_p50_ms` | ≤ 50 ms |
| `delta_p99_ms` | ≤ 200 ms |

Where `delta_X = X_recording - X_disabled`. Gate verdict is `PASS` when both
budgets are met; exit code 0. Exit code 1 on `FAIL`.

### Output

JSON objects to stdout. One per mode, plus a gate object when both modes run:

```json
{
  "mode": "mtf-recording",
  "n_warmup": 50,
  "n_samples": 500,
  "p50_ms": 9.01,
  "p95_ms": 9.67,
  "p99_ms": 10.99,
  ...
}
{
  "gate": {
    "delta_p50_ms": 7.30,
    "delta_p99_ms": 8.79,
    "p50_budget_ms": 50.0,
    "p99_budget_ms": 200.0,
    "verdict": "PASS",
    "failed_budgets": []
  }
}
```

### Fixture

Fixed input: `tests/fixtures/bench_btcusdt_1h_500bars.json` — 500 bars of
synthetic BTCUSDT 1h OHLCV data generated with `numpy.random.default_rng(42)`.
Deterministic: re-generating with the same seed produces identical output.

### Smoke tests

The benchmark has CI smoke tests (structure validation, not number gates):

```bash
cd backend
DATABASE_URL=sqlite+aiosqlite:///:memory: REDIS_URL=redis://x ENV=test \
  JWT_SECRET=test VAULT_KEY=0000000000000000000000000000000000000000000000000000000000000000 \
  python -m pytest tests/scripts/test_bench_aggregator_latency.py -v --no-cov
```

---

## verify_audit_replay.py

Replay-identity verification for all 7 hash-chained tables. Runs against a live
Postgres instance (set `DATABASE_URL`). Used via the ops-debug probe.
