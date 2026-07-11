import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Final

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    admin,
    admin_adapters,
    admin_backtest,  # SP-7 Phase B5
    admin_hyperopt,  # SP-7 Phase C4
    admin_ml,
    admin_monitoring,  # SP-7 Phase G3
    admin_rl,  # SP-4 Phase D — RL brain checkpoint registry
    admin_news,  # SP-9 Phase F5
    admin_patterns,
    admin_system,  # SP-PAUSE master pause/resume
    admin_test_trade,  # ad-hoc testnet round-trip smoke test
    admin_traps,
    bot_status,
    health,
    intermarket,  # SP-3.5 Phase E2
    me,
    predictions,  # Feature 2 — prediction accuracy telemetry
    scanner,  # SP-6 Phase A4
    scanner_fast,  # Feature 4 — multi-asset fast scanner
    tab1,
)
from app.api.routes import ws as ws_routes
from app.api.pause_middleware import register_pause_middleware
from app.auth.query_guard import attach_query_guard
from app.config import get_settings
from app.data.adapter_health import start_health_pinger_task
from app.data.adapters import aclose_all as _aclose_adapters
from app.data.intermarket_worker import (
    start_intermarket_cleanup_task,
    start_intermarket_snapshot_task,
)
from app.data.universe_sync import start_universe_sync_task
from app.shadow.universe_refresh import start_universe_refresh_task
from app.db.session import get_engine, get_session_factory
from app.ml.checkpoints import load_active_checkpoint
from app.rl.checkpoints import load_active_checkpoint as load_rl_active_checkpoint
from app.news.ingest_worker import (
    start_news_cleanup_task,
    start_news_ingest_task,
)
from app.ops.alert_routing import alert_admin as _route_alert
from app.ops.heartbeat import record_heartbeat as _record_heartbeat
from app.ops.monitoring import instrument_app
from app.ops.telegram_polling import (
    PollerConfig,
    start_telegram_poller,
)
from app.ml.validator import start_prediction_validator_task
from app.ops.verifier_scheduler import start_audit_verifier_task
from app.ops import worker_supervisor
from app.ops.worker_watchdog import start_worker_watchdog
from app.scanner.batch import start_scanner_batch_task
from app.shadow.worker import start_shadow_worker
from app.exchanges.binance_live import BinanceLiveClient
from app.trading.auto_promote import (
    AutoPromoteConfig,
    start_auto_promote_task,
)
from app.trading.execution.glue import (
    initialize_vault_cache,
    vault_keys,
)
from app.trading.execution.live_exit_monitor import (
    start_live_exit_monitor,
)
from app.trading.execution.liquidation_monitor import (
    start_liquidation_monitor,
)
from app.trading.preflight import check_audit_chain_intact, run_preflight
from app.ws.keepalive import start_keepalive_task
from app.ws.live_prediction import start_background_worker
from app.core.scoring.mtf_confluence import (
    start_mtf_cache_prewarm_task,
    start_mtf_cache_ttl_refresh_task,
)

# Configure root logger from LOG_LEVEL env var. docker-compose passes
# LOG_LEVEL=INFO from .env, but Python's default is WARNING for unconfigured
# loggers, so app.* INFO lines (including "loaded active checkpoint") were
# silently dropped in production. Uvicorn configures its own loggers
# separately so this only affects app.* — no conflict.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# PR-AUDIT-FIXES-1 (2026-05-23): mask Telegram bot tokens (and similar
# secrets) before they hit log files. httpx logs each request URL at INFO
# which previously included the full bot token in the path. The filter
# rewrites those tokens in-process so neither docker stdout nor any log
# aggregator sees the raw secret.
from app.ops.log_redaction import install_redaction_filter  # noqa: E402
install_redaction_filter()

log = logging.getLogger(__name__)


# PR-PREFLIGHT-ALERT — heartbeat row name for the boot-time preflight gate.
# Distinct from any other worker name in WORKER_REGISTRY so the row can be
# read as "preflight ran + outcome" rather than mixed in with a loop's beats.
_PREFLIGHT_WORKER_NAME: Final[str] = "preflight_gate"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    # SP-0.7 §7.3: query guard fires for any per-user table SELECT/UPDATE/DELETE
    # missing a user_id predicate. Dev raises (loud failure during the failing
    # test), prod warns (single missed predicate cannot brick a live request).
    # Skip in test/ci — those environments stand up their own engines via the
    # bot_status_factory fixture and attach the guard explicitly when a test
    # wants to assert the dev-mode raising behaviour.
    if settings.env not in {"test", "ci"}:
        attach_query_guard(
            get_engine().sync_engine,
            dev_mode=(settings.env == "development"),
        )
    # Skip background workers in test/CI runs — pytest imports app.main and we
    # don't want Binance WS connections racing the test event loop. Set
    # ENV=test (or WORKER_ENABLED=false) in pytest fixtures or CI to disable.
    live_worker = None
    shadow_worker = None
    universe_sync_task = None
    health_pinger_task = None
    audit_verifier_task = None
    news_ingest_task = None
    news_cleanup_task = None
    intermarket_snapshot_task = None
    intermarket_cleanup_task = None
    universe_refresh_task = None
    universe_refreshed_event: asyncio.Event | None = None
    auto_promote_task = None
    liquidation_monitor_task = None
    live_exit_monitor_task = None  # PR8 — TP/SL/timeout/external classification
    telegram_poller_task = None
    worker_watchdog_task = None
    scanner_batch_task = None
    prediction_validator_task = None
    ws_keepalive_task = None
    mtf_cache_prewarm_task = None
    mtf_cache_ttl_refresh_task = None
    symbol_allowlist_task = None
    ui_freshness_monitor_task = None  # PR10.5 / FU-28
    if settings.env not in {"test", "ci"} and settings.worker_enabled:
        # SP-1 §6.1: pin the active ML checkpoint at startup so the live
        # worker can call predict_ghost_candle. No active row → log warning
        # and continue (worker degrades to no-ghost mode automatically).
        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                await load_active_checkpoint(session)
        except Exception as e:  # noqa: BLE001
            log.warning("load_active_checkpoint failed at startup: %s", e)
        # SP-4 §6.1: load the active RL brain checkpoint. This is separate
        # from the ML (ConvLSTM L8) loader above. Without this call,
        # get_active_policy_and_checkpoint() always returns None and every
        # prediction silently uses brain_adjust=1.0 (equal-weight no-op).
        try:
            from app.rl.policy import PolicyNetwork
            async with session_factory() as session:
                await load_rl_active_checkpoint(session, model_factory=PolicyNetwork)
        except Exception as e:  # noqa: BLE001
            log.warning("load_rl_active_checkpoint failed at startup: %s", e)
        live_worker = start_background_worker()
        shadow_worker = start_shadow_worker()
        # SP-3.5 / shadow: daily 00:00 UTC asset_universe refresh — top-30
        # USDT-quoted Binance Futures perpetuals by 24h volume. The shadow
        # worker reads this table at startup; without this task the table
        # stays empty in production, the worker falls back to BTCUSDT-only,
        # and SP-8's promotion gates (which need 100+ trades on top-30) can
        # never accumulate. The orphaned task was caught while planning SP-8.
        universe_refreshed_event = asyncio.Event()
        universe_refresh_task = start_universe_refresh_task(
            get_session_factory(), universe_refreshed_event,
        )
        # The non-stateful workers below register themselves with
        # worker_supervisor so that worker_watchdog can replace a dead
        # task with a fresh one (self-healing). The supervisor is a
        # process-local registry of name->factory mappings — see
        # app/ops/worker_supervisor.py for the safety contract on what
        # is safe to register here.
        def _wrap(name: str, factory: Any) -> asyncio.Task[None]:
            return worker_supervisor.register(name, factory)

        # SP-3 Phase F: daily 02:00 UTC universe sync across all registered
        # adapters. Skipped in test/ci so the test event loop isn't racing
        # background tasks.
        universe_sync_task = _wrap(
            "universe_sync_task",
            lambda: start_universe_sync_task(get_session_factory()),
        )
        # SP-3 Phase F: every 5 min ping each adapter's health endpoint and
        # write to adapter_health (read by /api/v1/admin/adapters/health).
        health_pinger_task = _wrap(
            "health_pinger_task",
            lambda: start_health_pinger_task(get_session_factory()),
        )
        # SP-7 Phase D3: nightly 03:00 UTC audit hash-chain verifier across
        # the chained tables (predictions, paper_trades, shadow_trades). Any
        # detected break triggers alert_admin + an auth_violations row with
        # attempted_email='system'. Skipped in test/ci so the suite doesn't
        # carry the nightly background overhead.
        audit_verifier_task = _wrap(
            "audit_verifier_task",
            lambda: start_audit_verifier_task(get_session_factory()),
        )
        # SP-9 Phase D4: news ingest (5min crypto / 30min macro) + nightly
        # 04:00 UTC retention cleanup. Both are gated on the same env/worker
        # check so test/ci never hits CryptoPanic or downloads FinBERT.
        news_ingest_task = _wrap(
            "news_ingest_task",
            lambda: start_news_ingest_task(get_session_factory()),
        )
        news_cleanup_task = _wrap(
            "news_cleanup_task",
            lambda: start_news_cleanup_task(get_session_factory()),
        )
        # SP-3.5: 5min funding/OI snapshot worker + nightly 04:30 UTC cleanup.
        # Skipped in test/ci (no FAPI calls, no DB churn during pytest).
        intermarket_snapshot_task = _wrap(
            "intermarket_snapshot_task",
            lambda: start_intermarket_snapshot_task(get_session_factory()),
        )
        intermarket_cleanup_task = _wrap(
            "intermarket_cleanup_task",
            lambda: start_intermarket_cleanup_task(get_session_factory()),
        )
        # PR #97: 5-min watchdog that reads each worker's liveness signal
        # (DB MAX(timestamp) for workers with natural signals; heartbeats
        # for the rest), alerts via SMTP on staleness, and AUTO-RESTARTS
        # registered non-stateful workers via worker_supervisor.
        # Stateful workers (live_worker, shadow_worker, liquidation_monitor,
        # telegram_poller, ws_keepalive_task) stay alert-only.
        worker_watchdog_task = start_worker_watchdog(get_session_factory())
        # Feature 4 — multi-asset fast scanner. Every 60s, fetches klines
        # for the asset_universe (or fallback watchlist) and runs the
        # deterministic indicator-only fast_scan. Results cached in a
        # module-level dict; the /api/v1/scanner/fast endpoint reads
        # from that cache so requests are O(1).
        scanner_batch_task = _wrap(
            "scanner_batch_task",
            lambda: start_scanner_batch_task(get_session_factory()),
        )
        # Feature 2: 60s prediction validator. Picks up rows from
        # prediction_validations where target_ts has passed and the
        # actual close is available; computes was_correct + pnl_pct
        # so the chart UI can surface live accuracy telemetry.
        prediction_validator_task = _wrap(
            "prediction_validator_task",
            lambda: start_prediction_validator_task(get_session_factory()),
        )
        # Server-side WS keepalive — fans live-prediction WS subscriptions
        # across top-N universe so prediction_validations is populated 24/7
        # without anyone leaving a browser tab open. Replaces the
        # "open chart, leave tab open" trick we relied on before.
        ws_keepalive_task = start_keepalive_task(get_session_factory())

        # PR1 Phase 3: MTF kline cache workers. The pre-warm is single-shot
        # (60s deadline, fail-open); the TTL-refresh loop runs indefinitely,
        # refreshing entries within 20% of expiry every 30s. Both registered
        # in worker_registry.py (Correction 2 — no orphan tasks).
        mtf_cache_prewarm_task = start_mtf_cache_prewarm_task(
            get_session_factory(),
        )
        mtf_cache_ttl_refresh_task = start_mtf_cache_ttl_refresh_task(
            get_session_factory(),
        )

        # PR10: daily symbol allowlist refresh worker. NOT gated on
        # AUTONOMOUS_TRADING_ENABLED — snapshots are useful in all modes
        # (manual / telegram-approve / fully-auto). The dispatcher gate
        # consumes these snapshots only when SYMBOL_ALLOWLIST_ENABLED=True.
        from app.workers.symbol_allowlist_refresh import (
            start_symbol_allowlist_refresh,
        )
        from app.config import get_settings as _get_pr10_for_loop
        symbol_allowlist_task = start_symbol_allowlist_refresh(
            get_session_factory(), _get_pr10_for_loop,
        )

        # PR10.5 / FU-28 — UI data-pipeline freshness monitor. NOT gated
        # on AUTONOMOUS_TRADING; observability is useful in all modes.
        from app.workers.ui_freshness_monitor import (
            start_ui_freshness_monitor,
        )
        from app.config import get_settings as _get_fu28_settings
        ui_freshness_monitor_task = start_ui_freshness_monitor(
            get_session_factory(), _get_fu28_settings,
        )

        # SP-8 Phase J: gate the autonomous-trading subsystem on
        # AUTONOMOUS_TRADING_ENABLED + a passing pre-flight. Pre-flight
        # validates passphrase, vault decrypt, Binance permissions
        # (sec 9.3), migration applied, and audit chain intact. Any
        # failure means the live workers do NOT start; paper trading
        # + ghost candles + dashboard keep running normally.
        if settings.autonomous_trading_enabled:
            try:
                # PR-DECOUPLE-WORKERS: split the gate into two profiles.
                # The reader profile (4 checks) gates safety-net worker
                # spawn. The chain_intact check is run SEPARATELY purely
                # for differential alerting — a broken chain no longer
                # kills the safety net. Only run the chain check when
                # reader passed; if reader failed the spawn aborts anyway
                # and the chain status is irrelevant to alerting.
                async with session_factory() as preflight_session:
                    pf_reader = await run_preflight(
                        preflight_session,
                        use_testnet=settings.binance_use_testnet,
                        profile="chain_reader",
                    )
                    if pf_reader.all_passed:
                        chain_check = await check_audit_chain_intact(
                            preflight_session,
                        )
                    else:
                        chain_check = None
                if pf_reader.all_passed and chain_check is not None and chain_check.passed:
                    # Happy path: all 5 effective checks passed.
                    log.info(
                        "autonomous trading: pre-flight passed (%s + "
                        "audit_chain_intact)",
                        pf_reader.summary_line(),
                    )
                    # PR-PREFLIGHT-ALERT Fix C / PR-DECOUPLE-WORKERS:
                    # heartbeat row marking the preflight as "ran and
                    # passed". profile='chain_writer' indicates full
                    # 5-check success.
                    try:
                        await _record_heartbeat(
                            session_factory, _PREFLIGHT_WORKER_NAME,
                            status="passed",
                            details={
                                "profile": "chain_writer",
                                "passed_count": len(pf_reader.checks) + 1,
                                "total_count": len(pf_reader.checks) + 1,
                            },
                        )
                    except Exception as hb_exc:  # noqa: BLE001
                        log.warning(
                            "preflight pass heartbeat write failed: %s",
                            hb_exc,
                        )
                if pf_reader.all_passed:
                    # PR-DECOUPLE-WORKERS partial-pass branch: reader
                    # checks all green but the chain WRITER is blocked
                    # (FU-24 race). Safety-net workers are SAFE to spawn
                    # because they do not insert chained rows. Emit a
                    # different alert so the operator knows the bot is
                    # partially functional and what to do (FU-24 sweep).
                    assert chain_check is not None  # guaranteed by branch above
                    if not chain_check.passed:
                        log.warning(
                            "autonomous trading: chain WRITER blocked "
                            "(FU-24); safety-net OK — %s",
                            chain_check.detail,
                        )
                        try:
                            await _route_alert(
                                (
                                    "⚠️ Audit chain WRITER blocked "
                                    "(FU-24 race active). Safety-net "
                                    "workers RUNNING. Run FU-24 sweep "
                                    "when convenient. Live writes "
                                    "blocked until chain healed."
                                ),
                                level="critical",
                            )
                        except Exception as alert_exc:  # noqa: BLE001
                            log.error(
                                "chain-writer-blocked alert dispatch "
                                "failed: %s",
                                alert_exc,
                            )
                        try:
                            await _record_heartbeat(
                                session_factory, _PREFLIGHT_WORKER_NAME,
                                status="reader_only_passed",
                                details={
                                    "profile": "chain_reader",
                                    "passed_count": len(pf_reader.checks),
                                    "total_count": len(pf_reader.checks) + 1,
                                    "failed_checks": [chain_check.name],
                                    "failed_check_details": {
                                        chain_check.name: chain_check.detail,
                                    },
                                },
                            )
                        except Exception as hb_exc:  # noqa: BLE001
                            log.warning(
                                "preflight reader_only_passed heartbeat "
                                "write failed: %s",
                                hb_exc,
                            )
                    # SP-8 Phase J: cache the decrypted Binance keys at
                    # module level. The live worker calls vault_keys() on
                    # every tick — a cache miss returns None and the worker
                    # silently skips dispatch.
                    secrets_path = Path(
                        os.environ.get("VAULT_SECRETS_PATH", "/app/secrets.enc"),
                    )
                    vault_ok = initialize_vault_cache(
                        passphrase=settings.master_passphrase,
                        secrets_path=secrets_path,
                    )
                    if vault_ok:
                        # SP-8 Phase J: liquidation monitor — 30s poll of
                        # all open live_trades. Auto-closes at <10% buffer
                        # (more aggressive than spec — operator request to
                        # avoid Binance forced-liquidation fees).
                        keys = vault_keys()
                        assert keys is not None  # vault_ok=True guarantees this

                        def _binance_factory() -> BinanceLiveClient:
                            return BinanceLiveClient(
                                api_key=keys.binance_api_key,
                                api_secret=keys.binance_api_secret,
                                use_testnet=settings.binance_use_testnet,
                            )

                        liquidation_monitor_task = start_liquidation_monitor(
                            session_factory, _binance_factory,
                        )
                        log.warning(
                            "autonomous trading: vault cached + liquidation "
                            "monitor running (testnet=%s)",
                            settings.binance_use_testnet,
                        )

                        # PR8 — start the live_exit_monitor alongside the
                        # liquidation monitor. Same binance_factory, same
                        # session_factory. The settings_factory closure
                        # lets the loop re-read get_settings() each tick
                        # (operator can flip LIVE_COOLDOWN_HOURS_BY_OUTCOME
                        # via env + restart; the loop picks it up).
                        from app.config import get_settings as _get_pr8_settings_for_loop
                        live_exit_monitor_task = start_live_exit_monitor(
                            session_factory, _binance_factory,
                            _get_pr8_settings_for_loop,
                        )
                        log.warning(
                            "autonomous trading: live_exit_monitor running "
                            "(LIVE_COOLDOWN_ENABLED=%s)",
                            _get_pr8_settings_for_loop().LIVE_COOLDOWN_ENABLED,
                        )

                        # SP-8 Phase J: Telegram polling worker. Routes
                        # both sig:* (trade approvals) and rl_* (brain
                        # checkpoint approvals) callbacks. Without bot
                        # creds set, log + skip — the rest of the
                        # autonomous-trading subsystem still works in
                        # fully-auto / manual modes.
                        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
                        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
                        if bot_token and chat_id:
                            poller_cfg = PollerConfig(
                                bot_token=bot_token, chat_id=chat_id,
                                backend_internal_url=os.environ.get(
                                    "BACKEND_INTERNAL_URL",
                                    "http://localhost:8000",
                                ),
                            )
                            telegram_poller_task = start_telegram_poller(
                                session_factory,
                                config=poller_cfg,
                                binance_factory=_binance_factory,
                                use_testnet=settings.binance_use_testnet,
                                user_id=1,  # bootstrap admin
                            )
                            log.warning(
                                "telegram poller running (chat_id=%s)",
                                chat_id,
                            )
                        else:
                            log.info(
                                "telegram poller skipped: "
                                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
                                "not set",
                            )
                    else:
                        log.error(
                            "autonomous trading: vault decrypt failed at "
                            "startup; live workers will skip dispatch",
                        )
                else:
                    # PR-DECOUPLE-WORKERS: reader-side failure path —
                    # uses the existing PR-PREFLIGHT-ALERT semantics
                    # unchanged. Safety-net workers do NOT spawn.
                    failed_checks = [c.name for c in pf_reader.failures()]
                    failures = "; ".join(
                        f"{c.name}={c.detail}" for c in pf_reader.failures()
                    )
                    # PR-PREFLIGHT-ALERT Fix A: elevate to CRITICAL so an
                    # unconfigured-telegram operator still sees a level
                    # change in container logs (pre-PR was ERROR-only).
                    log.critical(
                        "autonomous trading DISABLED: pre-flight failed "
                        "(%d/%d) — %s",
                        len(pf_reader.failures()), len(pf_reader.checks),
                        failures,
                    )
                    passed_count = (
                        len(pf_reader.checks) - len(pf_reader.failures())
                    )
                    total_count = len(pf_reader.checks)
                    # PR-PREFLIGHT-ALERT Fix A — telegram alert.
                    # Best-effort; the alerter must NEVER kill lifespan.
                    try:
                        await _route_alert(
                            (
                                f"⚠️ Preflight FAILED "
                                f"({passed_count}/{total_count}) — "
                                f"autonomous workers NOT spawned. "
                                f"Failed checks: "
                                f"{', '.join(failed_checks)}. "
                                f"Investigate before next deploy."
                            ),
                            level="critical",
                        )
                    except Exception as alert_exc:  # noqa: BLE001
                        log.error(
                            "preflight-alert dispatch failed: %s",
                            alert_exc,
                        )
                    # PR-PREFLIGHT-ALERT Fix B — heartbeat row.
                    # record_heartbeat itself swallows internally; wrap
                    # in try/except for defense-in-depth so any
                    # unforeseen exception still doesn't break lifespan.
                    try:
                        await _record_heartbeat(
                            session_factory, _PREFLIGHT_WORKER_NAME,
                            status="failed",
                            details={
                                "passed_count": passed_count,
                                "total_count": total_count,
                                "failed_checks": failed_checks,
                                "failed_check_details": {
                                    c.name: c.detail
                                    for c in pf_reader.failures()
                                },
                            },
                        )
                    except Exception as hb_exc:  # noqa: BLE001
                        log.warning(
                            "preflight fail heartbeat write failed: %s",
                            hb_exc,
                        )
            except Exception as e:  # noqa: BLE001
                # PR-PREFLIGHT-ALERT Fix D: exception path. Elevate to
                # CRITICAL + alert + heartbeat (status='raised').
                # Both alert + heartbeat wrapped so a failed alerter or
                # heartbeat write cannot kill the lifespan.
                log.critical(
                    "autonomous trading pre-flight raised: %s", e,
                )
                try:
                    await _route_alert(
                        (
                            f"⚠️ Preflight RAISED: "
                            f"{type(e).__name__}: {str(e)[:200]}"
                        ),
                        level="critical",
                    )
                except Exception as alert_exc:  # noqa: BLE001
                    log.error(
                        "preflight-raised alert dispatch failed: %s",
                        alert_exc,
                    )
                try:
                    await _record_heartbeat(
                        session_factory, _PREFLIGHT_WORKER_NAME,
                        status="raised",
                        details={
                            "error_type": type(e).__name__,
                            "error_msg": str(e)[:500],
                        },
                    )
                except Exception as hb_exc:  # noqa: BLE001
                    log.warning(
                        "preflight raised heartbeat write failed: %s",
                        hb_exc,
                    )

            # SP-8 Phase J.2: daily auto-promotion worker. Independent of
            # pre-flight — auto-promotion only changes mode rows; it does
            # NOT execute trades on its own. The Telegram-approve / Fully-
            # auto modes themselves still need the live-trading workers
            # to actually place orders. Safe to start even when pre-flight
            # didn't pass: a mode change with no live worker is a no-op.
            ap_cfg = AutoPromoteConfig(
                to_telegram_enabled=settings.auto_promote_to_telegram_enabled,
                to_fullyauto_enabled=settings.auto_promote_to_fullyauto_enabled,
                consecutive_days=settings.auto_promote_consecutive_days,
            )
            if ap_cfg.any_enabled:
                auto_promote_task = start_auto_promote_task(
                    get_session_factory(), ap_cfg,
                )
                log.warning(
                    "auto-promote ENABLED: telegram=%s fullyauto=%s "
                    "consecutive_days=%d. Daily 03:30 UTC tick.",
                    ap_cfg.to_telegram_enabled,
                    ap_cfg.to_fullyauto_enabled,
                    ap_cfg.consecutive_days,
                )
    try:
        yield
    finally:
        if live_worker is not None:
            live_worker.cancel()
        if shadow_worker is not None:
            shadow_worker.cancel()
        if universe_sync_task is not None:
            universe_sync_task.cancel()
        if universe_refresh_task is not None:
            universe_refresh_task.cancel()
        if health_pinger_task is not None:
            health_pinger_task.cancel()
        if audit_verifier_task is not None:
            audit_verifier_task.cancel()
        if news_ingest_task is not None:
            news_ingest_task.cancel()
        if news_cleanup_task is not None:
            news_cleanup_task.cancel()
        if intermarket_snapshot_task is not None:
            intermarket_snapshot_task.cancel()
        if intermarket_cleanup_task is not None:
            intermarket_cleanup_task.cancel()
        if auto_promote_task is not None:
            auto_promote_task.cancel()
        if liquidation_monitor_task is not None:
            liquidation_monitor_task.cancel()
        if live_exit_monitor_task is not None:
            live_exit_monitor_task.cancel()
        if telegram_poller_task is not None:
            telegram_poller_task.cancel()
        if worker_watchdog_task is not None:
            worker_watchdog_task.cancel()
        if scanner_batch_task is not None:
            scanner_batch_task.cancel()
        if prediction_validator_task is not None:
            prediction_validator_task.cancel()
        if ws_keepalive_task is not None:
            ws_keepalive_task.cancel()
        if mtf_cache_prewarm_task is not None:
            mtf_cache_prewarm_task.cancel()
        if mtf_cache_ttl_refresh_task is not None:
            mtf_cache_ttl_refresh_task.cancel()
        if symbol_allowlist_task is not None:
            symbol_allowlist_task.cancel()
        if ui_freshness_monitor_task is not None:
            ui_freshness_monitor_task.cancel()
        await _aclose_adapters()


def create_app() -> FastAPI:
    app = FastAPI(
        title="trading-radar",
        version="0.1.0-sp-0",
        lifespan=lifespan,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )
    settings = get_settings()
    if settings.env == "development":
        # Local Vite dev server runs on a different port — needs CORS.
        # Production sits behind Cloudflare with same-origin routing.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    # SP-PAUSE: 423 gate for non-allow-listed requests when paused. Must
    # sit before instrument_app so Prometheus observes the 423 path.
    register_pause_middleware(app)
    app.include_router(health.router)
    app.include_router(tab1.router)
    app.include_router(bot_status.router)
    app.include_router(admin.router)
    app.include_router(admin_adapters.router)
    app.include_router(admin_backtest.router)  # SP-7 Phase B5
    app.include_router(admin_hyperopt.router)  # SP-7 Phase C4
    app.include_router(admin_ml.router)
    app.include_router(admin_monitoring.router)  # SP-7 Phase G3
    app.include_router(admin_rl.router)  # SP-4 Phase D
    app.include_router(admin_news.router)  # SP-9 Phase F5
    app.include_router(admin_patterns.router)
    app.include_router(admin_system.router)  # SP-PAUSE
    app.include_router(admin_test_trade.router)  # testnet smoke test
    app.include_router(admin_traps.router)
    app.include_router(me.router)
    app.include_router(predictions.router)  # Feature 2 — accuracy telemetry
    app.include_router(scanner.router)  # SP-6
    app.include_router(scanner_fast.router)  # Feature 4 — fast scanner
    app.include_router(intermarket.router)  # SP-3.5
    app.include_router(ws_routes.router)
    # SP-7 Phase F4: Prometheus instrumentation must happen AFTER every
    # router is added so every route is observed by the middleware.
    instrument_app(app)
    return app


app = create_app()
