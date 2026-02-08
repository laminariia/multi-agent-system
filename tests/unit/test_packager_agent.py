"""Unit tests for src.agents.packager.PackagerAgent.

All LLM calls and database operations are mocked.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from src.agents.packager import PackagerAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults."""
    project = {
        "project_id": "proj-test-pkg-001",
        "job_id": "job-test-pkg-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a landing page",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    state = create_initial_state(
        project=project, first_agent="packager", thread_id="thread-packager-test",
    )
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _make_delivery_json(
    project_id: str = "proj-test-pkg-001",
    files_count: int = 15,
) -> str:
    """Create a valid delivery package JSON string for mock LLM responses."""
    delivery = {
        "delivery_id": "del-test-001",
        "project_id": project_id,
        "files_count": files_count,
        "includes": ["source_code", "documentation", "screenshots"],
        "delivery_message": "Your landing page is ready! Here is what is included.",
        "readme_content": "# Landing Page\n\n## Setup\nnpm install && npm run dev",
        "missing_artifacts": [],
        "quality_notes": "All code passed Critic review.",
        "requires_hitl": True,
    }
    return json.dumps(delivery, default=str)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_packager_assembles_artifacts_creates_hitl(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Given artifacts in state -> requires_hitl=True, status='paused'."""
    delivery_json = _make_delivery_json()
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=delivery_json),
        CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
    ))

    agent = PackagerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts={
        "dev": ["artifact-dev-1", "artifact-dev-2"],
        "content": ["artifact-content-1"],
    })

    with (
        patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-pkg-001"),
        patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
    ):
        result = await agent._execute(state)

    assert result["requires_hitl"] is True
    assert result["status"] == "paused"
    assert result["next_agent"] is None
    assert result["hitl_request_id"] == "hitl-pkg-001"
    assert "packager" in result["artifacts"]
    assert len(result["artifacts"]["packager"]) == 1

    # Verify the stored delivery info is valid JSON.
    stored = json.loads(result["artifacts"]["packager"][0])
    assert stored["requires_hitl"] is True
    assert stored["files_count"] == 15


async def test_packager_no_artifacts_handles_gracefully(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """No execution artifacts -> next_agent=None, status stays 'active'."""
    agent = PackagerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # State with no execution agent artifacts (only scout/bid/planner present).
    state = _build_state(artifacts={
        "scout": ["job-id-1"],
        "bid": ["bid-id-1"],
        "planner": ["plan-json-1"],
    })

    with patch.object(agent, "_log_packaging_action", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] is None
    assert result["status"] == "active"
    # Should NOT set requires_hitl when there is nothing to package.
    assert result.get("requires_hitl", False) is False


async def test_packager_always_requires_hitl(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Verify requires_hitl is always True when artifacts are present, even if LLM omits it."""
    # LLM response intentionally has requires_hitl=False -- agent MUST override.
    delivery = {
        "delivery_id": "del-override",
        "project_id": "proj-test-pkg-001",
        "files_count": 5,
        "includes": ["source_code"],
        "delivery_message": "Ready.",
        "readme_content": "",
        "missing_artifacts": [],
        "quality_notes": "",
        "requires_hitl": False,  # LLM tries to bypass HITL
    }
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=json.dumps(delivery)),
        CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
    ))

    agent = PackagerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts={"dev": ["artifact-1"]})

    with (
        patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-override"),
        patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
    ):
        result = await agent._execute(state)

    # CRITICAL: requires_hitl must be True regardless of LLM output.
    assert result["requires_hitl"] is True
    assert result["status"] == "paused"

    # Also verify the stored artifact has requires_hitl=True.
    stored = json.loads(result["artifacts"]["packager"][0])
    assert stored["requires_hitl"] is True


async def test_packager_llm_failure_uses_fallback(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When LLM returns garbage, the packager should fall back to a basic delivery."""
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content="I cannot generate that for you."),
        CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
    ))

    agent = PackagerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts={"dev": ["art-1", "art-2"], "design": ["art-3"]})

    with (
        patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-fallback"),
        patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
    ):
        result = await agent._execute(state)

    assert result["requires_hitl"] is True
    assert result["status"] == "paused"
    assert "packager" in result["artifacts"]

    # Fallback delivery should still have basic structure.
    stored = json.loads(result["artifacts"]["packager"][0])
    assert stored["files_count"] == 3  # 2 dev + 1 design
    assert stored["requires_hitl"] is True


async def test_packager_creates_hitl_entry(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_create_hitl_entry should be called exactly once with correct parameters."""
    delivery_json = _make_delivery_json()
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=delivery_json),
        CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
    ))

    agent = PackagerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts={"dev": ["artifact-1"]})
    hitl_mock = AsyncMock(return_value="hitl-check-001")

    with (
        patch.object(agent, "_create_hitl_entry", hitl_mock),
        patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
    ):
        result = await agent._execute(state)

    hitl_mock.assert_awaited_once()
    # First positional arg is the project dict, second is delivery_info.
    call_args = hitl_mock.call_args
    assert call_args[0][0] == state["project"]
    assert isinstance(call_args[0][1], dict)
    assert call_args[0][1]["requires_hitl"] is True
    assert result["hitl_request_id"] == "hitl-check-001"


def test_packager_parse_delivery_valid_json(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_delivery_response should correctly parse valid JSON delivery output."""
    agent = PackagerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    raw = json.dumps({
        "delivery_id": "del-001",
        "project_id": "proj-001",
        "files_count": 10,
        "includes": ["source_code", "docs"],
        "delivery_message": "Project delivered.",
        "readme_content": "# README",
        "missing_artifacts": [],
        "quality_notes": "All good.",
        "requires_hitl": True,
    })

    result = agent._parse_delivery_response(raw)

    assert result is not None
    assert result["delivery_id"] == "del-001"
    assert result["files_count"] == 10
    assert result["requires_hitl"] is True
    assert len(result["includes"]) == 2


def test_packager_parse_delivery_invalid_json(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_delivery_response should return None on invalid JSON."""
    agent = PackagerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    result = agent._parse_delivery_response("this is not JSON at all")
    assert result is None


def test_packager_parse_delivery_code_fenced_json(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_delivery_response should handle markdown-fenced JSON from the LLM."""
    agent = PackagerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    inner = json.dumps({
        "delivery_id": "del-fenced",
        "project_id": "proj-fenced",
        "files_count": 3,
        "includes": ["source_code"],
        "delivery_message": "Ready.",
    })
    fenced = f"```json\n{inner}\n```"

    result = agent._parse_delivery_response(fenced)

    assert result is not None
    assert result["delivery_id"] == "del-fenced"
    assert result["requires_hitl"] is True  # invariant enforced even if missing


def test_packager_collect_execution_artifacts(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_collect_execution_artifacts should only include dev, content, design, critic."""
    agent = PackagerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    all_artifacts = {
        "scout": ["job-1"],
        "bid": ["bid-1"],
        "planner": ["plan-1"],
        "dev": ["code-1", "code-2"],
        "content": ["text-1"],
        "design": [],  # Empty -- should NOT be included.
        "critic": ["review-1"],
    }

    collected = agent._collect_execution_artifacts(all_artifacts)

    assert "dev" in collected
    assert "content" in collected
    assert "critic" in collected
    # scout, bid, planner are NOT execution agents.
    assert "scout" not in collected
    assert "bid" not in collected
    assert "planner" not in collected
    # design is empty, so should not be included.
    assert "design" not in collected
