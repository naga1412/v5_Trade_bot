# PR-AUDIT-FIXES-1 — Bundle of small fixes from PR-FULL-SYSTEM-AUDIT

**Date:** 2026-05-23
**Branch:** `feat/pr-audit-fixes-1`
**Base:** `dev`
**Class:** small bug + security + ops fix bundle. NO trading-logic change.

## Scope

PR-FULL-SYSTEM-AUDIT (2026-05-23) surfaced three small, attribution-safe
issues. This PR addresses two of them and explicitly defers the third because
its surface turned out to be larger than the original audit framing suggested.

### Fix 1 — `admin_backtest.py` ghost-import (Cat A3.1 / CRITICAL)

**Symptom:** `backend/app/api/routes/admin_backtest.py` imported
`tools.backtest.persist_backtest_result` and `tools.backtest.run_backtest` at
module load. The `tools.backtest` module does not exist anywhere in the
repository. The import is the same class of dead-code bug that
PR-BRAIN-BACKTEST-PHASEB5 fixed in `champion_challenger.py` (the
`_evaluate_sharpe` ghost-import).

**Concrete impact:**
- POST `/api/v1/admin/backtests` returns 500 (ModuleNotFoundError) every call.
- If FastAPI eagerly imports the routes module at startup (and it does, via
  the `include_router` call in `app.main`), the import error can crash the
  worker on boot — a latent landmine.

**Fix shape:** Remove the broken import. Replace the POST handler body with
an honest 501 NOT IMPLEMENTED that returns a clear message: *the backtest
harness for the SP-7 ConvLSTM path is not implemented, the underlying
`tools.backtest` module does not exist, GET still works*. The GET handler
reads the `backtests` table via raw SQL and never depended on
`tools.backtest`, so it stays intact. The router and `BacktestRunIn` /
`BacktestOut` Pydantic models stay so anyone wiring a real harness later has
the shape pre-defined.

### Fix 2 — Telegram bot token redacted in httpx logs (Cat I4.1 / IMPORTANT)

**Symptom:** httpx logs every outgoing HTTP request URL at INFO. The
telegram-poller hits
`https://api.telegram.org/bot<bot_id>:<secret>/getUpdates` on a 30-second
loop. Both the numeric bot_id AND the full secret token are in the URL path
and end up in plaintext in our log files / Docker stdout.

**Risk class:** anyone with read access to backend logs (vendor, contractor,
AI agent, support engineer) can lift the live Telegram bot token verbatim and
impersonate the bot — read messages, post messages, query updates.

**Fix shape:** Add `backend/app/ops/log_redaction.py` — a `logging.Filter`
subclass that runs a compiled regex sub over the formatted log message
before emission. Pattern is `bot\d{6,15}:[A-Za-z0-9_-]{20,80}` →
`bot***REDACTED***`. Tight enough not to false-positive on prose like
"robot" or "abbot" (they have no `\d+:` after them) or unrelated URLs. The
install function attaches the filter to `httpx`, `telegram`, `root`, and
the unnamed root logger; idempotent so test fixtures and hot-reload don't
stack duplicates. Wired from `app.main` right after `logging.basicConfig`
so the filter is live BEFORE the first HTTP request.

The filter uses the standard `record.getMessage()` → regex sub →
`record.msg = redacted, record.args = None` pattern because `logging.Filter`
runs before the formatter and naïvely rewriting `record.msg` while keeping
`record.args` would let the formatter re-interpolate and undo the redaction.

### Fix 3 — auto_promote_task watchdog conditional registration — DEFERRED

**Original audit framing:** the watchdog reports `auto_promote_task` as
`never_heartbeated` even though it's gated behind `AUTONOMOUS_TRADING_ENABLED`.
Add conditional registration so the watchdog doesn't alarm on a worker that
was never supposed to be running.

**Why deferred:** Reading `app/ops/worker_registry.py` and
`app/ops/worker_watchdog.py` shows the framework ALREADY supports this:

- `WorkerSpec.required_env: tuple[str, ...]` exists.
- `auto_promote_task`'s entry already has `required_env=("AUTONOMOUS_TRADING_ENABLED",)`.
- `worker_watchdog._env_gates_met()` correctly reads env, treats truthy
  strings as set, and `check_all_workers()` returns `expected_absent` state
  when gates are unmet.

So if the watchdog is actually firing on `auto_promote_task`, the cause is
NOT a missing conditional. The likely root cause is one of:

1. `AUTONOMOUS_TRADING_ENABLED=True` is in prod env (operator opted-in some
   time ago) and the worker is genuinely failing to spawn — preflight chain
   check or some downstream condition fails. The watchdog is correctly
   alarming on a worker that was supposed to be there.
2. The env-var is set to a value that `_env_gates_met` truthifies but the
   spawn logic in `worker_registry.start_workers()` rejects, producing a
   silent skip that doesn't update the registry.

In either case the right fix is to distinguish "env-gated absent" (silent —
already implemented) from "spawn-failed" (which deserves a different alert
shape). That's a feature, not a one-liner, and it requires inspecting the
actual prod heartbeat log to know which path is firing.

This PR explicitly does NOT touch the watchdog. A follow-up PR
(`feat/pr-audit-fixes-2-watchdog`) will inspect prod state and write the
correct fix.

## Files Changed

- `backend/app/api/routes/admin_backtest.py` — drop broken import, stub POST
  to 501, expand header docstring with audit-finding link.
- `backend/app/main.py` — wire `install_redaction_filter()` after
  `logging.basicConfig`.
- `backend/app/ops/log_redaction.py` — new module, ~108 LoC.
- `backend/tests/unit/test_log_redaction.py` — 7 tests covering: positive
  redaction in URL, preservation of unrelated URLs, negative cases (robot,
  abbot, short token), %s args interpolation, idempotent install, no-args
  records, module-load pattern compile sanity.

## Tests

`pytest backend/tests/unit/test_log_redaction.py -v` → 7/7 pass.

`pytest backend/tests/unit/` (minus `test_ml_checkpoints.py` Windows-pathlib
issue unrelated to this PR) → all green.

Manual smoke:
- Module import: `from app.api.routes import admin_backtest` → success.
- POST endpoint via TestClient with `require_admin` overridden →
  HTTP 501 with the explanatory `detail` message.

## Acceptance — post-deploy

- POST `/api/v1/admin/backtests` returns 501 (not 500). The pre-existing 500
  behavior was the bug we're fixing.
- GET `/api/v1/admin/backtests` still returns 200 with the list of recorded
  backtests.
- A grep of recent Docker log stdout for `bot[0-9]+:[A-Za-z0-9_-]{20,}` matches
  zero lines after the deploy (filter is live and the next telegram-poller
  iteration emits redacted form).
- A grep for `bot\*\*\*REDACTED\*\*\*` returns hits in `tr-backend` stdout
  from the telegram-poller.

## Out of scope

- Watchdog conditional registration (Fix 3) — deferred to a follow-up PR.
- Backtest harness re-implementation — deferred indefinitely; no operator was
  using it (per PR-BACKTEST-1 + this audit's findings).
