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


# Canonical liveness signal for workers that have one in a natural table.
# Workers without a natural signal use worker_heartbeats (HEARTBEAT below).
HEARTBEAT = "SELECT beat_at FROM worker_heartbeats WHERE worker_name = :n"


WORKER_REGISTRY: tuple[WorkerSpec, ...] = (
    # 1. Per-user dashboard live predictor. Cadence is event-driven on WS
    #    klines; on the active user's selected timeframe this can be once
    #    per minute (1m) up to once per day (1d). We track via heartbeat
    #    so we don't false-alarm when no users are connected.
    WorkerSpec(
        name="live_worker",
        description="WS-driven per-user live prediction worker",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=15 * 60,
        stateful=True,
        pending_heartbeat=True,
    ),
    # 2. Multi-symbol 1h shadow trading worker.
    WorkerSpec(
        name="shadow_worker",
        description="1h shadow paper-trade engine across the asset universe",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=2 * 60 * 60,  # 2x its 1h cadence
        stateful=True,  # holds open positions in memory
        pending_heartbeat=True,
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
        pending_heartbeat=True,
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
        pending_heartbeat=True,
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
        pending_heartbeat=True,
    ),
    # 11. 30s liquidation monitor — autonomous-trading-only.
    WorkerSpec(
        name="liquidation_monitor_task",
        description="30s poll of open live_trades; auto-close at <10% buffer",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=5 * 60,  # 30s cadence — be strict
        stateful=True,  # touches exchange — never auto-restart
        required_env=("AUTONOMOUS_TRADING_ENABLED",),
        pending_heartbeat=True,
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
        pending_heartbeat=True,
    ),
    # 13. Daily 03:30 UTC mode auto-promote — autonomous-trading-only.
    WorkerSpec(
        name="auto_promote_task",
        description="Daily 03:30 UTC tick: paper -> telegram -> fully-auto promotion",
        liveness_query=HEARTBEAT,
        max_staleness_seconds=26 * 60 * 60,
        stateful=False,
        required_env=("AUTONOMOUS_TRADING_ENABLED",),
        pending_heartbeat=True,
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
)


def by_name(name: str) -> WorkerSpec | None:
    """Lookup helper for the watchdog and admin endpoint."""
    for spec in WORKER_REGISTRY:
        if spec.name == name:
            return spec
    return None
