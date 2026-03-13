"""Bid management routes.

Provides endpoints for listing bids with job context (Kanban view),
viewing bid details, and updating bid status.

Mounted at ``/api/v1/bids``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import structlog
from litestar import Controller, get, patch
from litestar.exceptions import NotFoundException
from litestar.params import Parameter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.routes import _escape_like
from src.core.models import Bid, Job

logger = structlog.get_logger(__name__)


class BidController(Controller):
    """Browse and manage bids (Kanban view)."""

    path = "/api/v1/bids"
    tags = ["bids"]

    # -----------------------------------------------------------------
    # GET /api/v1/bids
    # -----------------------------------------------------------------

    @get(
        "/",
        summary="List bids with job context",
        description="Paginated bid list filterable by status, platform, and search. Includes joined job info.",
    )
    async def list_bids(
        self,
        db_session: AsyncSession,
        status: str | None = Parameter(
            default=None,
            description="Filter: draft | sent | viewed | shortlisted | won | lost | rejected",
        ),
        platform: str | None = Parameter(
            default=None,
            description="Filter by job platform: freelancer | upwork | fl_ru | kwork",
        ),
        search: str | None = Parameter(
            default=None,
            description="Search bids by job title (case-insensitive substring)",
        ),
        sort: str | None = Parameter(
            default=None,
            description="Sort order: newest | oldest | amount_high | amount_low",
        ),
        limit: int = Parameter(default=20, ge=1, le=100),
        offset: int = Parameter(default=0, ge=0),
    ) -> dict[str, Any]:
        """Return a filtered, paginated list of bids with job info."""
        stmt = select(Bid).options(selectinload(Bid.job))

        if status is not None:
            stmt = stmt.where(Bid.status == status)
        if platform is not None:
            stmt = stmt.join(Bid.job).where(Job.platform == platform)
        if isinstance(search, str):
            if platform is None:
                stmt = stmt.join(Bid.job)
            stmt = stmt.where(Job.title.ilike("%" + _escape_like(search) + "%"))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db_session.execute(count_stmt)).scalar_one()

        sort_map = {
            "newest": Bid.created_at.desc(),
            "oldest": Bid.created_at.asc(),
            "amount_high": Bid.bid_amount.desc(),
            "amount_low": Bid.bid_amount.asc(),
        }
        order = sort_map.get(sort or "", Bid.created_at.desc())
        stmt = stmt.order_by(order).limit(limit).offset(offset)
        result = await db_session.execute(stmt)
        bids = result.scalars().unique().all()

        return {
            "total": total,
            "bids": [_bid_to_dict(b) for b in bids],
        }

    # -----------------------------------------------------------------
    # GET /api/v1/bids/stats
    # -----------------------------------------------------------------

    @get("/stats", summary="Bid statistics for Kanban header")
    async def stats(
        self,
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Return aggregated bid stats: counts by status."""
        status_stmt = select(Bid.status, func.count()).group_by(Bid.status)
        status_rows = (await db_session.execute(status_stmt)).all()
        by_status = {row[0]: row[1] for row in status_rows}

        total_stmt = select(func.count()).select_from(Bid)
        total = (await db_session.execute(total_stmt)).scalar_one()

        # Revenue from won bids
        won_stmt = select(func.sum(Bid.bid_amount)).where(Bid.status == "won")
        won_revenue = (await db_session.execute(won_stmt)).scalar_one() or Decimal("0")

        return {
            "total": total,
            "by_status": by_status,
            "won_revenue": float(won_revenue),
        }

    # -----------------------------------------------------------------
    # GET /api/v1/bids/{bid_id}
    # -----------------------------------------------------------------

    @get(
        "/{bid_id:uuid}",
        summary="Bid detail",
        description="Full bid detail including proposal text and job context.",
    )
    async def get_bid(
        self,
        bid_id: uuid.UUID,
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Retrieve a single bid with its associated job.

        Raises:
            NotFoundException: When no bid matches the given UUID.
        """
        stmt = select(Bid).where(Bid.id == bid_id).options(selectinload(Bid.job))
        result = await db_session.execute(stmt)
        bid = result.scalar_one_or_none()

        if bid is None:
            raise NotFoundException(detail=f"Bid {bid_id} not found")

        return _bid_to_dict(bid, include_proposal=True)

    # -----------------------------------------------------------------
    # PATCH /api/v1/bids/{bid_id}
    # -----------------------------------------------------------------

    @patch(
        "/{bid_id:uuid}",
        summary="Update bid status",
        status_code=200,
    )
    async def update_bid(
        self,
        bid_id: uuid.UUID,
        data: dict[str, Any],
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Update a bid's status (e.g., mark as won/lost after platform response).

        Only ``status`` field is accepted.

        Raises:
            NotFoundException: When the bid does not exist.
        """
        stmt = select(Bid).where(Bid.id == bid_id)
        result = await db_session.execute(stmt)
        bid = result.scalar_one_or_none()

        if bid is None:
            raise NotFoundException(detail=f"Bid {bid_id} not found")

        allowed_statuses = ("draft", "sent", "viewed", "shortlisted", "won", "lost", "rejected")
        new_status = data.get("status")
        if new_status and new_status in allowed_statuses:
            bid.status = new_status

        await db_session.flush()
        logger.info("bid.updated", bid_id=str(bid_id), status=bid.status)

        return {"id": str(bid.id), "status": bid.status}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bid_to_dict(bid: Bid, *, include_proposal: bool = False) -> dict[str, Any]:
    """Serialize a Bid ORM instance to a dict."""
    job = bid.job
    d: dict[str, Any] = {
        "id": str(bid.id),
        "job_id": str(bid.job_id),
        "status": bid.status,
        "bid_amount": float(bid.bid_amount),
        "estimated_days": bid.estimated_days,
        "platform_bid_id": bid.platform_bid_id,
        "version": bid.version,
        "generation_model": bid.generation_model,
        "created_at": bid.created_at.isoformat() if bid.created_at else None,
        "sent_at": bid.sent_at.isoformat() if bid.sent_at else None,
        "response_at": bid.response_at.isoformat() if bid.response_at else None,
    }
    if include_proposal:
        d["proposal_text"] = bid.proposal_text

    if job is not None:
        d["job"] = {
            "id": str(job.id),
            "platform": job.platform,
            "title": job.title,
            "status": job.status,
            "budget_min": float(job.budget_min) if job.budget_min is not None else None,
            "budget_max": float(job.budget_max) if job.budget_max is not None else None,
            "currency": job.currency,
            "url": job.url,
            "score": float(job.score) if job.score is not None else None,
        }
    else:
        d["job"] = None

    return d
