from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config import get_settings
from app.api.routes import health, tab1


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _ = get_settings()
    yield


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
    return app


app = create_app()
