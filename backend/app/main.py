import asyncio
import logging
import os
from contextlib import asynccontextmanager

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
    admin_traps,
    bot_status,
    health,
    intermarket,  # SP-3.5 Phase E2
    me,
    scanner,  # SP-6 Phase A4
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
from app.news.ingest_worker import (
    start_news_cleanup_task,
    start_news_ingest_task,
)
from app.ops.monitoring import instrument_app
from app.ops.verifier_scheduler import start_audit_verifier_task
from app.shadow.worker import start_shadow_worker
from app.trading.auto_promote import (
    AutoPromoteConfig,
    start_auto_promote_task,
)
from app.trading.preflight import run_preflight
from app.ws.live_prediction import start_background_worker

# Configure root logger from LOG_LEVEL env var. docker-compose passes
# LOG_LEVEL=INFO from .env, but Python's default is WARNING for unconfigured
# loggers, so app.* INFO lines (including "loaded active checkpoint") were
# silently dropped in production. Uvicorn configures its own loggers
# separately so this only affects app.* — no conflict.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger(__name__)


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
        # SP-3 Phase F: daily 02:00 UTC universe sync across all registered
        # adapters. Skipped in test/ci so the test event loop isn't racing
        # background tasks.
        universe_sync_task = start_universe_sync_task(get_session_factory())
        # SP-3 Phase F: every 5 min ping each adapter's health endpoint and
        # write to adapter_health (read by /api/v1/admin/adapters/health).
        health_pinger_task = start_health_pinger_task(get_session_factory())
        # SP-7 Phase D3: nightly 03:00 UTC audit hash-chain verifier across
        # the chained tables (predictions, paper_trades, shadow_trades). Any
        # detected break triggers alert_admin + an auth_violations row with
        # attempted_email='system'. Skipped in test/ci so the suite doesn't
        # carry the nightly background overhead.
        audit_verifier_task = start_audit_verifier_task(get_session_factory())
        # SP-9 Phase D4: news ingest (5min crypto / 30min macro) + nightly
        # 04:00 UTC retention cleanup. Both are gated on the same env/worker
        # check so test/ci never hits CryptoPanic or downloads FinBERT.
        news_ingest_task = start_news_ingest_task(get_session_factory())
        news_cleanup_task = start_news_cleanup_task(get_session_factory())
        # SP-3.5: 5min funding/OI snapshot worker + nightly 04:30 UTC cleanup.
        # Skipped in test/ci (no FAPI calls, no DB churn during pytest).
        intermarket_snapshot_task = start_intermarket_snapshot_task(get_session_factory())
        intermarket_cleanup_task = start_intermarket_cleanup_task(get_session_factory())

        # SP-8 Phase J: gate the autonomous-trading subsystem on
        # AUTONOMOUS_TRADING_ENABLED + a passing pre-flight. Pre-flight
        # validates passphrase, vault decrypt, Binance permissions
        # (sec 9.3), migration applied, and audit chain intact. Any
        # failure means the live workers do NOT start; paper trading
        # + ghost candles + dashboard keep running normally.
        if settings.autonomous_trading_enabled:
            try:
                async with session_factory() as preflight_session:
                    pf = await run_preflight(
                        preflight_session, use_testnet=settings.binance_use_testnet,
                    )
                if pf.all_passed:
                    log.info(
                        "autonomous trading: pre-flight passed (%s); "
                        "live workers ready (wiring in next commit)",
                        pf.summary_line(),
                    )
                    # The actual live execution worker, telegram-approve
                    # poller extension, and liquidation monitor are wired
                    # in subsequent Phase J commits — keeping each new
                    # task in its own commit so a single regression can
                    # be reverted independently.
                else:
                    failures = "; ".join(
                        f"{c.name}={c.detail}" for c in pf.failures()
                    )
                    log.error(
                        "autonomous trading DISABLED: pre-flight failed "
                        "(%d/%d) — %s",
                        len(pf.failures()), len(pf.checks), failures,
                    )
            except Exception as e:  # noqa: BLE001
                log.error(
                    "autonomous trading pre-flight raised: %s", e,
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
    app.include_router(admin_traps.router)
    app.include_router(me.router)
    app.include_router(scanner.router)  # SP-6
    app.include_router(intermarket.router)  # SP-3.5
    app.include_router(ws_routes.router)
    # SP-7 Phase F4: Prometheus instrumentation must happen AFTER every
    # router is added so every route is observed by the middleware.
    instrument_app(app)
    return app


app = create_app()
