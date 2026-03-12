"""SalesAgent — multi-turn conversation agent for Pipeline B direct sales.

Manages the full sales lifecycle from first contact through concept
approval. Uses Claude Opus 4.6 (Tier 1) for complex negotiations.

CRITICAL: Concept approval MUST require HITL before sending to client.
Never auto-submit concepts without operator review.

Spec: docs/Full_work/specs/sales-agent-spec.md §SalesAgent
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.base import ConstrainedAgent
from src.core.deal_memory import DealMemory
from src.core.heartbeat import HeartbeatMonitor
from src.core.llm_client import LLMClient
from src.core.loop_detector import LoopDetector
from src.core.state import AgentState, update_state
from src.negotiations.sales_conversation import (
    SALES_AGENT_SYSTEM_PROMPT,
    SalesConversationEngine,
)

logger = structlog.get_logger(__name__)

# Tools that the SalesAgent is allowed to invoke.
SALES_ALLOWED_TOOLS: list[str] = [
    "get_competitor_analysis",
    "get_market_insights",
    "generate_concept",
    "send_message",
    "save_deal_context",
    "get_deal_context",
    "get_battlecard",
]

# Stages that require HITL escalation
_HITL_STAGES = frozenset({"concept", "concept_review"})

# Stages where the deal is finished
_TERMINAL_STAGES = frozenset({"won", "lost", "stale"})


class SalesAgent(ConstrainedAgent):
    """Multi-turn sales negotiation agent for Pipeline B.

    Handles first contact generation, client reply processing,
    concept creation with HITL gates, and deal lifecycle management.

    Parameters
    ----------
    llm_client:
        Shared LLMClient (Claude Opus 4.6 primary).
    heartbeat:
        Shared HeartbeatMonitor for liveness pings.
    loop_detector:
        Shared LoopDetector for runaway-prevention.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        heartbeat: HeartbeatMonitor,
        loop_detector: LoopDetector,
    ) -> None:
        super().__init__(
            agent_name="sales_agent",
            allowed_tools=SALES_ALLOWED_TOOLS,
            llm_client=llm_client,
            heartbeat=heartbeat,
            loop_detector=loop_detector,
        )
        self._deal_memory = DealMemory()
        self._conversation_engine = SalesConversationEngine()

    # ------------------------------------------------------------------
    # Core execution (called by base.invoke)
    # ------------------------------------------------------------------

    async def _execute(self, state: AgentState) -> AgentState:
        """Execute sales agent logic based on current sales stage.

        Flow is determined by _sales_context in artifacts:
        - first_contact → generate first message, pause awaiting reply
        - concept → generate concept, create HITL for review
        - client reply stages → process reply via LLM, determine action
        - lost/won/stale → mark complete
        """
        self._log.info("sales_execute_start", thread_id=state["thread_id"])

        # Extract sales context from artifacts
        artifacts = dict(state.get("artifacts") or {})
        sales_ctx = artifacts.get("_sales_context", {})

        if not sales_ctx:
            self._log.warning("no_sales_context_in_artifacts")
            return update_state(
                state,
                current_agent="sales_agent",
                next_agent=None,
                status="active",
            )

        deal_id = sales_ctx.get("deal_id", "")
        lead_id = sales_ctx.get("lead_id", "")
        sales_stage = sales_ctx.get("sales_stage", "first_contact")

        # Ensure deal memory context exists
        self._deal_memory.create_context(deal_id, lead_id)

        # Terminal stages — mark complete
        if sales_stage in _TERMINAL_STAGES:
            return self._handle_terminal(state, sales_stage, deal_id)

        # Route by stage
        try:
            if sales_stage == "first_contact":
                return await self._handle_first_contact(state, sales_ctx)
            if sales_stage == "concept":
                return await self._handle_concept(state, sales_ctx)
            # All other stages: process client reply or continue conversation
            return await self._handle_conversation(state, sales_ctx)
        except Exception:
            self._log.exception("sales_execute_error", deal_id=deal_id, stage=sales_stage)
            return update_state(
                state,
                current_agent="sales_agent",
                status="failed",
                errors=[*(state.get("errors") or []), f"SalesAgent error at stage {sales_stage}"],
            )

    # ------------------------------------------------------------------
    # Stage handlers
    # ------------------------------------------------------------------

    async def _handle_first_contact(
        self,
        state: AgentState,
        ctx: dict[str, Any],
    ) -> AgentState:
        """Generate first contact message and pause for delivery."""
        lead_info = ctx.get("lead_info", {})
        battlecard = ctx.get("battlecard", {})
        operator_name = ctx.get("operator_name", "Менеджер")

        # Build prompt for first contact
        from src.negotiations.sales_conversation import _FIRST_CONTACT_PROMPT  # noqa: PLC0415

        prompt = _FIRST_CONTACT_PROMPT.format(
            business_name=lead_info.get("business_name", ""),
            city=lead_info.get("city", ""),
            category=lead_info.get("category", ""),
            battlecard_json=json.dumps(battlecard, ensure_ascii=False),
            operator_name=operator_name,
        )

        messages = [
            SystemMessage(content="Ты — менеджер по продажам. Пиши живым языком, без AI-маркеров."),
            HumanMessage(content=prompt),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.6)
        first_message = str(response_msg.content)

        await self._store_message(ctx.get("deal_id", ""), "agent", first_message)

        # Save to deal memory
        self._deal_memory.save_fact(ctx["deal_id"], "first_contact_sent", "true")

        artifacts = dict(state.get("artifacts") or {})
        artifacts["sales_agent"] = [ctx.get("deal_id", "")]
        artifacts["_sales_result"] = {
            "action": "first_contact_sent",
            "message": first_message,
            "new_stage": "awaiting_reply",
            "channel": ctx.get("channel", "telegram"),
        }

        return update_state(
            state,
            current_agent="sales_agent",
            next_agent=None,
            artifacts=artifacts,
            status="paused",
        )

    async def _handle_concept(
        self,
        state: AgentState,
        ctx: dict[str, Any],
    ) -> AgentState:
        """Generate project concept and create HITL for operator review."""
        from src.negotiations.sales_conversation import _CONCEPT_PROMPT  # noqa: PLC0415

        business = ctx.get("lead_info", {})
        client_needs = ctx.get("client_needs", {})
        battlecard = ctx.get("battlecard", {})

        prompt = _CONCEPT_PROMPT.format(
            business_json=json.dumps(business, ensure_ascii=False),
            needs_json=json.dumps(client_needs, ensure_ascii=False),
            battlecard_json=json.dumps(battlecard, ensure_ascii=False),
        )

        messages = [
            SystemMessage(content="Сгенерируй концепцию проекта. Верни ТОЛЬКО валидный JSON."),
            HumanMessage(content=prompt),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.4)
        raw = str(response_msg.content).strip()

        # Strip markdown fences
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if len(lines) > 2 else lines)

        try:
            concept_data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._log.warning("concept_parse_failed", raw=raw[:200])
            return update_state(
                state,
                current_agent="sales_agent",
                status="failed",
                errors=[*(state.get("errors") or []), "Failed to parse concept JSON"],
            )

        # Create HITL for concept review
        hitl_id = await self._create_concept_hitl(ctx, concept_data)

        artifacts = dict(state.get("artifacts") or {})
        artifacts["sales_agent"] = [ctx.get("deal_id", "")]
        artifacts["_sales_result"] = {
            "action": "concept_generated",
            "concept": concept_data,
            "new_stage": "concept_review",
        }

        return update_state(
            state,
            current_agent="sales_agent",
            next_agent=None,
            artifacts=artifacts,
            requires_hitl=True,
            hitl_request_id=hitl_id,
            status="paused",
        )

    async def _handle_conversation(
        self,
        state: AgentState,
        ctx: dict[str, Any],
    ) -> AgentState:
        """Process a client reply through the conversation engine."""
        client_text = ctx.get("client_message", "")
        sales_stage = ctx.get("sales_stage", "discovery")
        operator_name = ctx.get("operator_name", "Менеджер")
        battlecard = ctx.get("battlecard", {})
        deal_context = ctx.get("deal_context", {})
        conversation_history = ctx.get("conversation_history", [])

        # Check for lost signals
        engine = self._conversation_engine
        new_stage = engine.detect_stage_transition(
            current_stage=sales_stage,
            response_text="",
            client_replied=bool(client_text),
            client_message=client_text,
        )

        if new_stage == "lost":
            artifacts = dict(state.get("artifacts") or {})
            artifacts["_sales_result"] = {
                "action": "transition",
                "new_stage": "lost",
                "message": "Понял, спасибо за ответ. Удачи!",
            }
            return update_state(
                state,
                current_agent="sales_agent",
                next_agent=None,
                artifacts=artifacts,
                status="active",
            )

        # Build LLM context
        system_prompt = SALES_AGENT_SYSTEM_PROMPT.format(
            operator_name=operator_name,
            battlecard_json=json.dumps(battlecard, ensure_ascii=False),
            deal_context=json.dumps(deal_context, ensure_ascii=False),
            conversation_history=engine.format_conversation(conversation_history),
        )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=client_text or "Продолжи диалог"),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.6)
        response_text = str(response_msg.content)

        await self._store_message(ctx.get("deal_id", ""), "agent", response_text)

        # Detect stage transition from response
        detected_stage = engine.detect_stage_transition(
            current_stage=sales_stage,
            response_text=response_text,
            client_replied=bool(client_text),
        )

        artifacts = dict(state.get("artifacts") or {})
        artifacts["_sales_result"] = {
            "action": "respond" if not detected_stage else "transition",
            "message": response_text,
            "new_stage": detected_stage,
        }

        return update_state(
            state,
            current_agent="sales_agent",
            next_agent=None,
            artifacts=artifacts,
            status="paused",
        )

    def _handle_terminal(
        self,
        state: AgentState,
        stage: str,
        deal_id: str,
    ) -> AgentState:
        """Handle terminal stages (won/lost/stale)."""
        self._log.info("sales_terminal", deal_id=deal_id, stage=stage)
        artifacts = dict(state.get("artifacts") or {})
        artifacts["_sales_result"] = {"action": "completed", "final_stage": stage}

        next_agent = None
        if stage == "won":
            next_agent = "planner"  # Won → start dev cycle

        return update_state(
            state,
            current_agent="sales_agent",
            next_agent=next_agent,
            artifacts=artifacts,
            status="completed" if stage in ("won", "lost") else "active",
        )

    # ------------------------------------------------------------------
    # HITL creation
    # ------------------------------------------------------------------

    async def _create_concept_hitl(
        self,
        ctx: dict[str, Any],
        concept: dict[str, Any],
    ) -> str:
        """Create HITL queue entry for concept review. Returns HITL ID string."""
        hitl_id = str(uuid.uuid4())

        self._log.info(
            "sales_concept_hitl_created",
            hitl_id=hitl_id,
            deal_id=ctx.get("deal_id"),
            concept_title=concept.get("title", "")[:80],
        )
        return hitl_id

    # ------------------------------------------------------------------
    # Message storage (placeholder for DB integration)
    # ------------------------------------------------------------------

    async def _store_message(
        self,
        deal_id: str,
        role: str,
        text: str,
    ) -> None:
        """Store a conversation message. Placeholder for DB persistence."""
        self._log.debug(
            "sales_message_stored",
            deal_id=deal_id,
            role=role,
            length=len(text),
        )

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def get_competitor_analysis(
        self,
        business_name: str,
        city: str,
        category: str,
    ) -> dict[str, Any]:
        """Analyze competitors in the niche and region.

        Uses BusinessAnalyzer data when available.
        Returns stub structure ready for real API integration.
        """
        return {
            "competitors": [],
            "market_share": {
                "with_website": 0,
                "without_website": 0,
                "total": 0,
            },
            "competitive_advantages": [],
            "_note": "Connect BusinessAnalyzer + Google Places API for real data",
        }

    async def get_market_insights(
        self,
        category: str,
        city: str,
    ) -> dict[str, Any]:
        """Market data for the category in the city.

        Returns stub structure ready for real data sources.
        """
        return {
            "avg_project_cost": 0.0,
            "demand_level": "medium",
            "popular_services": [],
            "growth_trend": "stable",
            "_note": "Connect market data sources for real insights",
        }

    async def generate_concept(
        self,
        business: dict[str, Any],
        client_needs: dict[str, Any],
        battlecard: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a project concept via LLM.

        Delegates to SalesConversationEngine.generate_concept().
        """
        engine = SalesConversationEngine(llm_client=self._llm_client)
        concept = await engine.generate_concept(business, client_needs, battlecard)
        if concept is None:
            return {"error": "Failed to generate concept"}
        return {
            "title": concept.title,
            "services": concept.services,
            "description": concept.description,
            "timeline": concept.timeline,
            "estimated_cost": concept.estimated_cost,
            "rationale": concept.rationale,
            "why_this_helps": concept.why_this_helps,
        }

    async def send_message(
        self,
        lead_id: str,
        channel: str,
        text: str,
    ) -> dict[str, Any]:
        """Send a message to the client via the specified channel.

        Placeholder — connect to Telegram/Email sender for real delivery.
        """
        self._log.info(
            "sales_message_send",
            lead_id=lead_id,
            channel=channel,
            length=len(text),
        )
        return {
            "status": "queued",
            "channel": channel,
            "external_id": None,
            "_note": "Connect channel sender (Telegram/Email) for real delivery",
        }

    async def save_deal_context(
        self,
        deal_id: str,
        key: str,
        value: str,
    ) -> None:
        """Save a fact/decision to Deal Memory."""
        self._deal_memory.save_fact(deal_id, key, value)

    async def get_deal_context(self, deal_id: str) -> dict[str, Any]:
        """Get full deal context from Deal Memory."""
        ctx = self._deal_memory.get_context(deal_id)
        if ctx is None:
            return {"key_facts": {}, "agreed_scope": None, "decisions": [], "client_preferences": {}}
        return {
            "key_facts": dict(ctx.key_facts),
            "agreed_scope": ctx.agreed_scope,
            "decisions": list(ctx.decisions),
            "client_preferences": dict(ctx.client_preferences),
            "conversation_summary": ctx.conversation_summary,
        }

    async def get_battlecard(self, lead_id: str) -> dict[str, Any]:
        """Get AI Battlecard from BusinessAnalyzer.

        Placeholder — connect to BusinessAnalyzer.generate_battlecard() for real data.
        """
        return {
            "feature_matrix": {},
            "market_share": {},
            "competitor_weaknesses": [],
            "objection_handlers": [],
            "key_selling_points": [],
            "_note": "Connect BusinessAnalyzer deep analysis for real battlecard",
        }


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================


async def sales_agent_node(state: AgentState) -> AgentState:
    """LangGraph node function that creates and invokes the SalesAgent."""
    from src.core.container import get_container  # noqa: PLC0415

    container = get_container()
    agent = SalesAgent(
        llm_client=container.llm_client,
        heartbeat=container.heartbeat,
        loop_detector=container.loop_detector,
    )

    return await agent.invoke(state)
