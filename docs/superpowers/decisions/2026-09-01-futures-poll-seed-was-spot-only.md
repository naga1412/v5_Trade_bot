# futures_poll's history seed was spot-only — 100% of the cohort, its entire existence, zero predictions ever

**Class:** incident record + fix ratification. **Do not amend by squash.** Any future change to this decision requires a new decision record replayed in order.

## What happened

Stage 1 (PR #536, merged 2026-08-31) wired `futures_poll_task` — the REST-polling supervisor for futures-only symbols (no spot pair, so the spot-WS fleet cannot cover them; see [[binance_futures_ws_geoblock]]) — into prod for the first time. Within the T+6h post-merge checkpoint, 7 symbols (`ZORA`, `SKR`, `HYPE`, `USELESS`, `1000PEPE`, `1000BONK`, `1000SHIB`) were observed crash-looping with `400 Bad Request` against `https://api.binance.com/api/v3/klines` — the **spot** REST endpoint.

Root cause, confirmed by direct code read: `run_live_prediction()` ([`app/ws/live_prediction.py`](../../../backend/app/ws/live_prediction.py)) — the single shared entrypoint for both the spot-WS fleet and the futures-only REST-poll fleet — unconditionally seeds its initial history via `BinanceClient.fetch_klines()`, a **spot** REST call, *before* it ever starts consuming the injected `candle_source` (the futures REST-poll feed built specifically for this cohort). This seed step was never adapted when Phase 4's futures-only injection point (`candle_source`/`symbol_source="futures_poll"`) was built — it correctly separates *ongoing* candle delivery between the two fleets but never touched the *seed*.

**Severity, confirmed empirically, not inferred**: queried `predictions.symbol_source` for every row since Stage 1's merge — **695 rows, 62 distinct symbols, 100% `symbol_source='established_top20'`. Zero rows with `symbol_source='futures_poll'`.** This is not "7 symbols are broken." Every futures-only symbol has no spot pair *by definition* — that is what the cohort means. The entire futures-only conversion — the specific capability the operator asked for because they trade these signals by hand — has never produced a single prediction, for its entire existence. The 61-symbol universe count reported at T+6h was, in reality, 54 symbols of genuine functional coverage plus 7 (all of the then-active futures-only cohort) contributing nothing while still being counted.

## Why it wasn't caught earlier

`futures_poll`'s own restart-with-backoff supervisor (`_run_futures_child_with_restart`) is a resilient-by-design pattern: a crash logs at `WARNING` and retries with exponential backoff (capped at 120s), by design, so one bad symbol can't take down the fleet. That resilience is exactly what let a **permanent** failure hide indefinitely — a transient-severity log for a structurally unrecoverable condition guarantees it sits there forever, retried forever, paging nobody. Compounding this: the supervisor's own heartbeat reported `children: len(children)` — a raw task-spawn count, not a "is this task actually producing anything" count — so a 100%-dead cohort still rendered as a healthy-looking number. This is the third time this specific pattern ("a healthy-looking count hiding a real hole") has surfaced this week, after the cohort-tag forward-reference and the empty-`layer_scores` restart-survivor population.

## The fix (one PR, three parts)

**a. Seed from the correct endpoint.** `run_live_prediction()` now branches on `symbol_source`: `"futures_poll"` seeds via a new `fetch_futures_seed_klines()` helper (`app/ws/futures_poll.py`) against Binance **Futures** REST (`/fapi/v1/klines`) through the shared rate-limited client; every other `symbol_source` keeps the original spot seed unchanged. This is the root-cause fix — every symbol in the cohort has a valid futures listing by construction (it's sourced from `/fapi/v1/exchangeInfo` via `live_fleet_universe`), so this endpoint is always correct for it.

**b. Severity.** A crash-restart loop now tracks a per-`(symbol, timeframe)` consecutive-crash streak (`_child_crash_streaks`). Below `_CHILD_DEAD_THRESHOLD` (5), behavior is unchanged (`WARNING`, resilient retry — a genuine transient blip has ample room to clear within a few retries, since backoff is already in the 40–80s range by the 4th–5th attempt). At and above the threshold, the crash escalates to `log.error` **and** a real `alert_admin(..., level="critical")` call through the Telegram→SMTP→log routing chain — fired once, on the crossing, not on every subsequent retry. A symbol that later succeeds clears its streak and drops out of the dead-set automatically (self-healing, no manual reset needed).

**c. The count means what it says.** `run_futures_poll`'s heartbeat (`_heartbeat_details`) now reports `children` (raw task-spawn count, kept for back-compat) **and** `children_producing` (`children` minus confirmed-dead) **and** `dead_symbols` (named, not just counted). `children_producing` is the number that should be read as coverage. A dead child's task keeps running at its capped backoff — it is excluded from the count, never killed outright, so it self-heals automatically if the underlying condition ever resolves.

## Scope note

This fix corrects `futures_poll`'s own heartbeat. `live_fleet_universe`'s row count is a **different, still-valid** concept — liquidity-floor *qualification*, not seed-success — and is not changed by this fix. The two numbers answer different questions; conflating them was never the bug, only `futures_poll`'s own `children` count conflating "spawned" with "producing" was.

## Verification

- Empirical confirmation of the 100% failure rate: `SELECT symbol_source, COUNT(*), COUNT(DISTINCT symbol) FROM predictions WHERE ts >= <Stage1 merge> GROUP BY symbol_source` — zero `futures_poll` rows.
- Empirical confirmation of the specific 7 symbols' complete silence: `SELECT symbol, COUNT(*) FROM predictions WHERE symbol IN (...) GROUP BY symbol` — no rows.
- Code-level confirmation the crash-looping request is not consuming a shared rate-limit bucket (a separate question, answered and closed as NOT a revert candidate): `run_live_prediction()` constructs a fresh, unshared `httpx.AsyncClient`/`RateLimitedClient`/`TokenBucket` on every call — each crash-restart's failed seed attempt draws from its own throwaway bucket, never the shared `intermarket_adapter().rate_client` the legitimate poll loop uses. Real Binance server-side per-IP impact of the 7-symbol crash loop: ~7 weight/min against a 1200/min cap (~0.6%), with backoff already saturated at its 120s cap.
- Unit tests: `tests/unit/test_live_prediction_candle_source.py` (seed-source branching, both directions) and `tests/ops/test_futures_poll_task.py` (crash-streak escalation, alert-fires-once, recovery-clears-state, heartbeat-details shape).

## Timing

Built and CI'd during Stage 1's 48h observation window (operator's explicit priority call: HIGH, build now). **Held, not merged, until the window closes** — merging during an active observation contaminates the very thing being observed. Merges as the first item after T+48h closes, ahead of Stage 2. This is remediation of what Stage 1's own observation exposed, not new dev work — it does not breach the standing dev freeze.

## Reversal criteria

Re-examined if:
1. A genuinely delisted or malformed symbol slips through `live_fleet_universe`'s liquidity-floor query and triggers a real, permanent crash-loop even after the futures-endpoint fix — expected to be rare (an edge case, not the structural 100% failure this fix closes), and is exactly the case `_CHILD_DEAD_THRESHOLD`'s escalation exists to surface loudly rather than silently.
2. `_CHILD_DEAD_THRESHOLD=5` proves too aggressive (false-positive "critical" alerts on genuine transient Binance issues) or too slow (a real permanent failure takes too long to surface) once observed against real traffic patterns — revisit the constant, not the escalation design.
