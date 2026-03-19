"""Outreach Agent -- enriches leads and generates personalized cold messages.

Pipeline B flow: GeoScout -> Outreach (enrich + draft messages) -> [HITL approval] -> Send

The Outreach Agent:
1. Loads unenriched leads from the database (status='new')
2. Runs enrichment waterfall (OSINT -> Hunter -> Apollo) to find contacts
3. Selects channel per lead (telegram if username available, else email)
4. Generates personalized messages via LLM (channel-specific prompts)
5. Queues messages for HITL approval (NEVER auto-sends)
6. Creates/updates an EmailCampaign to track the batch

CRITICAL: Message sending requires HITL approval. This agent only DRAFTS messages.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select, update

from src.agents.base import ConstrainedAgent
from src.core.database import get_db_session
from src.core.exceptions import LLMException, MASException
from src.core.heartbeat import HeartbeatMonitor
from src.core.json_repair import extract_json
from src.core.llm_client import LLMClient
from src.core.loop_detector import LoopDetector
from src.core.models import CampaignLead, EmailCampaign, HITLQueue, Lead
from src.core.state import AgentState, update_state
from src.enrichment.waterfall import EnrichmentWaterfall
from src.prompts.outreach import ChannelType, get_outreach_prompt

logger = structlog.get_logger(__name__)

OUTREACH_ALLOWED_TOOLS: list[str] = [
    "enrich_lead",
    "generate_email",
    "queue_email",
]

# Max leads to process per invocation (rate-limit protection).
_MAX_LEADS_PER_BATCH = 50


def _select_channel(lead: Lead) -> ChannelType:
    """Choose the best outreach channel for a lead.

    Prefers Telegram when a username is available (higher open rate),
    falls back to email.
    """
    if getattr(lead, "telegram_username", None):
        return "telegram"
    return "email"


class OutreachAgent(ConstrainedAgent):
    """Enriches leads and generates personalized outreach messages.

    Parameters
    ----------
    llm_client:
        Shared :class:`LLMClient` instance.
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

    async def _get_waterfall(self, user_id: str | None = None) -> EnrichmentWaterfall:
        """Get or lazily create the enrichment waterfall.

        When *user_id* is provided, credentials are loaded from the DB
        Settings table first (falling back to env vars).
        """
        if self._waterfall is None:
            hunter_key: str | None = None
            apollo_key: str | None = None

            # Try DB credentials first when user context is available
            if user_id:
                hunter_key = await self._get_credential("hunter_api_key", user_id)
                apollo_key = await self._get_credential("apollo_api_key", user_id)

            # Fall back to env vars
            if not hunter_key or not apollo_key:
                try:
                    from src.core.config import get_settings  # noqa: PLC0415

                    settings = get_settings()
                    hunter_key = hunter_key or settings.HUNTER_API_KEY
                    apollo_key = apollo_key or settings.APOLLO_API_KEY
                except (ImportError, AttributeError, ValueError):
                    logger.warning("outreach_waterfall_fallback", exc_info=True)

            self._waterfall = EnrichmentWaterfall(
                hunter_api_key=hunter_key or "",
                apollo_api_key=apollo_key or "",
            )
        return self._waterfall

    # ------------------------------------------------------------------
    # Core execution (called by base.invoke)
    # ------------------------------------------------------------------

    async def _execute(self, state: AgentState) -> AgentState:
        """Enrich leads and generate outreach messages (email + telegram).

        Flow:
        1. Determine target city from state.
        2. Load unenriched leads (``status='new'``) from DB.
        3. Run enrichment waterfall on each lead.
        4. Select channel per lead, generate personalized message via LLM.
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
                            "messages_drafted": 0,
                            "emails_drafted": 0,
                            "channel_counts": {"email": 0, "telegram": 0},
                        },
                    },
                    status="completed",
                    next_agent=None,
                )

            # 2. Enrich leads via waterfall.
            user_id = state.get("user_id")  # type: ignore[typeddict-item]
            waterfall = await self._get_waterfall(user_id=user_id)
            enriched_leads = await self._enrich_leads(leads, waterfall)

            # 2b. Run BusinessAnalyzer on enriched leads (best-effort).
            analysis_results = await self._analyze_leads(enriched_leads)

            # 3. Generate messages for enriched leads (multi-channel).
            messages_drafted = 0
            channel_counts: dict[str, int] = {"email": 0, "telegram": 0}
            if enriched_leads:
                campaign = await self._create_campaign(city)
                artifacts["campaign_id"] = str(campaign.id)
                messages_drafted, channel_counts = await self._generate_messages(enriched_leads, campaign)

                # 4. Create HITL request for batch approval.
                if messages_drafted > 0:
                    hitl_id = await self._create_hitl_request(
                        campaign,
                        city,
                        messages_drafted,
                        channel_counts,
                    )
                    artifacts["_outreach_hitl_id"] = str(hitl_id)

            # 5. Create touch sequences for enriched leads.
            touch_sequences = self._create_touch_sequences(enriched_leads)

            # 5b. Persist touch sequences to DB (best-effort).
            if touch_sequences:
                await self._persist_touch_sequences(touch_sequences, artifacts.get("campaign_id"))

            enrichment_cost = float(waterfall.total_cost)

            artifacts["_outreach_results"] = {
                "city": city,
                "leads_processed": len(leads),
                "enriched": len(enriched_leads),
                "messages_drafted": messages_drafted,
                # Keep emails_drafted for backward compat
                "emails_drafted": messages_drafted,
                "channel_counts": channel_counts,
                "enrichment_cost": enrichment_cost,
                "analysis_results": analysis_results,
                "touch_sequences": touch_sequences,
            }

            return update_state(
                state,
                artifacts=artifacts,
                requires_hitl=messages_drafted > 0,
                hitl_request_id=artifacts.get("_outreach_hitl_id"),
                current_agent="outreach",
                status="paused" if messages_drafted > 0 else "completed",
                next_agent=None,
            )

        except (MASException, OSError) as exc:
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
                select(Lead).where(Lead.city == city, Lead.status == "new").limit(_MAX_LEADS_PER_BATCH)
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
        (i.e. have an email address or telegram username).
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
                    # Update local object for downstream message generation.
                    lead.email = result.email
                    lead.status = "enriched"
                    enriched.append(lead)
                else:
                    await session.execute(update(Lead).where(Lead.id == lead.id).values(status="no_contact"))

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
    # Multi-channel message generation via LLM
    # ------------------------------------------------------------------

    async def _generate_messages(
        self,
        leads: list[Lead],
        campaign: EmailCampaign,
    ) -> tuple[int, dict[str, int]]:
        """Generate personalized messages for each enriched lead via LLM.

        Selects the best channel per lead and uses channel-specific prompts.

        Returns:
            Tuple of (total_drafted, channel_counts).
        """
        drafted = 0
        channel_counts: dict[str, int] = {"email": 0, "telegram": 0}

        async with get_db_session() as session:
            for lead in leads:
                if not lead.email and not getattr(lead, "telegram_username", None):
                    continue

                channel = _select_channel(lead)
                subject, body = await self._draft_message_for_lead(lead, channel)

                cl = CampaignLead(
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    status="pending",
                    channel_type=channel,
                    personalized_subject=subject,
                    personalized_body=body,
                )
                session.add(cl)
                drafted += 1
                channel_counts[channel] = channel_counts.get(channel, 0) + 1

            # Update campaign stats.
            campaign.total_leads = drafted
            session.add(campaign)
            await session.commit()

        self._log.info("outreach_messages_drafted", count=drafted, channels=channel_counts)
        return drafted, channel_counts

    async def _draft_message_for_lead(
        self,
        lead: Lead,
        channel: ChannelType,
    ) -> tuple[str | None, str]:
        """Call LLM to generate a personalized message for a single lead.

        Returns ``(subject, body)`` for email or ``(None, body)`` for telegram.
        On LLM failure, returns a safe fallback.
        """
        prompt = (
            f"Business: {lead.name}\n"
            f"Category: {lead.category or 'local business'}\n"
            f"City: {lead.city}\n"
            f"Address: {lead.address or 'N/A'}\n"
        )
        if channel == "telegram":
            tg_user = getattr(lead, "telegram_username", "")
            prompt += f"Telegram: @{tg_user}\n"

        try:
            system_prompt = get_outreach_prompt(channel)
            response_msg, _metrics = await self._call_llm(
                messages=[
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=prompt),
                ],
                temperature=0.7,
            )

            text = str(response_msg.content)
            msg_data = _parse_message_json(text)
            body = msg_data.get("body", "")
            if body:
                if channel == "email":
                    subject = msg_data.get("subject", f"Website for {lead.name}")
                    return subject, body
                return None, body

        except (LLMException, KeyError, ValueError):
            self._log.warning(
                "outreach_message_gen_failed",
                lead=lead.name,
                channel=channel,
                exc_info=True,
            )

        # Fallback templates.
        if channel == "telegram":
            return None, (
                f"Привет! Заметил, что у {lead.name} нет сайта. "
                "Делаю сайты для локального бизнеса — могу показать примеры, если интересно."
            )
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
    # Business analysis (best-effort)
    # ------------------------------------------------------------------

    async def _analyze_leads(self, leads: list[Lead]) -> list[dict[str, Any]]:
        """Run BusinessAnalyzer on enriched leads.

        Best-effort: if the analyzer fails for a lead, that lead is
        skipped. If the entire analyzer fails to initialise, returns [].
        """
        from src.core.business_analyzer import BusinessAnalyzer  # noqa: PLC0415

        results: list[dict[str, Any]] = []
        try:
            analyzer = BusinessAnalyzer()
        except Exception:  # noqa: BLE001
            self._log.warning("outreach_analyzer_init_failed", exc_info=True)
            return results

        for lead in leads:
            try:
                tier = getattr(lead, "analysis_tier", None) or "quick"
                analysis = await analyzer.analyze(lead, tier=tier)
                results.append(
                    {
                        "lead_id": str(lead.id),
                        "name": lead.name,
                        "tier": analysis.tier,
                        "website_exists": analysis.website.exists,
                    }
                )
            except Exception:  # noqa: BLE001
                self._log.warning(
                    "outreach_analyze_lead_failed",
                    lead=lead.name,
                    exc_info=True,
                )
                continue

        self._log.info("outreach_leads_analyzed", total=len(leads), analyzed=len(results))
        return results

    # ------------------------------------------------------------------
    # Touch sequence creation
    # ------------------------------------------------------------------

    def _create_touch_sequences(self, leads: list[Lead]) -> list[dict[str, Any]]:
        """Create touch sequence records for enriched leads.

        Builds follow-up schedules (Day 1/3/5/10) for each lead using
        the TouchSequenceManager. The actual scheduling/cron is handled
        by the API layer; this method only creates the data structures.
        """
        from src.core.touch_sequence import TOUCH_SCHEDULE, TouchSequenceManager  # noqa: PLC0415

        sequences: list[dict[str, Any]] = []
        try:
            mgr = TouchSequenceManager()
        except Exception:  # noqa: BLE001
            self._log.warning("outreach_touch_mgr_init_failed", exc_info=True)
            return sequences

        for lead in leads:
            try:
                seq = mgr.create_sequence(str(lead.id))
                # Build follow-up schedule from TOUCH_SCHEDULE
                follow_ups = [
                    {
                        "day": step.day,
                        "channel": mgr.resolve_channel(step, lead),
                        "template": step.template,
                        "auto_send": step.auto_send,
                    }
                    for step in TOUCH_SCHEDULE
                ]
                sequences.append(
                    {
                        "lead_id": str(lead.id),
                        "lead_name": lead.name,
                        "state": str(seq.state),
                        "follow_ups": follow_ups,
                    }
                )
            except Exception:  # noqa: BLE001
                self._log.warning(
                    "outreach_touch_seq_failed",
                    lead=lead.name,
                    exc_info=True,
                )
                continue

        self._log.info("outreach_touch_sequences_created", count=len(sequences))
        return sequences

    # ------------------------------------------------------------------
    # Touch sequence DB persistence (stub)
    # ------------------------------------------------------------------

    async def _persist_touch_sequences(
        self,
        sequences: list[dict[str, Any]],
        campaign_id: str | None = None,
    ) -> int:
        """Persist touch sequences to the database as scheduled_touches artifacts.

        Stores each sequence as a JSON blob in the agent_logs table (via HITL
        payload) so that background schedulers can pick them up for follow-up
        delivery. This is a best-effort operation; failures are logged but do
        not block the pipeline.

        Returns the number of sequences persisted.
        """
        persisted = 0
        try:
            async with get_db_session() as session:
                for seq in sequences:
                    try:
                        log_entry = HITLQueue(
                            type="scheduled_touch",
                            priority="low",
                            title=f"Touch sequence: {seq.get('lead_name', 'Unknown')}",
                            description=(
                                f"Follow-up sequence for lead {seq.get('lead_id', '?')} "
                                f"with {len(seq.get('follow_ups', []))} scheduled touches."
                            ),
                            payload={
                                "lead_id": seq.get("lead_id"),
                                "lead_name": seq.get("lead_name"),
                                "campaign_id": campaign_id,
                                "state": seq.get("state"),
                                "follow_ups": seq.get("follow_ups", []),
                            },
                            available_actions=["approve", "skip"],
                            status="scheduled",
                        )
                        session.add(log_entry)
                        persisted += 1
                    except Exception:  # noqa: BLE001
                        self._log.warning(
                            "outreach_persist_touch_failed",
                            lead_id=seq.get("lead_id"),
                            exc_info=True,
                        )
                        continue
                await session.commit()
        except Exception:  # noqa: BLE001
            self._log.warning("outreach_persist_touches_failed", exc_info=True)

        self._log.info(
            "outreach_touch_sequences_persisted",
            persisted=persisted,
            total=len(sequences),
        )
        return persisted

    # ------------------------------------------------------------------
    # HITL queue entry
    # ------------------------------------------------------------------

    async def _create_hitl_request(
        self,
        campaign: EmailCampaign,
        city: str,
        count: int,
        channel_counts: dict[str, int] | None = None,
    ) -> uuid.UUID:
        """Create HITL queue item for outreach batch approval.

        Returns the UUID of the created HITL request.
        """
        channels_desc = ""
        if channel_counts:
            parts = [f"{v} {k}" for k, v in channel_counts.items() if v > 0]
            channels_desc = f" ({', '.join(parts)})"

        async with get_db_session() as session:
            hitl = HITLQueue(
                type="outreach_approval",
                priority="normal",
                title=f"Approve {count} outreach messages for {city}{channels_desc}",
                description=(
                    f"Review and approve {count} personalized outreach messages "
                    f"for businesses in {city}.{channels_desc}"
                ),
                payload={
                    "campaign_id": str(campaign.id),
                    "city": city,
                    "message_count": count,
                    "channel_counts": channel_counts or {},
                },
                available_actions=["approve", "reject", "edit"],
                status="pending",
            )
            session.add(hitl)
            await session.commit()
            await session.refresh(hitl)
            return hitl.id


# ======================================================================
# Helpers
# ======================================================================


def _parse_message_json(text: str) -> dict[str, Any]:
    """Parse JSON from an LLM response using extract_json for robustness."""
    return extract_json(text, expected_type=dict)


# Keep old name for backward compatibility with tests
_parse_email_json = _parse_message_json


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================


async def outreach_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node wrapper for the Outreach agent.

    Creates an :class:`OutreachAgent` instance with shared dependencies
    from the DI container and runs it.  Uses ``dict[str, Any]`` signature
    to avoid LangGraph state reconstruction issues (see MEMORY.md).
    """
    from src.core.container import get_container  # noqa: PLC0415

    container = get_container()
    agent = OutreachAgent(
        llm_client=container.llm_client,
        heartbeat=container.heartbeat,
        loop_detector=container.loop_detector,
    )

    try:
        result = await agent.invoke(state)
    finally:
        # Clean up enrichment HTTP clients.
        if agent._waterfall is not None:  # noqa: SLF001
            try:
                await agent._waterfall.close()  # noqa: SLF001
            except (OSError, ConnectionError):
                logger.debug("outreach_waterfall_close_error", exc_info=True)

    return result
