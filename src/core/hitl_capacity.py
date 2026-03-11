"""HITL capacity management — prevent queue overflow.

Limits pending HITL items to MAX_PENDING (default 100).
Expires stale pending items after a configurable timeout.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, update

from src.core.exceptions import MASException
from src.core.models import HITLQueue

logger = structlog.get_logger(__name__)

_DEFAULT_MAX_PENDING = 100


class HITLCapacityExceeded(MASException):
    """Raised when the HITL pending queue is at capacity."""


async def check_hitl_capacity(
    session: Any,
    *,
    max_pending: int = _DEFAULT_MAX_PENDING,
) -> None:
    """Raise HITLCapacityExceeded if pending HITL items >= max_pending."""
    result = await session.execute(select(func.count()).select_from(HITLQueue).where(HITLQueue.status == "pending"))
    count = result.scalar_one()

    if count >= max_pending:
        logger.warning("hitl.capacity_exceeded", pending=count, max_pending=max_pending)
        raise HITLCapacityExceeded(f"HITL queue at capacity: {count}/{max_pending} pending items")


async def expire_stale_hitl(
    session: Any,
    *,
    timeout_minutes: int = 60,
) -> int:
    """Mark stale pending HITL items as expired. Returns count expired."""
    cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)

    result = await session.execute(
        update(HITLQueue)
        .where(
            HITLQueue.status == "pending",
            HITLQueue.created_at < cutoff,
        )
        .values(status="expired")
    )
    count = result.rowcount

    if count > 0:
        await session.flush()
        logger.info("hitl.expired_stale", count=count, timeout_minutes=timeout_minutes)

    return count
