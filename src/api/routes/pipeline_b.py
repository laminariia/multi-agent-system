"""API routes for Pipeline B -- Geo Scout + Outreach pipeline."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from litestar import Controller, get, post
from litestar.exceptions import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import require_role
from src.core.models import Lead

logger = structlog.get_logger(__name__)

# Strong references to background tasks so they aren't garbage-collected.
_background_tasks: set[asyncio.Task[Any]] = set()


class PipelineBController(Controller):
    """Pipeline B API endpoints for geo scanning and outreach."""

    path = "/api/v1/pipeline-b"

    @post("/scan", guards=[require_role("owner", "co_owner", "moderator")])
    async def start_scan(
        self,
        data: dict[str, Any],
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Trigger a Pipeline B geo scan for a city.

        Request body: {"city": "Berlin"}
        """
        city = data.get("city", "").strip()
        if not city:
            raise HTTPException(status_code=400, detail="City name is required")

        # Run pipeline in background (don't block the request)
        thread_id = uuid.uuid4().hex

        from src.core.graph import run_pipeline_b  # noqa: PLC0415

        # Start the pipeline as a background task (stored to prevent GC)
        task = asyncio.create_task(run_pipeline_b(city, thread_id=thread_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        logger.info("pipeline_b_scan_started", city=city, thread_id=thread_id)

        return {
            "status": "started",
            "thread_id": thread_id,
            "city": city,
            "message": (
                f"Geo scan started for {city}. "
                f"Check /api/v1/pipeline-b/leads?city={city} for results."
            ),
        }

    @get("/leads")
    async def list_leads(
        self,
        db_session: AsyncSession,
        city: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List discovered leads, optionally filtered by city and status."""
        query = select(Lead)

        if city:
            query = query.where(Lead.city == city)
        if status:
            query = query.where(Lead.status == status)

        query = query.order_by(Lead.discovered_at.desc()).limit(limit).offset(offset)

        result = await db_session.execute(query)
        leads = result.scalars().all()

        # Count total
        count_query = select(func.count(Lead.id))
        if city:
            count_query = count_query.where(Lead.city == city)
        if status:
            count_query = count_query.where(Lead.status == status)
        total = (await db_session.execute(count_query)).scalar() or 0

        return {
            "total": total,
            "leads": [
                {
                    "id": str(lead.id),
                    "name": lead.name,
                    "category": lead.category,
                    "city": lead.city,
                    "address": lead.address,
                    "phone": lead.phone,
                    "email": lead.email,
                    "status": lead.status,
                    "enrichment_source": lead.enrichment_source,
                    "discovered_at": (
                        lead.discovered_at.isoformat() if lead.discovered_at else None
                    ),
                }
                for lead in leads
            ],
        }

    @get("/stats")
    async def scan_stats(
        self,
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Get Pipeline B statistics."""
        # Count leads by status
        status_counts = await db_session.execute(
            select(Lead.status, func.count(Lead.id)).group_by(Lead.status)
        )
        status_map = {row[0]: row[1] for row in status_counts}

        # Count leads by city
        city_counts = await db_session.execute(
            select(Lead.city, func.count(Lead.id))
            .group_by(Lead.city)
            .order_by(func.count(Lead.id).desc())
            .limit(10)
        )
        top_cities = [{"city": row[0], "count": row[1]} for row in city_counts]

        return {
            "total_leads": sum(status_map.values()),
            "by_status": status_map,
            "top_cities": top_cities,
        }
