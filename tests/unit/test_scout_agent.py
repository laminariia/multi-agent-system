"""Unit tests for src.agents.scout.ScoutAgent.

All LLM calls, DB operations, and platform adapters are mocked.
"""

from __future__ import annotations

import json
from datetime import UTC
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.agents.scout import ScoutAgent
from src.core.exceptions import LLMInvalidResponseError
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state
from tests.factories import JobFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scored_json(jobs: list[dict[str, Any]]) -> str:
    """Wrap scored job dicts in a JSON string for the mock LLM response."""
    return json.dumps(jobs, default=str)


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults."""
    from datetime import datetime

    project = {
        "project_id": "proj-test-001",
        "job_id": "job-test-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a landing page",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    state = create_initial_state(project=project, first_agent="scout", thread_id="thread-scout-test")
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_scout_no_jobs_found_returns_no_next(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When all platform adapters return empty lists, next_agent should be None."""
    adapters = {
        "freelancer": MagicMock(fetch_jobs=AsyncMock(return_value=[])),
        "flru": MagicMock(fetch_jobs=AsyncMock(return_value=[])),
    }

    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    state = _build_state()
    # Patch DB-dependent methods.
    with patch.object(agent, "_deduplicate", new_callable=AsyncMock, return_value=[]):
        result = await agent._execute(state)

    assert result["next_agent"] is None


async def test_scout_qualified_jobs_routes_to_bid(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Qualified jobs (score >= 0.7, recommendation='bid') should set next_agent='bid'."""
    raw_jobs = JobFactory.create_batch(2, match_score=0.85, recommendation="bid")
    adapters = {
        "freelancer": MagicMock(fetch_jobs=AsyncMock(return_value=raw_jobs)),
    }

    scored_response = _make_scored_json([
        {**j, "match_score": 0.85, "recommendation": "bid"} for j in raw_jobs
    ])
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=scored_response),
        CallMetrics(agent_name="scout", model_id="gemini-3-flash", provider="google"),
    ))

    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    state = _build_state()
    with (
        patch.object(agent, "_deduplicate", new_callable=AsyncMock, return_value=raw_jobs),
        patch.object(agent, "_store_jobs", new_callable=AsyncMock, return_value=["id-1", "id-2"]),
        patch.object(agent, "_create_hitl_review", new_callable=AsyncMock),
        patch.object(agent, "_log_decision_summary", new_callable=AsyncMock),
    ):
        result = await agent._execute(state)

    assert result["next_agent"] == "bid"
    assert "scout" in result["artifacts"]


async def test_scout_dedup_filters_existing(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When all fetched jobs already exist in DB, next_agent should be None."""
    raw_jobs = JobFactory.create_batch(3)
    adapters = {
        "freelancer": MagicMock(fetch_jobs=AsyncMock(return_value=raw_jobs)),
    }

    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    state = _build_state()
    # Dedup returns empty -- everything was already in DB.
    with patch.object(agent, "_deduplicate", new_callable=AsyncMock, return_value=[]):
        result = await agent._execute(state)

    assert result["next_agent"] is None


async def test_scout_borderline_creates_hitl_review(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Jobs with score in [0.5, 0.7) should create HITL review entries."""
    raw_jobs = [JobFactory.create()]
    adapters = {
        "freelancer": MagicMock(fetch_jobs=AsyncMock(return_value=raw_jobs)),
    }

    scored_response = _make_scored_json([
        {**raw_jobs[0], "match_score": 0.6, "recommendation": "review"},
    ])
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=scored_response),
        CallMetrics(agent_name="scout", model_id="gemini-3-flash", provider="google"),
    ))

    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    state = _build_state()
    with (
        patch.object(agent, "_deduplicate", new_callable=AsyncMock, return_value=raw_jobs),
        patch.object(agent, "_store_jobs", new_callable=AsyncMock, return_value=[]),
        patch.object(agent, "_create_hitl_review", new_callable=AsyncMock) as mock_hitl,
        patch.object(agent, "_log_decision_summary", new_callable=AsyncMock),
    ):
        result = await agent._execute(state)

    # HITL review should have been called for the borderline job.
    mock_hitl.assert_awaited_once()
    # No qualified jobs means next_agent is None.
    assert result["next_agent"] is None


def test_scout_parse_scored_response_valid_json(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_scored_response should correctly parse valid JSON output."""
    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters={},
    )

    valid_json = json.dumps([
        {"title": "Job A", "match_score": 0.9, "recommendation": "bid"},
        {"title": "Job B", "match_score": 0.3, "recommendation": "skip"},
    ])

    result = agent._parse_scored_response(valid_json, expected_count=2)
    assert len(result) == 2
    assert result[0]["match_score"] == 0.9
    assert result[0]["recommendation"] == "bid"
    assert result[1]["match_score"] == 0.3
    assert result[1]["recommendation"] == "skip"


def test_scout_parse_scored_response_invalid_json_raises(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_scored_response should raise LLMInvalidResponseError on invalid JSON."""
    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters={},
    )

    with pytest.raises(LLMInvalidResponseError):
        agent._parse_scored_response("this is not json at all", expected_count=1)
