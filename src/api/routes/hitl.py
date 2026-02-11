"""Human-in-the-Loop (HITL) queue routes.

Provides endpoints for listing pending HITL items, resolving them, and
retrieving aggregate statistics.  Mounted at ``/api/v1/hitl``.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
import structlog
from litestar import Controller, Request, get, post
from litestar.exceptions import NotFoundException
from litestar.params import Parameter
from litestar.security.jwt import Token
from sqlalchemy import case, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import require_role
from src.api.schemas import (
    HITLItemSchema,
    HITLPendingResponseSchema,
    HITLResolveRequestSchema,
    HITLResolveResponseSchema,
    HITLStatsSchema,
    HITLTodayStatsSchema,
    HITLTypeStatsSchema,
)
from src.core.exceptions import MASException
from src.core.models import HITLQueue, User

logger = structlog.get_logger(__name__)

# Maps HITL types to their expected downstream action after approval
_NEXT_ACTION_MAP: dict[str, dict[str, str]] = {
    "bid_approval": {
        "approve": "bid_will_be_submitted",
        "edit": "bid_will_be_resubmitted_with_edits",
        "reject": "bid_discarded",
        "skip": "bid_skipped",
        "later": "bid_deferred",
    },
    "code_review": {
        "approve": "code_accepted",
        "reject": "code_rejected_for_rework",
        "edit": "code_revised",
        "skip": "code_review_skipped",
        "later": "code_review_deferred",
    },
    "delivery": {
        "approve": "delivery_will_proceed",
        "reject": "delivery_rejected",
        "edit": "delivery_revised",
        "skip": "delivery_skipped",
        "later": "delivery_deferred",
    },
    "revision": {
        "approve": "revision_accepted",
        "reject": "revision_rejected",
        "edit": "revision_modified",
        "skip": "revision_skipped",
        "later": "revision_deferred",
    },
    "scope_creep": {
        "approve": "scope_change_approved_by_client",
        "reject": "scope_change_declined",
        "edit": "scope_change_negotiated",
        "skip": "scope_change_skipped",
        "later": "scope_change_deferred",
    },
    "plan_review": {
        "approve": "plan_approved_for_execution",
        "reject": "plan_rejected_for_revision",
        "edit": "plan_modified_by_human",
        "skip": "plan_review_skipped",
        "later": "plan_review_deferred",
    },
    "alert": {
        "approve": "alert_acknowledged",
        "reject": "alert_dismissed",
        "skip": "alert_skipped",
        "later": "alert_deferred",
    },
    "email_approval": {
        "approve": "emails_will_be_sent",
        "reject": "emails_discarded",
        "edit": "emails_revised_and_sent",
        "skip": "emails_skipped",
        "later": "emails_deferred",
    },
    "final_review": {
        "approve": "work_delivered_to_client",
        "reject": "work_rejected_for_rework",
        "edit": "work_revised_and_delivered",
        "skip": "delivery_skipped",
        "later": "delivery_deferred",
    },
    "job_review": {
        "approve": "job_accepted_for_bidding",
        "reject": "job_rejected",
        "edit": "job_criteria_modified",
        "skip": "job_skipped",
        "later": "job_review_deferred",
    },
}


def _next_action(hitl_type: str, resolution: str) -> str:
    """Determine the downstream action label for a given type + resolution."""
    return _NEXT_ACTION_MAP.get(hitl_type, {}).get(resolution, f"{resolution}_processed")


class HITLController(Controller):
    """Manages the HITL approval queue."""

    path = "/api/v1/hitl"
    tags = ["hitl"]

    # -----------------------------------------------------------------
    # GET /api/v1/hitl/pending
    # -----------------------------------------------------------------

    @get(
        "/pending",
        summary="List pending HITL items",
        description="Paginated list of HITL items awaiting resolution, optionally filtered by type.",
    )
    async def list_pending(
        self,
        db_session: AsyncSession,
        type: str | None = Parameter(default=None, description="Filter by type: bid_approval, code_review, etc."),
        limit: int = Parameter(default=20, ge=1, le=100, description="Page size"),
        offset: int = Parameter(default=0, ge=0, description="Pagination offset"),
    ) -> HITLPendingResponseSchema:
        """Return pending HITL items ordered by priority (urgent first) then creation date."""
        base = select(HITLQueue).where(HITLQueue.status == "pending")

        if type is not None:
            base = base.where(HITLQueue.type == type)

        # Count total + urgent
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await db_session.execute(count_stmt)).scalar_one()

        urgent_stmt = (
            select(func.count())
            .select_from(
                base.where(HITLQueue.priority == "urgent").subquery()
            )
        )
        pending_urgent = (await db_session.execute(urgent_stmt)).scalar_one()

        # Fetch page ordered by priority weight then newest first
        priority_order = case(
            (HITLQueue.priority == "urgent", 0),
            (HITLQueue.priority == "normal", 1),
            (HITLQueue.priority == "low", 2),
            else_=3,
        )
        items_stmt = (
            base
            .order_by(priority_order, HITLQueue.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db_session.execute(items_stmt)
        rows = result.scalars().all()

        items = [
            HITLItemSchema(
                id=row.id,
                type=row.type,
                priority=row.priority,
                title=row.title,
                description=row.description,
                expires_at=row.expires_at,
                payload=row.payload,
                available_actions=row.available_actions,
                created_at=row.created_at,
            )
            for row in rows
        ]

        return HITLPendingResponseSchema(
            items=items,
            total=total,
            pending_urgent=pending_urgent,
        )

    # -----------------------------------------------------------------
    # POST /api/v1/hitl/{hitl_id}/resolve
    # -----------------------------------------------------------------

    @post(
        "/{hitl_id:uuid}/resolve",
        summary="Resolve a HITL item",
        guards=[require_role("owner")],
    )
    async def resolve(
        self,
        hitl_id: uuid.UUID,
        data: HITLResolveRequestSchema,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
        valkey: aioredis.Redis,
    ) -> HITLResolveResponseSchema:
        """Approve, reject, edit, skip, or defer a HITL item.

        Raises:
            NotFoundException: When the HITL item does not exist.
            MASException: When the item is expired or already resolved.
        """
        stmt = select(HITLQueue).where(HITLQueue.id == hitl_id)
        result = await db_session.execute(stmt)
        item = result.scalar_one_or_none()

        if item is None:
            raise NotFoundException(detail=f"HITL item {hitl_id} not found")

        # Guard: already resolved
        if item.status == "resolved":
            raise MASException(
                "This HITL request has already been resolved",
                details={"error_code": "HITL_ALREADY_RESOLVED", "status_code": 409},
            )

        # Guard: expired
        now = datetime.now(UTC)
        if item.expires_at is not None and item.expires_at < now:
            # Auto-mark as expired
            item.status = "expired"
            await db_session.flush()
            raise MASException(
                "This HITL request has expired",
                details={"error_code": "HITL_EXPIRED", "status_code": 410, "expired_at": item.expires_at.isoformat()},
            )

        # Guard: invalid action
        if data.action not in item.available_actions:
            raise MASException(
                f"Action '{data.action}' is not available. Allowed: {item.available_actions}",
                details={"error_code": "VALIDATION_ERROR", "status_code": 422},
            )

        # Apply resolution
        item.status = "resolved"
        item.resolution = data.action
        item.resolution_note = data.note
        item.resolved_by = request.user.id
        item.resolved_at = now

        # If the action is "edit", merge the edited payload
        if data.action == "edit" and data.edited_payload is not None:
            merged_payload = {**item.payload, **data.edited_payload}
            item.payload = merged_payload

        await db_session.flush()

        next_action = _next_action(item.type, data.action)

        logger.info(
            "hitl.resolved",
            hitl_id=str(hitl_id),
            action=data.action,
            resolved_by=str(request.user.id),
            next_action=next_action,
        )

        # Notify Telegram bot via Valkey pub/sub
        try:
            await valkey.publish(
                "hitl:resolved:bot",
                json.dumps({
                    "hitl_id": str(hitl_id),
                    "type": item.type,
                    "title": item.title,
                    "action": data.action,
                    "next_action": next_action,
                    "resolved_by": request.user.email,
                }),
            )
        except Exception:
            logger.warning("hitl.bot_notify_failed", hitl_id=str(hitl_id), exc_info=True)

        return HITLResolveResponseSchema(
            id=item.id,
            status="resolved",
            resolution=data.action,
            next_action=next_action,
        )

    # -----------------------------------------------------------------
    # GET /api/v1/hitl/stats
    # -----------------------------------------------------------------

    @get(
        "/stats",
        summary="HITL queue statistics",
        description="Today's counts, average resolution time, and per-type breakdown.",
    )
    async def stats(
        self,
        db_session: AsyncSession,
    ) -> HITLStatsSchema:
        """Compute and return aggregate HITL statistics for today."""
        now = datetime.now(UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # -- Today's aggregates -----------------------------------------------
        today_base = select(HITLQueue).where(HITLQueue.created_at >= today_start)

        pending_count = (
            await db_session.execute(
                select(func.count())
                .select_from(today_base.where(HITLQueue.status == "pending").subquery())
            )
        ).scalar_one()

        resolved_count = (
            await db_session.execute(
                select(func.count())
                .select_from(today_base.where(HITLQueue.status == "resolved").subquery())
            )
        ).scalar_one()

        expired_count = (
            await db_session.execute(
                select(func.count())
                .select_from(today_base.where(HITLQueue.status == "expired").subquery())
            )
        ).scalar_one()

        # -- Average resolution time (minutes) --------------------------------
        avg_stmt = (
            select(
                func.avg(
                    extract("epoch", HITLQueue.resolved_at) - extract("epoch", HITLQueue.created_at)
                )
            )
            .where(HITLQueue.status == "resolved")
            .where(HITLQueue.resolved_at.is_not(None))
        )
        avg_seconds_raw = (await db_session.execute(avg_stmt)).scalar_one()
        avg_resolution_minutes = round(float(avg_seconds_raw) / 60.0, 1) if avg_seconds_raw else 0.0

        # -- Per-type breakdown -----------------------------------------------
        type_stmt = (
            select(
                HITLQueue.type,
                HITLQueue.status,
                func.count().label("cnt"),
            )
            .where(HITLQueue.status.in_(["pending", "resolved"]))
            .group_by(HITLQueue.type, HITLQueue.status)
        )
        type_rows = (await db_session.execute(type_stmt)).all()

        by_type: dict[str, HITLTypeStatsSchema] = {}
        for row in type_rows:
            hitl_type, status_val, cnt = row[0], row[1], row[2]
            if hitl_type not in by_type:
                by_type[hitl_type] = HITLTypeStatsSchema(pending=0, resolved=0)
            if status_val == "pending":
                by_type[hitl_type].pending = cnt
            elif status_val == "resolved":
                by_type[hitl_type].resolved = cnt

        return HITLStatsSchema(
            today=HITLTodayStatsSchema(
                pending=pending_count,
                resolved=resolved_count,
                expired=expired_count,
            ),
            avg_resolution_time_minutes=avg_resolution_minutes,
            by_type=by_type,
        )
