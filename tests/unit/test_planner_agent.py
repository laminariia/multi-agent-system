"""Unit tests for src.agents.planner.PlannerAgent.

All LLM calls and database operations are mocked.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from src.agents.planner import _MAX_REPLANS, PlannerAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults."""
    project = {
        "project_id": "proj-test-001",
        "job_id": "job-test-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a landing page with React and Tailwind CSS",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    state = create_initial_state(
        project=project, first_agent="planner", thread_id="thread-planner-test",
    )
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _make_plan_json(
    project_id: str = "proj-test-001",
    total_hours: float = 8.0,
) -> str:
    """Create a valid plan JSON string for mock LLM responses."""
    plan = {
        "project_id": project_id,
        "phases": [
            {
                "name": "Setup",
                "tasks": [
                    {
                        "id": "task_1",
                        "description": "Set up React project with Tailwind CSS",
                        "assigned_to": "dev",
                        "estimated_hours": 1.0,
                        "dependencies": [],
                        "deliverables": ["package.json", "tailwind.config.js"],
                    },
                ],
            },
            {
                "name": "Implementation",
                "tasks": [
                    {
                        "id": "task_2",
                        "description": "Create responsive hero section",
                        "assigned_to": "dev",
                        "estimated_hours": 3.0,
                        "dependencies": ["task_1"],
                        "deliverables": ["src/components/Hero.tsx"],
                    },
                    {
                        "id": "task_3",
                        "description": "Write landing page copy",
                        "assigned_to": "content",
                        "estimated_hours": 2.0,
                        "dependencies": [],
                        "deliverables": ["copy.md"],
                    },
                ],
            },
            {
                "name": "Review",
                "tasks": [
                    {
                        "id": "task_4",
                        "description": "Quality review of all deliverables",
                        "assigned_to": "critic",
                        "estimated_hours": 2.0,
                        "dependencies": ["task_2", "task_3"],
                        "deliverables": ["review_report"],
                    },
                ],
            },
        ],
        "total_estimated_hours": total_hours,
        "critical_path": ["task_1", "task_2", "task_4"],
        "risks": ["Client may request scope changes"],
    }
    return json.dumps(plan, default=str)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_planner_generates_plan_routes_to_dev(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """LLM returns valid plan JSON -> next_agent='dev', artifacts['planner'] exists."""
    plan_json = _make_plan_json()
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=plan_json),
        CallMetrics(agent_name="planner", model_id="claude-opus-4-6", provider="anthropic"),
    ))

    agent = PlannerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state()

    with patch.object(agent, "_log_planning_action", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] == "dev"
    assert "planner" in result["artifacts"]
    assert len(result["artifacts"]["planner"]) == 1

    # Verify the stored plan is valid JSON.
    stored_plan = json.loads(result["artifacts"]["planner"][0])
    assert stored_plan["project_id"] == "proj-test-001"
    assert len(stored_plan["phases"]) == 3
    assert stored_plan["total_estimated_hours"] == 8.0


async def test_planner_empty_requirements_handles_gracefully(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Empty requirements -> still produces a minimal plan and routes to dev."""
    agent = PlannerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # Override project to have empty requirements.
    project = {
        "project_id": "proj-empty-001",
        "job_id": "job-empty-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "",
        "budget": 200.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    state = _build_state(project=project)

    with patch.object(agent, "_log_planning_action", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] == "dev"
    assert "planner" in result["artifacts"]
    assert len(result["artifacts"]["planner"]) == 1

    # The minimal plan should have a clarification task.
    stored_plan = json.loads(result["artifacts"]["planner"][0])
    assert stored_plan["project_id"] == "proj-empty-001"
    assert len(stored_plan["risks"]) > 0
    assert any("empty" in risk.lower() or "unclear" in risk.lower() for risk in stored_plan["risks"])


async def test_planner_max_replans_triggers_hitl(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When re-plan count reaches _MAX_REPLANS, the agent should escalate to HITL."""
    agent = PlannerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # Pre-fill artifacts with _MAX_REPLANS existing plans.
    existing_plans = [_make_plan_json() for _ in range(_MAX_REPLANS)]
    state = _build_state(artifacts={"planner": existing_plans})

    result = await agent._execute(state)

    assert result["requires_hitl"] is True
    assert result["status"] == "paused"
    assert result["next_agent"] is None
    assert any("re-plan" in err.lower() for err in result["errors"])


def test_planner_parse_response_valid_json(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_plan_response should correctly parse valid JSON plan output."""
    agent = PlannerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    valid_json = json.dumps({
        "project_id": "proj-001",
        "phases": [
            {
                "name": "Phase 1",
                "tasks": [
                    {
                        "id": "task_1",
                        "description": "Setup project",
                        "assigned_to": "dev",
                        "estimated_hours": 2.0,
                        "dependencies": [],
                        "deliverables": ["repo"],
                    },
                ],
            },
        ],
        "total_estimated_hours": 2.0,
        "critical_path": ["task_1"],
        "risks": ["None identified"],
    })

    result = agent._parse_plan_response(valid_json)

    assert result is not None
    assert result["project_id"] == "proj-001"
    assert len(result["phases"]) == 1
    assert result["total_estimated_hours"] == 2.0
    assert result["phases"][0]["tasks"][0]["id"] == "task_1"
    assert result["phases"][0]["tasks"][0]["assigned_to"] == "dev"
    assert result["critical_path"] == ["task_1"]


def test_planner_parse_response_invalid_json(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_plan_response should return None on invalid JSON."""
    agent = PlannerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    result = agent._parse_plan_response("this is definitely not JSON at all")
    assert result is None


def test_planner_parse_response_code_fenced_json(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_plan_response should handle markdown-fenced JSON from the LLM."""
    agent = PlannerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    inner = json.dumps({
        "project_id": "proj-fenced",
        "phases": [],
        "total_estimated_hours": 0,
        "critical_path": [],
        "risks": [],
    })
    fenced = f"```json\n{inner}\n```"

    result = agent._parse_plan_response(fenced)
    assert result is not None
    assert result["project_id"] == "proj-fenced"


def test_planner_parse_response_missing_phases_defaults(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_plan_response should default missing 'phases' to an empty list."""
    agent = PlannerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    raw = json.dumps({"project_id": "proj-no-phases"})

    result = agent._parse_plan_response(raw)
    assert result is not None
    assert result["phases"] == []
    assert result["total_estimated_hours"] == 0.0
    assert result["critical_path"] == []
    assert result["risks"] == []


async def test_planner_llm_failure_returns_error(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When LLM returns garbage, the planner should add an error and not crash."""
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content="Sorry, I cannot help with that."),
        CallMetrics(agent_name="planner", model_id="claude-opus-4-6", provider="anthropic"),
    ))

    agent = PlannerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state()
    result = await agent._execute(state)

    assert result["next_agent"] is None
    assert any("unparseable" in err.lower() for err in result["errors"])


async def test_planner_determines_content_as_first_agent(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When the first task is assigned to 'content', next_agent should be 'content'."""
    plan = {
        "project_id": "proj-content-first",
        "phases": [
            {
                "name": "Content",
                "tasks": [
                    {
                        "id": "task_1",
                        "description": "Write blog post",
                        "assigned_to": "content",
                        "estimated_hours": 2.0,
                        "dependencies": [],
                        "deliverables": ["blog.md"],
                    },
                ],
            },
        ],
        "total_estimated_hours": 2.0,
        "critical_path": ["task_1"],
        "risks": [],
    }

    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=json.dumps(plan)),
        CallMetrics(agent_name="planner", model_id="claude-opus-4-6", provider="anthropic"),
    ))

    agent = PlannerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state()

    with patch.object(agent, "_log_planning_action", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] == "content"
