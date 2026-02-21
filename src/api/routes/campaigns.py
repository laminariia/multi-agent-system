"""API routes for Campaign CRUD (Pipeline B outreach campaigns)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from litestar import Controller, delete, get, post, put
from litestar.exceptions import HTTPException
from litestar.params import Parameter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import require_role
from src.api.schemas import (
    CampaignCreateSchema,
    CampaignListResponseSchema,
    CampaignResponseSchema,
    CampaignUpdateSchema,
)
from src.core.models import CampaignLead, EmailCampaign, Lead

logger = structlog.get_logger(__name__)


def _campaign_to_response(campaign: EmailCampaign) -> CampaignResponseSchema:
    """Convert an EmailCampaign model to a response schema."""
    return CampaignResponseSchema(
        id=campaign.id,
        name=campaign.name,
        subject_template=campaign.subject_template,
        body_template=campaign.body_template,
        target_cities=campaign.target_cities,
        target_categories=campaign.target_categories,
        status=campaign.status,
        total_leads=campaign.total_leads,
        sent_count=campaign.sent_count,
        open_count=campaign.open_count,
        reply_count=campaign.reply_count,
        bounce_count=campaign.bounce_count,
        created_at=campaign.created_at,
        started_at=campaign.started_at,
    )


class CampaignController(Controller):
    """CRUD endpoints for outreach campaigns."""

    path = "/api/v1/campaigns"

    @get("/")
    async def list_campaigns(
        self,
        db_session: AsyncSession,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> CampaignListResponseSchema:
        """List campaigns with optional status filter."""
        query = select(EmailCampaign)
        count_query = select(func.count(EmailCampaign.id))

        if status:
            query = query.where(EmailCampaign.status == status)
            count_query = count_query.where(EmailCampaign.status == status)

        query = query.order_by(EmailCampaign.created_at.desc()).limit(limit).offset(offset)

        result = await db_session.execute(query)
        campaigns = result.scalars().all()
        total = (await db_session.execute(count_query)).scalar() or 0

        return CampaignListResponseSchema(
            campaigns=[_campaign_to_response(c) for c in campaigns],
            total=total,
        )

    @get("/{campaign_id:str}")
    async def get_campaign(
        self,
        db_session: AsyncSession,
        campaign_id: str,
    ) -> CampaignResponseSchema:
        """Get a single campaign by ID."""
        campaign = await _get_campaign_or_404(db_session, campaign_id)
        return _campaign_to_response(campaign)

    @post("/", guards=[require_role("owner", "co_owner", "moderator")])
    async def create_campaign(
        self,
        data: CampaignCreateSchema,
        db_session: AsyncSession,
    ) -> CampaignResponseSchema:
        """Create a new outreach campaign."""
        campaign = EmailCampaign(
            name=data.name,
            subject_template=data.subject_template,
            body_template=data.body_template,
            target_cities=data.target_cities,
            target_categories=data.target_categories,
            status="draft",
        )
        db_session.add(campaign)
        await db_session.flush()
        await db_session.refresh(campaign)
        logger.info("campaign.created", campaign_id=str(campaign.id), name=campaign.name)
        return _campaign_to_response(campaign)

    @put("/{campaign_id:str}", guards=[require_role("owner", "co_owner", "moderator")])
    async def update_campaign(
        self,
        data: CampaignUpdateSchema,
        db_session: AsyncSession,
        campaign_id: str,
    ) -> CampaignResponseSchema:
        """Update an existing campaign."""
        campaign = await _get_campaign_or_404(db_session, campaign_id)

        for field_name in ("name", "subject_template", "body_template", "target_cities", "target_categories", "status"):
            value = getattr(data, field_name, None)
            if value is not None:
                setattr(campaign, field_name, value)

        await db_session.flush()
        await db_session.refresh(campaign)
        logger.info("campaign.updated", campaign_id=str(campaign.id))
        return _campaign_to_response(campaign)

    @delete("/{campaign_id:str}", guards=[require_role("owner", "co_owner")], status_code=200)
    async def delete_campaign(
        self,
        db_session: AsyncSession,
        campaign_id: str,
    ) -> dict[str, str]:
        """Delete a campaign and all associated lead links."""
        campaign = await _get_campaign_or_404(db_session, campaign_id)
        await db_session.delete(campaign)
        await db_session.flush()
        logger.info("campaign.deleted", campaign_id=campaign_id)
        return {"status": "deleted", "campaign_id": campaign_id}

    @post(
        "/{campaign_id:str}/start",
        guards=[require_role("owner", "co_owner", "moderator")],
    )
    async def start_campaign(
        self,
        db_session: AsyncSession,
        campaign_id: str,
    ) -> CampaignResponseSchema:
        """Start a campaign -- attaches matching leads and sets status to active.

        Leads are matched by target_cities and target_categories filters.
        Only leads with status 'enriched' and a non-null email are included.
        """
        campaign = await _get_campaign_or_404(db_session, campaign_id)

        if campaign.status not in ("draft", "paused"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot start campaign in '{campaign.status}' status",
            )

        # Find matching leads
        lead_query = select(Lead).where(
            Lead.status == "enriched",
            Lead.email.isnot(None),
        )
        if campaign.target_cities:
            lead_query = lead_query.where(Lead.city.in_(campaign.target_cities))
        if campaign.target_categories:
            lead_query = lead_query.where(Lead.category.in_(campaign.target_categories))

        result = await db_session.execute(lead_query)
        leads = result.scalars().all()

        # Attach leads to campaign
        for lead in leads:
            # Check if already linked
            existing = await db_session.execute(
                select(CampaignLead).where(
                    CampaignLead.campaign_id == campaign.id,
                    CampaignLead.lead_id == lead.id,
                )
            )
            if existing.scalar_one_or_none() is None:
                db_session.add(CampaignLead(
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    status="pending",
                ))

        campaign.status = "active"
        campaign.started_at = datetime.now(UTC)
        campaign.total_leads = len(leads)

        await db_session.flush()
        await db_session.refresh(campaign)

        logger.info(
            "campaign.started",
            campaign_id=str(campaign.id),
            leads_attached=len(leads),
        )
        return _campaign_to_response(campaign)

    @get("/{campaign_id:str}/stats")
    async def get_campaign_stats(
        self,
        db_session: AsyncSession,
        campaign_id: str,
    ) -> dict[str, Any]:
        """Get send/fail/bounce/pending breakdown for a campaign."""
        await _get_campaign_or_404(db_session, campaign_id)
        campaign_uuid = uuid.UUID(campaign_id)

        result = await db_session.execute(
            select(CampaignLead.status, func.count())
            .where(CampaignLead.campaign_id == campaign_uuid)
            .group_by(CampaignLead.status)
        )
        status_counts = {row[0]: row[1] for row in result.all()}

        return {
            "campaign_id": campaign_id,
            "sent": status_counts.get("sent", 0),
            "failed": status_counts.get("failed", 0),
            "bounced": status_counts.get("bounced", 0),
            "pending": status_counts.get("pending", 0),
            "approved": status_counts.get("approved", 0),
            "total": sum(status_counts.values()),
        }

    @get("/{campaign_id:str}/leads")
    async def list_campaign_leads(
        self,
        db_session: AsyncSession,
        campaign_id: str,
        status: str | None = None,
        limit: int = Parameter(default=50, ge=1, le=200),
        offset: int = Parameter(default=0, ge=0),
    ) -> dict[str, Any]:
        """List leads associated with a campaign."""
        await _get_campaign_or_404(db_session, campaign_id)
        campaign_uuid = uuid.UUID(campaign_id)

        query = (
            select(CampaignLead, Lead)
            .join(Lead, CampaignLead.lead_id == Lead.id)
            .where(CampaignLead.campaign_id == campaign_uuid)
        )
        if status:
            query = query.where(CampaignLead.status == status)
        query = query.limit(limit).offset(offset)

        result = await db_session.execute(query)
        rows = result.all()

        count_query = (
            select(func.count())
            .select_from(CampaignLead)
            .where(CampaignLead.campaign_id == campaign_uuid)
        )
        if status:
            count_query = count_query.where(CampaignLead.status == status)
        total = (await db_session.execute(count_query)).scalar() or 0

        return {
            "total": total,
            "leads": [
                {
                    "lead_id": str(cl.lead_id),
                    "name": lead.name,
                    "email": lead.email,
                    "city": lead.city,
                    "category": lead.category,
                    "status": cl.status,
                    "personalized_subject": cl.personalized_subject,
                    "sent_at": cl.sent_at.isoformat() if cl.sent_at else None,
                    "opened_at": cl.opened_at.isoformat() if cl.opened_at else None,
                    "replied_at": cl.replied_at.isoformat() if cl.replied_at else None,
                }
                for cl, lead in rows
            ],
        }


async def _get_campaign_or_404(db_session: AsyncSession, campaign_id: str) -> EmailCampaign:
    """Fetch a campaign by UUID string, or raise 404."""
    try:
        campaign_uuid = uuid.UUID(campaign_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid campaign ID format: {campaign_id}"
        ) from exc

    result = await db_session.execute(
        select(EmailCampaign).where(EmailCampaign.id == campaign_uuid)
    )
    campaign = result.scalar_one_or_none()

    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return campaign
