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
    scheduler = None
    bus = None
    try:
        # Idempotent create_all keeps single-node deployments (compose, demo)
        # zero-touch. Alembic owns the schema once migrations are introduced.
        from app.db.session import init_models

        await init_models()
        bus = await _connect_event_bus(settings)
        from app.monitor.scheduler import start_scheduler

        scheduler = start_scheduler(settings)
    except Exception:  # noqa: BLE001 — serve REST even if the twin loop can't start
        import logging

        logging.getLogger(__name__).exception(
            "scheduler unavailable (database down?); API will serve without live updates"
        )
    yield
    if scheduler is not None:
        from app.monitor.scheduler import stop_scheduler

        await stop_scheduler()
    if bus is not None:
        await bus.stop()


async def _connect_event_bus(settings):
    """Use Redis pub/sub when reachable (multi-replica fan-out); otherwise keep
    the default in-process bus. Returns the RedisBus, or None for in-memory."""
    import logging

    from redis.asyncio import from_url

    from app.events.bus import RedisBus, set_bus

    log = logging.getLogger(__name__)
    try:
        client = from_url(settings.redis_url)
        await client.ping()
    except Exception:  # noqa: BLE001 — Redis is optional, never fatal
        log.warning("redis unavailable at %s; using in-process event bus", settings.redis_url)
        return None
    bus = RedisBus(client)
    await bus.start()
    set_bus(bus)
    log.info("event bus: redis pub/sub (%s)", settings.redis_url)
    return bus


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

    from app.events.websocket import router as ws_router

    app.include_router(ws_router)
    return app


app = create_app()
