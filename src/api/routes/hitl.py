"""Human-in-the-Loop (HITL) queue routes.

Provides endpoints for listing pending HITL items, resolving them, and
retrieving aggregate statistics.  Mounted at ``/api/v1/hitl``.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as aioredis
import structlog
from litestar import Controller, Request, delete, get, post
from litestar.channels import ChannelsPlugin
from litestar.exceptions import NotFoundException
from litestar.params import Parameter
from litestar.security.jwt import Token
from sqlalchemy import Date, case, cast, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import require_role
from src.api.routes import _escape_like
from src.api.schemas import (
    HITLBulkResolveRequestSchema,
    HITLBulkResolveResponseSchema,
    HITLDetailResponseSchema,
    HITLItemSchema,
    HITLPendingResponseSchema,
    HITLResolveRequestSchema,
    HITLResolveResponseSchema,
    HITLStatsSchema,
    HITLTodayStatsSchema,
    HITLTrendDaySchema,
    HITLTrendsResponseSchema,
    HITLTrendTotalsSchema,
    HITLTypeStatsSchema,
    HITLViewingLockResponseSchema,
)
from src.api.websocket import CHANNEL_HITL_RESOLVED, publish_event
from src.core.exceptions import MASException
from src.core.models import HITLQueue, User

logger = structlog.get_logger(__name__)

# Valkey key prefix and TTL for HITL viewing locks
_HITL_LOCK_PREFIX = "mas:hitl_lock:"
_HITL_LOCK_TTL = 300  # 5 minutes

# Set to prevent GC of fire-and-forget resume tasks (asyncio.create_task pattern)
_background_resume_tasks: set[Any] = set()

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
    "dev_launch": {
        "approve": "development_cycle_started",
        "reject": "development_cycle_cancelled",
        "skip": "dev_launch_skipped",
        "later": "dev_launch_deferred",
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
    "agent_failure": {
        "resume": "agent_will_retry",
        "skip": "agent_skipped",
        "manual": "manual_artifacts_provided",
        "later": "agent_failure_deferred",
    },
    "delivery_hold": {
        "deliver_now": "delivery_released_early",
        "wait": "delivery_hold_continued",
        "later": "delivery_hold_deferred",
    },
    "manual_action": {
        "approve": "manual_action_completed",
        "reject": "manual_action_rejected",
        "skip": "manual_action_skipped",
        "later": "manual_action_deferred",
    },
    "outreach_approval": {
        "approve": "outreach_emails_will_be_sent",
        "reject": "outreach_emails_discarded",
        "edit": "outreach_emails_revised",
        "skip": "outreach_skipped",
        "later": "outreach_deferred",
    },
    "design_review": {
        "design_approve": "design_forwarded_to_client",
        "design_revise": "design_revision_requested",
        "design_reject": "design_rejected_escalate_planner",
        "approve": "design_forwarded_to_client",
        "request_changes": "design_revision_requested",
        "reject": "design_rejected_escalate_planner",
        "later": "design_review_deferred",
    },
    "design_client_approval": {
        "client_approved": "design_approved_start_development",
        "client_changes": "design_client_changes_requested",
        "client_rejected": "design_client_rejected_escalate_planner",
        "approve": "design_approved_start_development",
        "request_changes": "design_client_changes_requested",
        "reject": "design_client_rejected_escalate_planner",
        "later": "design_client_approval_deferred",
    },
    "concept_review": {
        "approve": "concept_approved_for_delivery",
        "edit": "concept_revised_for_regeneration",
        "reject": "concept_discarded",
        "skip": "concept_review_skipped",
        "later": "concept_review_deferred",
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
        description="Paginated list of HITL items awaiting resolution, optionally filtered by type and search term.",
    )
    async def list_pending(
        self,
        db_session: AsyncSession,
        type: str | None = Parameter(default=None, description="Filter by type: bid_approval, code_review, etc."),
        search: str | None = Parameter(
            default=None,
            description="Case-insensitive title substring filter",
        ),
        limit: int = Parameter(default=20, ge=1, le=100, description="Page size"),
        offset: int = Parameter(default=0, ge=0, description="Pagination offset"),
    ) -> HITLPendingResponseSchema:
        """Return pending HITL items ordered by priority (urgent first) then creation date."""
        base = select(HITLQueue).where(HITLQueue.status == "pending")

        if type is not None:
            base = base.where(HITLQueue.type == type)

        if isinstance(search, str):
            base = base.where(HITLQueue.title.ilike("%" + _escape_like(search) + "%"))

        # Count total + urgent
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await db_session.execute(count_stmt)).scalar_one()

        urgent_stmt = select(func.count()).select_from(base.where(HITLQueue.priority == "urgent").subquery())
        pending_urgent = (await db_session.execute(urgent_stmt)).scalar_one()

        # Fetch page ordered by priority weight then newest first
        priority_order = case(
            (HITLQueue.priority == "urgent", 0),
            (HITLQueue.priority == "normal", 1),
            (HITLQueue.priority == "low", 2),
            else_=3,
        )
        items_stmt = base.order_by(priority_order, HITLQueue.created_at.desc()).limit(limit).offset(offset)
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
    # GET /api/v1/hitl/{hitl_id} — detail with viewing-lock info
    # -----------------------------------------------------------------

    @get(
        "/{hitl_id:uuid}",
        summary="Get HITL item detail",
        description="Returns full detail of a single HITL item including viewing-lock information.",
    )
    async def get_detail(
        self,
        hitl_id: uuid.UUID,
        db_session: AsyncSession,
        valkey: aioredis.Redis,
    ) -> HITLDetailResponseSchema:
        """Return a single HITL item with ``locked_by`` from Valkey.

        Raises:
            NotFoundException: When the HITL item does not exist.
        """
        stmt = select(HITLQueue).where(HITLQueue.id == hitl_id)
        result = await db_session.execute(stmt)
        item = result.scalar_one_or_none()

        if item is None:
            raise NotFoundException(detail=f"HITL item {hitl_id} not found")

        # Look up viewing lock from Valkey (soft, non-fatal on error)
        locked_by: str | None = None
        try:
            raw = await valkey.get(f"{_HITL_LOCK_PREFIX}{hitl_id}")
            if raw is not None:
                locked_by = raw.decode() if isinstance(raw, bytes) else str(raw)
        except (OSError, ConnectionError):
            logger.debug("hitl.viewing_lock_check_failed", hitl_id=str(hitl_id), exc_info=True)

        return HITLDetailResponseSchema(
            id=item.id,
            type=item.type,
            priority=item.priority,
            title=item.title,
            description=item.description,
            expires_at=item.expires_at,
            payload=item.payload,
            available_actions=item.available_actions,
            created_at=item.created_at,
            locked_by=locked_by,
        )

    # -----------------------------------------------------------------
    # POST /api/v1/hitl/{hitl_id}/viewing — acquire viewing lock
    # -----------------------------------------------------------------

    @post(
        "/{hitl_id:uuid}/viewing",
        summary="Acquire HITL viewing lock",
        description="Soft-lock a HITL item to indicate that an operator is viewing it. TTL 5 min.",
        status_code=200,
    )
    async def acquire_viewing_lock(
        self,
        hitl_id: uuid.UUID,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
        valkey: aioredis.Redis,
    ) -> HITLViewingLockResponseSchema:
        """Acquire a soft viewing lock on a HITL item via Valkey SET NX EX.

        - If the lock is free, acquires it and returns ``locked=True``.
        - If the lock is already held by the same user, refreshes TTL and returns ``locked=True``.
        - If the lock is held by another user, returns ``locked=False`` with their user_id.
        - On Valkey errors, degrades gracefully returning ``locked=False``.

        Raises:
            NotFoundException: When the HITL item does not exist.
        """
        # Verify HITL item exists
        stmt = select(HITLQueue).where(HITLQueue.id == hitl_id)
        result = await db_session.execute(stmt)
        item = result.scalar_one_or_none()

        if item is None:
            raise NotFoundException(detail=f"HITL item {hitl_id} not found")

        user_id = str(request.user.id)
        lock_key = f"{_HITL_LOCK_PREFIX}{hitl_id}"

        try:
            # Attempt atomic SET NX EX
            acquired = await valkey.set(lock_key, user_id, nx=True, ex=_HITL_LOCK_TTL)

            if acquired:
                logger.debug("hitl.viewing_lock_acquired", hitl_id=str(hitl_id), user_id=user_id)
                return HITLViewingLockResponseSchema(locked=True, locked_by=user_id)

            # Lock exists — check who holds it
            current_holder_raw = await valkey.get(lock_key)
            if current_holder_raw is not None:
                current_holder = (
                    current_holder_raw.decode() if isinstance(current_holder_raw, bytes) else str(current_holder_raw)
                )
            else:
                # Race condition: lock expired between SET and GET — treat as free
                current_holder = None

            if current_holder == user_id:
                # Same user — refresh TTL (re-entrant)
                await valkey.expire(lock_key, _HITL_LOCK_TTL)
                logger.debug("hitl.viewing_lock_refreshed", hitl_id=str(hitl_id), user_id=user_id)
                return HITLViewingLockResponseSchema(locked=True, locked_by=user_id)

            # Another user holds the lock
            logger.debug(
                "hitl.viewing_lock_conflict",
                hitl_id=str(hitl_id),
                requested_by=user_id,
                held_by=current_holder,
            )
            return HITLViewingLockResponseSchema(locked=False, locked_by=current_holder)

        except (OSError, ConnectionError):
            logger.warning("hitl.viewing_lock_acquire_failed", hitl_id=str(hitl_id), exc_info=True)
            return HITLViewingLockResponseSchema(locked=False, locked_by=None)

    # -----------------------------------------------------------------
    # DELETE /api/v1/hitl/{hitl_id}/viewing — release viewing lock
    # -----------------------------------------------------------------

    @delete(
        "/{hitl_id:uuid}/viewing",
        summary="Release HITL viewing lock",
        description="Release the soft viewing lock on a HITL item.",
        status_code=200,
    )
    async def release_viewing_lock(
        self,
        hitl_id: uuid.UUID,
        request: Request[User, Token, Any],
        valkey: aioredis.Redis,
    ) -> HITLViewingLockResponseSchema:
        """Release a viewing lock. Only the lock owner may release it.

        - If the lock is not held, returns ``locked=False`` (idempotent).
        - If the lock is held by another user, raises ``MASException``.
        - On Valkey errors, degrades gracefully returning ``locked=False``.
        """
        user_id = str(request.user.id)
        lock_key = f"{_HITL_LOCK_PREFIX}{hitl_id}"

        try:
            current_holder_raw = await valkey.get(lock_key)

            if current_holder_raw is None:
                # Lock not held — idempotent success
                return HITLViewingLockResponseSchema(locked=False, locked_by=None)

            current_holder = (
                current_holder_raw.decode() if isinstance(current_holder_raw, bytes) else str(current_holder_raw)
            )

            if current_holder != user_id:
                raise MASException(
                    "Cannot release viewing lock held by another operator",
                    details={"error_code": "LOCK_HELD_BY_ANOTHER", "status_code": 409},
                )

            await valkey.delete(lock_key)
            logger.debug("hitl.viewing_lock_released", hitl_id=str(hitl_id), user_id=user_id)
            return HITLViewingLockResponseSchema(locked=False, locked_by=None)

        except MASException:
            raise
        except (OSError, ConnectionError):
            logger.warning("hitl.viewing_lock_release_failed", hitl_id=str(hitl_id), exc_info=True)
            return HITLViewingLockResponseSchema(locked=False, locked_by=None)

    # -----------------------------------------------------------------
    # POST /api/v1/hitl/{hitl_id}/resolve
    # -----------------------------------------------------------------

    @post(
        "/{hitl_id:uuid}/resolve",
        summary="Resolve a HITL item",
        guards=[require_role("owner", "co_owner")],
        status_code=200,
    )
    async def resolve(
        self,
        hitl_id: uuid.UUID,
        data: HITLResolveRequestSchema,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
        valkey: aioredis.Redis,
        channels: ChannelsPlugin,
    ) -> HITLResolveResponseSchema:
        """Approve, reject, edit, skip, or defer a HITL item.

        Raises:
            NotFoundException: When the HITL item does not exist.
            MASException: When the item is expired or already resolved.
        """
        stmt = select(HITLQueue).where(HITLQueue.id == hitl_id).with_for_update()
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
                json.dumps(
                    {
                        "hitl_id": str(hitl_id),
                        "type": item.type,
                        "title": item.title,
                        "action": data.action,
                        "next_action": next_action,
                        "resolved_by": request.user.email,
                    }
                ),
            )
        except (OSError, ConnectionError):
            logger.warning("hitl.bot_notify_failed", hitl_id=str(hitl_id), exc_info=True)

        # Publish real-time event for connected dashboard clients
        try:
            await publish_event(
                channels,
                CHANNEL_HITL_RESOLVED,
                {
                    "type": "hitl:resolved",
                    "data": {
                        "hitl_id": str(hitl_id),
                        "hitl_type": item.type,
                        "title": item.title,
                        "action": data.action,
                        "next_action": next_action,
                        "resolved_by": request.user.email,
                    },
                },
            )
        except (OSError, ConnectionError):
            logger.debug("hitl.ws_publish_failed", hitl_id=str(hitl_id), exc_info=True)

        # Resume the paused pipeline if this HITL type has a graph to resume.
        # Fire-and-forget: the pipeline runs asynchronously; the HTTP
        # response returns immediately so the dashboard stays responsive.
        _RESUMABLE_TYPES = {
            "bid_approval",
            "dev_launch",
            "plan_review",
            "email_approval",
            "final_review",
            "agent_failure",
            "outreach_approval",
            "delivery_hold",
            "design_review",
            "design_client_approval",
            "concept_review",
        }
        thread_id_from_payload = (item.payload or {}).get("thread_id")
        if item.type in _RESUMABLE_TYPES and thread_id_from_payload and data.action != "later":
            try:
                import asyncio  # noqa: PLC0415

                from src.core.graph import resume_from_hitl as _resume  # noqa: PLC0415

                hitl_response = {
                    "action": data.action,
                    "note": data.note,
                }
                if data.edited_payload:
                    hitl_response["edits"] = data.edited_payload
                if item.type == "bid_approval":
                    hitl_response["bid_ids"] = [(item.payload or {}).get("bid_id", "")]

                task = asyncio.create_task(
                    _resume(
                        thread_id_from_payload,
                        hitl_response,
                        hitl_type=item.type,
                        valkey=valkey,
                    ),
                )
                # Prevent GC of fire-and-forget task
                _background_resume_tasks.add(task)
                task.add_done_callback(_background_resume_tasks.discard)

                logger.info(
                    "hitl.resume_dispatched",
                    hitl_id=str(hitl_id),
                    thread_id=thread_id_from_payload,
                    hitl_type=item.type,
                    action=data.action,
                )
            except Exception:  # noqa: BLE001 — intentional: dynamic import + task creation, any failure is non-fatal
                logger.warning(
                    "hitl.resume_dispatch_failed",
                    hitl_id=str(hitl_id),
                    exc_info=True,
                )

        return HITLResolveResponseSchema(
            id=item.id,
            status="resolved",
            resolution=data.action,
            next_action=next_action,
        )

    # -----------------------------------------------------------------
    # POST /api/v1/hitl/bulk-resolve
    # -----------------------------------------------------------------

    @post(
        "/bulk-resolve",
        summary="Bulk resolve multiple HITL items",
        guards=[require_role("owner", "co_owner")],
        status_code=200,
    )
    async def bulk_resolve(
        self,
        data: HITLBulkResolveRequestSchema,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
        valkey: aioredis.Redis,
        channels: ChannelsPlugin,
    ) -> HITLBulkResolveResponseSchema:
        """Resolve multiple HITL items in one request with the same action.

        Skips items that are already resolved, expired, or do not support
        the requested action, recording per-item errors.
        """
        now = datetime.now(UTC)
        resolved_count = 0
        failed_count = 0
        errors: list[dict[str, Any]] = []
        resolved_ids: list[str] = []

        # Fetch all items in a single query with row-level lock
        stmt = select(HITLQueue).where(HITLQueue.id.in_(data.ids)).with_for_update()
        result = await db_session.execute(stmt)
        items_by_id = {item.id: item for item in result.scalars().all()}

        for item_id in data.ids:
            item = items_by_id.get(item_id)

            if item is None:
                failed_count += 1
                errors.append({"id": str(item_id), "error": "Not found"})
                continue

            if item.status == "resolved":
                failed_count += 1
                errors.append({"id": str(item_id), "error": "Already resolved"})
                continue

            if item.expires_at is not None and item.expires_at < now:
                item.status = "expired"
                failed_count += 1
                errors.append({"id": str(item_id), "error": "Expired"})
                continue

            if data.action not in item.available_actions:
                failed_count += 1
                errors.append(
                    {
                        "id": str(item_id),
                        "error": f"Action '{data.action}' not available",
                    }
                )
                continue

            # Apply resolution
            item.status = "resolved"
            item.resolution = data.action
            item.resolution_note = data.note
            item.resolved_by = request.user.id
            item.resolved_at = now
            resolved_count += 1
            resolved_ids.append(str(item_id))

        await db_session.flush()

        logger.info(
            "hitl.bulk_resolved",
            resolved=resolved_count,
            failed=failed_count,
            action=data.action,
            resolved_by=str(request.user.id),
        )

        # Fire-and-forget resume for each resumable item
        _RESUMABLE_TYPES = {
            "bid_approval",
            "dev_launch",
            "plan_review",
            "email_approval",
            "final_review",
            "agent_failure",
            "outreach_approval",
            "delivery_hold",
            "design_review",
            "design_client_approval",
            "concept_review",
        }
        for item_id_str in resolved_ids:
            item_id_uuid = uuid.UUID(item_id_str)
            item = items_by_id.get(item_id_uuid)
            if item is None:
                continue
            thread_id_from_payload = (item.payload or {}).get("thread_id")
            if item.type in _RESUMABLE_TYPES and thread_id_from_payload and data.action != "later":
                try:
                    import asyncio  # noqa: PLC0415

                    from src.core.graph import resume_from_hitl as _resume  # noqa: PLC0415

                    hitl_response: dict[str, Any] = {
                        "action": data.action,
                        "note": data.note,
                    }
                    if item.type == "bid_approval":
                        hitl_response["bid_ids"] = [(item.payload or {}).get("bid_id", "")]

                    task = asyncio.create_task(
                        _resume(
                            thread_id_from_payload,
                            hitl_response,
                            hitl_type=item.type,
                            valkey=valkey,
                        ),
                    )
                    _background_resume_tasks.add(task)
                    task.add_done_callback(_background_resume_tasks.discard)
                except Exception:  # noqa: BLE001 — intentional: dynamic import + task creation, any failure is non-fatal
                    logger.warning(
                        "hitl.bulk_resume_dispatch_failed",
                        hitl_id=item_id_str,
                        exc_info=True,
                    )

        # Single WebSocket publish with all resolved IDs
        if resolved_ids:
            try:
                await publish_event(
                    channels,
                    CHANNEL_HITL_RESOLVED,
                    {
                        "type": "hitl:bulk_resolved",
                        "data": {
                            "hitl_ids": resolved_ids,
                            "action": data.action,
                            "resolved_count": resolved_count,
                            "resolved_by": request.user.email,
                        },
                    },
                )
            except (OSError, ConnectionError):
                logger.debug("hitl.bulk_ws_publish_failed", exc_info=True)

            # Single Valkey pub/sub notification with summary
            try:
                await valkey.publish(
                    "hitl:resolved:bot",
                    json.dumps(
                        {
                            "bulk": True,
                            "hitl_ids": resolved_ids,
                            "action": data.action,
                            "resolved_count": resolved_count,
                            "resolved_by": request.user.email,
                        }
                    ),
                )
            except (OSError, ConnectionError):
                logger.warning("hitl.bulk_bot_notify_failed", exc_info=True)

        return HITLBulkResolveResponseSchema(
            resolved=resolved_count,
            failed=failed_count,
            errors=errors,
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
                select(func.count()).select_from(today_base.where(HITLQueue.status == "pending").subquery())
            )
        ).scalar_one()

        resolved_count = (
            await db_session.execute(
                select(func.count()).select_from(today_base.where(HITLQueue.status == "resolved").subquery())
            )
        ).scalar_one()

        expired_count = (
            await db_session.execute(
                select(func.count()).select_from(today_base.where(HITLQueue.status == "expired").subquery())
            )
        ).scalar_one()

        # -- Average resolution time (minutes) --------------------------------
        avg_stmt = (
            select(func.avg(extract("epoch", HITLQueue.resolved_at) - extract("epoch", HITLQueue.created_at)))
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

    # -----------------------------------------------------------------
    # GET /api/v1/hitl/trends
    # -----------------------------------------------------------------

    @get(
        "/trends",
        summary="HITL resolution trends",
        description="Daily created/resolved counts over the last N days.",
    )
    async def trends(
        self,
        db_session: AsyncSession,
        days: int = Parameter(default=7, ge=1, le=90, description="Number of days to look back"),
    ) -> HITLTrendsResponseSchema:
        """Return per-day created and resolved counts for the last *days* days."""
        now = datetime.now(UTC)
        window_start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)

        # -- Created per day ---------------------------------------------------
        created_stmt = (
            select(
                cast(HITLQueue.created_at, Date).label("day"),
                func.count().label("cnt"),
            )
            .where(HITLQueue.created_at >= window_start)
            .group_by(cast(HITLQueue.created_at, Date))
        )
        created_rows = (await db_session.execute(created_stmt)).all()
        created_map: dict[str, int] = {str(row[0]): row[1] for row in created_rows}

        # -- Resolved per day (by resolved_at) ---------------------------------
        resolved_stmt = (
            select(
                cast(HITLQueue.resolved_at, Date).label("day"),
                func.count().label("cnt"),
            )
            .where(HITLQueue.resolved_at >= window_start)
            .where(HITLQueue.resolved_at.is_not(None))
            .group_by(cast(HITLQueue.resolved_at, Date))
        )
        resolved_rows = (await db_session.execute(resolved_stmt)).all()
        resolved_map: dict[str, int] = {str(row[0]): row[1] for row in resolved_rows}

        # -- Build day-by-day list (most recent first) -------------------------
        trend_days: list[HITLTrendDaySchema] = []
        total_created = 0
        total_resolved = 0

        for offset in range(days):
            day = (now - timedelta(days=offset)).date()
            day_str = str(day)
            c = created_map.get(day_str, 0)
            r = resolved_map.get(day_str, 0)
            total_created += c
            total_resolved += r
            trend_days.append(HITLTrendDaySchema(date=day_str, created=c, resolved=r))

        return HITLTrendsResponseSchema(
            days=days,
            trends=trend_days,
            totals=HITLTrendTotalsSchema(
                created=total_created,
                resolved=total_resolved,
                pending=max(total_created - total_resolved, 0),
            ),
        )
