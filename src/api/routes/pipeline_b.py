"""API routes for Pipeline B -- Geo Scout + Outreach pipeline."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from litestar import Controller, get, post
from litestar.connection import Request
from litestar.exceptions import HTTPException
from litestar.params import Parameter
from litestar.security.jwt import Token
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import require_role
from src.api.schemas import PipelineBScanRequestSchema
from src.core.models import Lead, User

logger = structlog.get_logger(__name__)

# Strong references to background tasks so they aren't garbage-collected.
_background_tasks: set[asyncio.Task[Any]] = set()


class PipelineBController(Controller):
    """Pipeline B API endpoints for geo scanning and outreach."""

    path = "/api/v1/pipeline-b"

    @post("/scan", guards=[require_role("owner", "co_owner", "moderator")])
    async def start_scan(
        self,
        data: PipelineBScanRequestSchema,
        db_session: AsyncSession,
        request: Request[User, Token, Any],
    ) -> dict[str, Any]:
        """Trigger a Pipeline B geo scan for a city.

        Request body: {"city": "Berlin"}
        """
        city = data.city.strip()

        # Run pipeline in background (don't block the request)
        thread_id = uuid.uuid4().hex

        from src.core.graph import run_pipeline_b  # noqa: PLC0415

        # Start the pipeline as a background task (stored to prevent GC)
        task = asyncio.create_task(
            run_pipeline_b(city, thread_id=thread_id, user_id=str(request.user.id))
        )
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
        search: str | None = None,
        sort: str | None = Parameter(
            default=None,
            description="Sort order: newest | oldest | name_asc | name_desc | city_asc",
        ),
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List discovered leads, optionally filtered by city, status, and name search."""
        query = select(Lead)

        if city:
            query = query.where(Lead.city == city)
        if status:
            query = query.where(Lead.status == status)
        if search:
            query = query.where(Lead.name.ilike(f"%{search}%"))

        sort_map = {
            "newest": Lead.discovered_at.desc(),
            "oldest": Lead.discovered_at.asc(),
            "name_asc": Lead.name.asc(),
            "name_desc": Lead.name.desc(),
            "city_asc": Lead.city.asc().nulls_last(),
        }
        order = sort_map.get(sort, Lead.discovered_at.desc())
        query = query.order_by(order).limit(limit).offset(offset)

        result = await db_session.execute(query)
        leads = result.scalars().all()

        # Count total
        count_query = select(func.count(Lead.id))
        if city:
            count_query = count_query.where(Lead.city == city)
        if status:
            count_query = count_query.where(Lead.status == status)
        if search:
            count_query = count_query.where(Lead.name.ilike(f"%{search}%"))
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
                    "latitude": float(lead.latitude) if lead.latitude is not None else None,
                    "longitude": float(lead.longitude) if lead.longitude is not None else None,
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

    @get("/leads/{lead_id:str}")
    async def get_lead(
        self,
        db_session: AsyncSession,
        lead_id: str,
    ) -> dict[str, Any]:
        """Get detailed information about a single lead by UUID."""
        try:
            lead_uuid = uuid.UUID(lead_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid lead ID format: {lead_id}"
            ) from exc

        result = await db_session.execute(select(Lead).where(Lead.id == lead_uuid))
        lead = result.scalar_one_or_none()

        if lead is None:
            raise HTTPException(status_code=404, detail="Lead not found")

        return {
            "id": str(lead.id),
            "name": lead.name,
            "category": lead.category,
            "city": lead.city,
            "country": lead.country,
            "address": lead.address,
            "latitude": float(lead.latitude) if lead.latitude is not None else None,
            "longitude": float(lead.longitude) if lead.longitude is not None else None,
            "h3_index": lead.h3_index,
            "phone": lead.phone,
            "email": lead.email,
            "website": lead.website,
            "social_links": lead.social_links,
            "enrichment_source": lead.enrichment_source,
            "enrichment_cost": (
                float(lead.enrichment_cost) if lead.enrichment_cost is not None else None
            ),
            "enrichment_data": lead.enrichment_data,
            "status": lead.status,
            "osm_id": lead.osm_id,
            "discovered_at": (
                lead.discovered_at.isoformat() if lead.discovered_at else None
            ),
        }

    @post(
        "/leads/{lead_id:str}/enrich",
        guards=[require_role("owner", "co_owner", "moderator")],
    )
    async def enrich_lead(
        self,
        db_session: AsyncSession,
        lead_id: str,
        request: Request[User, Token, Any],
    ) -> dict[str, Any]:
        """Trigger enrichment waterfall for a single lead.

        The waterfall order is: OSINT (free) -> Hunter.io -> Apollo.io.
        Enrichment runs synchronously for a single lead so the caller
        gets the result immediately.
        """
        try:
            lead_uuid = uuid.UUID(lead_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid lead ID format: {lead_id}"
            ) from exc

        result = await db_session.execute(select(Lead).where(Lead.id == lead_uuid))
        lead = result.scalar_one_or_none()

        if lead is None:
            raise HTTPException(status_code=404, detail="Lead not found")

        # Run enrichment waterfall
        try:
            from src.enrichment.waterfall import EnrichmentWaterfall  # noqa: PLC0415

            waterfall = EnrichmentWaterfall()
            enrichment = await waterfall.enrich(
                name=lead.name,
                address=lead.address or "",
                city=lead.city or "",
                phone=lead.phone or "",
            )

            lead.email = enrichment.get("email") or lead.email
            lead.phone = enrichment.get("phone") or lead.phone
            lead.website = enrichment.get("website") or lead.website
            lead.social_links = enrichment.get("social_links") or lead.social_links
            lead.enrichment_source = enrichment.get("source", "osint")
            lead.enrichment_data = enrichment
            lead.status = "enriched"

            await db_session.flush()
            await waterfall.close()

            logger.info("lead.enriched", lead_id=lead_id, source=lead.enrichment_source)
            return {
                "status": "enriched",
                "lead_id": lead_id,
                "source": lead.enrichment_source,
                "email": lead.email,
            }
        except Exception as exc:
            logger.warning("lead.enrichment_failed", lead_id=lead_id, error=str(exc))
            return {
                "status": "failed",
                "lead_id": lead_id,
                "error": str(exc),
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
