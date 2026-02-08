"""Unit tests for src.agents.dev.DevAgent.

All LLM calls, DB operations, and infrastructure are mocked.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.agents.dev import DevAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults for the Dev Agent."""
    project = {
        "project_id": "proj-test-001",
        "job_id": "job-test-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a landing page with React and Tailwind CSS",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=timezone.utc),
    }
    state = create_initial_state(project=project, first_agent="dev", thread_id="thread-dev-test")
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _make_code_response(
    files: list[dict[str, str]] | None = None,
    dependencies: list[str] | None = None,
) -> str:
    """Build a valid code generation JSON response string."""
    if files is None:
        files = [
            {
                "path": "src/components/Hero.tsx",
                "content": "export default function Hero() { return <div>Hero</div>; }",
                "language": "typescript",
            },
            {
                "path": "src/styles/globals.css",
                "content": "@tailwind base;\n@tailwind components;\n@tailwind utilities;",
                "language": "css",
            },
        ]
    return json.dumps({
        "files": files,
        "dependencies": dependencies or ["react", "tailwindcss"],
        "build_commands": ["npm install", "npm run build"],
        "test_commands": ["npm test"],
        "deployment_notes": "Deploy to Vercel with `vercel --prod`.",
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_dev_generates_code_routes_to_content(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When LLM returns valid code JSON, next_agent should be 'content' and artifacts['dev'] should exist."""
    code_response = _make_code_response()
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=code_response),
        CallMetrics(agent_name="dev", model_id="claude-opus-4-6", provider="anthropic"),
    ))

    agent = DevAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state()
    with patch.object(agent, "_log_code_generated", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] == "content"
    assert "dev" in result["artifacts"]
    assert len(result["artifacts"]["dev"]) == 2  # artifact_id + serialized JSON


async def test_dev_with_plan_context(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When artifacts['planner'] exists, the agent uses plan context in LLM call."""
    code_response = _make_code_response()
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=code_response),
        CallMetrics(agent_name="dev", model_id="claude-opus-4-6", provider="anthropic"),
    ))

    agent = DevAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # Include planner artifacts with a plan.
    plan = json.dumps({
        "phases": [
            {
                "name": "Development",
                "tasks": [
                    {
                        "id": "task_1",
                        "description": "Build hero section with React",
                        "assigned_to": "dev",
                        "estimated_hours": 2.0,
                    }
                ],
            }
        ],
    })
    state = _build_state(artifacts={"planner": [plan]})

    with patch.object(agent, "_log_code_generated", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] == "content"
    assert "dev" in result["artifacts"]

    # Verify the LLM was called with plan context in the messages.
    call_args = mock_llm_client.call.call_args
    messages = call_args[0][1]  # positional arg: messages
    user_msg_content = str(messages[1].content)
    assert "Build hero section with React" in user_msg_content


def test_dev_parse_response_valid(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_code_response should correctly parse valid JSON output."""
    agent = DevAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    valid_json = json.dumps({
        "files": [
            {
                "path": "index.html",
                "content": "<html><body>Hello</body></html>",
                "language": "html",
            }
        ],
        "dependencies": [],
        "build_commands": [],
        "test_commands": [],
        "deployment_notes": "Open index.html in a browser.",
    })

    result = agent._parse_code_response(valid_json)
    assert result is not None
    assert len(result["files"]) == 1
    assert result["files"][0]["path"] == "index.html"
    assert result["files"][0]["language"] == "html"
    assert result["dependencies"] == []
    assert result["deployment_notes"] == "Open index.html in a browser."


def test_dev_parse_response_invalid(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_code_response should return None for invalid JSON."""
    agent = DevAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    result = agent._parse_code_response("this is not valid json at all")
    assert result is None


def test_dev_parse_response_missing_files(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_code_response should return None when files array is empty."""
    agent = DevAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    no_files = json.dumps({"files": [], "dependencies": []})
    result = agent._parse_code_response(no_files)
    assert result is None


def test_dev_parse_response_strips_markdown_fences(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_code_response should strip markdown code fences before parsing."""
    agent = DevAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    inner = json.dumps({
        "files": [{"path": "app.py", "content": "print('hello')", "language": "python"}],
        "dependencies": ["flask"],
        "build_commands": [],
        "test_commands": [],
        "deployment_notes": "",
    })
    fenced = f"```json\n{inner}\n```"

    result = agent._parse_code_response(fenced)
    assert result is not None
    assert result["files"][0]["path"] == "app.py"


def test_dev_infer_language():
    """_infer_language should map file extensions to language names."""
    assert DevAgent._infer_language("app.tsx") == "typescript"
    assert DevAgent._infer_language("styles.css") == "css"
    assert DevAgent._infer_language("main.py") == "python"
    assert DevAgent._infer_language("README.md") == "markdown"
    assert DevAgent._infer_language("unknown.xyz") == "text"


async def test_dev_no_task_context_returns_no_next(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When there is no task context (no plan, no current_task, no requirements), next_agent is None."""
    agent = DevAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # Create state with empty project requirements.
    state = _build_state()
    state["project"]["requirements"] = ""  # type: ignore[typeddict-item]
    state["current_task"] = None

    result = await agent._execute(state)
    assert result["next_agent"] is None
