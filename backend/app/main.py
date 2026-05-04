from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import bot_status, health, tab1
from app.api.routes import ws as ws_routes
from app.config import get_settings
from app.shadow.worker import start_shadow_worker
from app.ws.live_prediction import start_background_worker


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
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
    app.include_router(health.router)
    app.include_router(tab1.router)
    app.include_router(bot_status.router)
    app.include_router(ws_routes.router)
    return app


app = create_app()
