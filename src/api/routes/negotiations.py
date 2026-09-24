"""Negotiation management routes.

Provides endpoints for listing active negotiations, viewing negotiation
details & messages, operator take-over / release, manual messaging, and
aggregated analytics.  Mounted at ``/api/v1/negotiations``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from litestar import Controller, Request, get, post
from litestar.channels import ChannelsPlugin
from litestar.exceptions import NotFoundException
from litestar.params import Parameter
from litestar.security.jwt import Token
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import require_role
from src.api.routes import _escape_like
from src.api.schemas import NegotiationReleaseSchema, NegotiationSendMessageSchema
from src.api.websocket import (
    CHANNEL_NEGOTIATION_MESSAGE,
    CHANNEL_NEGOTIATION_STATE,
    publish_event,
)
from src.core.exceptions import MASException
from src.core.models import Bid, ClientMessage, Job, Negotiation, User

logger = structlog.get_logger(__name__)


class NegotiationController(Controller):
    """Browse and manage bid negotiations."""

    path = "/api/v1/negotiations"
    tags = ["negotiations"]

    # -----------------------------------------------------------------
    # GET /api/v1/negotiations
    # -----------------------------------------------------------------

    @get(
        "/",
        summary="List active negotiations",
        description="Paginated list of negotiations with filters for state, platform, and search.",
    )
    async def list_negotiations(
        self,
        db_session: AsyncSession,
        negotiation_state: str | None = Parameter(
            default=None,
            query="state",
            description="Filter by negotiation state (e.g. initial, qualifying, negotiating, operator_override)",
        ),
        platform: str | None = Parameter(
            default=None,
            description="Filter by platform (freelancer, upwork, fl_ru, kwork)",
        ),
        search: str | None = Parameter(
            default=None,
            description="Case-insensitive substring search on job title",
        ),
        sort_by: str | None = Parameter(
            default=None,
            description="Sort order: newest (default), oldest, amount",
        ),
        limit: int = Parameter(default=20, ge=1, le=100, description="Page size"),
        offset: int = Parameter(default=0, ge=0, description="Pagination offset"),
    ) -> dict[str, Any]:
        """Return a filtered, paginated list of negotiations joined with bid and job data."""

        # Base query: Negotiation joined with Bid and Job
        base = select(Negotiation).join(Bid, Negotiation.bid_id == Bid.id).join(Job, Bid.job_id == Job.id)

        if negotiation_state is not None:
            base = base.where(Negotiation.state == negotiation_state)

        if platform is not None:
            base = base.where(Job.platform == platform)

        if isinstance(search, str):
            base = base.where(Job.title.ilike("%" + _escape_like(search) + "%"))

        # Total count
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await db_session.execute(count_stmt)).scalar_one()

        # Sort
        sort_map: dict[str, Any] = {
            "newest": Negotiation.created_at.desc(),
            "oldest": Negotiation.created_at.asc(),
            "amount": Negotiation.current_amount.desc().nulls_last(),
        }
        order = sort_map.get(sort_by, Negotiation.created_at.desc())  # type: ignore[arg-type]
        base = base.order_by(order).limit(limit).offset(offset)

        result = await db_session.execute(base)
        rows = result.scalars().unique().all()

        items: list[dict[str, Any]] = []
        for neg in rows:
            # Fetch bid and job info via a lightweight query
            bid_stmt = select(Bid).join(Job, Bid.job_id == Job.id).where(Bid.id == neg.bid_id)
            bid_result = await db_session.execute(bid_stmt)
            bid = bid_result.scalar_one_or_none()

            item: dict[str, Any] = {
                "id": str(neg.id),
                "bid_id": str(neg.bid_id),
                "state": neg.state,
                "previous_state": neg.previous_state,
                "rounds": neg.rounds,
                "original_amount": float(neg.original_amount) if neg.original_amount else None,
                "current_amount": float(neg.current_amount) if neg.current_amount else None,
                "outcome": neg.outcome,
                "followup_count": neg.followup_count,
                "created_at": neg.created_at.isoformat() if neg.created_at else None,
                "updated_at": neg.updated_at.isoformat() if neg.updated_at else None,
            }
            if bid is not None:
                job_stmt = select(Job).where(Job.id == bid.job_id)
                job_result = await db_session.execute(job_stmt)
                job = job_result.scalar_one_or_none()
                item["job_title"] = job.title if job else None
                item["platform"] = job.platform if job else None
                item["bid_amount"] = float(bid.bid_amount) if bid.bid_amount else None
            items.append(item)

        return {"items": items, "total": total}

    # -----------------------------------------------------------------
    # GET /api/v1/negotiations/{bid_id}
    # -----------------------------------------------------------------

    @get(
        "/{bid_id:uuid}",
        summary="Get negotiation details",
        description="Full negotiation detail including state, history, and metrics.",
    )
    async def get_negotiation(
        self,
        bid_id: uuid.UUID,
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Return full negotiation details for a bid, including message count and bid info.

        Raises:
            NotFoundException: When no negotiation exists for the given bid.
        """
        stmt = select(Negotiation).where(Negotiation.bid_id == bid_id)
        result = await db_session.execute(stmt)
        neg = result.scalar_one_or_none()

        if neg is None:
            raise NotFoundException(detail=f"Negotiation for bid {bid_id} not found")

        # Count messages
        msg_count_stmt = select(func.count()).select_from(
            select(ClientMessage).where(ClientMessage.bid_id == bid_id).subquery()
        )
        message_count = (await db_session.execute(msg_count_stmt)).scalar_one()

        # Fetch bid + job
        bid_stmt = select(Bid).where(Bid.id == bid_id)
        bid = (await db_session.execute(bid_stmt)).scalar_one_or_none()

        bid_info: dict[str, Any] = {}
        if bid is not None:
            job_stmt = select(Job).where(Job.id == bid.job_id)
            job = (await db_session.execute(job_stmt)).scalar_one_or_none()
            bid_info = {
                "bid_id": str(bid.id),
                "bid_amount": float(bid.bid_amount) if bid.bid_amount else None,
                "status": bid.status,
                "job_id": str(bid.job_id),
                "job_title": job.title if job else None,
                "platform": job.platform if job else None,
            }

        return {
            "id": str(neg.id),
            "bid_id": str(neg.bid_id),
            "state": neg.state,
            "previous_state": neg.previous_state,
            "state_version": neg.state_version,
            "state_reason": neg.state_reason,
            "original_amount": float(neg.original_amount) if neg.original_amount else None,
            "current_amount": float(neg.current_amount) if neg.current_amount else None,
            "final_amount": float(neg.final_amount) if neg.final_amount else None,
            "rounds": neg.rounds,
            "followup_count": neg.followup_count,
            "last_followup_at": neg.last_followup_at.isoformat() if neg.last_followup_at else None,
            "outcome": neg.outcome,
            "history": neg.history,
            "metadata": neg.metadata_json,
            "message_count": message_count,
            "created_at": neg.created_at.isoformat() if neg.created_at else None,
            "updated_at": neg.updated_at.isoformat() if neg.updated_at else None,
            "resolved_at": neg.resolved_at.isoformat() if neg.resolved_at else None,
            "bid": bid_info,
        }

    # -----------------------------------------------------------------
    # GET /api/v1/negotiations/{bid_id}/messages
    # -----------------------------------------------------------------

    @get(
        "/{bid_id:uuid}/messages",
        summary="Get conversation history",
        description="Paginated conversation messages for a negotiation.",
    )
    async def get_messages(
        self,
        bid_id: uuid.UUID,
        db_session: AsyncSession,
        direction: str | None = Parameter(
            default=None,
            description="Filter by direction: inbound, outbound",
        ),
        limit: int = Parameter(default=50, ge=1, le=200, description="Page size"),
        offset: int = Parameter(default=0, ge=0, description="Pagination offset"),
    ) -> dict[str, Any]:
        """Return conversation messages for a bid, sorted by created_at ascending.

        Raises:
            NotFoundException: When no negotiation exists for the given bid.
        """
        # Verify negotiation exists
        neg_stmt = select(Negotiation).where(Negotiation.bid_id == bid_id)
        neg_result = await db_session.execute(neg_stmt)
        if neg_result.scalar_one_or_none() is None:
            raise NotFoundException(detail=f"Negotiation for bid {bid_id} not found")

        base = select(ClientMessage).where(ClientMessage.bid_id == bid_id)

        if direction is not None:
            base = base.where(ClientMessage.direction == direction)

        # Total count
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await db_session.execute(count_stmt)).scalar_one()

        # Fetch page sorted by created_at ascending (chronological)
        base = base.order_by(ClientMessage.created_at.asc()).limit(limit).offset(offset)
        result = await db_session.execute(base)
        rows = result.scalars().all()

        messages = [
            {
                "id": str(msg.id),
                "bid_id": str(msg.bid_id),
                "direction": msg.direction,
                "sender": msg.sender,
                "message_type": msg.message_type,
                "content": msg.content,
                "platform": msg.platform,
                "external_id": msg.external_id,
                "auto_generated": msg.auto_generated,
                "hitl_reviewed": msg.hitl_reviewed,
                "metadata": msg.metadata_json,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
            for msg in rows
        ]

        return {"messages": messages, "total": total}

    # -----------------------------------------------------------------
    # POST /api/v1/negotiations/{bid_id}/messages
    # -----------------------------------------------------------------

    @post(
        "/{bid_id:uuid}/messages",
        summary="Operator sends a message",
        description="Manually send a message from the operator in the negotiation chat.",
        guards=[require_role("owner", "co_owner")],
        status_code=201,
    )
    async def send_message(
        self,
        bid_id: uuid.UUID,
        data: NegotiationSendMessageSchema,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
        channels: ChannelsPlugin,
    ) -> dict[str, Any]:
        """Create and persist an outbound operator message.

        Body:
            content (str): The message text to send.

        Raises:
            NotFoundException: When no negotiation exists for the given bid.
        """

        content = data.content.strip()

        # Verify negotiation exists
        neg_stmt = select(Negotiation).where(Negotiation.bid_id == bid_id)
        neg_result = await db_session.execute(neg_stmt)
        neg = neg_result.scalar_one_or_none()

        if neg is None:
            raise NotFoundException(detail=f"Negotiation for bid {bid_id} not found")

        # Resolve platform from the bid's job
        bid_stmt = select(Bid).where(Bid.id == bid_id)
        bid = (await db_session.execute(bid_stmt)).scalar_one_or_none()
        platform: str | None = None
        if bid is not None:
            job_stmt = select(Job).where(Job.id == bid.job_id)
            job = (await db_session.execute(job_stmt)).scalar_one_or_none()
            platform = job.platform if job else None

        # Create outbound operator message
        msg = ClientMessage(
            bid_id=bid_id,
            direction="outbound",
            sender="operator",
            content=content,
            platform=platform,
            auto_generated=False,
            hitl_reviewed=True,
        )
        db_session.add(msg)
        await db_session.flush()

        logger.info(
            "negotiation.operator_message",
            bid_id=str(bid_id),
            message_id=str(msg.id),
            by=str(request.user.id),
        )

        msg_payload = {
            "id": str(msg.id),
            "bid_id": str(msg.bid_id),
            "direction": msg.direction,
            "sender": msg.sender,
            "content": msg.content,
            "platform": msg.platform,
            "auto_generated": msg.auto_generated,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        }

        # Publish WebSocket event (guarded)
        try:
            await publish_event(
                channels,
                CHANNEL_NEGOTIATION_MESSAGE,
                {
                    "type": "negotiation:new_message",
                    "data": {
                        "bid_id": str(bid_id),
                        "message": msg_payload,
                    },
                },
            )
        except (OSError, ConnectionError):
            logger.debug("negotiation.ws_publish_failed", bid_id=str(bid_id), exc_info=True)

        return msg_payload

    # -----------------------------------------------------------------
    # POST /api/v1/negotiations/{bid_id}/take-over
    # -----------------------------------------------------------------

    @post(
        "/{bid_id:uuid}/take-over",
        summary="Operator takes control",
        description="Transition negotiation to operator_override state.",
        guards=[require_role("owner", "co_owner")],
        status_code=200,
    )
    async def take_over(
        self,
        bid_id: uuid.UUID,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
        channels: ChannelsPlugin,
    ) -> dict[str, Any]:
        """Move negotiation to ``operator_override`` state.

        Raises:
            NotFoundException: When no negotiation exists for the given bid.
            MASException: When already in operator_override state (409).
        """
        stmt = select(Negotiation).where(Negotiation.bid_id == bid_id).with_for_update()
        result = await db_session.execute(stmt)
        neg = result.scalar_one_or_none()

        if neg is None:
            raise NotFoundException(detail=f"Negotiation for bid {bid_id} not found")

        if neg.state == "operator_override":
            raise MASException(
                "Negotiation is already under operator control",
                details={"error_code": "CONFLICT", "status_code": 409},
            )

        old_state = neg.state
        neg.previous_state = old_state
        neg.state = "operator_override"
        neg.state_version += 1
        neg.state_reason = f"Operator take-over by user {request.user.id}"
        neg.updated_at = datetime.now(UTC)

        await db_session.flush()

        logger.info(
            "negotiation.take_over",
            bid_id=str(bid_id),
            old_state=old_state,
            by=str(request.user.id),
        )

        # Publish WebSocket event (guarded)
        try:
            await publish_event(
                channels,
                CHANNEL_NEGOTIATION_STATE,
                {
                    "type": "negotiation:state_changed",
                    "data": {
                        "bid_id": str(bid_id),
                        "old_state": old_state,
                        "new_state": "operator_override",
                        "reason": "operator_take_over",
                    },
                },
            )
        except (OSError, ConnectionError):
            logger.debug("negotiation.ws_publish_failed", bid_id=str(bid_id), exc_info=True)

        return {
            "id": str(neg.id),
            "bid_id": str(neg.bid_id),
            "state": neg.state,
            "previous_state": neg.previous_state,
        }

    # -----------------------------------------------------------------
    # POST /api/v1/negotiations/{bid_id}/release
    # -----------------------------------------------------------------

    @post(
        "/{bid_id:uuid}/release",
        summary="Release control back to AI",
        description="Operator releases negotiation control, optionally specifying target state.",
        guards=[require_role("owner", "co_owner")],
        status_code=200,
    )
    async def release(
        self,
        bid_id: uuid.UUID,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
        channels: ChannelsPlugin,
        data: NegotiationReleaseSchema | None = None,
    ) -> dict[str, Any]:
        """Release operator control of a negotiation back to AI.

        Optional body:
            target_state (str): State to transition to. Defaults to previous_state.

        Raises:
            NotFoundException: When no negotiation exists for the given bid.
            MASException: When not in operator_override state (409).
        """
        stmt = select(Negotiation).where(Negotiation.bid_id == bid_id).with_for_update()
        result = await db_session.execute(stmt)
        neg = result.scalar_one_or_none()

        if neg is None:
            raise NotFoundException(detail=f"Negotiation for bid {bid_id} not found")

        if neg.state != "operator_override":
            raise MASException(
                "Negotiation is not under operator control",
                details={"error_code": "CONFLICT", "status_code": 409},
            )

        # Determine target state: from body, previous_state, or default to "qualifying"
        target_state = None
        if data is not None:
            target_state = data.target_state

        if not target_state:
            target_state = neg.previous_state or "qualifying"

        old_state = neg.state
        neg.previous_state = old_state
        neg.state = target_state
        neg.state_version += 1
        neg.state_reason = f"Operator release by user {request.user.id}"
        neg.updated_at = datetime.now(UTC)

        await db_session.flush()

        logger.info(
            "negotiation.release",
            bid_id=str(bid_id),
            new_state=target_state,
            by=str(request.user.id),
        )

        # Publish WebSocket event (guarded)
        try:
            await publish_event(
                channels,
                CHANNEL_NEGOTIATION_STATE,
                {
                    "type": "negotiation:state_changed",
                    "data": {
                        "bid_id": str(bid_id),
                        "old_state": old_state,
                        "new_state": target_state,
                        "reason": "operator_release",
                    },
                },
            )
        except (OSError, ConnectionError):
            logger.debug("negotiation.ws_publish_failed", bid_id=str(bid_id), exc_info=True)

        return {
            "id": str(neg.id),
            "bid_id": str(neg.bid_id),
            "state": neg.state,
            "previous_state": neg.previous_state,
        }

    # -----------------------------------------------------------------
    # GET /api/v1/negotiations/analytics
    # -----------------------------------------------------------------

    @get(
        "/analytics",
        summary="Negotiation analytics",
        description="Aggregated negotiation metrics for the dashboard.",
    )
    async def analytics(
        self,
        db_session: AsyncSession,
        days: int = Parameter(default=30, ge=1, le=365, description="Number of days to look back"),
    ) -> dict[str, Any]:
        """Return negotiation analytics for the given period.

        Metrics: total, by_state, by_platform, avg_rounds, conversion_rate,
        avg_time_to_close_days, followup_effectiveness.
        """
        window_start = datetime.now(UTC) - timedelta(days=days)

        # Base: negotiations created within the window
        base = select(Negotiation).where(Negotiation.created_at >= window_start)

        # Total negotiations
        total_stmt = select(func.count()).select_from(base.subquery())
        total = (await db_session.execute(total_stmt)).scalar_one()

        # By state
        state_stmt = (
            select(Negotiation.state, func.count().label("cnt"))
            .where(Negotiation.created_at >= window_start)
            .group_by(Negotiation.state)
        )
        state_rows = (await db_session.execute(state_stmt)).all()
        by_state: dict[str, int] = {row[0]: row[1] for row in state_rows}

        # By platform (join with Bid -> Job)
        platform_stmt = (
            select(Job.platform, func.count().label("cnt"))
            .select_from(Negotiation)
            .join(Bid, Negotiation.bid_id == Bid.id)
            .join(Job, Bid.job_id == Job.id)
            .where(Negotiation.created_at >= window_start)
            .group_by(Job.platform)
        )
        platform_rows = (await db_session.execute(platform_stmt)).all()
        by_platform: dict[str, int] = {row[0]: row[1] for row in platform_rows}

        # Avg rounds
        avg_rounds_stmt = select(func.avg(Negotiation.rounds)).where(Negotiation.created_at >= window_start)
        avg_rounds_raw = (await db_session.execute(avg_rounds_stmt)).scalar_one()
        avg_rounds = round(float(avg_rounds_raw), 1) if avg_rounds_raw else 0.0

        # Conversion rate: won / total (where outcome is set)
        won_stmt = select(func.count()).select_from(
            select(Negotiation).where(Negotiation.created_at >= window_start, Negotiation.outcome == "won").subquery()
        )
        won_count = (await db_session.execute(won_stmt)).scalar_one()
        conversion_rate = round((won_count / total) * 100.0, 1) if total > 0 else 0.0

        # Avg time to close (for resolved negotiations)
        from sqlalchemy import extract  # noqa: PLC0415

        avg_close_stmt = select(
            func.avg(extract("epoch", Negotiation.resolved_at) - extract("epoch", Negotiation.created_at))
        ).where(
            Negotiation.created_at >= window_start,
            Negotiation.resolved_at.is_not(None),
        )
        avg_close_raw = (await db_session.execute(avg_close_stmt)).scalar_one()
        avg_time_to_close_days = round(float(avg_close_raw) / 86400.0, 1) if avg_close_raw else 0.0

        # Follow-up effectiveness: negotiations with followup_count > 0 that won
        followup_total_stmt = select(func.count()).select_from(
            select(Negotiation)
            .where(
                Negotiation.created_at >= window_start,
                Negotiation.followup_count > 0,
            )
            .subquery()
        )
        followup_total = (await db_session.execute(followup_total_stmt)).scalar_one()

        followup_won_stmt = select(func.count()).select_from(
            select(Negotiation)
            .where(
                Negotiation.created_at >= window_start,
                Negotiation.followup_count > 0,
                Negotiation.outcome == "won",
            )
            .subquery()
        )
        followup_won = (await db_session.execute(followup_won_stmt)).scalar_one()
        followup_effectiveness = round((followup_won / followup_total) * 100.0, 1) if followup_total > 0 else 0.0

        return {
            "period_days": days,
            "total": total,
            "by_state": by_state,
            "by_platform": by_platform,
            "avg_rounds": avg_rounds,
            "conversion_rate": conversion_rate,
            "avg_time_to_close_days": avg_time_to_close_days,
            "followup_effectiveness": followup_effectiveness,
            "won": won_count,
            "active": by_state.get("qualifying", 0) + by_state.get("negotiating", 0) + by_state.get("initial", 0),
        }
