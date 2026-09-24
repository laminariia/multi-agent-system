"""Unit tests for medium-priority fixes W2: M8 + M9.

M8: Critic revision limit escalation — when revision_count > MAX_REVISIONS,
    create HITL entry with type="revision_escalation" instead of generic "code_review".

M9: Circuit breaker success-based reset — in HALF_OPEN state, require N
    consecutive successes (default 2) before transitioning back to CLOSED.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.adapters.circuit_breaker import CircuitBreaker, CircuitState
from src.agents.critic import _MAX_REVISION_CYCLES, CriticAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_critic_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults for the Critic Agent."""
    project = {
        "project_id": "proj-m8-001",
        "job_id": "job-m8-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a landing page with React and Tailwind CSS",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    state = create_initial_state(project=project, first_agent="critic", thread_id="thread-m8-test")
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _make_dev_artifacts() -> dict[str, list[str]]:
    """Build a minimal set of dev artifacts for the critic to review."""
    code_data = json.dumps(
        {
            "files": [
                {
                    "path": "src/components/Hero.tsx",
                    "content": "export default function Hero() { return <div>Hero</div>; }",
                    "language": "typescript",
                }
            ],
            "dependencies": ["react", "tailwindcss"],
            "build_commands": ["npm install", "npm run build"],
        }
    )
    return {"dev": ["artifact-id-001", code_data]}


def _make_review_response(
    verdict: str = "approve",
    score: float = 0.92,
    issues: list[dict[str, Any]] | None = None,
    revision_type: str = "none",
) -> str:
    """Build a critic review JSON response string."""
    return json.dumps(
        {
            "verdict": verdict,
            "score": score,
            "revision_type": revision_type,
            "issues": issues or [],
            "passed_checks": ["compiles", "tests_pass"],
            "failed_checks": [],
            "revision_instructions": "" if verdict == "approve" else "Fix the issues.",
        }
    )


# ===========================================================================
# M8: Critic Revision Limit Escalation
# ===========================================================================


class TestM8CriticRevisionEscalation:
    """When revision_count >= MAX_REVISIONS, the Critic must create a HITL
    entry with type='revision_escalation' (not 'code_review')."""

    async def test_escalation_creates_hitl_with_revision_escalation_type(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """HITL entry type must be 'revision_escalation' when max revisions exceeded."""
        review_response = _make_review_response(verdict="revise", score=0.72, revision_type="minor")
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=review_response),
                CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
            )
        )

        agent = CriticAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        # Simulate revision_count already at MAX (3)
        artifacts = _make_dev_artifacts()
        artifacts["_critic_revision_count"] = [str(_MAX_REVISION_CYCLES)]

        state = _build_critic_state(artifacts=artifacts)

        with (
            patch.object(agent, "_log_review_decision", new_callable=AsyncMock),
            patch.object(agent, "_create_hitl_escalation", new_callable=AsyncMock) as mock_escalation,
        ):
            result = await agent._execute(state)

        # Must pause for HITL
        assert result["requires_hitl"] is True
        assert result["status"] == "paused"
        assert result["hitl_request_id"] is not None

        # Escalation must have been called
        mock_escalation.assert_called_once()
        call_kwargs = mock_escalation.call_args[1]
        assert "revision_limit_exceeded" in call_kwargs["reason"]

    async def test_escalation_hitl_entry_type_is_revision_escalation(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """The actual DB HITL entry must use type='revision_escalation'."""
        review_response = _make_review_response(verdict="revise", score=0.72, revision_type="minor")
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=review_response),
                CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
            )
        )

        agent = CriticAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        artifacts = _make_dev_artifacts()
        artifacts["_critic_revision_count"] = [str(_MAX_REVISION_CYCLES)]
        state = _build_critic_state(artifacts=artifacts)

        captured_hitl = {}

        async def _capture_hitl_creation(**kwargs):
            captured_hitl.update(kwargs)

        with (
            patch.object(agent, "_log_review_decision", new_callable=AsyncMock),
            patch.object(agent, "_create_hitl_escalation", side_effect=_capture_hitl_creation),
        ):
            result = await agent._execute(state)

        # Verify the escalation was called with revision_escalation type
        assert "reason" in captured_hitl
        assert "revision_limit_exceeded" in captured_hitl["reason"]

    async def test_escalation_at_exactly_max_revisions(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """At revision_count == MAX_REVISIONS, must escalate (boundary test)."""
        review_response = _make_review_response(verdict="approve", score=0.92)
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=review_response),
                CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
            )
        )

        agent = CriticAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        artifacts = _make_dev_artifacts()
        artifacts["_critic_revision_count"] = [str(_MAX_REVISION_CYCLES)]
        state = _build_critic_state(artifacts=artifacts)

        with (
            patch.object(agent, "_log_review_decision", new_callable=AsyncMock),
            patch.object(agent, "_create_hitl_escalation", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        # Even with approve verdict, max revisions forces HITL escalation
        assert result["requires_hitl"] is True
        assert result["status"] == "paused"

    async def test_escalation_above_max_revisions(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """At revision_count > MAX_REVISIONS, must still escalate."""
        review_response = _make_review_response(verdict="revise", score=0.72, revision_type="minor")
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=review_response),
                CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
            )
        )

        agent = CriticAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        artifacts = _make_dev_artifacts()
        artifacts["_critic_revision_count"] = [str(_MAX_REVISION_CYCLES + 2)]
        state = _build_critic_state(artifacts=artifacts)

        with (
            patch.object(agent, "_log_review_decision", new_callable=AsyncMock),
            patch.object(agent, "_create_hitl_escalation", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        assert result["requires_hitl"] is True
        assert result["status"] == "paused"

    async def test_no_escalation_below_max_revisions(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Below MAX_REVISIONS, revision should proceed normally (no HITL)."""
        review_response = _make_review_response(
            verdict="revise",
            score=0.72,
            revision_type="minor",
            issues=[{"severity": "major", "category": "code", "description": "Bug"}],
        )
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=review_response),
                CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
            )
        )

        agent = CriticAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        artifacts = _make_dev_artifacts()
        artifacts["_critic_revision_count"] = [str(_MAX_REVISION_CYCLES - 1)]
        state = _build_critic_state(artifacts=artifacts)

        with patch.object(agent, "_log_review_decision", new_callable=AsyncMock):
            result = await agent._execute(state)

        assert result["requires_hitl"] is False
        assert result["status"] == "active"
        assert result["next_agent"] == "dev"

    async def test_escalation_hitl_db_entry_uses_correct_type(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Verify the _create_hitl_escalation method writes type='revision_escalation' to DB."""
        agent = CriticAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        with patch("src.agents.critic.get_db_session") as mock_get_db:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_db.return_value = mock_ctx

            await agent._create_hitl_escalation(
                thread_id="thread-test",
                hitl_id="hitl-test-id",
                reason="revision_limit_exceeded (3 cycles)",
                score=0.72,
                revision_count=3,
                project={"title": "Test Project"},
            )

        # Verify the HITLQueue entry was added to the session
        mock_session.add.assert_called_once()
        hitl_entry = mock_session.add.call_args[0][0]
        assert hitl_entry.type == "revision_escalation"

    async def test_escalation_log_verdict_is_escalate_hitl(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """The log entry verdict should be 'escalate_hitl' for revision limit escalation."""
        review_response = _make_review_response(verdict="revise", score=0.72, revision_type="minor")
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=review_response),
                CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
            )
        )

        agent = CriticAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        artifacts = _make_dev_artifacts()
        artifacts["_critic_revision_count"] = [str(_MAX_REVISION_CYCLES)]
        state = _build_critic_state(artifacts=artifacts)

        with (
            patch.object(agent, "_log_review_decision", new_callable=AsyncMock) as mock_log,
            patch.object(agent, "_create_hitl_escalation", new_callable=AsyncMock),
        ):
            await agent._execute(state)

        mock_log.assert_called_once()
        log_kwargs = mock_log.call_args[1]
        assert log_kwargs["verdict"] == "escalate_hitl"


# ===========================================================================
# M9: Circuit Breaker Success-Based Reset
# ===========================================================================


class TestM9CircuitBreakerSuccessReset:
    """In HALF_OPEN state, require N consecutive successes (default 2)
    before transitioning back to CLOSED."""

    async def test_single_success_stays_half_open(self):
        """One success in HALF_OPEN should NOT close the circuit (need N=2)."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.05, success_threshold=2)
        cb.record_failure()
        await asyncio.sleep(0.1)
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN

    async def test_n_successes_closes_circuit(self):
        """N consecutive successes in HALF_OPEN should close the circuit."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.05, success_threshold=2)
        cb.record_failure()
        await asyncio.sleep(0.1)
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    async def test_default_success_threshold_is_two(self):
        """Default success_threshold should be 2."""
        cb = CircuitBreaker("test")
        assert cb._success_threshold == 2

    async def test_custom_success_threshold(self):
        """Custom success_threshold should be respected."""
        cb = CircuitBreaker("test", success_threshold=5)
        assert cb._success_threshold == 5

    async def test_failure_resets_success_count_in_half_open(self):
        """A failure in HALF_OPEN should reset the success count and reopen."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.05, success_threshold=3)
        cb.record_failure()
        await asyncio.sleep(0.1)
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()  # 1/3
        cb.record_failure()  # reopen
        assert cb.state == CircuitState.OPEN

    async def test_success_in_closed_state_still_works(self):
        """record_success in CLOSED state should keep circuit CLOSED (no regression)."""
        cb = CircuitBreaker("test", success_threshold=2)
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    async def test_three_successes_with_threshold_three(self):
        """Exactly N successes with threshold=3 should close."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.05, success_threshold=3)
        cb.record_failure()
        await asyncio.sleep(0.1)
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    async def test_success_count_tracked_in_stats(self):
        """Stats should include half_open_successes count."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.05, success_threshold=3)
        cb.record_failure()
        await asyncio.sleep(0.1)

        cb.record_success()
        stats = cb.stats
        assert "half_open_successes" in stats
        assert stats["half_open_successes"] == 1

    async def test_half_open_allows_multiple_probes_with_higher_max(self):
        """With half_open_max >= success_threshold, multiple probes can succeed."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.05, half_open_max=3, success_threshold=2)
        cb.record_failure()
        await asyncio.sleep(0.1)
        assert cb.state == CircuitState.HALF_OPEN

        # Multiple probes allowed
        assert cb.can_execute() is True

    async def test_context_manager_respects_success_threshold(self):
        """Context manager success in HALF_OPEN should track towards threshold."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.05, success_threshold=2)
        cb.record_failure()
        await asyncio.sleep(0.1)
        assert cb.state == CircuitState.HALF_OPEN

        # First success via context manager
        async with cb:
            pass
        assert cb.state == CircuitState.HALF_OPEN

        # Second success closes
        async with cb:
            pass
        assert cb.state == CircuitState.CLOSED

    async def test_success_threshold_one_behaves_like_original(self):
        """With success_threshold=1, a single success closes (backward compat)."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.05, success_threshold=1)
        cb.record_failure()
        await asyncio.sleep(0.1)
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    async def test_closed_state_resets_half_open_success_count(self):
        """When circuit closes, half_open success count should reset."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.05, success_threshold=2)
        cb.record_failure()
        await asyncio.sleep(0.1)

        cb.record_success()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb._half_open_successes == 0
