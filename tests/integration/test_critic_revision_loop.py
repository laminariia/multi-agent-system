"""Integration tests for the Critic Agent revision loop.

Verifies the full cycle: Critic reviews -> routes based on verdict ->
Dev/Planner/HITL receives correct feedback. Tests cover:

1. Approve on first pass -> packager
2. Minor revision loop (Critic -> Dev -> Critic) with revision count increment
3. Three revisions exceeded -> HITL escalation
4. Major revision -> Planner re-decomposition

All LLM calls and DB operations are mocked; the focus is on state transitions
and correct routing through the revision loop logic.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from src.agents.critic import (
    _APPROVE_THRESHOLD,
    _MAX_REVISION_CYCLES,
    _SEMGREP_CRITICAL_PENALTY,
    _SEMGREP_WARNING_PENALTY,
    CriticAgent,
)
from src.agents.dev import DevAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults."""
    project = {
        "project_id": "proj-loop-001",
        "job_id": "job-loop-001",
        "platform": "freelancer",
        "client": {"name": "Loop Test Client"},
        "requirements": "Build a responsive landing page with React and Tailwind CSS",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    state = create_initial_state(
        project=project,
        first_agent="critic",
        thread_id="thread-revision-loop",
    )
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _make_dev_artifacts() -> dict[str, list[str]]:
    """Build minimal dev artifacts for the critic to review."""
    code_data = json.dumps({
        "files": [
            {
                "path": "src/components/Hero.tsx",
                "content": "export default function Hero() { return <div>Hero</div>; }",
                "language": "typescript",
            }
        ],
        "dependencies": ["react"],
    })
    return {"dev": ["artifact-id-001", code_data]}


def _make_review_response(
    verdict: str = "approve",
    score: float = 0.92,
    revision_type: str = "none",
    issues: list[dict[str, Any]] | None = None,
    revision_instructions: str = "",
) -> str:
    """Build a critic review JSON response string."""
    return json.dumps({
        "verdict": verdict,
        "score": score,
        "revision_type": revision_type,
        "issues": issues or [],
        "passed_checks": ["compiles", "tests_pass", "security"],
        "failed_checks": [],
        "revision_instructions": revision_instructions,
    })


# ---------------------------------------------------------------------------
# Test 1: Approve on first pass -> packager
# ---------------------------------------------------------------------------


async def test_approve_first_pass_routes_to_packager(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
) -> None:
    """Clean code approved on first review routes directly to packager."""
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

    # Verify routing.
    assert result["next_agent"] == "packager"
    assert result["requires_hitl"] is False
    assert result["status"] == "active"

    # Verify critic artifact is stored.
    assert "critic" in result["artifacts"]
    critic_data = json.loads(result["artifacts"]["critic"][0])
    assert critic_data["verdict"] == "approve"
    assert critic_data["score"] >= _APPROVE_THRESHOLD

    # Revision count should be 0 (first pass).
    revision_count = int(result["artifacts"]["_critic_revision_count"][0])
    assert revision_count == 0


# ---------------------------------------------------------------------------
# Test 2: Minor revision loop (Critic -> Dev -> Critic)
# ---------------------------------------------------------------------------


async def test_minor_revision_loop_increments_count(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
) -> None:
    """Minor revision routes to dev with incremented revision count.

    Then simulate dev producing new code and critic approving on second pass.
    """
    # --- Pass 1: Critic requests minor revision ---
    review_pass1 = _make_review_response(
        verdict="revise",
        score=0.72,
        revision_type="minor",
        issues=[{
            "severity": "minor",
            "category": "accessibility",
            "description": "Missing alt attribute on <img>",
            "location": "src/components/Hero.tsx:5",
            "suggestion": "Add descriptive alt text",
        }],
        revision_instructions="Add alt attribute to the <img> tag.",
    )
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=review_pass1),
        CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
    ))

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts=_make_dev_artifacts())
    with patch.object(agent, "_log_review_decision", new_callable=AsyncMock):
        result_pass1 = await agent._execute(state)

    # Verify routing to dev.
    assert result_pass1["next_agent"] == "dev"
    assert result_pass1["requires_hitl"] is False
    assert result_pass1["status"] == "active"

    # Revision count incremented to 1.
    rev_count_1 = int(result_pass1["artifacts"]["_critic_revision_count"][0])
    assert rev_count_1 == 1
    assert result_pass1["artifacts"]["_critic_revision_type"] == ["minor"]

    # --- Pass 2: Critic approves the revised code ---
    review_pass2 = _make_review_response(verdict="approve", score=0.90)
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=review_pass2),
        CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
    ))

    # Simulate dev producing revised artifacts (state carries revision count forward).
    state_pass2 = _build_state(artifacts=result_pass1["artifacts"])
    with patch.object(agent, "_log_review_decision", new_callable=AsyncMock):
        result_pass2 = await agent._execute(state_pass2)

    # Should now route to packager.
    assert result_pass2["next_agent"] == "packager"
    assert result_pass2["requires_hitl"] is False


# ---------------------------------------------------------------------------
# Test 3: Three revisions exceeded -> HITL escalation
# ---------------------------------------------------------------------------


async def test_three_revisions_escalates_to_hitl(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
) -> None:
    """After 3 revision cycles, any verdict triggers HITL escalation."""
    # LLM would say "revise" but max revisions override.
    review_response = _make_review_response(
        verdict="revise",
        score=0.75,
        revision_type="minor",
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

    # Set revision count to the limit.
    artifacts = _make_dev_artifacts()
    artifacts["_critic_revision_count"] = [str(_MAX_REVISION_CYCLES)]

    state = _build_state(artifacts=artifacts)
    with (
        patch.object(agent, "_log_review_decision", new_callable=AsyncMock),
        patch.object(agent, "_create_hitl_escalation", new_callable=AsyncMock) as mock_escalation,
    ):
        result = await agent._execute(state)

    # Must escalate to HITL.
    assert result["requires_hitl"] is True
    assert result["status"] == "paused"
    assert result["next_agent"] is None
    assert result["hitl_request_id"] is not None

    # Verify HITL escalation was created with correct reason.
    mock_escalation.assert_called_once()
    call_kwargs = mock_escalation.call_args.kwargs
    assert "revision_limit_exceeded" in call_kwargs["reason"]
    assert call_kwargs["revision_count"] == _MAX_REVISION_CYCLES


# ---------------------------------------------------------------------------
# Test 4: Major revision -> Planner re-decomposition
# ---------------------------------------------------------------------------


async def test_major_revision_routes_to_planner(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
) -> None:
    """Major revision routes to planner for task re-decomposition."""
    review_response = _make_review_response(
        verdict="revise",
        score=0.65,
        revision_type="major",
        issues=[{
            "severity": "major",
            "category": "architecture",
            "description": "Component hierarchy needs restructuring",
            "location": "src/",
            "suggestion": "Split into smaller components with proper state management",
        }],
        revision_instructions="Restructure the component hierarchy.",
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

    # Verify routing to planner.
    assert result["next_agent"] == "planner"
    assert result["requires_hitl"] is False
    assert result["status"] == "active"

    # Revision metadata stored.
    assert result["artifacts"]["_critic_revision_type"] == ["major"]
    rev_count = int(result["artifacts"]["_critic_revision_count"][0])
    assert rev_count == 1

    # Critic artifacts contain the review.
    critic_review = json.loads(result["artifacts"]["critic"][0])
    assert critic_review["verdict"] == "revise"
    assert critic_review["revision_type"] == "major"


# ---------------------------------------------------------------------------
# Test 5: Semgrep score penalties (bonus coverage)
# ---------------------------------------------------------------------------


async def test_semgrep_warnings_reduce_score(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
) -> None:
    """Semgrep warnings should apply score penalties without blocking."""
    from src.security.semgrep_gate import ScanResult, SemgrepFinding

    # LLM gives 0.90 (would normally approve), but Semgrep finds 2 warnings.
    review_response = _make_review_response(verdict="approve", score=0.90)
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=review_response),
        CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
    ))

    # Semgrep returns non-blocked result with 2 warnings.
    scan_result_with_warnings = ScanResult(
        findings=[
            SemgrepFinding(
                rule_id="mas-no-console-log",
                severity="WARNING",
                message="console.log detected",
                path="main.js",
                line=5,
                code_snippet="console.log('debug')",
            ),
            SemgrepFinding(
                rule_id="mas-unused-import",
                severity="WARNING",
                message="Unused import detected",
                path="main.js",
                line=1,
                code_snippet="import fs from 'fs'",
            ),
        ],
        critical_count=0,
        warning_count=2,
        blocked=False,
    )

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts=_make_dev_artifacts())
    with (
        patch.object(agent, "_run_semgrep_scan", new_callable=AsyncMock, return_value=scan_result_with_warnings),
        patch.object(agent, "_log_review_decision", new_callable=AsyncMock),
        patch.object(agent, "_create_hitl_escalation", new_callable=AsyncMock),
    ):
        result = await agent._execute(state)

    # Score should be reduced: 0.90 - (2 * 0.1) = 0.70.
    critic_review = json.loads(result["artifacts"]["critic"][0])
    expected_score = 0.90 - (2 * _SEMGREP_WARNING_PENALTY)
    assert abs(critic_review["score"] - expected_score) < 0.001

    # With adjusted score of 0.70, verdict "approve" but score < 0.85 doesn't meet
    # _APPROVE_THRESHOLD, and verdict isn't "revise", so it falls to reject → HITL.
    assert result["requires_hitl"] is True
    assert result["status"] == "paused"


async def test_semgrep_critical_penalty_exceeds_threshold(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
) -> None:
    """A single Semgrep CRITICAL finding should apply -0.3 penalty."""
    from src.security.semgrep_gate import ScanResult, SemgrepFinding

    # LLM gives 0.88 (would approve), but 1 critical finding.
    review_response = _make_review_response(verdict="approve", score=0.88)
    mock_llm_client.call = AsyncMock(return_value=(
        AIMessage(content=review_response),
        CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
    ))

    scan_result_critical = ScanResult(
        findings=[
            SemgrepFinding(
                rule_id="mas-sql-injection",
                severity="ERROR",
                message="SQL injection detected",
                path="db.py",
                line=10,
                code_snippet="query = f'SELECT * FROM users WHERE id={uid}'",
            ),
        ],
        critical_count=1,
        warning_count=0,
        blocked=False,  # Non-blocked but penalized.
    )

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts=_make_dev_artifacts())
    with (
        patch.object(agent, "_run_semgrep_scan", new_callable=AsyncMock, return_value=scan_result_critical),
        patch.object(agent, "_log_review_decision", new_callable=AsyncMock),
        patch.object(agent, "_create_hitl_escalation", new_callable=AsyncMock),
    ):
        result = await agent._execute(state)

    # Score: 0.88 - 0.30 = 0.58 (below revise threshold → reject → HITL).
    critic_review = json.loads(result["artifacts"]["critic"][0])
    expected_score = 0.88 - _SEMGREP_CRITICAL_PENALTY
    assert abs(critic_review["score"] - expected_score) < 0.001
    assert result["requires_hitl"] is True


# ---------------------------------------------------------------------------
# Test 7: Dev Agent revision prompt includes critic feedback
# ---------------------------------------------------------------------------


async def test_dev_build_revision_prompt_includes_critic_feedback(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
) -> None:
    """Dev Agent should build a revision-aware prompt when critic feedback exists."""
    agent = DevAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    # State with critic artifacts (revision scenario).
    artifacts = _make_dev_artifacts()
    artifacts["critic"] = [json.dumps({
        "verdict": "revise",
        "score": 0.72,
        "revision_type": "minor",
        "issues": [{
            "severity": "minor",
            "category": "accessibility",
            "description": "Missing alt attribute on <img>",
            "location": "src/components/Hero.tsx:5",
            "suggestion": "Add descriptive alt text",
        }],
        "revision_instructions": "Add alt attribute to the <img> tag.",
        "failed_checks": ["accessibility"],
    })]
    artifacts["_critic_revision_count"] = ["1"]

    state = _build_state(artifacts=artifacts)
    state["current_agent"] = "dev"

    # Extract critic feedback.
    feedback = agent._extract_critic_feedback(state)
    assert feedback is not None
    assert feedback["verdict"] == "revise"

    # Build revision prompt.
    task_context = {"description": "Build landing page", "assigned_agent": "dev"}
    prompt = agent._build_revision_prompt(state, task_context, feedback)

    # Verify prompt contains critic feedback.
    assert "Critic Agent Review Feedback" in prompt
    assert "Missing alt attribute" in prompt
    assert "Add alt attribute" in prompt
    assert "revision #1" in prompt
    assert "accessibility" in prompt


async def test_dev_extract_critic_feedback_returns_none_on_fresh_run(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
) -> None:
    """Dev Agent should return None when no critic artifacts exist."""
    agent = DevAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts=_make_dev_artifacts())
    feedback = agent._extract_critic_feedback(state)
    assert feedback is None
