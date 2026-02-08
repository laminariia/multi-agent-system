"""Unit tests for src.agents.bid.BidAgent.

All LLM calls and database operations are mocked.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from src.agents.bid import BidAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state
from tests.factories import JobFactory, ProposalFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults."""
    project = {
        "project_id": "proj-test-002",
        "job_id": "job-test-002",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a landing page",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    state = create_initial_state(project=project, first_agent="bid", thread_id="thread-bid-test")
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_bid_no_artifacts_returns_no_next(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When there are no scout artifacts in state, next_agent should be None."""
    agent = BidAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts={})
    result = await agent._execute(state)

    assert result["next_agent"] is None
    assert result["status"] == "active"


async def test_bid_generates_proposal_requires_hitl(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """A successful proposal generation should set requires_hitl=True and status='paused'."""
    job = JobFactory.create()
    proposal = ProposalFactory.create()

    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=json.dumps(proposal, default=str)),
        CallMetrics(agent_name="bid", model_id="gemini-3-flash", provider="google"),
    ))

    agent = BidAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts={"scout": [job["id"]]})

    with (
        patch.object(agent, "_load_jobs", new_callable=AsyncMock, return_value=[job]),
        patch.object(agent, "_fetch_similar_bids", new_callable=AsyncMock, return_value=[]),
        patch.object(agent, "_store_bid", new_callable=AsyncMock, return_value="bid-001"),
        patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-001"),
        patch.object(agent, "_log_bid_generated", new_callable=AsyncMock),
    ):
        result = await agent._execute(state)

    assert result["requires_hitl"] is True
    assert result["status"] == "paused"
    assert result["hitl_request_id"] == "hitl-001"


async def test_bid_always_sets_requires_hitl_true(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Even if the LLM sets requires_hitl=False, the agent should override it to True."""
    job = JobFactory.create()
    # LLM returns requires_hitl=False -- the agent MUST override this.
    proposal = ProposalFactory.create(requires_hitl=False)

    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=json.dumps(proposal, default=str)),
        CallMetrics(agent_name="bid", model_id="gemini-3-flash", provider="google"),
    ))

    agent = BidAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts={"scout": [job["id"]]})

    with (
        patch.object(agent, "_load_jobs", new_callable=AsyncMock, return_value=[job]),
        patch.object(agent, "_fetch_similar_bids", new_callable=AsyncMock, return_value=[]),
        patch.object(agent, "_store_bid", new_callable=AsyncMock, return_value="bid-002"),
        patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-002"),
        patch.object(agent, "_log_bid_generated", new_callable=AsyncMock),
    ):
        result = await agent._execute(state)

    assert result["requires_hitl"] is True


async def test_bid_stores_bid_in_db(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_store_bid should be called for each qualified job."""
    job = JobFactory.create()
    proposal = ProposalFactory.create()

    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=json.dumps(proposal, default=str)),
        CallMetrics(agent_name="bid", model_id="gemini-3-flash", provider="google"),
    ))

    agent = BidAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts={"scout": [job["id"]]})
    store_mock = AsyncMock(return_value="bid-003")

    with (
        patch.object(agent, "_load_jobs", new_callable=AsyncMock, return_value=[job]),
        patch.object(agent, "_fetch_similar_bids", new_callable=AsyncMock, return_value=[]),
        patch.object(agent, "_store_bid", store_mock),
        patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-003"),
        patch.object(agent, "_log_bid_generated", new_callable=AsyncMock),
    ):
        await agent._execute(state)

    store_mock.assert_awaited_once()
    call_args = store_mock.call_args
    assert call_args[0][0] == job  # first positional arg is the job dict


async def test_bid_creates_hitl_entry(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_create_hitl_entry should be called for each generated bid."""
    job = JobFactory.create()
    proposal = ProposalFactory.create()

    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=json.dumps(proposal, default=str)),
        CallMetrics(agent_name="bid", model_id="gemini-3-flash", provider="google"),
    ))

    agent = BidAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts={"scout": [job["id"]]})
    hitl_mock = AsyncMock(return_value="hitl-004")

    with (
        patch.object(agent, "_load_jobs", new_callable=AsyncMock, return_value=[job]),
        patch.object(agent, "_fetch_similar_bids", new_callable=AsyncMock, return_value=[]),
        patch.object(agent, "_store_bid", new_callable=AsyncMock, return_value="bid-004"),
        patch.object(agent, "_create_hitl_entry", hitl_mock),
        patch.object(agent, "_log_bid_generated", new_callable=AsyncMock),
    ):
        await agent._execute(state)

    hitl_mock.assert_awaited_once()
    call_args = hitl_mock.call_args
    assert call_args[0][0] == job  # first positional arg is the job dict
    assert call_args[0][2] == "bid-004"  # third positional arg is the bid_id


def test_bid_parse_proposal_valid(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_proposal_response should correctly parse a valid JSON proposal."""
    agent = BidAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    raw = json.dumps({
        "proposal_text": "I can build this for you.",
        "bid_amount": 250,
        "delivery_days": 7,
        "confidence_score": 0.8,
        "milestones": [
            {"description": "Phase 1", "amount": 125, "days": 3},
            {"description": "Phase 2", "amount": 125, "days": 4},
        ],
        "requires_hitl": True,
    })

    result = agent._parse_proposal_response(raw)

    assert result is not None
    assert result["proposal_text"] == "I can build this for you."
    assert result["bid_amount"] == 250.0
    assert result["delivery_days"] == 7
    assert result["confidence_score"] == 0.8
    assert len(result["milestones"]) == 2
    assert result["requires_hitl"] is True


def test_bid_parse_proposal_missing_field_returns_none(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_proposal_response should return None when required fields are missing."""
    agent = BidAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # Missing 'bid_amount' and 'delivery_days'.
    raw = json.dumps({
        "proposal_text": "I can build this for you.",
    })

    result = agent._parse_proposal_response(raw)
    assert result is None


def test_bid_parse_proposal_code_fenced_json(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_proposal_response should handle markdown-fenced JSON from the LLM."""
    agent = BidAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    raw = '```json\n{"proposal_text": "Hello", "bid_amount": 100, "delivery_days": 3}\n```'
    result = agent._parse_proposal_response(raw)

    assert result is not None
    assert result["bid_amount"] == 100.0
    assert result["requires_hitl"] is True  # invariant enforced
