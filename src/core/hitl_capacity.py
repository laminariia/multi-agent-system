"""HITL capacity management — prevent queue overflow.

Limits pending HITL items to MAX_PENDING (default 100).
Expires stale pending items after a configurable timeout.
Tracks items nearing expiry for countdown alerts.
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


async def get_expiring_items(
    session: Any,
    *,
    threshold_hours: float = 6.0,
) -> list[dict[str, Any]]:
    """Get pending HITL items expiring within *threshold_hours*.

    Returns a list of dicts with item metadata and countdown fields:
    - ``time_remaining_seconds``: seconds until expiry (negative if past)
    - ``pct_elapsed``: percentage of total TTL elapsed (0-100)
    """
    now = datetime.now(UTC)
    threshold_dt = now + timedelta(hours=threshold_hours)

    stmt = (
        select(HITLQueue)
        .where(
            HITLQueue.status == "pending",
            HITLQueue.expires_at.is_not(None),
            HITLQueue.expires_at <= threshold_dt,
        )
        .order_by(HITLQueue.expires_at.asc())
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()

    items: list[dict[str, Any]] = []
    for row in rows:
        total_ttl = (row.expires_at - row.created_at).total_seconds()
        elapsed = (now - row.created_at).total_seconds()
        remaining = (row.expires_at - now).total_seconds()
        pct = min(max((elapsed / total_ttl) * 100.0, 0.0), 100.0) if total_ttl > 0 else 100.0

        items.append(
            {
                "id": str(row.id),
                "type": row.type,
                "title": row.title,
                "priority": row.priority,
                "expires_at": row.expires_at.isoformat(),
                "created_at": row.created_at.isoformat(),
                "time_remaining_seconds": round(remaining, 1),
                "pct_elapsed": round(pct, 1),
            }
        )

    logger.debug(
        "hitl.expiring_items_checked",
        threshold_hours=threshold_hours,
        count=len(items),
    )
    return items


async def get_expired_items_count(session: Any) -> int:
    """Count expired but unresolved HITL items (expires_at < now, status still pending)."""
    now = datetime.now(UTC)
    stmt = select(func.count()).select_from(
        select(HITLQueue)
        .where(
            HITLQueue.status == "pending",
            HITLQueue.expires_at.is_not(None),
            HITLQueue.expires_at < now,
        )
        .subquery()
    )
    count = (await session.execute(stmt)).scalar_one()

    if count > 0:
        logger.warning("hitl.expired_unresolved", count=count)

    return count
