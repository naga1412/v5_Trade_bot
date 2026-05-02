from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config import get_settings
from app.api.routes import health, tab1
from app.api.routes import ws as ws_routes
from app.ws.live_prediction import start_background_worker


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _ = get_settings()
    worker = start_background_worker()
    try:
        yield
    finally:
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
