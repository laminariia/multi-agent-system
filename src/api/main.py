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
from litestar.channels.backends.redis import RedisChannelsPubSubBackend
from litestar.config.cors import CORSConfig
from litestar.exceptions import HTTPException
from litestar.middleware import AbstractMiddleware
from litestar.middleware.rate_limit import RateLimitConfig
from litestar.openapi import OpenAPIConfig
from litestar.openapi.plugins import RedocRenderPlugin, SwaggerRenderPlugin
from litestar.types import Receive, Scope, Send
from sqlalchemy import text as sa_text

from src.api.dependencies import provide_db_session, provide_settings, provide_valkey
from src.api.guards import jwt_auth
from src.api.routes.agents import AgentController
from src.api.routes.auth import AuthController
from src.api.routes.campaigns import CampaignController
from src.api.routes.deals import DealController
from src.api.routes.health import health_check
from src.api.routes.hitl import HITLController
from src.api.routes.jobs import JobController
from src.api.routes.metrics import MetricsController
from src.api.routes.orchestrator import OrchestratorController
from src.api.routes.pipeline_b import PipelineBController
from src.api.routes.settings import SettingsController
from src.api.routes.telegram_channels import TelegramChannelController
from src.api.routes.users import UserController
from src.api.schemas import ErrorResponseSchema, ErrorSchema
from src.api.websocket import (
    CHANNEL_AGENT_HEARTBEAT,
    CHANNEL_AGENT_LOG,
    CHANNEL_HITL_NEW,
    CHANNEL_HITL_RESOLVED,
    CHANNEL_NOTIFICATION,
    CHANNEL_ORCH_GOAL,
    CHANNEL_ORCH_LOG,
    CHANNEL_ORCH_STATUS,
    CHANNEL_PROJECT_UPDATE,
    ws_handler,
)
from src.core.config import get_settings
from src.core.database import engine, get_valkey
from src.core.exceptions import MASException
from src.monitoring.sentry_config import init_sentry

logger = structlog.get_logger(__name__)


# =============================================================================
# Security headers middleware
# =============================================================================


class SecurityHeadersMiddleware(AbstractMiddleware):
    """Inject standard security headers into every HTTP response."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                security_headers: list[tuple[bytes, bytes]] = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"x-xss-protection", b"1; mode=block"),
                ]
                if not get_settings().DEBUG:
                    security_headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
                    )
                existing = list(message.get("headers", []))
                existing.extend(security_headers)
                message["headers"] = existing
            await send(message)

        await self.app(scope, receive, send_with_headers)


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


async def _seed_admin_user(settings: object) -> None:
    """Create an admin user if the users table is empty and env vars are set."""
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from src.api.guards import hash_password
    from src.core.database import get_db_session
    from src.core.models import User

    admin_email = getattr(settings, "ADMIN_EMAIL", "")
    admin_password = getattr(settings, "ADMIN_PASSWORD", "")
    if not admin_email or not admin_password:
        return

    try:
        async with get_db_session() as session:
            result = await session.execute(sa_select(func.count()).select_from(User))
            count = result.scalar()
            if count and count > 0:
                return

            user = User(
                email=admin_email,
                password_hash=hash_password(admin_password),
                role="owner",
                status="active",
            )
            session.add(user)
            await session.commit()
            logger.info("admin_user_seeded", email=admin_email)
    except Exception:
        logger.warning("admin_seed_failed", exc_info=True)


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

    # Initialize Semantic Cache (best-effort -- does not block startup)
    try:
        import asyncpg  # noqa: PLC0415

        from src.core.semantic_cache import SemanticCache  # noqa: PLC0415

        db_pool = await asyncpg.create_pool(settings.DATABASE_URL, min_size=1, max_size=3)
        semantic_cache = SemanticCache(valkey=valkey, db_pool=db_pool)
        await semantic_cache.ensure_index()
        app.state.semantic_cache = semantic_cache
        app.state.semantic_cache_db_pool = db_pool
        logger.info("app.semantic_cache_initialized")
    except Exception as exc:
        logger.warning("app.semantic_cache_init_failed", error=str(exc))
        app.state.semantic_cache = None
        app.state.semantic_cache_db_pool = None

    # Wire semantic cache into the shared DI container.
    from src.core.container import get_container  # noqa: PLC0415

    container = get_container()
    container.semantic_cache = getattr(app.state, "semantic_cache", None)

    # Initialize LLM Request Queue (P3.12 — best-effort)
    try:
        from src.core.llm_queue import LLMRequestQueue  # noqa: PLC0415

        llm_queue = LLMRequestQueue(
            llm_client=container.llm_client,
            max_concurrent=settings.LLM_MAX_CONCURRENT,
        )
        await llm_queue.start()
        container.llm_queue = llm_queue
        logger.info("app.llm_queue_initialized", max_concurrent=settings.LLM_MAX_CONCURRENT)
    except Exception as exc:
        logger.warning("app.llm_queue_init_failed", error=str(exc))

    # Initialize Decision Memory (P3.13 — best-effort)
    try:
        from src.core.decision_memory import DecisionMemory  # noqa: PLC0415
        from src.knowledge.ingestion import KnowledgeIngestionPipeline  # noqa: PLC0415
        from src.knowledge.retrieval import KnowledgeRetriever  # noqa: PLC0415

        dm_db_pool = getattr(app.state, "semantic_cache_db_pool", None)
        if dm_db_pool is not None:
            ingestion = KnowledgeIngestionPipeline(db_pool=dm_db_pool)
            retriever = KnowledgeRetriever(db_pool=dm_db_pool, valkey=valkey)
            container.decision_memory = DecisionMemory(ingestion=ingestion, retriever=retriever)
            logger.info("app.decision_memory_initialized")
        else:
            logger.warning("app.decision_memory_skipped", reason="no db_pool available")
    except Exception as exc:
        logger.warning("app.decision_memory_init_failed", error=str(exc))

    # Seed admin user if users table is empty and ADMIN_EMAIL/PASSWORD are set.
    await _seed_admin_user(settings)

    # Crash recovery — resume stale pipeline threads (best-effort, non-blocking)
    try:
        from src.core.crash_recovery import startup_crash_recovery  # noqa: PLC0415
        from src.core.database import get_db_session as _get_session  # noqa: PLC0415

        async with _get_session() as recovery_session:
            summary = await startup_crash_recovery(recovery_session)
            logger.info("app.crash_recovery_complete", **summary)
    except Exception as exc:
        logger.warning("app.crash_recovery_failed", error=str(exc))

    yield

    # ── Shutdown ────────────────────────────────────────────────────
    logger.info("app.shutting_down")

    # Tear down the agent DI container (browser pool, HTTP clients, etc.).
    from src.core.container import teardown_container  # noqa: PLC0415

    await teardown_container()

    # Close semantic cache DB pool
    cache_pool = getattr(app.state, "semantic_cache_db_pool", None)
    if cache_pool is not None:
        try:
            await cache_pool.close()
        except Exception:
            logger.warning("semantic_cache_pool_close_failed", exc_info=True)

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
    allow_origins=_settings.cors_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    allow_credentials=True,
)

# Default rate limit (applied globally; per-route overrides are set on controllers).
# Set high because Railway reverse proxy collapses all client IPs into one
# internal address — IP-based limiting effectively caps ALL users together.
rate_limit_config = RateLimitConfig(
    rate_limit=("minute", 300),
    exclude=["/health", "/schema", "/swagger", "/redoc", "/metrics"],
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
        CHANNEL_ORCH_STATUS,
        CHANNEL_ORCH_GOAL,
        CHANNEL_ORCH_LOG,
    ],
    arbitrary_channels_allowed=True,
)

# OpenAPI with Swagger UI and Redoc
openapi_config = OpenAPIConfig(
    title="MAS API",
    version="1.0",
    description="Multi-Agent Service REST API for freelance automation and cold outreach.",
    path="/schema",
    render_plugins=[
        SwaggerRenderPlugin(),
        RedocRenderPlugin(),
    ],
)


# =============================================================================
# Application instance
# =============================================================================


app = Litestar(
    route_handlers=[
        health_check,
        AuthController,
        CampaignController,
        DealController,
        HITLController,
        AgentController,
        JobController,
        MetricsController,
        OrchestratorController,
        PipelineBController,
        SettingsController,
        TelegramChannelController,
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
    middleware=[rate_limit_config.middleware, SecurityHeadersMiddleware],
    plugins=[channels_plugin],
    openapi_config=openapi_config,
    lifespan=[lifespan],
    debug=_settings.DEBUG,
)
