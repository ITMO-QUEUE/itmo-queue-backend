import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from itmo_queue.api.router import api_router
from itmo_queue.core.config import Settings, get_settings


class HealthResponse(BaseModel):
    """HTTP application liveness; does not describe dependency readiness."""

    status: Literal["ok"] = Field(description="HTTP application is running.", examples=["ok"])


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

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "ITMO.Queue REST API. Business endpoints use the /api/v1 prefix. "
            "OpenAPI is the source of client contracts. "
            "The /health endpoint checks HTTP liveness only."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        openapi_tags=[
            {"name": "Infrastructure", "description": "HTTP application liveness checks."}
        ],
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.include_router(api_router)

    @app.get(
        "/health",
        response_model=HealthResponse,
        operation_id="getHealth",
        response_description="HTTP application is running.",
        tags=["Infrastructure"],
        summary="HTTP liveness check",
        description=(
            "Checks the HTTP application without contacting PostgreSQL or Redis. "
            "No request body or parameters. No endpoint-specific error responses. "
            "Does not check database or cache readiness."
        ),
    )
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    return app


app = create_app()
