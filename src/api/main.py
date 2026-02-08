"""Litestar application factory for the Multi-Agent Service API.

Launch with::

    litestar --app src.api.main:app run --reload

Features:
- JWT authentication (``JWTAuth[User]``)
- CORS configuration
- ChannelsPlugin for WebSocket real-time events
- Rate-limiting middleware
- Structured exception handlers for the MAS exception hierarchy
- OpenAPI 3.1 documentation at ``/schema``
- Lifespan hooks for DB engine and Valkey connection management
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from litestar import Litestar, MediaType, Request, Response
from litestar.channels import ChannelsPlugin
from litestar.exceptions import HTTPException
from litestar.channels.backends.redis import RedisChannelsPubSubBackend
from litestar.config.cors import CORSConfig
from litestar.middleware.rate_limit import RateLimitConfig
from litestar.openapi import OpenAPIConfig
from sqlalchemy import text as sa_text

from src.api.dependencies import provide_db_session, provide_settings, provide_valkey
from src.api.guards import jwt_auth
from src.api.routes.agents import AgentController
from src.api.routes.auth import AuthController
from src.api.routes.health import health_check
from src.api.routes.hitl import HITLController
from src.api.routes.jobs import JobController
from src.api.routes.metrics import MetricsController
from src.api.routes.users import UserController
from src.api.schemas import ErrorResponseSchema, ErrorSchema
from src.api.websocket import (
    CHANNEL_AGENT_HEARTBEAT,
    CHANNEL_AGENT_LOG,
    CHANNEL_HITL_NEW,
    CHANNEL_HITL_RESOLVED,
    CHANNEL_NOTIFICATION,
    CHANNEL_PROJECT_UPDATE,
    ws_handler,
)
from src.core.config import get_settings
from src.core.database import engine, get_valkey
from src.core.exceptions import MASException
from src.monitoring.sentry_config import init_sentry

logger = structlog.get_logger(__name__)


# =============================================================================
# Exception handlers
# =============================================================================


def _mas_exception_handler(request: Request, exc: MASException) -> Response[ErrorResponseSchema]:
    """Map any ``MASException`` (and subclasses) to a structured JSON error."""
    details = exc.details or {}
    status_code = int(details.pop("status_code", 500))
    error_code = str(details.pop("error_code", type(exc).__name__))

    return Response(
        content=ErrorResponseSchema(
            error=ErrorSchema(
                code=error_code,
                message=str(exc),
                details=details,
            ),
        ),
        status_code=status_code,
        media_type=MediaType.JSON,
    )


def _http_exception_handler(request: Request, exc: HTTPException) -> Response[ErrorResponseSchema]:
    """Return structured JSON for Litestar HTTP exceptions (404, 405, etc.)."""
    return Response(
        content=ErrorResponseSchema(
            error=ErrorSchema(
                code=f"HTTP_{exc.status_code}",
                message=exc.detail if exc.detail else str(exc),
                details={},
            ),
        ),
        status_code=exc.status_code,
        media_type=MediaType.JSON,
    )


def _generic_exception_handler(request: Request, exc: Exception) -> Response[ErrorResponseSchema]:
    """Catch-all for unexpected exceptions so the API never leaks stack traces."""
    logger.error("unhandled_exception", error=str(exc), exc_info=exc)
    return Response(
        content=ErrorResponseSchema(
            error=ErrorSchema(
                code="INTERNAL_ERROR",
                message="An unexpected error occurred",
                details={},
            ),
        ),
        status_code=500,
        media_type=MediaType.JSON,
    )


# =============================================================================
# Lifespan
# =============================================================================


@asynccontextmanager
async def lifespan(app: Litestar) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown of long-lived resources.

    On startup:
        - Verify the database engine can connect.
        - Verify Valkey is reachable.
    On shutdown:
        - Dispose the SQLAlchemy async engine.
        - Close the Valkey connection pool.
    """
    settings = get_settings()

    # ── Startup ─────────────────────────────────────────────────────
    logger.info("app.starting", version=settings.APP_VERSION)

    # Verify DB
    try:
        async with engine.connect() as conn:
            await conn.execute(sa_text("SELECT 1"))
        logger.info("app.db_connected", url=settings.DATABASE_URL.split("@")[-1])
    except Exception as exc:
        logger.error("app.db_connection_failed", error=str(exc))

    # Verify Valkey
    valkey = get_valkey()
    try:
        await valkey.ping()
        logger.info("app.valkey_connected")
    except Exception as exc:
        logger.error("app.valkey_connection_failed", error=str(exc))

    # Initialize Sentry
    init_sentry(
        dsn=settings.SENTRY_DSN,
        environment="production" if not settings.DEBUG else "development",
    )

    yield

    # ── Shutdown ────────────────────────────────────────────────────
    logger.info("app.shutting_down")
    await engine.dispose()
    try:
        await valkey.aclose()  # type: ignore[attr-defined]
    except Exception:
        logger.warning("valkey_close_failed", exc_info=True)
    logger.info("app.shutdown_complete")


# =============================================================================
# App configuration
# =============================================================================

_settings = get_settings()

# CORS
cors_config = CORSConfig(
    allow_origins=_settings.CORS_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    allow_credentials=True,
)

# Default rate limit (applied globally; per-route overrides are set on controllers)
rate_limit_config = RateLimitConfig(
    rate_limit=("minute", 60),
    exclude=["/health", "/schema", "/metrics"],
)

# ChannelsPlugin for WebSocket real-time events
_valkey_for_channels = get_valkey()
channels_backend = RedisChannelsPubSubBackend(redis=_valkey_for_channels)

channels_plugin = ChannelsPlugin(
    backend=channels_backend,
    channels=[
        CHANNEL_AGENT_HEARTBEAT,
        CHANNEL_AGENT_LOG,
        CHANNEL_HITL_NEW,
        CHANNEL_HITL_RESOLVED,
        CHANNEL_PROJECT_UPDATE,
        CHANNEL_NOTIFICATION,
    ],
    arbitrary_channels_allowed=True,
)

# OpenAPI
openapi_config = OpenAPIConfig(
    title="MAS API",
    version="1.0",
    description="Multi-Agent Service REST API for freelance automation and cold outreach.",
    path="/schema",
)


# =============================================================================
# Application instance
# =============================================================================


app = Litestar(
    route_handlers=[
        health_check,
        AuthController,
        HITLController,
        AgentController,
        JobController,
        MetricsController,
        UserController,
        ws_handler,
    ],
    dependencies={
        "db_session": provide_db_session,
        "valkey": provide_valkey,
        "settings": provide_settings,
    },
    on_app_init=[jwt_auth.on_app_init],
    exception_handlers={
        MASException: _mas_exception_handler,  # type: ignore[dict-item]
        HTTPException: _http_exception_handler,  # type: ignore[dict-item]
        Exception: _generic_exception_handler,  # type: ignore[dict-item]
    },
    cors_config=cors_config,
    middleware=[rate_limit_config.middleware],
    plugins=[channels_plugin],
    openapi_config=openapi_config,
    lifespan=[lifespan],
    debug=_settings.DEBUG,
)
