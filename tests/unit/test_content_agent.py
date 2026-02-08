"""Unit tests for src.agents.content.ContentAgent.

All LLM calls and database operations are mocked.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.agents.content import ContentAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults for the Content Agent."""
    project = {
        "project_id": "proj-test-001",
        "job_id": "job-test-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a landing page with compelling copy",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=timezone.utc),
    }
    state = create_initial_state(project=project, first_agent="content", thread_id="thread-content-test")
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _make_content_json(
    content_type: str = "landing_page",
    deliverable_count: int = 2,
) -> str:
    """Build a valid content JSON response string."""
    deliverables = []
    for i in range(deliverable_count):
        deliverables.append({
            "name": f"deliverable_{i+1}",
            "content": f"Sample content for deliverable {i+1}",
            "alternatives": [f"Alt A for {i+1}", f"Alt B for {i+1}"],
            "notes": f"This deliverable works because of reason {i+1}.",
        })
    return json.dumps({
        "content_type": content_type,
        "deliverables": deliverables,
        "word_count": 150,
        "reading_time_seconds": 45,
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_content_generates_copy_routes_to_design(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """LLM returns valid content JSON -> next_agent='design', artifacts['content'] exists."""
    content_response = _make_content_json("landing_page", 3)
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=content_response),
        CallMetrics(agent_name="content", model_id="claude-haiku-4-5", provider="anthropic"),
    ))

    agent = ContentAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state()
    with patch.object(agent, "_log_content_generated", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] == "design"
    assert "content" in result["artifacts"]
    assert len(result["artifacts"]["content"]) == 1

    # Verify the stored artefact is valid JSON containing deliverables.
    stored = json.loads(result["artifacts"]["content"][0])
    assert stored["content_type"] == "landing_page"
    assert len(stored["deliverables"]) == 3


async def test_content_with_dev_context(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When artifacts['dev'] exists, the agent uses dev context in its prompt."""
    content_response = _make_content_json("documentation", 1)
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=content_response),
        CallMetrics(agent_name="content", model_id="claude-haiku-4-5", provider="anthropic"),
    ))

    agent = ContentAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # Pre-populate dev artefacts in the state.
    state = _build_state(
        artifacts={"dev": ["main.py: React landing page component"]},
    )

    with patch.object(agent, "_log_content_generated", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] == "design"
    assert "content" in result["artifacts"]
    # Dev artefacts should still be present.
    assert "dev" in result["artifacts"]


async def test_content_no_requirements_returns_no_next(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When there are no requirements in the project, next_agent should be None."""
    agent = ContentAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # Project with empty requirements.
    state = _build_state()
    state["project"]["requirements"] = ""  # type: ignore[typeddict-item]

    result = await agent._execute(state)

    assert result["next_agent"] is None
    assert result["status"] == "active"


async def test_content_llm_returns_invalid_json(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When the LLM returns non-JSON, the agent should return next_agent=None gracefully."""
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content="This is not valid JSON at all."),
        CallMetrics(agent_name="content", model_id="claude-haiku-4-5", provider="anthropic"),
    ))

    agent = ContentAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state()
    result = await agent._execute(state)

    assert result["next_agent"] is None
    assert result["status"] == "active"


def test_content_parse_response_valid(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_content_response should correctly parse valid JSON output."""
    agent = ContentAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    valid_json = json.dumps({
        "content_type": "email",
        "deliverables": [
            {
                "name": "subject_line",
                "content": "Unlock Your Growth Potential",
                "alternatives": ["Transform Your Business Today", "Discover What You're Missing"],
                "notes": "Uses curiosity gap technique.",
            },
            {
                "name": "body_copy",
                "content": "Hi {{name}}, I noticed your business...",
                "alternatives": ["Dear {{name}},", "Hello {{name}},"],
                "notes": "Personalized opening with specific reference.",
            },
        ],
        "word_count": 85,
        "reading_time_seconds": 25,
    })

    result = agent._parse_content_response(valid_json)

    assert result is not None
    assert result["content_type"] == "email"
    assert len(result["deliverables"]) == 2
    assert result["word_count"] == 85
    assert result["reading_time_seconds"] == 25
    assert result["deliverables"][0]["name"] == "subject_line"


def test_content_parse_response_invalid(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_content_response should return None on invalid JSON."""
    agent = ContentAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    result = agent._parse_content_response("this is not json at all")
    assert result is None


def test_content_parse_response_missing_deliverables(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_content_response should return None when deliverables are missing."""
    agent = ContentAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # Valid JSON but missing the required 'deliverables' field.
    raw = json.dumps({"content_type": "email", "word_count": 50})
    result = agent._parse_content_response(raw)
    assert result is None


def test_content_parse_response_code_fenced_json(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_content_response should handle markdown-fenced JSON from the LLM."""
    agent = ContentAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    inner = json.dumps({
        "content_type": "landing_page",
        "deliverables": [{"name": "hero", "content": "Go Big", "alternatives": [], "notes": "test"}],
        "word_count": 2,
        "reading_time_seconds": 1,
    })
    raw = f"```json\n{inner}\n```"

    result = agent._parse_content_response(raw)
    assert result is not None
    assert result["content_type"] == "landing_page"
    assert len(result["deliverables"]) == 1


def test_content_infer_content_type():
    """_infer_content_type should map requirements keywords to content types."""
    assert ContentAgent._infer_content_type("Build a landing page") == "landing_page"
    assert ContentAgent._infer_content_type("Write a cold email sequence") == "email"
    assert ContentAgent._infer_content_type("Create API documentation") == "documentation"
    assert ContentAgent._infer_content_type("Design button labels and error messages") == "ui_text"
    assert ContentAgent._infer_content_type("Write a blog article about SEO") == "blog_post"
    assert ContentAgent._infer_content_type("Create product descriptions for our listing") == "product_description"
    assert ContentAgent._infer_content_type("Build something amazing") == "landing_page"  # default
