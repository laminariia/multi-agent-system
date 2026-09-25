"""Integration tests for the Scout -> Bid -> HITL pipeline.

These tests exercise the full flow across multiple agents with mocked
LLM and database layers.  They verify that state transitions, artifact
propagation, and HITL gating work correctly end-to-end.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage

from src.agents.bid import BidAgent
from src.agents.scout import ScoutAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state
from tests.factories import JobFactory, ProposalFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_pipeline_state(**overrides: Any) -> AgentState:
    """Build a fresh AgentState for pipeline integration tests."""
    project = {
        "project_id": "proj-pipe-001",
        "job_id": "job-pipe-001",
        "platform": "freelancer",
        "client": {"name": "Pipeline Client", "rating": 4.5, "reviews": 10, "hire_rate": 0.7},
        "requirements": "Full-stack web application with React frontend",
        "budget": 1500.0,
        "deadline": datetime(2026, 4, 1, tzinfo=UTC),
    }
    state = create_initial_state(project=project, first_agent="scout", thread_id="thread-pipeline-001")
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_scout_to_bid_pipeline_hitl_required(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Full pipeline: Scout finds qualified jobs -> Bid generates proposals -> requires_hitl=True.

    Steps:
    1. Scout fetches jobs from mock adapter.
    2. Scout scores them via mock LLM -- all qualify.
    3. Scout returns state with next_agent='bid' and artifact IDs.
    4. Bid picks up artifact IDs, loads jobs, generates proposals.
    5. Bid returns state with requires_hitl=True and status='paused'.
    """
    # -- Setup qualified jobs --
    qualified_jobs = JobFactory.create_batch(2, match_score=0.88, recommendation="bid")
    scored_response = json.dumps(
        [{**j, "match_score": 0.88, "recommendation": "bid"} for j in qualified_jobs], default=str
    )

    # -- LLM responses: first call for scout scoring, second for bid proposal --
    proposal = ProposalFactory.create(bid_amount=400, delivery_days=5)
    proposal_json = json.dumps(proposal, default=str)

    scout_llm_response = (
        AIMessage(content=scored_response),
        CallMetrics(agent_name="scout", model_id="gemini-3-flash", provider="google"),
    )
    bid_llm_response = (
        AIMessage(content=proposal_json),
        CallMetrics(agent_name="bid", model_id="gemini-3-flash", provider="google"),
    )

    # -- Mock adapters --
    adapters = {
        "freelancer": MagicMock(fetch_jobs=AsyncMock(return_value=qualified_jobs)),
    }

    # ====== PHASE 1: Scout ======
    mock_llm_client.call = AsyncMock(return_value=scout_llm_response)

    scout = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    state = _build_pipeline_state()
    stored_ids = ["stored-id-1", "stored-id-2"]

    with (
        patch.object(scout, "_deduplicate", new_callable=AsyncMock, return_value=qualified_jobs),
        patch.object(scout, "_store_jobs", new_callable=AsyncMock, return_value=stored_ids),
        patch.object(scout, "_create_hitl_review", new_callable=AsyncMock),
        patch.object(scout, "_log_decision_summary", new_callable=AsyncMock),
    ):
        scout_result = await scout._execute(state)

    # Verify Scout routed to Bid.
    assert scout_result["next_agent"] == "bid"
    assert scout_result["artifacts"]["scout"] == stored_ids

    # ====== PHASE 2: Bid ======
    mock_llm_client.call = AsyncMock(return_value=bid_llm_response)

    bid_agent = BidAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # Build the Bid input state from Scout output.
    bid_input = dict(scout_result)
    bid_input["current_agent"] = "bid"

    # The bid agent needs to load jobs from DB -- mock that.
    loaded_jobs = [
        {**qualified_jobs[0], "id": stored_ids[0]},
        {**qualified_jobs[1], "id": stored_ids[1]},
    ]

    with (
        patch.object(bid_agent, "_load_jobs", new_callable=AsyncMock, return_value=loaded_jobs),
        patch.object(bid_agent, "_fetch_similar_bids", new_callable=AsyncMock, return_value=[]),
        patch.object(bid_agent, "_store_bid", new_callable=AsyncMock, side_effect=["bid-a", "bid-b"]),
        patch.object(bid_agent, "_create_hitl_entry", new_callable=AsyncMock, side_effect=["hitl-a", "hitl-b"]),
        patch.object(bid_agent, "_log_bid_generated", new_callable=AsyncMock),
    ):
        bid_result = await bid_agent._execute(bid_input)  # type: ignore[arg-type]

    # Verify HITL gating.
    assert bid_result["requires_hitl"] is True
    assert bid_result["status"] == "paused"
    assert bid_result["next_agent"] is None  # paused for HITL
    assert "bid" in bid_result["artifacts"]
    assert len(bid_result["artifacts"]["bid"]) == 2


async def test_pipeline_no_jobs_stops_early(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When no platform returns any jobs, the pipeline should stop without reaching Bid."""
    adapters = {
        "freelancer": MagicMock(fetch_jobs=AsyncMock(return_value=[])),
        "flru": MagicMock(fetch_jobs=AsyncMock(return_value=[])),
    }

    scout = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    state = _build_pipeline_state()

    with patch.object(scout, "_deduplicate", new_callable=AsyncMock, return_value=[]):
        scout_result = await scout._execute(state)

    assert scout_result["next_agent"] is None
    assert scout_result["status"] == "active"

    # The LLM should never have been called (no jobs to score).
    mock_llm_client.call.assert_not_awaited()
