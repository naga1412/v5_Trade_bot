"""Single source of truth for the 13 background workers.

Each entry declares:
  - ``name``: stable identifier used in logs, alerts, and the watchdog.
  - ``description``: one-line human summary for the admin endpoint.
  - ``liveness_query``: SQL returning a single timestamp = the most recent
    activity. The watchdog computes ``now() - max(beat_at)`` and alerts
    if it exceeds ``max_staleness``. ``None`` means "no liveness signal
    available" — watchdog skips it (still listed in /admin/workers).
  - ``max_staleness_seconds``: how long without a beat before the worker
    is considered DEAD. Generous — accounts for worker cadence + jitter.
  - ``stateful``: True if auto-restart is unsafe (open positions, vault
    cache, exchange connections). Watchdog only ALERTS for these; it
    does not recreate the task.
  - ``required_env``: env-var gates the lifespan checks before spawning.
    A worker with unmet gates is "expected absent" — watchdog ignores it.

The CI test ``tests/unit/test_worker_registry_consistency.py`` enforces:
  - Every entry's name appears as a ``start_<name>`` reference in main.py.
  - Every worker function in app/ has at least one log.info() within its
    first 30 lines (so we don't ship another silent worker).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    description: str
    # SQL returning a single TIMESTAMPTZ column. None = no DB liveness signal
    # (the worker should record heartbeats instead — see app/ops/heartbeat.py).
    liveness_query: str | None
    max_staleness_seconds: int
    stateful: bool
    required_env: tuple[str, ...] = ()
    # PR #97 declared 8 workers using the HEARTBEAT liveness query, but the
    # follow-up PR that adds record_heartbeat() calls inside each worker's
    # main loop hasn't shipped yet. Until it does, those workers' MAX(beat_at)
    # legitimately returns NULL — but reporting that as 'never_heartbeated'
    # creates ~8 false alarms on every watchdog tick and hides any real
    # never_heartbeated condition we care about. Workers flagged here are
    # reported in a separate 'pending_heartbeat' bucket and don't trigger
    # alerts. Flag is removed when the heartbeat call lands in that worker.
    pending_heartbeat: bool = False
    # FU-15: True for tasks that fire once at startup, exit clean, and never
    # heartbeat again. The watchdog reports such tasks as
    # `single_shot_completed` (non-alarming) instead of `no_signal`.
    # The worker records ONE heartbeat with `status="single_shot_completed"`
    # on clean exit. `mtf_cache_prewarm_task` is the canonical example.
    single_shot: bool = False
    # Healer B3 (2026-07-23): tuple of env vars that gate whether the worker
    # ACTUALLY DOES WORK. When none are set to a truthy value, the watchdog
    # classifies the worker as `expected_dormant` (info, not alarm) even if
    # it never heartbeats. Distinct from `required_env`, which gates whether
    # the worker is even SPAWNED — `optional_gate_env` covers workers that
    # ARE spawned but self-disable when their feature flag is off (auto_promote_task
    # is the canonical example: spawned when AUTONOMOUS_TRADING_ENABLED is
    # set, but early-returns when AUTO_PROMOTE_TO_* is not enabled).
    #
    # Cry-wolf prevention: without this, auto_promote_task alarms as
    # never_heartbeated on every watchdog tick under the current opt-in-off
    # prod config — training the operator to ignore the watchdog.
    optional_gate_env: tuple[str, ...] = ()


# Canonical liveness signal for workers that have one in a natural table.
# Workers without a natural signal use worker_heartbeats (HEARTBEAT below).
HEARTBEAT = "SELECT beat_at FROM worker_heartbeats WHERE worker_name = :n"


WORKER_REGISTRY: tuple[WorkerSpec, ...] = (
    # 1. Singleton BTC/USDT @ 1h predictor. Spawned by start_background_worker()
    #    in app/main.py with NO arguments → uses run_live_prediction defaults
    #    (symbol_pair="BTC/USDT", timeframe="1h"). The "per-user" framing in
    #    the prior version of this comment was aspirational and inaccurate;
    #    there is only one instance and it's hard-coded to BTC/USDT @ 1h.
    #
    #    PR-WATCHDOG-MAX-STALENESS-TUNE (2026-05-24): cadence is exactly ONE
    #    beat per closed candle. For 1h timeframe that's 3600s between beats.
    #    The previous 15-min threshold (900s) was set under the assumption
    #    of multi-timeframe operation (1m..1d) and produced FALSE-POSITIVE
    #    stale alarms for 45 minutes of every healthy hour — confirmed in
    #    today's incident where ~3 hours of operator triage was spent
    #    chasing a healthy worker's natural mid-cycle silence (see
    #    feedback_v5_bot_diagnostic_discipline.md lesson 14). Bumped to
    #    3700s = 1h cadence + 100s grace; threshold is strict `>` in
    #    worker_watchdog.py:215 so any genuine inter-candle stall longer
    #    than ~1h:40s will still alarm.
    #
    #    Note for future contributors: if this worker is ever extended to
    #    serve multiple timeframes (1m, 5m, 15m, etc.), this budget needs
    #    to shrink to the FASTEST configured timeframe's natural cadence
    #    plus grace. The current value is correct ONLY while live_worker
    #    remains a 1h-only singleton.
    WorkerSpec(
        name="live_worker",
        description="Singleton BTC/USDT @ 1h WS-driven live prediction worker",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=3700,  # 1h candle cadence + 100s slack
        stateful=True,
        # FU-1 closed for live_worker — record_heartbeat wired in
        # run_live_prediction's per-candle iteration body.
    ),
    # 2. Multi-symbol 1h shadow trading worker.
    WorkerSpec(
        name="shadow_worker",
        description="Multi-TF (1h + 15m) shadow paper-trade engine across the asset universe",
        liveness_query=HEARTBEAT,
        # PR3 B7 (spec §4.7): tightened from 2h (sized for 1h cadence) to
        # 30min (2× the 15m cadence). Same safety factor as the old budget
        # vs the old cadence. PR3 also wires the heartbeat at every
        # _handle_candle invocation (already FU-1-closed). Running the
        # 15m lane with the old 2h budget would have hidden a stalled
        # 15m stream for 2h — operationally unacceptable per §6.3.
        max_staleness_seconds=30 * 60,
        stateful=True,  # holds open positions in memory
    ),
    # 3. Daily 00:00 UTC asset_universe refresh.
    WorkerSpec(
        name="universe_refresh_task",
        description="Daily 00:00 UTC top-30 USDT-M futures universe refresh",
        liveness_query="SELECT max(snapshot_at) FROM asset_universe",
        max_staleness_seconds=26 * 60 * 60,  # 24h cadence + 2h slack
        stateful=False,
    ),
    # 4. Daily 02:00 UTC universe sync across adapters.
    WorkerSpec(
        name="universe_sync_task",
        description="Daily 02:00 UTC sync of universe_history across adapters",
        liveness_query="SELECT max(last_synced_at) FROM universe_history",
        max_staleness_seconds=26 * 60 * 60,
        stateful=False,
    ),
    # 5. 5-min adapter health pinger.
    WorkerSpec(
        name="health_pinger_task",
        description="5-min adapter health endpoint pinger",
        liveness_query="SELECT max(checked_at) FROM adapter_health",
        max_staleness_seconds=15 * 60,  # 5min cadence + 10min slack
        stateful=False,
    ),
    # 6. Nightly 03:00 UTC audit hash-chain verifier.
    WorkerSpec(
        name="audit_verifier_task",
        description="Nightly 03:00 UTC hash-chain verifier",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=26 * 60 * 60,
        stateful=False,
        # FU-1 closed for audit_verifier_task — record_heartbeat wired in
        # run_audit_verifier_loop per tick (or paused-skip).
    ),
    # 7. 5-min crypto / 30-min macro news ingest.
    WorkerSpec(
        name="news_ingest_task",
        description="5-min crypto + 30-min macro news ingest",
        liveness_query="SELECT max(fetched_at) FROM news_items",
        # 2 hours, not 45 min. The liveness signal is "MAX(fetched_at)
        # in news_items" — but that only advances when an adapter
        # successfully writes a NEW article. Yahoo RSS macro polls
        # every 30 min but frequently returns 0 new articles (news flow
        # isn't constant); CryptoPanic is disabled in prod (no api_key).
        # 45 min was tripping the stale alert almost continuously,
        # generating ~12 log warnings/hour with no real failure.
        # 2 h is generous and reflects the actual cadence of real news.
        max_staleness_seconds=2 * 60 * 60,
        stateful=False,
    ),
    # 8. Nightly 04:00 UTC news cleanup.
    WorkerSpec(
        name="news_cleanup_task",
        description="Nightly 04:00 UTC news_items retention cleanup",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=26 * 60 * 60,
        stateful=False,
        # FU-1 closed for news_cleanup_task — record_heartbeat wired in
        # run_news_cleanup_loop per nightly tick.
    ),
    # 9. 5-min funding/OI snapshot.
    WorkerSpec(
        name="intermarket_snapshot_task",
        description="5-min funding rate + open interest snapshot",
        # Column is captured_at (verified against migration 0014), not ts.
        # The first prod watchdog run hit UndefinedColumnError here, which
        # also exposed the InFailedSQLTransactionError-cascade bug below.
        liveness_query="SELECT max(captured_at) FROM intermarket_snapshots",
        max_staleness_seconds=15 * 60,
        stateful=False,
    ),
    # 10. Nightly 04:30 UTC intermarket cleanup.
    WorkerSpec(
        name="intermarket_cleanup_task",
        description="Nightly 04:30 UTC intermarket_snapshots cleanup",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=26 * 60 * 60,
        stateful=False,
        # FU-1 closed for intermarket_cleanup_task — record_heartbeat wired
        # in run_intermarket_cleanup_loop per nightly tick.
    ),
    # 11. 30s liquidation monitor — autonomous-trading-only.
    WorkerSpec(
        name="liquidation_monitor_task",
        description="30s poll of open live_trades; auto-close at <10% buffer",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=5 * 60,  # 30s cadence — be strict
        stateful=True,  # touches exchange — never auto-restart
        required_env=("AUTONOMOUS_TRADING_ENABLED",),
        # FU-1 closed for liquidation_monitor_task — record_heartbeat wired in
        # run_liquidation_monitor_loop per poll tick.
    ),
    # 11b. PR8 — 30s live-exit monitor (TP/SL/TIMEOUT/EXTERNAL_CLOSE).
    # Mirrors liquidation_monitor's cadence + staleness budget. Different
    # responsibility: liq monitor watches the buffer-to-liquidation;
    # this monitor watches the TP/SL/timeout bracket. Both run when
    # AUTONOMOUS_TRADING_ENABLED.
    WorkerSpec(
        name="live_exit_monitor",
        description=(
            "30s poll of open live_trades; writes exit_reason + cooldown "
            "on TP/SL/TIMEOUT/EXTERNAL_CLOSE (PR8)"
        ),
        liveness_query=HEARTBEAT,
        max_staleness_seconds=5 * 60,
        stateful=True,
        required_env=("AUTONOMOUS_TRADING_ENABLED",),
    ),
    # 12. Telegram poller — autonomous-trading-only + creds required.
    WorkerSpec(
        name="telegram_poller_task",
        description="Telegram long-poll for trade + brain-checkpoint approvals",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=10 * 60,
        stateful=True,
        required_env=(
            "AUTONOMOUS_TRADING_ENABLED", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        ),
        # FU-1 closed for telegram_poller_task — record_heartbeat wired in
        # run_telegram_poller per long-poll cycle (success + backoff paths).
    ),
    # 13. Daily 03:30 UTC mode auto-promote — autonomous-trading-only.
    WorkerSpec(
        name="auto_promote_task",
        description="Daily 03:30 UTC tick: paper -> telegram -> fully-auto promotion",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=26 * 60 * 60,
        stateful=False,
        required_env=("AUTONOMOUS_TRADING_ENABLED",),
        # Healer B3 (2026-07-23): the loop early-returns without heartbeating
        # when neither AUTO_PROMOTE_TO_TELEGRAM_ENABLED nor
        # AUTO_PROMOTE_TO_FULLYAUTO_ENABLED is truthy. Both default false, so
        # under current prod config this worker is intentionally idle — the
        # watchdog must class it expected_dormant rather than never_heartbeated
        # to stop the daily cry-wolf alarm.
        optional_gate_env=(
            "AUTO_PROMOTE_TO_TELEGRAM_ENABLED",
            "AUTO_PROMOTE_TO_FULLYAUTO_ENABLED",
        ),
        # FU-1 closed for auto_promote_task — record_heartbeat wired in
        # run_auto_promote_loop per daily evaluation cycle.
    ),
    # 14. Feature 4 — 60s multi-asset fast scanner.
    WorkerSpec(
        name="scanner_batch_task",
        description="60s deterministic fast-scan over the asset universe",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=10 * 60,  # 60s cadence + 9min slack
        stateful=False,
    ),
    # 15. Feature 2 — 60s prediction accuracy validator.
    WorkerSpec(
        name="prediction_validator_task",
        description="60s loop validating predictions against next-bar actual close",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=10 * 60,
        stateful=False,
    ),
    # 16. Server-side WS keepalive fleet — fans run_live_prediction across
    #     the top-N universe so prediction_validations is populated 24/7
    #     without a human leaving a browser tab open.
    WorkerSpec(
        name="ws_keepalive_task",
        description="Fans live-prediction WS subscriptions across top-N universe (1h)",
        liveness_query=HEARTBEAT,
        # 5-min heartbeat cadence + 10-min slack.
        max_staleness_seconds=15 * 60,
        # Owns N live Binance WS connections; auto-restart could re-open
        # WS faster than Binance's per-IP rate window — alert-only is safer.
        stateful=True,
    ),
    # 16b. Phase 4 Task 17 — REST-polling supervisor for futures-only
    #      symbols (the liquidity-floor cohort that doesn't qualify for the
    #      top-N spot-WS fleet above). Structurally mirrors ws_keepalive_task:
    #      own child-task set, own reconciliation loop, 5-min heartbeat
    #      cadence (FUTURES_POLL_HEARTBEAT_SECONDS). stateful=True for the
    #      same open-position-safety reason ws_keepalive_task is stateful —
    #      a watchdog-triggered restart would cancel every child task
    #      (run_futures_poll's `finally` block), and on the fresh restart
    #      the desired-symbol set is recomputed from the liquidity-floor
    #      cohort only (_load_desired_futures_symbols), NOT from currently
    #      open positions — so a symbol that has since dropped off the
    #      liquidity floor but still has an open live position would not be
    #      restarted, silently losing its candle feed. Auto-restart is only
    #      safe within _refresh_futures_children's own reconciliation (which
    #      does carry the open-position override); a supervisor-level
    #      restart does not. Alert-only, matching ws_keepalive_task.
    WorkerSpec(
        name="futures_poll_task",
        description="REST-polls Binance Futures klines for the liquidity-floor futures-only symbol cohort (1h)",
        liveness_query=HEARTBEAT,
        # 5-min heartbeat cadence + 10-min slack — identical budget to
        # ws_keepalive_task, which shares the same cadence.
        max_staleness_seconds=15 * 60,
        stateful=True,
    ),
    # 17. MTF cache pre-warm — single-shot at startup; loads the top-30
    #     universe and calls prewarm_cache (60s hard deadline, fail-open).
    #     FU-1 H9 + FU-15: emits ONE heartbeat with
    #     status='single_shot_completed' on clean exit; watchdog classifies
    #     as 'single_shot_completed' (non-alarming) per FU-15 state machine.
    WorkerSpec(
        name="mtf_cache_prewarm_task",
        description="Single-shot MTF kline cache pre-warm over the top-30 universe",
        liveness_query=HEARTBEAT,  # FU-15: watchdog reads single-shot heartbeat
        max_staleness_seconds=10 * 60,  # bypassed by single_shot branch
        stateful=False,
        single_shot=True,
    ),
    # 18. MTF cache TTL-refresh loop — long-running; every 30s scans for
    #     cache entries within 20% of expiry and refreshes them proactively.
    #     Heartbeats each iteration so the watchdog can detect stalls.
    WorkerSpec(
        name="mtf_cache_ttl_refresh_task",
        description="30s MTF kline cache TTL-refresh loop (simple, no stampede protection)",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=5 * 60,  # 30s cadence + ~4.5min slack
        stateful=False,
    ),
    # 19. PR10 — daily symbol allowlist refresh (NOT autonomous-trading-gated;
    #     snapshots are useful in all modes — manual, telegram-approve, and
    #     fully-auto. The dispatcher gate only *consumes* them when
    #     SYMBOL_ALLOWLIST_ENABLED=True). Single-writer worker, so FU-24's
    #     concurrent-insert race on insert_with_chain doesn't apply.
    WorkerSpec(
        name="symbol_allowlist_refresh",
        description=(
            "Daily compute of per-symbol Sharpe + allowlist snapshot (PR10). "
            "Writes one symbol_performance_snapshots row per symbol."
        ),
        liveness_query=HEARTBEAT,
        # Healer B2 (2026-07-23): tightened from 2 days ("1 missed run allowed")
        # to cadence + 10% grace, matching the live_worker treatment. Under
        # the old 48h budget the 2026-07-22 stale incident (last successful
        # cycle 7/20 17:47 UTC) took ~24h longer than necessary to visibly
        # cross the alarm threshold. 26h = 24h cadence + 2h slack — one
        # missed cycle now alarms within an hour of the next scheduled fire.
        max_staleness_seconds=26 * 60 * 60,
        stateful=False,  # safe to auto-restart
    ),
    # 20. PR10.5 / FU-28 — UI data-pipeline freshness monitor.
    #     Observes pnl_tick emission rate vs open positions; alerts on
    #     staleness; optional auto-recycle (default OFF — shadow_worker
    #     is stateful).
    WorkerSpec(
        name="ui_freshness_monitor",
        description=(
            "5-min poll of pnl_tick emission freshness (FU-28). "
            "Logs WARN + heartbeat 'degraded' when open positions have "
            "no recent ticks; optional auto-recycle when enabled."
        ),
        liveness_query=HEARTBEAT,
        max_staleness_seconds=15 * 60,
        stateful=False,
    ),
    # 21. Healer Phase 0 — detect-only monitoring layer. Runs the C1-C4
    #     detectors on a 5-min cadence + persists findings to
    #     healer_findings for the healer-status ops-debug probe.
    WorkerSpec(
        name="healer_detector_task",
        description=(
            "5-min poll of C1-C4 detectors (dispatch error rate, "
            "score-distribution anomaly, per-symbol prediction "
            "freshness, blocked-rate anomaly)"
        ),
        liveness_query=HEARTBEAT,
        max_staleness_seconds=15 * 60,  # 5-min cadence + 10-min slack
        stateful=False,
    ),
    # 22. PR5 — daily p_win isotonic calibration refit. Writes
    #     long.pkl/short.pkl consumed by predict_p_win at prediction
    #     time. Record-only column (predictions.p_win) — no live-money
    #     decision depends on this worker's output.
    WorkerSpec(
        name="p_win_refit",
        description=(
            "Daily re-fit of per-direction isotonic p_win calibration "
            "models against closed shadow_trades (PR5)."
        ),
        liveness_query=HEARTBEAT,
        # Cadence + ~10% grace, matching symbol_allowlist_refresh's
        # daily-cadence treatment (Healer B2).
        max_staleness_seconds=26 * 60 * 60,
        stateful=False,  # safe to auto-restart
    ),
)


def by_name(name: str) -> WorkerSpec | None:
    """Lookup helper for the watchdog and admin endpoint."""
    for spec in WORKER_REGISTRY:
        if spec.name == name:
            return spec
    return None
