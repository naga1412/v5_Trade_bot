from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import bot_status, health, tab1
from app.api.routes import ws as ws_routes
from app.auth.query_guard import attach_query_guard
from app.config import get_settings
from app.db.session import get_engine
from app.shadow.worker import start_shadow_worker
from app.ws.live_prediction import start_background_worker


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
    if settings.env not in {"test", "ci"} and settings.worker_enabled:
        live_worker = start_background_worker()
        shadow_worker = start_shadow_worker()
    try:
        yield
    finally:
        if live_worker is not None:
            live_worker.cancel()
        if shadow_worker is not None:
            shadow_worker.cancel()


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
    app.include_router(ws_routes.router)
    return app


app = create_app()
