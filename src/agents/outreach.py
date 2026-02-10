"""Outreach Agent -- enriches leads and generates personalized cold emails.

Pipeline B flow: GeoScout -> Outreach (enrich + draft emails) -> [HITL approval] -> Send

The Outreach Agent:
1. Loads unenriched leads from the database (status='new')
2. Runs enrichment waterfall (OSINT -> Hunter -> Apollo) to find emails
3. For leads with emails, generates personalized cold emails via LLM
4. Queues emails for HITL approval (NEVER auto-sends)
5. Creates/updates an EmailCampaign to track the batch

CRITICAL: Email sending requires HITL approval. This agent only DRAFTS emails.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select, update

from src.agents.base import ConstrainedAgent
from src.core.database import get_db_session
from src.core.heartbeat import HeartbeatMonitor
from src.core.llm_client import LLMClient
from src.core.loop_detector import LoopDetector
from src.core.models import CampaignLead, EmailCampaign, HITLQueue, Lead
from src.core.state import AgentState, update_state
from src.enrichment.waterfall import EnrichmentWaterfall

logger = structlog.get_logger(__name__)

OUTREACH_ALLOWED_TOOLS: list[str] = [
    "enrich_lead",
    "generate_email",
    "queue_email",
]

# Max leads to process per invocation (rate-limit protection).
_MAX_LEADS_PER_BATCH = 50

OUTREACH_SYSTEM_PROMPT = """\
You are an expert cold email writer for a web development agency.
Your task is to write a personalized cold email to a local business that doesn't have a website.

Requirements:
- Keep it SHORT (under 150 words)
- Personalize based on the business type and location
- Clear value proposition: "we build websites that bring customers"
- One clear CTA (call-to-action): reply to schedule a free consultation
- Professional but friendly tone
- NO spam phrases ("limited time offer", "act now", etc.)
- Include the business name naturally

Respond with ONLY a JSON object:
{
    "subject": "Subject line here",
    "body": "Email body here"
}
"""


class OutreachAgent(ConstrainedAgent):
    """Enriches leads and generates personalized cold emails.

    Parameters
    ----------
    llm_client:
        Shared :class:`LLMClient` instance (Claude Haiku primary).
    heartbeat:
        Shared :class:`HeartbeatMonitor` for liveness pings.
    loop_detector:
        Shared :class:`LoopDetector` for runaway-prevention.
    waterfall:
        :class:`EnrichmentWaterfall` instance (optional -- created with
        settings if not provided).
    """

    def __init__(
        self,
        llm_client: LLMClient,
        heartbeat: HeartbeatMonitor,
        loop_detector: LoopDetector,
        waterfall: EnrichmentWaterfall | None = None,
    ) -> None:
        super().__init__(
            agent_name="outreach",
            allowed_tools=OUTREACH_ALLOWED_TOOLS,
            llm_client=llm_client,
            heartbeat=heartbeat,
            loop_detector=loop_detector,
        )
        self._waterfall = waterfall

    def _get_waterfall(self) -> EnrichmentWaterfall:
        """Get or lazily create the enrichment waterfall."""
        if self._waterfall is None:
            try:
                from src.core.config import get_settings  # noqa: PLC0415

                settings = get_settings()
                self._waterfall = EnrichmentWaterfall(
                    hunter_api_key=settings.HUNTER_API_KEY,
                    apollo_api_key=settings.APOLLO_API_KEY,
                )
            except Exception:  # noqa: BLE001
                logger.warning("outreach_waterfall_fallback", exc_info=True)
                self._waterfall = EnrichmentWaterfall()
        return self._waterfall

    # ------------------------------------------------------------------
    # Core execution (called by base.invoke)
    # ------------------------------------------------------------------

    async def _execute(self, state: AgentState) -> AgentState:
        """Enrich leads and generate cold emails.

        Reads city from ``artifacts["_geo_scan_results"]["city"]`` or
        ``state["project"]["requirements"]`` as fallback.

        Flow:
        1. Determine target city from state.
        2. Load unenriched leads (``status='new'``) from DB.
        3. Run enrichment waterfall on each lead.
        4. Generate personalized email for each enriched lead.
        5. Create EmailCampaign + CampaignLead entries.
        6. Create HITL request for batch approval.
        7. Return state with ``requires_hitl=True``.
        """
        artifacts = dict(state.get("artifacts") or {})
        scan_results = artifacts.get("_geo_scan_results", {})
        city = scan_results.get("city", "")
        if not city:
            city = (state.get("project") or {}).get("requirements", "")

        if not city:
            return update_state(
                state,
                status="failed",
                errors=[*state["errors"], "No city context for outreach"],
                next_agent=None,
            )

        self._log.info("outreach_starting", city=city)

        try:
            # 1. Load unenriched leads.
            leads = await self._load_leads(city)
            if not leads:
                self._log.info("outreach_no_leads", city=city)
                return update_state(
                    state,
                    artifacts={
                        **artifacts,
                        "_outreach_results": {
                            "city": city,
                            "enriched": 0,
                            "emails_drafted": 0,
                        },
                    },
                    status="completed",
                    next_agent=None,
                )

            # 2. Enrich leads via waterfall.
            waterfall = self._get_waterfall()
            enriched_leads = await self._enrich_leads(leads, waterfall)

            # 3. Generate emails for enriched leads.
            emails_drafted = 0
            if enriched_leads:
                campaign = await self._create_campaign(city)
                emails_drafted = await self._generate_emails(enriched_leads, campaign)

                # 4. Create HITL request for email approval.
                if emails_drafted > 0:
                    hitl_id = await self._create_hitl_request(
                        campaign, city, emails_drafted,
                    )
                    artifacts["_outreach_hitl_id"] = str(hitl_id)

            enrichment_cost = float(waterfall.total_cost)

            artifacts["_outreach_results"] = {
                "city": city,
                "leads_processed": len(leads),
                "enriched": len(enriched_leads),
                "emails_drafted": emails_drafted,
                "enrichment_cost": enrichment_cost,
            }

            return update_state(
                state,
                artifacts=artifacts,
                requires_hitl=emails_drafted > 0,
                hitl_request_id=artifacts.get("_outreach_hitl_id"),
                current_agent="outreach",
                status="paused" if emails_drafted > 0 else "completed",
                next_agent=None,
            )

        except Exception as exc:  # noqa: BLE001
            self._log.exception("outreach_error", city=city, error=str(exc))
            return update_state(
                state,
                errors=[*state["errors"], f"Outreach error: {exc}"],
                status="failed",
                next_agent=None,
            )

    # ------------------------------------------------------------------
    # Lead loading
    # ------------------------------------------------------------------

    async def _load_leads(self, city: str) -> list[Lead]:
        """Load unenriched leads for the given city."""
        async with get_db_session() as session:
            result = await session.execute(
                select(Lead)
                .where(Lead.city == city, Lead.status == "new")
                .limit(_MAX_LEADS_PER_BATCH)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Enrichment
    # ------------------------------------------------------------------

    async def _enrich_leads(
        self,
        leads: list[Lead],
        waterfall: EnrichmentWaterfall,
    ) -> list[Lead]:
        """Run enrichment waterfall on each lead, updating the DB.

        Returns the subset of leads that were successfully enriched
        (i.e. have an email address).
        """
        enriched: list[Lead] = []

        async with get_db_session() as session:
            for lead in leads:
                result = await waterfall.enrich(
                    lead.name,
                    lead.city or "",
                    domain=None,  # No domain for offline businesses
                )

                if result.email:
                    cost = Decimal("0")
                    if result.raw_data and "cost" in result.raw_data:
                        cost = Decimal(str(result.raw_data["cost"]))

                    await session.execute(
                        update(Lead)
                        .where(Lead.id == lead.id)
                        .values(
                            email=result.email,
                            phone=result.phone or lead.phone,
                            enrichment_source=result.source,
                            enrichment_cost=cost,
                            enrichment_data=result.raw_data,
                            status="enriched",
                        )
                    )
                    # Update local object for downstream email generation.
                    lead.email = result.email
                    lead.status = "enriched"
                    enriched.append(lead)
                else:
                    await session.execute(
                        update(Lead)
                        .where(Lead.id == lead.id)
                        .values(status="no_contact")
                    )

            await session.commit()

        self._log.info(
            "outreach_enriched",
            total=len(leads),
            with_email=len(enriched),
        )
        return enriched

    # ------------------------------------------------------------------
    # Campaign creation
    # ------------------------------------------------------------------

    async def _create_campaign(self, city: str) -> EmailCampaign:
        """Create a new email campaign for this batch."""
        async with get_db_session() as session:
            campaign = EmailCampaign(
                name=f"Pipeline B -- {city}",
                subject_template="Website for {{business_name}}",
                body_template="",  # Personalized per lead via LLM
                target_cities=[city],
                status="draft",
            )
            session.add(campaign)
            await session.commit()
            await session.refresh(campaign)
            return campaign

    # ------------------------------------------------------------------
    # Email generation via LLM
    # ------------------------------------------------------------------

    async def _generate_emails(
        self,
        leads: list[Lead],
        campaign: EmailCampaign,
    ) -> int:
        """Generate personalized emails for each enriched lead via LLM.

        Returns the number of emails drafted.
        """
        drafted = 0
        async with get_db_session() as session:
            for lead in leads:
                if not lead.email:
                    continue

                subject, body = await self._draft_email_for_lead(lead)

                cl = CampaignLead(
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    status="pending",
                    personalized_subject=subject,
                    personalized_body=body,
                )
                session.add(cl)
                drafted += 1

            # Update campaign stats.
            campaign.total_leads = drafted
            session.add(campaign)
            await session.commit()

        self._log.info("outreach_emails_drafted", count=drafted)
        return drafted

    async def _draft_email_for_lead(self, lead: Lead) -> tuple[str, str]:
        """Call LLM to generate a personalized email for a single lead.

        Returns ``(subject, body)``.  On LLM failure, returns a safe
        fallback template.
        """
        prompt = (
            f"Business: {lead.name}\n"
            f"Category: {lead.category or 'local business'}\n"
            f"City: {lead.city}\n"
            f"Address: {lead.address or 'N/A'}\n"
        )

        try:
            response_msg, _metrics = await self._call_llm(
                messages=[
                    SystemMessage(content=OUTREACH_SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ],
                temperature=0.7,
            )

            text = str(response_msg.content)
            email_data = _parse_email_json(text)
            subject = email_data.get("subject", f"Website for {lead.name}")
            body = email_data.get("body", "")
            if body:
                return subject, body

        except Exception:  # noqa: BLE001
            self._log.warning(
                "outreach_email_gen_failed",
                lead=lead.name,
                exc_info=True,
            )

        # Fallback template.
        return (
            f"Website for {lead.name}",
            (
                f"Hi! I noticed {lead.name} doesn't have a website yet. "
                "We build professional websites that help local businesses "
                "attract more customers. Would you be open to a quick chat "
                "about how we could help? Reply to this email and we'll "
                "schedule a free consultation."
            ),
        )

    # ------------------------------------------------------------------
    # HITL queue entry
    # ------------------------------------------------------------------

    async def _create_hitl_request(
        self,
        campaign: EmailCampaign,
        city: str,
        count: int,
    ) -> uuid.UUID:
        """Create HITL queue item for email batch approval.

        Returns the UUID of the created HITL request.
        """
        async with get_db_session() as session:
            hitl = HITLQueue(
                type="email_approval",
                priority="normal",
                title=f"Approve {count} cold emails for {city}",
                description=(
                    f"Review and approve {count} personalized cold emails "
                    f"for businesses in {city}."
                ),
                payload={
                    "campaign_id": str(campaign.id),
                    "city": city,
                    "email_count": count,
                },
                available_actions=["approve", "reject", "edit"],
                status="pending",
            )
            session.add(hitl)
            await session.commit()
            await session.refresh(hitl)
            return hitl.id

        # Unreachable -- satisfies type checker for async context manager.
        return uuid.uuid4()  # pragma: no cover


# ======================================================================
# Helpers
# ======================================================================


def _parse_email_json(text: str) -> dict[str, Any]:
    """Parse JSON from an LLM response, stripping markdown code fences."""
    cleaned = text.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        if len(parts) >= 2:  # noqa: PLR2004
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            cleaned = inner.strip()

    return json.loads(cleaned)


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================


async def outreach_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node wrapper for the Outreach agent.

    Creates an :class:`OutreachAgent` instance with minimal dependencies
    and runs it.  Uses ``dict[str, Any]`` signature to avoid LangGraph
    state reconstruction issues (see MEMORY.md).
    """
    from src.core.heartbeat import HeartbeatMonitor  # noqa: PLC0415
    from src.core.llm_client import LLMClient  # noqa: PLC0415
    from src.core.loop_detector import LoopDetector  # noqa: PLC0415

    agent = OutreachAgent(
        llm_client=LLMClient(),
        heartbeat=HeartbeatMonitor(),
        loop_detector=LoopDetector(),
    )

    result = await agent.invoke(state)

    # Clean up enrichment HTTP clients.
    if agent._waterfall is not None:  # noqa: SLF001
        await agent._waterfall.close()  # noqa: SLF001

    return result
