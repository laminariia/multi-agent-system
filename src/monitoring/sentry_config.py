"""Sentry SDK initialisation for the Multi-Agent Service.

Configures Sentry with the Litestar (asyncio + SQLAlchemy) integrations,
sampling rates, PII scrubbing, and a ``before_send`` filter that drops
expected transient errors (rate-limit responses) to keep the event budget
useful.
"""
from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def init_sentry(dsn: str | None, environment: str = "production") -> None:
    """Initialize Sentry SDK with Litestar integration.

    Args:
        dsn: Sentry DSN string. If ``None`` or empty, Sentry is not initialized.
        environment: Environment name (production, staging, development).
    """
    if not dsn:
        logger.info("sentry_disabled", reason="no DSN configured")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    except ImportError:
        logger.warning("sentry_sdk_not_installed")
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=0.1,  # 10 % of transactions
        profiles_sample_rate=0.1,
        send_default_pii=False,
        integrations=[
            AsyncioIntegration(),
            SqlalchemyIntegration(),
        ],
        before_send=_before_send,
    )
    logger.info("sentry_initialized", environment=environment)


def _before_send(event: dict, hint: dict) -> dict | None:
    """Filter out noisy or sensitive events before sending to Sentry."""
    # Don't send rate-limit errors -- they are expected and retried internally.
    if "exc_info" in hint:
        exc_type = hint["exc_info"][0]
        if exc_type and exc_type.__name__ in (
            "LLMRateLimitError",
            "PlatformRateLimitError",
        ):
            return None
    return event
