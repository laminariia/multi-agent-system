"""API routes for Pipeline B -- Geo Scout + Outreach pipeline."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
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
from src.api.routes import _escape_like
from src.api.schemas import PipelineBScanRequestSchema
from src.core.models import Lead, TouchHistory, User
from src.core.touch_sequence import TouchState

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

        Request body: {"city": "Berlin", "mode": "geoscanner", "categories": [...]}
        """
        city = data.city.strip()
        mode = data.mode
        categories = data.categories

        # Run pipeline in background (don't block the request)
        thread_id = uuid.uuid4().hex

        from src.core.graph import run_pipeline_b  # noqa: PLC0415

        # Start the pipeline as a background task (stored to prevent GC)
        task = asyncio.create_task(run_pipeline_b(city, thread_id=thread_id, user_id=str(request.user.id)))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        logger.info(
            "pipeline_b_scan_started",
            city=city,
            mode=mode,
            categories=categories,
            thread_id=thread_id,
        )

        return {
            "status": "started",
            "thread_id": thread_id,
            "city": city,
            "mode": mode,
            "categories": categories,
            "message": (
                f"Geo scan started for {city} (mode={mode}). Check /api/v1/pipeline-b/leads?city={city} for results."
            ),
        }

    @get("/leads")
    async def list_leads(
        self,
        db_session: AsyncSession,
        city: str | None = None,
        status: str | None = None,
        temperature: str | None = Parameter(
            default=None,
            description="Filter by temperature: hot | warm | cold",
        ),
        category: str | None = Parameter(
            default=None,
            description="Filter by business category",
        ),
        source: str | None = Parameter(
            default=None,
            description="Filter by source: geo_scanner | web_search | telegram",
        ),
        search: str | None = None,
        sort: str | None = Parameter(
            default=None,
            description="Sort order: newest | oldest | name_asc | name_desc | city_asc | score_desc",
        ),
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List discovered leads with filtering by city, status, temperature, category, source."""
        query = select(Lead)

        if city:
            query = query.where(Lead.city == city)
        if status:
            query = query.where(Lead.status == status)
        if temperature:
            query = query.where(Lead.temperature == temperature)
        if category:
            query = query.where(Lead.category == category)
        if source:
            query = query.where(Lead.source == source)
        if search:
            query = query.where(Lead.name.ilike("%" + _escape_like(search) + "%"))

        sort_map = {
            "newest": Lead.discovered_at.desc(),
            "oldest": Lead.discovered_at.asc(),
            "name_asc": Lead.name.asc(),
            "name_desc": Lead.name.desc(),
            "city_asc": Lead.city.asc().nulls_last(),
            "score_desc": Lead.lead_score.desc().nulls_last(),
        }
        order = sort_map.get(sort, Lead.discovered_at.desc())
        query = query.order_by(order).limit(limit).offset(offset)

        result = await db_session.execute(query)
        leads = result.scalars().all()

        # Count total with same filters
        count_query = select(func.count(Lead.id))
        if city:
            count_query = count_query.where(Lead.city == city)
        if status:
            count_query = count_query.where(Lead.status == status)
        if temperature:
            count_query = count_query.where(Lead.temperature == temperature)
        if category:
            count_query = count_query.where(Lead.category == category)
        if source:
            count_query = count_query.where(Lead.source == source)
        if search:
            count_query = count_query.where(Lead.name.ilike("%" + _escape_like(search) + "%"))
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
                    "telegram_username": lead.telegram_username,
                    "status": lead.status,
                    "temperature": lead.temperature,
                    "lead_score": lead.lead_score,
                    "source": lead.source,
                    "enrichment_source": lead.enrichment_source,
                    "discovered_at": (lead.discovered_at.isoformat() if lead.discovered_at else None),
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
        """Get detailed information about a single lead by UUID, including touch history."""
        try:
            lead_uuid = uuid.UUID(lead_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid lead ID format: {lead_id}") from exc

        result = await db_session.execute(select(Lead).where(Lead.id == lead_uuid))
        lead = result.scalar_one_or_none()

        if lead is None:
            raise HTTPException(status_code=404, detail="Lead not found")

        # Fetch touch history for this lead
        th_result = await db_session.execute(
            select(TouchHistory).where(TouchHistory.lead_id == lead_uuid).order_by(TouchHistory.created_at.asc())
        )
        touches = th_result.scalars().all()

        touch_history_list = [
            {
                "id": str(th.id),
                "step_index": th.step_index,
                "template": th.template,
                "channel": th.channel,
                "content": th.content,
                "status": th.status,
                "error_message": th.error_message,
                "created_at": th.created_at.isoformat() if th.created_at else None,
            }
            for th in touches
        ]

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
            "telegram_username": lead.telegram_username,
            "website": lead.website,
            "social_links": lead.social_links,
            "enrichment_source": lead.enrichment_source,
            "enrichment_cost": (float(lead.enrichment_cost) if lead.enrichment_cost is not None else None),
            "enrichment_data": lead.enrichment_data,
            "status": lead.status,
            "osm_id": lead.osm_id,
            "discovered_at": (lead.discovered_at.isoformat() if lead.discovered_at else None),
            "lead_score": lead.lead_score,
            "temperature": lead.temperature,
            "google_rating": (float(lead.google_rating) if lead.google_rating is not None else None),
            "review_count": lead.review_count,
            # Pipeline B extensions
            "analysis_tier": lead.analysis_tier,
            "battlecard_json": lead.battlecard_json,
            "scoring_rules": lead.scoring_rules,
            "touch_state": lead.touch_state,
            "touch_count": lead.touch_count,
            "next_touch_at": (lead.next_touch_at.isoformat() if lead.next_touch_at else None),
            "last_contacted_at": (lead.last_contacted_at.isoformat() if lead.last_contacted_at else None),
            "channel_used": lead.channel_used,
            "touch_history": touch_history_list,
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
            raise HTTPException(status_code=400, detail=f"Invalid lead ID format: {lead_id}") from exc

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
        except Exception as exc:  # noqa: BLE001 -- intentional: enrichment waterfall may fail in many ways
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
        status_counts = await db_session.execute(select(Lead.status, func.count(Lead.id)).group_by(Lead.status))
        status_map = {row[0]: row[1] for row in status_counts}

        # Count leads by city
        city_counts = await db_session.execute(
            select(Lead.city, func.count(Lead.id)).group_by(Lead.city).order_by(func.count(Lead.id).desc()).limit(10)
        )
        top_cities = [{"city": row[0], "count": row[1]} for row in city_counts]

        return {
            "total_leads": sum(status_map.values()),
            "by_status": status_map,
            "top_cities": top_cities,
        }

    @get("/analytics")
    async def get_analytics(
        self,
        db_session: AsyncSession,
        days: int = Parameter(default=30, ge=1, le=365, description="Lookback window in days"),
    ) -> dict[str, Any]:
        """Pipeline B analytics: leads by status, temperature, city, and conversion rate."""
        cutoff = datetime.now(UTC) - timedelta(days=days)

        # Leads by status (within window)
        status_result = await db_session.execute(
            select(Lead.status, func.count(Lead.id)).where(Lead.discovered_at >= cutoff).group_by(Lead.status)
        )
        leads_by_status = {row[0]: row[1] for row in status_result}

        # Leads by temperature (within window)
        temp_result = await db_session.execute(
            select(Lead.temperature, func.count(Lead.id))
            .where(Lead.discovered_at >= cutoff, Lead.temperature.isnot(None))
            .group_by(Lead.temperature)
        )
        leads_by_temperature = {row[0]: row[1] for row in temp_result}

        # Leads by city -- top 10 (within window)
        city_result = await db_session.execute(
            select(Lead.city, func.count(Lead.id))
            .where(Lead.discovered_at >= cutoff, Lead.city.isnot(None))
            .group_by(Lead.city)
            .order_by(func.count(Lead.id).desc())
            .limit(10)
        )
        leads_by_city = [{"city": row[0], "count": row[1]} for row in city_result]

        # Conversion rate: leads that became deals or replied vs total
        total_in_window = sum(leads_by_status.values())
        converted_statuses = {"replied", "enriched", "contacted"}
        converted = sum(count for status, count in leads_by_status.items() if status in converted_statuses)
        conversion_rate = (converted / total_in_window * 100) if total_in_window > 0 else 0.0

        return {
            "days": days,
            "total_leads": total_in_window,
            "leads_by_status": leads_by_status,
            "leads_by_temperature": leads_by_temperature,
            "leads_by_city": leads_by_city,
            "conversion_rate": round(conversion_rate, 2),
        }

    # ------------------------------------------------------------------
    # Touch sequence control
    # ------------------------------------------------------------------

    @post(
        "/touch-sequence/{lead_id:str}/pause",
        guards=[require_role("owner", "co_owner", "moderator")],
    )
    async def pause_touch_sequence(
        self,
        db_session: AsyncSession,
        lead_id: str,
        request: Request[User, Token, Any],
    ) -> dict[str, Any]:
        """Pause a lead's touch sequence."""
        try:
            lead_uuid = uuid.UUID(lead_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid lead ID format: {lead_id}") from exc

        result = await db_session.execute(select(Lead).where(Lead.id == lead_uuid).with_for_update())
        lead = result.scalar_one_or_none()

        if lead is None:
            raise HTTPException(status_code=404, detail="Lead not found")

        if lead.touch_state in (TouchState.STOPPED, TouchState.COMPLETED, TouchState.REPLIED):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot pause: touch sequence is in terminal state '{lead.touch_state}'",
            )

        if lead.touch_state == TouchState.PAUSED:
            return {"status": "already_paused", "lead_id": lead_id}

        lead.touch_state = TouchState.PAUSED
        lead.next_touch_at = None
        await db_session.flush()

        logger.info("touch.paused", lead_id=lead_id, by=str(request.user.id))

        return {"status": "paused", "lead_id": lead_id}

    @post(
        "/touch-sequence/{lead_id:str}/resume",
        guards=[require_role("owner", "co_owner", "moderator")],
    )
    async def resume_touch_sequence(
        self,
        db_session: AsyncSession,
        lead_id: str,
        request: Request[User, Token, Any],
    ) -> dict[str, Any]:
        """Resume a paused touch sequence, recalculating next_touch_at."""
        try:
            lead_uuid = uuid.UUID(lead_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid lead ID format: {lead_id}") from exc

        result = await db_session.execute(select(Lead).where(Lead.id == lead_uuid).with_for_update())
        lead = result.scalar_one_or_none()

        if lead is None:
            raise HTTPException(status_code=404, detail="Lead not found")

        if lead.touch_state != TouchState.PAUSED:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot resume: touch sequence is not paused (current: '{lead.touch_state}')",
            )

        from src.core.touch_sequence import TouchSequenceManager  # noqa: PLC0415

        # Restore to active state and recalculate next touch
        lead.touch_state = TouchState.ACTIVE
        next_at = TouchSequenceManager._compute_next_touch_at(lead.touch_count or 0)
        lead.next_touch_at = next_at
        await db_session.flush()

        logger.info(
            "touch.resumed",
            lead_id=lead_id,
            next_touch_at=str(next_at) if next_at else None,
            by=str(request.user.id),
        )

        return {
            "status": "resumed",
            "lead_id": lead_id,
            "next_touch_at": next_at.isoformat() if next_at else None,
        }
