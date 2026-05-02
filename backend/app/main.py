from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config import get_settings
from app.api.routes import health, tab1
from app.api.routes import ws as ws_routes
from app.ws.live_prediction import start_background_worker


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    # Skip the live worker in test/CI runs — pytest imports app.main and we don't
    # want a Binance WS connection racing the test event loop. Set ENV=test (or
    # WORKER_ENABLED=false) in pytest fixtures or CI to disable.
    worker = None
    if settings.env not in {"test", "ci"} and settings.worker_enabled:
        worker = start_background_worker()
    try:
        yield
    finally:
        if worker is not None:
            worker.cancel()


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
    app.include_router(ws_routes.router)
    return app


app = create_app()
