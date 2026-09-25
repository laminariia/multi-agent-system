"""Health-check endpoint.

``GET /health`` verifies database and Valkey connectivity and returns
a structured response suitable for Docker health checks, load balancers,
and the front-end status indicator.
"""

from __future__ import annotations

from datetime import UTC, datetime

import redis.asyncio as aioredis
import structlog
from litestar import get
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import HealthResponseSchema
from src.core.config import Settings

logger = structlog.get_logger(__name__)


@get(
    "/health",
    tags=["health"],
    summary="System health check",
    description="Returns connectivity status for PostgreSQL and Valkey, plus the API version.",
    exclude_from_auth=True,
)
async def health_check(
    db_session: AsyncSession,
    valkey: aioredis.Redis,
    settings: Settings,
) -> HealthResponseSchema:
    """Check database and Valkey connectivity and report overall health.

    Returns:
        A ``HealthResponseSchema`` with per-service connectivity flags.
    """
    db_connected = False
    valkey_connected = False

    # -- PostgreSQL -----------------------------------------------------------
    try:
        await db_session.execute(text("SELECT 1"))
        db_connected = True
    except Exception as exc:
        logger.warning("health_check.db_failed", error=str(exc))

    # -- Valkey ---------------------------------------------------------------
    try:
        pong = await valkey.ping()
        valkey_connected = bool(pong)
    except Exception as exc:
        logger.warning("health_check.valkey_failed", error=str(exc))

    # -- Overall status -------------------------------------------------------
    if db_connected and valkey_connected:
        status = "healthy"
    elif db_connected or valkey_connected:
        status = "degraded"
    else:
        status = "unhealthy"

    return HealthResponseSchema(
        status=status,
        db_connected=db_connected,
        valkey_connected=valkey_connected,
        version=settings.APP_VERSION,
        timestamp=datetime.now(UTC),
    )
