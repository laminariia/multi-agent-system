"""API routes for Deals — Pipeline B → A bridge."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from litestar import Controller, get, patch, post
from litestar.connection import Request
from litestar.exceptions import HTTPException, NotFoundException
from litestar.params import Parameter
from litestar.security.jwt import Token
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import require_role
from src.api.routes import _escape_like
from src.api.schemas import DealCreateSchema, DealUpdateSchema
from src.core.models import Deal, Lead, User

logger = structlog.get_logger(__name__)

# Strong references to background tasks so they aren't garbage-collected.
_background_tasks: set[asyncio.Task[Any]] = set()


class DealController(Controller):
    """Deal management and Pipeline B → A bridge."""

    path = "/api/v1/deals"

    @post("/", guards=[require_role("owner", "co_owner")])
    async def create_deal(
        self,
        data: DealCreateSchema,
        db_session: AsyncSession,
        request: Request[User, Token, Any],
    ) -> dict[str, Any]:
        """Create a new deal, optionally linked to a Pipeline B lead."""
        # Validate lead_id if provided
        if data.lead_id:
            try:
                lead_uuid = uuid.UUID(data.lead_id)
            except (ValueError, TypeError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid lead_id format: {data.lead_id}",
                ) from exc

            result = await db_session.execute(select(Lead).where(Lead.id == lead_uuid))
            lead = result.scalar_one_or_none()
            if lead is None:
                raise NotFoundException(detail="Lead not found")

        deal = Deal(
            title=data.title,
            agreed_scope=data.agreed_scope,
            budget=data.budget,
            lead_id=uuid.UUID(data.lead_id) if data.lead_id else None,
            deadline=data.deadline,
            client_context=data.client_context,
            design_versions=data.design_versions,
            conversation_history=data.conversation_history,
        )
        db_session.add(deal)
        await db_session.flush()

        logger.info("deal.created", deal_id=str(deal.id), title=data.title)

        return {
            "status": "created",
            "id": str(deal.id),
            "title": deal.title,
        }

    @get("/")
    async def list_deals(
        self,
        db_session: AsyncSession,
        status: str | None = None,
        search: str | None = None,
        sort: str | None = Parameter(
            default=None,
            description="Sort order: newest | oldest | title_asc | budget_desc",
        ),
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List deals with optional filtering by status and title search."""
        query = select(Deal)

        if status:
            query = query.where(Deal.status == status)
        if search:
            query = query.where(Deal.title.ilike("%" + _escape_like(search) + "%"))

        sort_map = {
            "newest": Deal.created_at.desc(),
            "oldest": Deal.created_at.asc(),
            "title_asc": Deal.title.asc(),
            "budget_desc": Deal.budget.desc().nulls_last(),
        }
        order = sort_map.get(sort, Deal.created_at.desc())
        query = query.order_by(order).limit(limit).offset(offset)

        result = await db_session.execute(query)
        deals = result.scalars().all()

        # Count total
        count_query = select(func.count(Deal.id))
        if status:
            count_query = count_query.where(Deal.status == status)
        if search:
            count_query = count_query.where(Deal.title.ilike("%" + _escape_like(search) + "%"))
        total = (await db_session.execute(count_query)).scalar() or 0

        return {
            "total": total,
            "deals": [
                {
                    "id": str(deal.id),
                    "lead_id": str(deal.lead_id) if deal.lead_id else None,
                    "title": deal.title,
                    "status": deal.status,
                    "agreed_scope": deal.agreed_scope,
                    "budget": float(deal.budget) if deal.budget is not None else None,
                    "deadline": deal.deadline.isoformat() if deal.deadline else None,
                    "pipeline_a_thread_id": deal.pipeline_a_thread_id,
                    "created_at": deal.created_at.isoformat() if deal.created_at else None,
                    "updated_at": deal.updated_at.isoformat() if deal.updated_at else None,
                }
                for deal in deals
            ],
        }

    @get("/{deal_id:str}")
    async def get_deal(
        self,
        deal_id: str,
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Get detailed information about a single deal."""
        try:
            deal_uuid = uuid.UUID(deal_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid deal ID format: {deal_id}",
            ) from exc

        result = await db_session.execute(select(Deal).where(Deal.id == deal_uuid))
        deal = result.scalar_one_or_none()

        if deal is None:
            raise NotFoundException(detail="Deal not found")

        return {
            "id": str(deal.id),
            "lead_id": str(deal.lead_id) if deal.lead_id else None,
            "title": deal.title,
            "status": deal.status,
            "agreed_scope": deal.agreed_scope,
            "budget": float(deal.budget) if deal.budget is not None else None,
            "deadline": deal.deadline.isoformat() if deal.deadline else None,
            "client_context": deal.client_context,
            "design_versions": deal.design_versions,
            "conversation_history": deal.conversation_history,
            "pipeline_a_thread_id": deal.pipeline_a_thread_id,
            "created_at": deal.created_at.isoformat() if deal.created_at else None,
            "updated_at": deal.updated_at.isoformat() if deal.updated_at else None,
        }

    @patch("/{deal_id:str}", guards=[require_role("owner", "co_owner")])
    async def update_deal(
        self,
        deal_id: str,
        data: DealUpdateSchema,
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Update deal fields (status, scope, budget, etc.)."""
        try:
            deal_uuid = uuid.UUID(deal_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid deal ID format: {deal_id}",
            ) from exc

        result = await db_session.execute(select(Deal).where(Deal.id == deal_uuid))
        deal = result.scalar_one_or_none()

        if deal is None:
            raise NotFoundException(detail="Deal not found")

        # Apply only provided (non-None) fields
        update_fields = data.model_dump(exclude_unset=True)
        for field, value in update_fields.items():
            setattr(deal, field, value)

        await db_session.flush()

        logger.info("deal.updated", deal_id=deal_id, fields=list(update_fields.keys()))

        return {
            "status": "updated",
            "id": str(deal.id),
            "updated_fields": list(update_fields.keys()),
        }

    @post(
        "/{deal_id:str}/start-development",
        guards=[require_role("owner", "co_owner")],
    )
    async def start_development(
        self,
        deal_id: str,
        db_session: AsyncSession,
        request: Request[User, Token, Any],
    ) -> dict[str, Any]:
        """Launch Pipeline A (development cycle) for a won deal.

        This is the Pipeline B → A bridge. It:
        1. Validates the deal exists and has status 'won'
        2. Builds a ProjectContext from deal data
        3. Launches Pipeline A (Planner → agents → Critic → Packager)
        4. Stores the pipeline_a_thread_id back on the deal
        """
        try:
            deal_uuid = uuid.UUID(deal_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid deal ID format: {deal_id}",
            ) from exc

        result = await db_session.execute(
            select(Deal).where(Deal.id == deal_uuid).with_for_update(),
        )
        deal = result.scalar_one_or_none()

        if deal is None:
            raise NotFoundException(detail="Deal not found")

        # Reject deals already in development
        if deal.status == "in_development":
            from src.core.exceptions import MASException  # noqa: PLC0415

            raise MASException(
                f"Deal {deal_id} is already in development (thread: {deal.pipeline_a_thread_id}).",
                details={"error_code": "CONFLICT", "status_code": 409},
            )

        # Only 'won' deals can start development
        if deal.status != "won":
            from src.core.exceptions import MASException  # noqa: PLC0415

            raise MASException(
                f"Only deals with status 'won' can start development. Current status: '{deal.status}'.",
                details={"error_code": "CONFLICT", "status_code": 409},
            )

        thread_id = f"deal-pipeline-{deal.id}"

        # Build pipeline payload from deal context
        from src.worker.tasks import run_project_pipeline  # noqa: PLC0415

        payload: dict[str, Any] = {
            "project_id": str(deal.id),
            "job_id": "",  # No job — this comes from Pipeline B
            "platform": "outreach",
            "requirements": deal.agreed_scope or "",
            "budget": float(deal.budget) if deal.budget else 0,
            "client": deal.client_context or {},
            "user_id": str(request.user.id),
        }

        if deal.deadline:
            payload["deadline"] = deal.deadline

        # Include design versions as initial artifacts hint
        if deal.design_versions:
            payload["design_versions"] = deal.design_versions

        # Launch Pipeline A as background task
        task = asyncio.create_task(run_project_pipeline(payload))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        # Update deal with pipeline linkage
        deal.status = "in_development"
        deal.pipeline_a_thread_id = thread_id
        await db_session.flush()

        logger.info(
            "deal.development_started",
            deal_id=deal_id,
            thread_id=thread_id,
            by=str(request.user.id),
        )

        return {
            "status": "started",
            "deal_id": str(deal.id),
            "thread_id": thread_id,
            "has_design_versions": deal.design_versions is not None and len(deal.design_versions) > 0,
            "message": f"Pipeline A started for deal {deal.id}. Thread ID: {thread_id}.",
        }
