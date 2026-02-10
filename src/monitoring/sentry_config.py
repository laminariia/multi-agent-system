"""Sentry SDK initialisation for the Multi-Agent Service.

Configures Sentry with the Litestar (asyncio + SQLAlchemy) integrations,
sampling rates, PII scrubbing, and a ``before_send`` filter that drops
expected transient errors (rate-limit responses) to keep the event budget
useful.

Also provides :func:`start_agent_transaction` for wrapping agent execution
in a Sentry transaction with ``op="agent"`` so each agent run appears as a
distinct trace in Sentry Performance.
"""
from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

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

    integrations: list[Any] = [
        AsyncioIntegration(),
        SqlalchemyIntegration(),
    ]

    # Litestar integration is available since sentry-sdk >= 1.13
    try:
        from sentry_sdk.integrations.litestar import LitestarIntegration

        integrations.append(LitestarIntegration())
    except ImportError:
        logger.debug("sentry_litestar_integration_not_available")

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=0.1,  # 10 % of transactions
        profiles_sample_rate=0.1,
        send_default_pii=False,
        integrations=integrations,
        before_send=_before_send,
    )
    logger.info("sentry_initialized", environment=environment)


@contextmanager
def start_agent_transaction(
    agent_name: str,
    thread_id: str,
) -> Generator[Any, None, None]:
    """Start a Sentry transaction for an agent execution.

    If ``sentry_sdk`` is not installed or not initialized, yields ``None``
    transparently so callers never need to guard imports.

    Args:
        agent_name: Canonical agent identifier (e.g. ``"scout"``).
        thread_id: LangGraph thread ID for correlation.

    Yields:
        The Sentry transaction object, or ``None`` if Sentry is unavailable.
    """
    try:
        import sentry_sdk
    except ImportError:
        yield None
        return

    transaction = sentry_sdk.start_transaction(
        op="agent",
        name=agent_name,
    )
    transaction.set_tag("agent.role", agent_name)
    transaction.set_tag("thread.id", thread_id)
    try:
        yield transaction
    except Exception:
        transaction.set_status("internal_error")
        raise
    else:
        transaction.set_status("ok")
    finally:
        transaction.finish()


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
