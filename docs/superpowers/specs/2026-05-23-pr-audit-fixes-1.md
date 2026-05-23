# PR-AUDIT-FIXES-1 — Bundle of small fixes from PR-FULL-SYSTEM-AUDIT

**Date:** 2026-05-23
**Branch:** `feat/pr-audit-fixes-1`
**Base:** `dev`
**Class:** small security + ops fix. NO trading-logic change.

## Scope

PR-FULL-SYSTEM-AUDIT (2026-05-23) surfaced three candidates. After deeper
investigation, **only Fix 2 is shipped**:

- **Fix 1** turned out to be a **bad audit finding** — see "Fix 1 retracted".
- **Fix 2** is a real security fix and ships in this PR.
- **Fix 3** is deferred (framework already supports the conditional pattern;
  real fix needs prod-state inspection).

## Fix 1 — RETRACTED — admin_backtest.py ghost-import

The audit claimed `backend/app/api/routes/admin_backtest.py` imported a
non-existent `tools.backtest` module at line 24. **This was wrong.** The
module exists at `backend/tools/backtest.py` (552 LoC, fully implemented:
`run_backtest`, `persist_backtest_result`, `_default_bars_loader`,
`_compute_params_hash`, etc.). The import resolves cleanly when FastAPI
imports the routes module.

The audit tool used did not find `backend/tools/backtest.py` (probably
searched for `tools/backtest.py` at the repo root instead of inside the
`backend/` working directory where pytest / uvicorn cwd from).

The 501-stub PR commit was reverted to dev's original implementation. The
existing integration tests in `tests/integration/test_api_admin_backtest.py`
that monkeypatch `tools.backtest._default_bars_loader` and expect HTTP 201
continue to pass.

Lesson: audit findings that claim "module X does not exist" must be
verified by running the actual import (`python -c "import tools.backtest"`
from inside the backend directory) before being acted on.

## Fix 2 — Telegram bot token redacted in httpx logs (Cat I4.1 / IMPORTANT)

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
`record.msg = redacted, record.args = ()` pattern because `logging.Filter`
runs before the formatter and naïvely rewriting `record.msg` while keeping
`record.args` would let the formatter re-interpolate and undo the redaction.

## Fix 3 — auto_promote_task watchdog conditional registration — DEFERRED

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

A follow-up PR (`feat/pr-audit-fixes-2-watchdog`) will inspect prod state
and write the correct fix.

## Files Changed (final)

- `backend/app/main.py` — wire `install_redaction_filter()` after
  `logging.basicConfig`.
- `backend/app/ops/log_redaction.py` — new module, ~108 LoC.
- `backend/tests/unit/test_log_redaction.py` — 8 tests: positive redaction
  in URL, preservation of unrelated URLs, negative cases (robot, abbot,
  short token), %s args interpolation, idempotent install (httpx AND root),
  no-args records, module-load pattern compile sanity, bad-getMessage
  swallow.

`backend/app/api/routes/admin_backtest.py` is **NOT changed** in the final
PR (Fix 1 retracted; reverted to dev's version).

## Tests

`pytest backend/tests/unit/test_log_redaction.py -v` → 8/8 pass.

Full backend unit suite (minus unrelated Windows pathlib
`test_ml_checkpoints.py` failure) — all green locally.

Integration tests in `test_api_admin_backtest.py` continue to pass on the
unchanged admin_backtest.py.

## Acceptance — post-deploy

- A grep of recent Docker log stdout for `bot[0-9]+:[A-Za-z0-9_-]{20,}` matches
  zero lines after the deploy (filter is live and the next telegram-poller
  iteration emits redacted form).
- A grep for `bot\*\*\*REDACTED\*\*\*` returns hits in `tr-backend` stdout
  from the telegram-poller.
- POST `/api/v1/admin/backtests` continues to return its normal 201/422/etc.
  responses (unchanged by this PR).

## Out of scope

- Watchdog conditional registration (Fix 3) — deferred to a follow-up PR.
