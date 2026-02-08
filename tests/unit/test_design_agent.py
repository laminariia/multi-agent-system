"""Unit tests for src.agents.design.DesignAgent.

All LLM calls and database operations are mocked.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.agents.design import DesignAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults for the Design Agent."""
    project = {
        "project_id": "proj-test-001",
        "job_id": "job-test-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a responsive landing page with modern design",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=timezone.utc),
    }
    state = create_initial_state(project=project, first_agent="design", thread_id="thread-design-test")
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _make_design_json(
    design_type: str = "ui_mockup",
    deliverable_count: int = 2,
) -> str:
    """Build a valid design JSON response string."""
    deliverables = []
    for i in range(deliverable_count):
        deliverables.append({
            "name": f"page_{i+1}_desktop",
            "format": "spec",
            "dimensions": "1440x900",
            "specs": {
                "colors": ["#3B82F6", "#1E293B", "#F8FAFC"],
                "fonts": ["Inter"],
                "components_used": ["hero_section", "features_grid"],
                "layout": f"Layout description for deliverable {i+1}",
                "responsive_notes": "Tablet: stack; Mobile: full-width",
                "dark_mode": "Invert background to #0F172A",
            },
        })
    return json.dumps({
        "design_type": design_type,
        "deliverables": deliverables,
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_design_generates_specs_routes_to_critic(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """LLM returns valid design JSON -> next_agent='critic', artifacts['design'] exists."""
    design_response = _make_design_json("ui_mockup", 2)
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=design_response),
        CallMetrics(agent_name="design", model_id="claude-sonnet-4-5", provider="anthropic"),
    ))

    agent = DesignAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state()
    with patch.object(agent, "_log_design_generated", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] == "critic"
    assert "design" in result["artifacts"]
    assert len(result["artifacts"]["design"]) == 1

    # Verify the stored artefact is valid JSON containing deliverables.
    stored = json.loads(result["artifacts"]["design"][0])
    assert stored["design_type"] == "ui_mockup"
    assert len(stored["deliverables"]) == 2


async def test_design_with_content_context(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When artifacts['content'] exists, the agent uses content context in its prompt."""
    design_response = _make_design_json("ui_mockup", 1)
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=design_response),
        CallMetrics(agent_name="design", model_id="claude-sonnet-4-5", provider="anthropic"),
    ))

    agent = DesignAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # Pre-populate content artefacts in the state.
    content_artifact = json.dumps({
        "content_type": "landing_page",
        "deliverables": [{"name": "hero_headline", "content": "Transform Your Business"}],
        "word_count": 50,
        "reading_time_seconds": 15,
    })
    state = _build_state(
        artifacts={"content": [content_artifact]},
    )

    with patch.object(agent, "_log_design_generated", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] == "critic"
    assert "design" in result["artifacts"]
    # Content artefacts should still be present.
    assert "content" in result["artifacts"]


async def test_design_no_requirements_returns_no_next(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When there are no requirements in the project, next_agent should be None."""
    agent = DesignAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state()
    state["project"]["requirements"] = ""  # type: ignore[typeddict-item]

    result = await agent._execute(state)

    assert result["next_agent"] is None
    assert result["status"] == "active"


async def test_design_llm_returns_invalid_json(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When the LLM returns non-JSON, the agent should return next_agent=None gracefully."""
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content="I cannot generate a valid design spec right now."),
        CallMetrics(agent_name="design", model_id="claude-sonnet-4-5", provider="anthropic"),
    ))

    agent = DesignAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state()
    result = await agent._execute(state)

    assert result["next_agent"] is None
    assert result["status"] == "active"


def test_design_parse_response_valid(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_design_response should correctly parse valid JSON output."""
    agent = DesignAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    valid_json = json.dumps({
        "design_type": "ui_mockup",
        "deliverables": [
            {
                "name": "homepage_desktop",
                "format": "spec",
                "dimensions": "1440x900",
                "specs": {
                    "colors": ["#3B82F6", "#1E293B"],
                    "fonts": ["Inter"],
                    "components_used": ["hero_section", "features_grid", "cta_section"],
                    "layout": "Full-width hero with centered text, 3-column features grid below.",
                    "responsive_notes": "Tablet: 2-column grid; Mobile: single column stacked.",
                    "dark_mode": "Invert background to #0F172A, text to #F1F5F9.",
                },
            },
            {
                "name": "homepage_mobile",
                "format": "spec",
                "dimensions": "375x812",
                "specs": {
                    "colors": ["#3B82F6", "#1E293B"],
                    "fonts": ["Inter"],
                    "components_used": ["hero_section", "features_stack", "cta_section"],
                    "layout": "Single column, full-width sections stacked vertically.",
                    "responsive_notes": "This is the mobile variant.",
                    "dark_mode": "Same dark palette as desktop variant.",
                },
            },
        ],
    })

    result = agent._parse_design_response(valid_json)

    assert result is not None
    assert result["design_type"] == "ui_mockup"
    assert len(result["deliverables"]) == 2
    assert result["deliverables"][0]["name"] == "homepage_desktop"
    assert result["deliverables"][1]["dimensions"] == "375x812"


def test_design_parse_response_invalid(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_design_response should return None on invalid JSON."""
    agent = DesignAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    result = agent._parse_design_response("this is not json at all")
    assert result is None


def test_design_parse_response_missing_deliverables(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_design_response should return None when deliverables are missing."""
    agent = DesignAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    raw = json.dumps({"design_type": "ui_mockup"})
    result = agent._parse_design_response(raw)
    assert result is None


def test_design_parse_response_code_fenced_json(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_design_response should handle markdown-fenced JSON from the LLM."""
    agent = DesignAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    inner = json.dumps({
        "design_type": "graphic",
        "deliverables": [{
            "name": "logo_primary",
            "format": "spec",
            "dimensions": "512x512",
            "specs": {
                "colors": ["#3B82F6"],
                "fonts": ["Inter"],
                "components_used": [],
                "layout": "Circular logo with stylized initials.",
                "responsive_notes": "N/A for logo.",
                "dark_mode": "White variant on dark backgrounds.",
            },
        }],
    })
    raw = f"```json\n{inner}\n```"

    result = agent._parse_design_response(raw)
    assert result is not None
    assert result["design_type"] == "graphic"
    assert len(result["deliverables"]) == 1


def test_design_infer_design_type():
    """_infer_design_type should map requirements keywords to design types."""
    assert DesignAgent._infer_design_type("Build a landing page") == "ui_mockup"
    assert DesignAgent._infer_design_type("Create admin dashboard") == "ui_mockup"
    assert DesignAgent._infer_design_type("Design a new logo for our brand") == "graphic"
    assert DesignAgent._infer_design_type("Create an icon set for navigation") == "icon_set"
    assert DesignAgent._infer_design_type("Build a design system") == "design_system"
    assert DesignAgent._infer_design_type("Create a wireframe for the user flow") == "wireframe"
    assert DesignAgent._infer_design_type("Build something amazing") == "ui_mockup"  # default
