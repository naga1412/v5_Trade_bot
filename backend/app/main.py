import logging
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
    admin_patterns,
    admin_traps,
    bot_status,
    health,
    me,
    scanner,  # SP-6 Phase A4
    tab1,
)
from app.api.routes import ws as ws_routes
from app.auth.query_guard import attach_query_guard
from app.config import get_settings
from app.data.adapter_health import start_health_pinger_task
from app.data.adapters import aclose_all as _aclose_adapters
from app.data.universe_sync import start_universe_sync_task
from app.db.session import get_engine, get_session_factory
from app.ml.checkpoints import load_active_checkpoint
from app.news.ingest_worker import (
    start_news_cleanup_task,
    start_news_ingest_task,
)
from app.ops.monitoring import instrument_app
from app.ops.verifier_scheduler import start_audit_verifier_task
from app.shadow.worker import start_shadow_worker
from app.ws.live_prediction import start_background_worker

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
    try:
        yield
    finally:
        if live_worker is not None:
            live_worker.cancel()
        if shadow_worker is not None:
            shadow_worker.cancel()
        if universe_sync_task is not None:
            universe_sync_task.cancel()
        if health_pinger_task is not None:
            health_pinger_task.cancel()
        if audit_verifier_task is not None:
            audit_verifier_task.cancel()
        if news_ingest_task is not None:
            news_ingest_task.cancel()
        if news_cleanup_task is not None:
            news_cleanup_task.cancel()
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
    app.include_router(health.router)
    app.include_router(tab1.router)
    app.include_router(bot_status.router)
    app.include_router(admin.router)
    app.include_router(admin_adapters.router)
    app.include_router(admin_backtest.router)  # SP-7 Phase B5
    app.include_router(admin_hyperopt.router)  # SP-7 Phase C4
    app.include_router(admin_ml.router)
    app.include_router(admin_monitoring.router)  # SP-7 Phase G3
    app.include_router(admin_patterns.router)
    app.include_router(admin_traps.router)
    app.include_router(me.router)
    app.include_router(scanner.router)  # SP-6
    app.include_router(ws_routes.router)
    # SP-7 Phase F4: Prometheus instrumentation must happen AFTER every
    # router is added so every route is observed by the middleware.
    instrument_app(app)
    return app


app = create_app()
