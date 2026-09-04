"""net-twin backend application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.core.config import get_settings
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    # Development convenience: ensure tables exist. Production schema is
    # managed by Alembic migrations.
    if settings.app_env == "development":
        from app.db.session import init_models

        await init_models()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="net-twin",
        description="Real-time digital twin for network topology discovery and monitoring",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # tighten in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    return app


app = create_app()
