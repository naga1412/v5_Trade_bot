# PR-WATCHDOG-MAX-STALENESS-TUNE — stop alerting on healthy 1h-cadence workers

**Date:** 2026-05-24
**Branch:** `fix/watchdog-max-staleness-per-cadence`
**Base:** `dev`
**Class:** behavior-changing alert threshold tuning. NO trading-logic change. NO new code paths. Single-value change in `worker_registry.py` + 3 new regression tests.

## Symptom that motivated this PR

Today's morning (2026-05-24, ~05:00–11:30 UTC) burned **~3 hours of operator + diagnostic-agent time** chasing what looked like repeated silent deaths of `live_worker`. The probe stack reported:

```
ALERT  live_worker: stale (stale=2658s, max=900s) action=ALERT-ONLY (stateful)
ALERT  live_worker: stale (stale=2727s, max=900s)
ALERT  live_worker: stale (stale=2929s, max=900s)
...
```

These alarms accumulated continuously regardless of whether the worker was actually dead. They contributed to wrong-mental-model investigation paths (shipping observability code for `BinanceKlineStream` that never fired because nothing was actually broken on the WS adapter) and only converged on the right answer when the `11:00:06 UTC` heartbeat landed exactly on schedule and revealed `live_worker` is a 1h-cadence worker — not a continuous-loop worker.

The mismatch:

| Worker | Natural cadence | Old `max_staleness_seconds` | Behavior |
|---|---|---|---|
| `live_worker` | **3600s** (1 beat per 1h candle close) | **900s** (15 minutes) | False-positive ALERT for ~45 min of every hour |

The watchdog uses strict-`>` comparison ([`worker_watchdog.py:215`](backend/app/ops/worker_watchdog.py#L215)): `elif stale > spec.max_staleness_seconds:`. With `max=900s` and a worker that legitimately beats every 3600s, the watchdog flags the worker as stale at staleness=901s — about 15 minutes into every hour — and keeps flagging it until the next candle close ~45 minutes later. The "DEAD worker" log line then accumulates indefinitely, swamping any signal that a worker is **actually** dead.

## Audit scope — every WorkerSpec checked

Per the directive's audit-while-you're-in-there scope, I read all 20 entries in `WORKER_REGISTRY` and cross-referenced cadence with threshold. Result:

| Worker | Natural cadence | `max_staleness_seconds` | Verdict |
|---|---|---|---|
| **live_worker** | **3600s (1h candle)** | **900s** → **3700s** | ❌ **FIX** |
| `shadow_worker` | 900s (15m fastest TF on multi-TF lane) | 1800s | ✓ |
| `universe_refresh_task` | 86400s | 93600s | ✓ |
| `universe_sync_task` | 86400s | 93600s | ✓ |
| `health_pinger_task` | 300s | 900s | ✓ |
| `audit_verifier_task` | 86400s | 93600s | ✓ |
| `news_ingest_task` | ~1800s (Yahoo macro 30m) | 7200s | ✓ (explicitly tuned for sparse-news reality) |
| `news_cleanup_task` | 86400s | 93600s | ✓ |
| `intermarket_snapshot_task` | 300s | 900s | ✓ |
| `intermarket_cleanup_task` | 86400s | 93600s | ✓ |
| `liquidation_monitor_task` | 30s | 300s | ✓ |
| `live_exit_monitor` | 30s | 300s | ✓ |
| `telegram_poller_task` | ~60s long-poll cycle | 600s | ✓ |
| `auto_promote_task` | 86400s | 93600s | ✓ |
| `scanner_batch_task` | 60s | 600s | ✓ |
| `prediction_validator_task` | 60s | 600s | ✓ |
| **`ws_keepalive_task`** | **300s (5min SUPERVISOR heartbeat)** | 900s | ✓ — supervisor beats every 5min independent of children's cadence |
| `mtf_cache_prewarm_task` | single-shot | (bypassed) | ✓ |
| `mtf_cache_ttl_refresh_task` | 30s | 300s | ✓ |
| `symbol_allowlist_refresh` | 86400s | 172800s (2-day budget) | ✓ |
| `ui_freshness_monitor` | 300s | 900s | ✓ |

**Only one worker has a mismatch.** All others are 2x–10x natural cadence, appropriate for transient-blip resilience without crying wolf.

Worth highlighting `ws_keepalive_task` — at first glance, since it spawns 20 per-symbol 1h-cadence child tasks, one could assume the SUPERVISOR also has 1h cadence. But the supervisor itself records a heartbeat every 5 min (per `KEEPALIVE_HEARTBEAT_SECONDS=300` in [`backend/app/ws/keepalive.py:57`](backend/app/ws/keepalive.py#L57)). Its 900s `max_staleness_seconds` is correctly sized to that 300s cadence, not to the children's cadence.

## Fix

Single-value bump in [`backend/app/ops/worker_registry.py`](backend/app/ops/worker_registry.py):

```python
# Before
max_staleness_seconds=15 * 60,  # 900s — false-positive for 45min/hour

# After
max_staleness_seconds=3700,  # 1h candle cadence + 100s slack
```

100s of slack above the natural 3600s cadence accounts for: variable WS message arrival latency, GIL contention during the per-candle predictor work, asyncpg connection-pool acquisition delays, and clock skew between the worker's heartbeat write and the watchdog's `NOW()` read. Strict `>` comparison means we alarm at staleness=3701s — about 1h:1min — which is the right tradeoff: fast enough to catch a genuinely dead worker before the next candle's natural beat would have fired, slow enough to never alarm on a healthy worker.

Sanity cap of 4500s in the test (1h15m): any looser delays real-death detection past acceptable operator-response windows.

### Worker description correction

The prior comment claimed live_worker is "per-user" and supports "1m..1d timeframes." This is aspirational/inaccurate — [`start_background_worker()`](backend/app/ws/live_prediction.py) at `main.py:164` spawns `run_live_prediction()` with NO arguments → defaults to `("BTC/USDT", "1h")`. The worker is a singleton, BTC/USDT only, 1h-only. I corrected the comment to reflect actual behavior + added a forward-looking note for any future contributor extending it to multi-timeframe.

## Tests

Three new regression tests in [`backend/tests/unit/test_worker_registry_consistency.py`](backend/tests/unit/test_worker_registry_consistency.py):

1. **`test_live_worker_max_staleness_matches_1h_cadence`** — direct assertion that `live_worker.max_staleness_seconds` is in `[3600, 4500]`. Catches anyone who accidentally tightens it back to 15-min or loosens it past the sanity cap.
2. **`test_no_worker_has_max_staleness_below_its_cadence`** — sweeps every worker with a derivable natural cadence (via `_NATURAL_CADENCE_SECONDS` table) and asserts `max_staleness >= cadence`. Future-proofs against re-introducing the live_worker class of cry-wolf mismatch for a different worker.
3. **`test_natural_cadence_table_covers_every_heartbeat_worker`** — meta-test: every worker using `HEARTBEAT` as its liveness signal MUST appear in `_NATURAL_CADENCE_SECONDS`. Catches new workers added without a cadence entry, ensuring the sweep guard remains complete.

22/22 tests pass locally (`test_worker_registry_consistency.py` + `test_worker_watchdog.py`).

## Prometheus alertmanager rules

Checked `infra/prometheus/alert_rules.yml` and `prometheus.yml` — no references to `live_worker` or `max_staleness`. The Prometheus alertmanager doesn't currently consume the per-worker threshold; alerts flow only through the in-process watchdog → `alert_admin` → Telegram path. **No parallel adjustment needed.**

## Soak class

Behavior-changing alert threshold. **4h dev soak** required, must span at least one full BTCUSDT/1h candle close (so the new threshold is observed during the natural mid-cycle silence). Acceptance: zero `live_worker: stale` alerts during the 4h window while the worker is healthy. If alerts STILL fire after deploy, something else is wrong with the worker itself.

## Post-deploy verification

```bash
# On Hetzner host:
cd /opt/trading-radar
docker compose logs backend --since 4h 2>&1 | \
  grep -iE "worker_watchdog.*live_worker.*stale|live_worker: stale"
# Expected: ZERO matches during a healthy 4h window.
```

Negative result = success.

## Out of scope

- **Adding per-timeframe constants table** — operator proposed `TIMEFRAME_MAX_STALENESS = {"1h": 3700, "15m": 1000, ...}` as a possible refactor. **Skipped:** only one worker has the mismatch today; a constants table for a one-consumer case is over-engineering. If future workers gain a configurable timeframe, the table can be added then with the cadence-derivation comment co-located.
- **Watchdog scrape interval** — the watchdog itself ticks every 300s ([`worker_watchdog.py:40`](backend/app/ops/worker_watchdog.py#L40)). With max_staleness=3700s, real death-detection latency is `max_staleness + scrape_interval = 4000s ≈ 67 min` worst case. Acceptable for a 1h-cadence worker (next natural beat is at most 1h away anyway — we don't gain meaningful detection latency by tightening the scrape).
- **Telegram alertmanager severity tuning** — current alert is `severity="critical"` for non-self-healed workers. Stateful workers (incl. live_worker) get `ALERT-ONLY` since they can't be auto-restarted. Their alarm severity is correct as-is; this PR just stops the alarm from firing for the wrong reason.

## Risk surface

- Real death detection delay extended from `max=900s + scrape=300s = 20min` to `max=3700s + scrape=300s = 67min` for live_worker. Acceptable per the cadence: a healthy live_worker is silent for up to 60min between candles anyway, so "death within the silent window" was always invisible.
- No behavior change to ANY other worker.
- No code-path change to the trading path, predictor, dispatcher, or any persistence layer.
- Zero blast radius beyond watchdog signal-to-noise.
