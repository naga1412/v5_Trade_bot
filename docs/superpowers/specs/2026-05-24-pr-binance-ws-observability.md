# PR-BINANCE-WS-OBSERVABILITY — add logging to BinanceKlineStream's bare-except

**Date:** 2026-05-24
**Branch:** `feat/pr-binance-ws-observability`
**Base:** `dev`
**Class:** pure observability addition. NO logic change. NO retry-behavior change. NO trading-path impact.

## Symptom that motivated this PR

2026-05-24: `live_worker` died silently TWICE in 90 minutes (~05:00 UTC and ~07:00 UTC). Each incident produced:

- Zero error log lines.
- Zero exception tracebacks.
- Zero telegram alerts.
- A stale heartbeat (45-60 min) detected by the in-process worker_watchdog.
- Predictions table simply stopped getting new rows.

Both incidents survived container restarts (`docker compose up -d --force-recreate backend` AND `docker compose restart backend`). Each operator investigation took ~60 minutes of diagnostic work because there was no log evidence of WHY the worker stopped producing predictions.

## Root cause

[`backend/app/data/adapters/binance.py:303-324`](backend/app/data/adapters/binance.py#L303-L324) `BinanceKlineStream.stream()` is the WS adapter underlying ALL prediction-producing tasks (`live_worker` singleton for BTCUSDT/1h, AND `ws_keepalive_task`'s 20-symbol fleet). Pre-PR the resilient retry loop looked like:

```python
async def stream(self) -> AsyncIterator[ValidatorCandle]:
    connect = self._connect or self._real_connect
    backoff = 1.0
    while True:
        try:
            async for raw in connect(self.url):
                ...
                yield ValidatorCandle(...)
        except Exception:  # noqa: BLE001 - resilient WS loop
            await asyncio.sleep(min(30.0, backoff))
            backoff = min(30.0, backoff * 2)
```

**Every exception was swallowed silently.** The bare-except masked Binance API errors, network failures, TLS handshake issues, WebSocket protocol errors, DNS failures, rate-limit responses — all silent. The caller (`run_live_prediction`) sees no exception (it's caught upstream) AND no new candles (since `stream()` is stuck in retry-with-no-yield mode) AND no heartbeat (heartbeat fires INSIDE the `async for candle` loop body).

The bare-except's `# resilient WS loop` comment captured the original intent — "don't let a transient hiccup crash the worker" — but the implementation traded crash-resilience for diagnostic-opacity. **Today's lesson: silent retry is not resilience; it's failure that defeats observability.**

## Fix

**Pure addition.** No change to the retry-loop behavior, no change to backoff timing, no change to control flow. The fix is observability-only:

```python
consecutive_failures = 0
while True:
    try:
        async for raw in connect(self.url):
            backoff = 1.0
            consecutive_failures = 0  # reset on every successful yield
            ...
    except Exception as e:  # noqa: BLE001 - resilient WS loop
        consecutive_failures += 1
        log_fn = log.error if consecutive_failures == 1 else log.warning
        log_fn(
            "BinanceKlineStream(%s/%s) connect/recv failed "
            "(consecutive=%d, next_retry_in=%.1fs): %s: %s",
            self.symbol, self.timeframe, consecutive_failures,
            min(30.0, backoff), type(e).__name__, e,
        )
        await asyncio.sleep(min(30.0, backoff))
        backoff = min(30.0, backoff * 2)
```

### Log-severity progression

- **First failure of a continuous-failure window → `ERROR`**: a new problem appearing in a previously-healthy stream needs to be loud enough for routine log scanning to notice immediately.
- **Subsequent failures during the backoff window → `WARNING`**: same problem, still retrying. Demoting to WARNING avoids spamming ERROR-level for every retry within a 60-second backoff progression.
- **Successful candle yield → counter resets to 0**: a NEW failure after recovery re-logs at ERROR, preventing "same problem masking different problem" patterns.

### Log message format

`BinanceKlineStream(SYMBOL/TIMEFRAME) connect/recv failed (consecutive=N, next_retry_in=Xs): ExceptionType: detail`

Operator grep targets:
- `consecutive=1` → first hit of a fresh failure window.
- `consecutive=5` (or higher) → sustained problem (worth investigating).
- `BinanceKlineStream(BTCUSDT/1h)` vs `BinanceKlineStream(ETHUSDT/5m)` → which symbol/timeframe stream is unhealthy.

## What this PR explicitly does NOT do

- **No heartbeat-status update from inside the adapter.** `BinanceKlineStream` is a pure data adapter today (no `session_factory` injection). Threading the session through would require a constructor API change + caller updates. Worth doing as a follow-up PR but architecturally bigger than this observability fix.
- **No telegram alert on N consecutive failures.** Sending Telegram from inside a low-level data adapter is architecturally wrong (adapters should be pure I/O). The right place for "Telegram on sustained adapter failure" is at the caller layer (`run_live_prediction` or a supervisor task). Follow-up PR.
- **No retry behavior change.** Backoff timing, max retry, exception scope — all preserved exactly. The PR is observability-only.

These deferrals keep the PR scope tight and the blast radius zero — purely additive logging.

## Coverage scope

The fix is applied to a single function in `BinanceKlineStream.stream()` (the shared WS adapter). **Both prediction-producing paths benefit automatically:**

- `live_worker` (singleton, BTCUSDT/1h, spawned via `start_background_worker()` in `app/main.py:164`).
- `ws_keepalive_task` (top-20 universe fleet, spawned via `start_keepalive_task()` in `app/main.py:256`, each child task calls `run_live_prediction(symbol, "1h")`).

Both paths construct a `BinanceKlineStream` and consume its `stream()` async iterator → both benefit from the new logging without any caller change.

## Tests

[`backend/tests/integration/test_binance_adapter.py`](backend/tests/integration/test_binance_adapter.py) — 4 new tests appended to the existing file (already covers `BinanceKlineStream` with `_connect` injection pattern):

1. **`test_bare_except_logs_first_failure_at_error_level`** — inject `_connect` that raises on first call, asserts an ERROR record exists with the exception type, symbol, timeframe, and `consecutive=1` marker.
2. **`test_bare_except_logs_subsequent_failures_at_warning_level`** — inject 4 consecutive failures, asserts 1 ERROR + 3 WARNINGs with monotonically increasing `consecutive=N`.
3. **`test_successful_candle_resets_consecutive_failure_counter`** — fail/succeed/fail/succeed pattern, asserts TWO ERROR records (one per failure window) with `consecutive=1` each.
4. **`test_no_log_records_when_stream_runs_cleanly`** — regression test: happy path produces zero ERROR/WARNING records (no observability spam).

Local: `pytest tests/integration/test_binance_adapter.py -v` → **6/6 pass** (2 existing + 4 new).

## Acceptance — post-deploy

- Next time any WS stream hiccups (any `BinanceKlineStream` instance, any cause), the operator sees an ERROR-level log line immediately. Investigation goes from "hour of panic, no evidence" to "1-second log grep with the actual exception."
- No false-positive ERRORs on the happy path (test `test_no_log_records_when_stream_runs_cleanly` enforces this).
- No behavior change in production: retry-with-backoff loop runs identically pre/post-PR.

## Risk surface

- **Zero** trading-path impact (data adapter; no dispatch/order code touched).
- **Zero** behavior change (purely additive logging, identical retry semantics).
- **Negligible** log volume: ERROR fires once per failure-window-onset; WARNINGs fire only during sustained failures (typically self-limiting to ~30s of retries before the upstream issue resolves OR Binance kicks the connection).

## Follow-up PRs (NOT this PR)

- `PR-BINANCE-WS-HEARTBEAT-STATUS`: thread `session_factory` into `BinanceKlineStream` so the adapter can `record_heartbeat(status='error', details={'reason': str(e), ...})` from inside the except block. Makes the watchdog detect "running but errored" vs "stale heartbeat" distinguishable.
- `PR-BINANCE-WS-ALERT-ON-SUSTAINED-FAILURE`: at the caller layer (`run_live_prediction` or a supervisor), watch for `consecutive >= N` log records and send a Telegram alert when sustained failure crosses an operator-tunable threshold (e.g., N=10, ~5 minutes of failing).
