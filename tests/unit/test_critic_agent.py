"""Unit tests for src.agents.critic.CriticAgent.

All LLM calls, DB operations, and infrastructure are mocked.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from src.agents.critic import _MAX_REVISION_CYCLES, CriticAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults for the Critic Agent."""
    project = {
        "project_id": "proj-test-001",
        "job_id": "job-test-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a landing page with React and Tailwind CSS",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    state = create_initial_state(project=project, first_agent="critic", thread_id="thread-critic-test")
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _make_dev_artifacts() -> dict[str, list[str]]:
    """Build a minimal set of dev artifacts for the critic to review."""
    code_data = json.dumps({
        "files": [
            {
                "path": "src/components/Hero.tsx",
                "content": "export default function Hero() { return <div>Hero</div>; }",
                "language": "typescript",
            }
        ],
        "dependencies": ["react", "tailwindcss"],
        "build_commands": ["npm install", "npm run build"],
        "test_commands": ["npm test"],
        "deployment_notes": "Deploy to Vercel.",
    })
    return {"dev": ["artifact-id-001", code_data]}


def _make_review_response(
    verdict: str = "approve",
    score: float = 0.92,
    issues: list[dict[str, Any]] | None = None,
    revision_type: str = "none",
) -> str:
    """Build a critic review JSON response string."""
    return json.dumps({
        "verdict": verdict,
        "score": score,
        "revision_type": revision_type,
        "issues": issues or [],
        "passed_checks": ["compiles", "tests_pass", "security"],
        "failed_checks": [],
        "revision_instructions": "" if verdict == "approve" else "Fix the reported issues.",
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_critic_approves_high_score(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When verdict='approve' and score >= 0.85, next_agent should be 'packager'."""
    review_response = _make_review_response(verdict="approve", score=0.92)
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=review_response),
        CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
    ))

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts=_make_dev_artifacts())
    with patch.object(agent, "_log_review_decision", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] == "packager"
    assert result["requires_hitl"] is False
    assert result["status"] == "active"
    assert "critic" in result["artifacts"]


async def test_critic_requests_revision(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When verdict='revise' and score between 0.60-0.84, next_agent should be 'dev' for minor revisions."""
    review_response = _make_review_response(
        verdict="revise",
        score=0.72,
        revision_type="minor",
        issues=[
            {
                "severity": "major",
                "category": "code",
                "description": "Missing error handling in API call",
                "location": "src/components/Hero.tsx:15",
                "suggestion": "Wrap fetch call in try/catch block",
            }
        ],
    )
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=review_response),
        CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
    ))

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts=_make_dev_artifacts())
    with patch.object(agent, "_log_review_decision", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] == "dev"
    assert result["requires_hitl"] is False
    assert result["status"] == "active"
    # Revision count should be incremented.
    revision_count = int(result["artifacts"]["_critic_revision_count"][0])
    assert revision_count == 1


async def test_critic_rejects_low_score(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When verdict='reject' and score < 0.60, requires_hitl=True and status='paused'."""
    review_response = _make_review_response(
        verdict="reject",
        score=0.40,
        issues=[
            {
                "severity": "critical",
                "category": "security",
                "description": "SQL injection vulnerability detected",
                "location": "src/db/query.py:25",
                "suggestion": "Use parameterised queries instead of string concatenation",
            },
            {
                "severity": "critical",
                "category": "code",
                "description": "Build fails with multiple errors",
                "location": "src/",
                "suggestion": "Fix TypeScript compilation errors",
            },
        ],
    )
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=review_response),
        CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
    ))

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts=_make_dev_artifacts())
    with patch.object(agent, "_log_review_decision", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["requires_hitl"] is True
    assert result["status"] == "paused"
    assert result["next_agent"] is None
    assert result["hitl_request_id"] is not None


async def test_critic_max_revisions_escalates(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When revision_count >= 3, requires_hitl=True regardless of verdict."""
    # Even though verdict is "revise" with a decent score, max revisions override.
    review_response = _make_review_response(verdict="revise", score=0.75)
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=review_response),
        CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
    ))

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # Set revision count to the max.
    artifacts = _make_dev_artifacts()
    artifacts["_critic_revision_count"] = [str(_MAX_REVISION_CYCLES)]

    state = _build_state(artifacts=artifacts)
    with patch.object(agent, "_log_review_decision", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["requires_hitl"] is True
    assert result["status"] == "paused"
    assert result["next_agent"] is None


async def test_critic_no_artifacts_returns_no_next(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When there are no artifacts to review, next_agent should be None."""
    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts={})
    result = await agent._execute(state)

    assert result["next_agent"] is None
    assert result["status"] == "active"


def test_critic_parse_review_response_valid(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_review_response should correctly parse valid JSON output."""
    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    valid_json = json.dumps({
        "verdict": "approve",
        "score": 0.88,
        "issues": [{"severity": "minor", "category": "code", "description": "Unused import"}],
        "passed_checks": ["security", "tests"],
        "failed_checks": [],
        "revision_instructions": "",
    })

    result = agent._parse_review_response(valid_json)
    assert result is not None
    assert result["verdict"] == "approve"
    assert result["score"] == 0.88
    assert len(result["issues"]) == 1
    assert result["issues"][0]["severity"] == "minor"


def test_critic_parse_review_response_invalid(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_review_response should return None for invalid JSON."""
    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    result = agent._parse_review_response("this is not valid json")
    assert result is None


def test_critic_parse_review_response_normalises_score(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_review_response should clamp score to [0.0, 1.0] range."""
    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # Score above 1.0 should be clamped.
    over_json = json.dumps({"verdict": "approve", "score": 1.5, "issues": []})
    result = agent._parse_review_response(over_json)
    assert result is not None
    assert result["score"] == 1.0

    # Score below 0.0 should be clamped.
    under_json = json.dumps({"verdict": "reject", "score": -0.5, "issues": []})
    result = agent._parse_review_response(under_json)
    assert result is not None
    assert result["score"] == 0.0


def test_critic_parse_review_response_invalid_verdict(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_review_response should default invalid verdicts to 'reject'."""
    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    bad_verdict = json.dumps({"verdict": "maybe", "score": 0.5, "issues": []})
    result = agent._parse_review_response(bad_verdict)
    assert result is not None
    assert result["verdict"] == "reject"


async def test_critic_approve_boundary_score(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Score exactly at the approve threshold (0.85) should route to packager."""
    review_response = _make_review_response(verdict="approve", score=0.85)
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=review_response),
        CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
    ))

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts=_make_dev_artifacts())
    with patch.object(agent, "_log_review_decision", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] == "packager"
    assert result["requires_hitl"] is False


# ---------------------------------------------------------------------------
# Revision Classification Tests
# ---------------------------------------------------------------------------


async def test_critic_major_revision_routes_to_planner(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When revision_type='major', next_agent should be 'planner' for re-decomposition."""
    review_response = _make_review_response(
        verdict="revise",
        score=0.68,
        revision_type="major",
        issues=[
            {
                "severity": "major",
                "category": "code",
                "description": "Hero section needs complete redesign",
                "location": "src/components/Hero.tsx",
                "suggestion": "Restructure component hierarchy and add interactive elements",
            }
        ],
    )
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=review_response),
        CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
    ))

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts=_make_dev_artifacts())
    with patch.object(agent, "_log_review_decision", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] == "planner"
    assert result["requires_hitl"] is False
    assert result["status"] == "active"
    assert result["artifacts"]["_critic_revision_type"] == ["major"]


async def test_critic_scope_creep_escalates_to_hitl(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When revision_type='scope_creep', requires_hitl=True regardless of score."""
    review_response = _make_review_response(
        verdict="revise",
        score=0.70,
        revision_type="scope_creep",
        issues=[
            {
                "severity": "major",
                "category": "scope",
                "description": "Client requests admin panel — not in original spec",
                "location": "N/A",
                "suggestion": "Discuss scope change with client before proceeding",
            }
        ],
    )
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=review_response),
        CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
    ))

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts=_make_dev_artifacts())
    with patch.object(agent, "_log_review_decision", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["requires_hitl"] is True
    assert result["status"] == "paused"
    assert result["next_agent"] is None
    assert result["hitl_request_id"] is not None
    assert result["artifacts"]["_critic_revision_type"] == ["scope_creep"]


async def test_critic_minor_revision_routes_to_dev(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When revision_type='minor' (explicit), next_agent should be 'dev'."""
    review_response = _make_review_response(
        verdict="revise",
        score=0.78,
        revision_type="minor",
        issues=[
            {
                "severity": "minor",
                "category": "code",
                "description": "Missing alt attribute on image",
                "location": "src/components/Hero.tsx:42",
                "suggestion": "Add descriptive alt text",
            }
        ],
    )
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=review_response),
        CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
    ))

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts=_make_dev_artifacts())
    with patch.object(agent, "_log_review_decision", new_callable=AsyncMock):
        result = await agent._execute(state)

    assert result["next_agent"] == "dev"
    assert result["requires_hitl"] is False
    assert result["status"] == "active"
    assert result["artifacts"]["_critic_revision_type"] == ["minor"]


def test_critic_parse_revision_type_normalisation(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_parse_review_response should normalise invalid revision_type values."""
    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # Invalid revision_type with revise verdict → defaults to "minor"
    bad_type = json.dumps({
        "verdict": "revise",
        "score": 0.72,
        "revision_type": "unknown_type",
        "issues": [],
    })
    result = agent._parse_review_response(bad_type)
    assert result is not None
    assert result["revision_type"] == "minor"

    # Invalid revision_type with approve verdict → defaults to "none"
    bad_type_approve = json.dumps({
        "verdict": "approve",
        "score": 0.92,
        "revision_type": "invalid",
        "issues": [],
    })
    result = agent._parse_review_response(bad_type_approve)
    assert result is not None
    assert result["revision_type"] == "none"

    # Missing revision_type → defaults to "none"
    no_type = json.dumps({
        "verdict": "approve",
        "score": 0.90,
        "issues": [],
    })
    result = agent._parse_review_response(no_type)
    assert result is not None
    assert result["revision_type"] == "none"


async def test_critic_default_revision_type_when_missing(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When LLM returns revise verdict without revision_type, should default to minor → dev."""
    review_response = json.dumps({
        "verdict": "revise",
        "score": 0.75,
        "issues": [{"severity": "minor", "category": "code", "description": "Small fix needed"}],
        "passed_checks": ["security"],
        "failed_checks": ["style"],
        "revision_instructions": "Fix style issues.",
    })
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=review_response),
        CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
    ))

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts=_make_dev_artifacts())
    with patch.object(agent, "_log_review_decision", new_callable=AsyncMock):
        result = await agent._execute(state)

    # Should default to minor → route to dev
    assert result["next_agent"] == "dev"
    assert result["requires_hitl"] is False
