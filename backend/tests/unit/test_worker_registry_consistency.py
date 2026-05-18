"""CI gate: every worker in WORKER_REGISTRY must be wired + observable.

Why this exists: PR #95 / #97 root-caused a 13-hour-silent shadow worker
to two failure modes — the worker had no INFO logging on its happy path,
and the multi-stream reader silently swallowed reconnect exceptions.
Without this CI gate we'd ship the same bug again with the next worker.

Two assertions per worker:

  1. **Wiring**: WorkerSpec.name appears as an identifier in main.py
     (i.e. the lifespan actually owns the asyncio.Task). A worker that
     exists in the registry but isn't spawned is invisible to the
     watchdog and to ops in general.
  2. **Observability**: the worker's source module contains either
     ``log.info(`` (so the happy path is visible in production logs)
     OR ``record_heartbeat(`` (so the watchdog can see it). Either is
     sufficient. Workers without natural DB liveness signals are
     expected to choose the heartbeat option.

This is a static check — it does not run any worker. It catches the
class of bug that integration tests can't, namely "we forgot to wire it
up at all" and "we shipped a worker that prints nothing forever."
"""

from __future__ import annotations

import inspect
from pathlib import Path

import app.main as main_module
from app.ops.worker_registry import HEARTBEAT, WORKER_REGISTRY

# Map registry name -> the source module path whose source must contain
# at least one log.info(...) or record_heartbeat(...). Co-located so a
# new worker just adds one row here + one row in the registry.
WORKER_SOURCE_MODULES: dict[str, str] = {
    "live_worker": "app/ws/live_prediction.py",
    "shadow_worker": "app/shadow/worker.py",
    "universe_refresh_task": "app/shadow/universe_refresh.py",
    "universe_sync_task": "app/data/universe_sync.py",
    "health_pinger_task": "app/data/adapter_health.py",
    "audit_verifier_task": "app/ops/verifier_scheduler.py",
    "news_ingest_task": "app/news/ingest_worker.py",
    "news_cleanup_task": "app/news/ingest_worker.py",
    "intermarket_snapshot_task": "app/data/intermarket_worker.py",
    "intermarket_cleanup_task": "app/data/intermarket_worker.py",
    "liquidation_monitor_task": "app/trading/execution/liquidation_monitor.py",
    "live_exit_monitor": "app/trading/execution/live_exit_monitor.py",
    "telegram_poller_task": "app/ops/telegram_polling.py",
    "auto_promote_task": "app/trading/auto_promote.py",
    "scanner_batch_task": "app/scanner/batch.py",
    "prediction_validator_task": "app/ml/validator.py",
    "ws_keepalive_task": "app/ws/keepalive.py",
    "mtf_cache_prewarm_task": "app/core/scoring/mtf_confluence.py",
    "mtf_cache_ttl_refresh_task": "app/core/scoring/mtf_confluence.py",
}


def test_every_registry_entry_has_a_source_module_mapping() -> None:
    """Catches a registry entry without a corresponding observability target."""
    missing = {spec.name for spec in WORKER_REGISTRY} - set(WORKER_SOURCE_MODULES)
    assert not missing, (
        f"WORKER_REGISTRY entries with no WORKER_SOURCE_MODULES mapping: "
        f"{sorted(missing)}. Add a mapping in this test."
    )


def test_every_worker_is_wired_in_main_lifespan() -> None:
    """WorkerSpec.name must appear in main.py source.

    The lifespan owns each task as a local variable named after the
    registry name; if the name doesn't appear, the worker isn't spawned
    and the watchdog will report ``never_heartbeated`` forever.
    """
    src = inspect.getsource(main_module)
    missing = [spec.name for spec in WORKER_REGISTRY if spec.name not in src]
    assert not missing, (
        f"WORKER_REGISTRY entries not referenced in main.py lifespan: "
        f"{missing}. Either wire the worker in lifespan() or remove from "
        "the registry."
    )


def test_every_worker_module_is_observable() -> None:
    """Each worker module must contain log.info( or record_heartbeat(.

    Catches the silent-worker class of bug. Either form is fine: log.info
    gives line-by-line visibility in container logs, record_heartbeat
    gives the watchdog a DB-visible liveness signal.
    """
    repo_root = Path(__file__).resolve().parents[2]
    silent: list[str] = []
    for name, rel_path in WORKER_SOURCE_MODULES.items():
        full = repo_root / rel_path
        text = full.read_text(encoding="utf-8")
        if "log.info(" not in text and "record_heartbeat(" not in text:
            silent.append(f"{name} ({rel_path})")
    assert not silent, (
        f"Workers with no log.info() or record_heartbeat() in their source "
        f"module: {silent}. Add observability or the watchdog can't see them."
    )


def test_every_heartbeat_query_uses_the_HEARTBEAT_constant() -> None:
    """If a worker uses the heartbeat liveness path, it must use the constant.

    Catches typos in the SQL — :n binding is sensitive and easy to drift.
    """
    for spec in WORKER_REGISTRY:
        if spec.liveness_query is None:
            continue
        if "worker_heartbeats" in spec.liveness_query:
            assert spec.liveness_query == HEARTBEAT, (
                f"{spec.name}: liveness_query references worker_heartbeats "
                f"but doesn't equal the HEARTBEAT constant. Use HEARTBEAT "
                f"to keep the binding name (:n) consistent."
            )


def test_no_pending_heartbeat_after_fu1() -> None:
    """FU-1 closes every worker's heartbeat gap — no spec may still flag pending.

    PR #97 introduced ``pending_heartbeat=True`` as a temporary placeholder
    for workers that had a HEARTBEAT liveness_query but no in-loop
    ``record_heartbeat(...)`` call yet. The watchdog reported those in a
    non-alarming ``pending_heartbeat`` bucket. FU-1 ships the
    in-loop calls; once it lands no spec should still carry that flag, and
    leaving one would silently suppress a real never-heartbeated alert.
    """
    pending = [s.name for s in WORKER_REGISTRY if s.pending_heartbeat]
    assert not pending, (
        f"WORKER_REGISTRY still has pending_heartbeat=True entries: "
        f"{pending}. FU-1 wired in-loop record_heartbeat for every worker — "
        f"drop the flag (or, if a new worker is being added before its "
        f"heartbeat is wired, complete the wiring first)."
    )
