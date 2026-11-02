import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from itmo_queue.api.router import api_router
from itmo_queue.core.config import Settings, get_settings


class HealthResponse(BaseModel):
    status: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(level=settings.log_level)
        # These constructors create lazy pools; startup does not contact services.
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        try:
            redis = Redis.from_url(str(settings.redis_url), decode_responses=True)
            try:
                app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
                app.state.redis = redis
                yield
            finally:
                await redis.aclose()
        finally:
            await engine.dispose()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.include_router(api_router)

    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["Infrastructure"],
        summary="HTTP liveness check",
        description="Checks the HTTP application without contacting PostgreSQL or Redis.",
    )
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    return app


app = create_app()
