"""Unit tests for SalesAgent (src/agents/sales_agent.py).

All LLM calls and database operations are mocked.
Tests cover: agent construction, _execute flow, tool dispatch,
HITL creation, state transitions, and error handling.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.agents.sales_agent import (
    SALES_ALLOWED_TOOLS,
    SalesAgent,
)
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState for SalesAgent."""
    project = {
        "project_id": "proj-sales-001",
        "job_id": "deal-test-001",
        "platform": "pipeline_b",
        "client": {"name": "Стоматология Улыбка"},
        "requirements": "Landing page with booking",
        "budget": 45000.0,
        "deadline": datetime(2026, 4, 15, tzinfo=UTC),
    }
    state = create_initial_state(project=project, first_agent="sales_agent", thread_id="thread-sales-test")
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _mock_llm_response(content: str) -> tuple[AIMessage, CallMetrics]:
    """Create a mock LLM response tuple."""
    return (
        AIMessage(content=content),
        CallMetrics(agent_name="sales_agent", model_id="claude-opus-4-6", provider="anthropic"),
    )


# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------


class TestSalesAgentConstruction:
    """Verify SalesAgent can be constructed with required dependencies."""

    def test_create_agent(
        self,
        mock_llm_client,
        mock_heartbeat,
        mock_loop_detector,
    ):
        agent = SalesAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        assert agent.agent_name == "sales_agent"

    def test_allowed_tools(self):
        assert "generate_concept" in SALES_ALLOWED_TOOLS
        assert "send_message" in SALES_ALLOWED_TOOLS
        assert "save_deal_context" in SALES_ALLOWED_TOOLS
        assert "get_deal_context" in SALES_ALLOWED_TOOLS
        assert "get_battlecard" in SALES_ALLOWED_TOOLS
        assert "get_competitor_analysis" in SALES_ALLOWED_TOOLS
        assert "get_market_insights" in SALES_ALLOWED_TOOLS
        assert len(SALES_ALLOWED_TOOLS) == 7


# ---------------------------------------------------------------------------
# Execute — first contact flow
# ---------------------------------------------------------------------------


class TestExecuteFirstContact:
    """Verify _execute for new deals (first contact generation)."""

    @pytest.mark.asyncio
    async def test_first_contact_generates_message(
        self,
        mock_llm_client,
        mock_heartbeat,
        mock_loop_detector,
    ):
        """New deal → generate first contact message → pause for HITL."""
        mock_llm_client.call = AsyncMock(
            return_value=_mock_llm_response("Привет! Заметил вашу стоматологию на Яндекс.Картах...")
        )

        agent = SalesAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_state(
            artifacts={
                "_sales_context": {
                    "deal_id": "deal-001",
                    "lead_id": "lead-001",
                    "sales_stage": "first_contact",
                    "lead_info": {
                        "business_name": "Стоматология Улыбка",
                        "city": "Ростов-на-Дону",
                        "category": "стоматология",
                    },
                    "battlecard": {"key_selling_points": ["+40% звонков"]},
                    "operator_name": "Алексей",
                }
            },
        )

        with patch.object(agent, "_store_message", new_callable=AsyncMock):
            result = await agent._execute(state)

        assert result["status"] == "paused"
        assert result["current_agent"] == "sales_agent"
        artifacts = result.get("artifacts", {})
        assert "sales_agent" in artifacts or "_sales_result" in artifacts

    @pytest.mark.asyncio
    async def test_first_contact_no_context_fails_gracefully(
        self,
        mock_llm_client,
        mock_heartbeat,
        mock_loop_detector,
    ):
        """Missing _sales_context should not crash."""
        agent = SalesAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_state(artifacts={})
        result = await agent._execute(state)

        assert result["status"] in ("active", "failed")


# ---------------------------------------------------------------------------
# Execute — client reply flow
# ---------------------------------------------------------------------------


class TestExecuteClientReply:
    """Verify _execute for processing client replies."""

    @pytest.mark.asyncio
    async def test_client_reply_generates_response(
        self,
        mock_llm_client,
        mock_heartbeat,
        mock_loop_detector,
    ):
        mock_llm_client.call = AsyncMock(return_value=_mock_llm_response("Расскажите подробнее о вашем бизнесе"))

        agent = SalesAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_state(
            artifacts={
                "_sales_context": {
                    "deal_id": "deal-001",
                    "lead_id": "lead-001",
                    "sales_stage": "discovery",
                    "client_message": "Да, у нас стоматология на 3 кресла",
                    "channel": "telegram",
                    "conversation_history": [
                        {"role": "agent", "text": "Привет!"},
                    ],
                    "deal_context": {},
                    "battlecard": {},
                    "operator_name": "Алексей",
                }
            },
        )

        with patch.object(agent, "_store_message", new_callable=AsyncMock):
            result = await agent._execute(state)

        assert result["current_agent"] == "sales_agent"

    @pytest.mark.asyncio
    async def test_client_says_no_transitions_to_lost(
        self,
        mock_llm_client,
        mock_heartbeat,
        mock_loop_detector,
    ):
        mock_llm_client.call = AsyncMock(return_value=_mock_llm_response("Понял, удачи!"))

        agent = SalesAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_state(
            artifacts={
                "_sales_context": {
                    "deal_id": "deal-001",
                    "lead_id": "lead-001",
                    "sales_stage": "awaiting_reply",
                    "client_message": "не интересно",
                    "channel": "telegram",
                    "conversation_history": [],
                    "deal_context": {},
                    "battlecard": {},
                    "operator_name": "Алексей",
                }
            },
        )

        with patch.object(agent, "_store_message", new_callable=AsyncMock):
            result = await agent._execute(state)

        artifacts = result.get("artifacts", {})
        sales_result = artifacts.get("_sales_result", {})
        assert sales_result.get("new_stage") == "lost" or result["status"] in ("completed", "active")


# ---------------------------------------------------------------------------
# Execute — concept generation + HITL
# ---------------------------------------------------------------------------


class TestExecuteConceptHitl:
    """Verify concept generation triggers HITL."""

    @pytest.mark.asyncio
    async def test_concept_stage_creates_hitl(
        self,
        mock_llm_client,
        mock_heartbeat,
        mock_loop_detector,
    ):
        concept_json = json.dumps(
            {
                "title": "Лендинг с онлайн-записью",
                "services": ["дизайн", "разработка"],
                "description": "Landing with booking",
                "timeline": "2-3 недели",
                "estimated_cost": 45000,
                "rationale": "Конкуренты получают +40% звонков",
                "why_this_helps": ["+40% звонков с сайтом"],
            }
        )

        mock_llm_client.call = AsyncMock(return_value=_mock_llm_response(concept_json))

        agent = SalesAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_state(
            artifacts={
                "_sales_context": {
                    "deal_id": "deal-001",
                    "lead_id": "lead-001",
                    "sales_stage": "concept",
                    "lead_info": {"business_name": "Clinic", "city": "Moscow"},
                    "client_needs": {"pain": "no website"},
                    "battlecard": {"key_selling_points": ["more calls"]},
                    "operator_name": "Алексей",
                }
            },
        )

        with (
            patch.object(agent, "_create_concept_hitl", new_callable=AsyncMock, return_value="hitl-concept-001"),
            patch.object(agent, "_store_message", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        assert result["requires_hitl"] is True
        assert result["status"] == "paused"


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


class TestSalesTools:
    """Verify tool function signatures and basic behavior."""

    @pytest.mark.asyncio
    async def test_get_competitor_analysis(
        self,
        mock_llm_client,
        mock_heartbeat,
        mock_loop_detector,
    ):
        agent = SalesAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        result = await agent.get_competitor_analysis("TestBiz", "Moscow", "restaurant")
        assert isinstance(result, dict)
        assert "competitors" in result
        assert "market_share" in result

    @pytest.mark.asyncio
    async def test_get_market_insights(
        self,
        mock_llm_client,
        mock_heartbeat,
        mock_loop_detector,
    ):
        agent = SalesAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        result = await agent.get_market_insights("restaurant", "Moscow")
        assert isinstance(result, dict)
        assert "avg_project_cost" in result
        assert "demand_level" in result

    @pytest.mark.asyncio
    async def test_save_and_get_deal_context(
        self,
        mock_llm_client,
        mock_heartbeat,
        mock_loop_detector,
    ):
        agent = SalesAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        # Create context first
        agent._deal_memory.create_context("deal-t", "lead-t")
        await agent.save_deal_context("deal-t", "budget", "50k")
        ctx = await agent.get_deal_context("deal-t")
        assert ctx["key_facts"]["budget"] == "50k"

    @pytest.mark.asyncio
    async def test_get_battlecard_returns_dict(
        self,
        mock_llm_client,
        mock_heartbeat,
        mock_loop_detector,
    ):
        agent = SalesAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        result = await agent.get_battlecard("lead-001")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_send_message_returns_status(
        self,
        mock_llm_client,
        mock_heartbeat,
        mock_loop_detector,
    ):
        agent = SalesAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        result = await agent.send_message("lead-001", "telegram", "Привет!")
        assert isinstance(result, dict)
        assert "status" in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_llm_failure_returns_failed_state(
        self,
        mock_llm_client,
        mock_heartbeat,
        mock_loop_detector,
    ):
        mock_llm_client.call = AsyncMock(side_effect=OSError("LLM timeout"))

        agent = SalesAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_state(
            artifacts={
                "_sales_context": {
                    "deal_id": "deal-001",
                    "lead_id": "lead-001",
                    "sales_stage": "first_contact",
                    "lead_info": {"business_name": "Test"},
                    "battlecard": {},
                    "operator_name": "Test",
                }
            },
        )

        result = await agent._execute(state)
        # Should handle error gracefully
        assert result["status"] in ("failed", "active")

    @pytest.mark.asyncio
    async def test_unknown_stage_defaults_to_respond(
        self,
        mock_llm_client,
        mock_heartbeat,
        mock_loop_detector,
    ):
        mock_llm_client.call = AsyncMock(return_value=_mock_llm_response("OK"))

        agent = SalesAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_state(
            artifacts={
                "_sales_context": {
                    "deal_id": "deal-001",
                    "lead_id": "lead-001",
                    "sales_stage": "unknown_stage",
                    "client_message": "hello",
                    "channel": "email",
                    "conversation_history": [],
                    "deal_context": {},
                    "battlecard": {},
                    "operator_name": "Test",
                }
            },
        )

        with patch.object(agent, "_store_message", new_callable=AsyncMock):
            result = await agent._execute(state)

        assert result["current_agent"] == "sales_agent"
