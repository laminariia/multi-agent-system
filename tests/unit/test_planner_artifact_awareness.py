"""Tests for P1.8 — Planner Artifact Awareness.

When Planner is re-invoked (after Critic major revision), it should
see existing execution artifacts and Critic feedback so it doesn't
redo completed work.

Covers:
- _build_artifact_context() produces correct summary
- _generate_plan() receives artifact context on re-plan
- First plan (no artifacts) has no artifact context
- Critic feedback included when available
- Skipped agents info included
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.core.state import create_initial_state, update_state

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_ctx(**overrides: Any) -> dict[str, Any]:
    base = {
        "project_id": "proj-replan-001",
        "job_id": "job-replan-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a REST API with auth and admin dashboard",
        "budget": 2000.0,
        "deadline": datetime(2026, 5, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return base


def _make_state(**overrides: Any) -> dict[str, Any]:
    state = create_initial_state(project=_project_ctx())
    if overrides:
        state = update_state(state, **overrides)
    return state


# ===========================================================================
# 1. Artifact Context Builder
# ===========================================================================


class TestBuildArtifactContext:
    """_build_artifact_context produces correct summary."""

    def test_no_execution_artifacts_returns_none(self):
        """First plan — no execution artifacts → returns None."""
        from src.agents.planner import PlannerAgent

        state = _make_state(artifacts={})
        result = PlannerAgent._build_artifact_context(state)
        assert result is None

    def test_no_execution_artifacts_with_planner_only(self):
        """Only planner artifacts → no execution context."""
        from src.agents.planner import PlannerAgent

        state = _make_state(artifacts={"planner": ['{"phases": []}']})
        result = PlannerAgent._build_artifact_context(state)
        assert result is None

    def test_dev_artifacts_included(self):
        """Dev artifacts produce context."""
        from src.agents.planner import PlannerAgent

        state = _make_state(artifacts={"dev": ["api.py code", "models.py code"]})
        result = PlannerAgent._build_artifact_context(state)
        assert result is not None
        assert "dev" in result.lower()
        assert "2" in result  # 2 artifacts

    def test_multiple_execution_agents(self):
        """Multiple execution agent artifacts listed."""
        from src.agents.planner import PlannerAgent

        state = _make_state(
            artifacts={
                "dev": ["backend code"],
                "content": ["homepage copy"],
                "design": ["logo.svg"],
            }
        )
        result = PlannerAgent._build_artifact_context(state)
        assert result is not None
        assert "dev" in result.lower()
        assert "content" in result.lower()
        assert "design" in result.lower()

    def test_critic_feedback_included(self):
        """Critic artifacts (feedback) included in context."""
        from src.agents.planner import PlannerAgent

        state = _make_state(
            artifacts={
                "dev": ["code"],
                "critic": ['{"score": 0.4, "issues": ["SQL injection risk"]}'],
            }
        )
        result = PlannerAgent._build_artifact_context(state)
        assert result is not None
        assert "critic" in result.lower()

    def test_critic_revision_type_included(self):
        """_critic_revision_type artifact included in context."""
        from src.agents.planner import PlannerAgent

        state = _make_state(
            artifacts={
                "dev": ["code"],
                "_critic_revision_type": ["major"],
            }
        )
        result = PlannerAgent._build_artifact_context(state)
        assert result is not None
        assert "major" in result.lower()

    def test_skipped_agents_mentioned(self):
        """Skipped agents from partial failure recovery included."""
        from src.agents.planner import PlannerAgent

        state = _make_state(
            artifacts={"dev": ["code"]},
            skipped_agents=["design"],
        )
        result = PlannerAgent._build_artifact_context(state)
        assert result is not None
        assert "design" in result.lower()
        assert "skip" in result.lower()


# ===========================================================================
# 2. Plan Generation with Artifact Context
# ===========================================================================


class TestPlannerReplanWithArtifacts:
    """Planner includes artifact context in LLM prompt during re-plan."""

    @pytest.fixture()
    def planner_deps(self, mock_llm_client, mock_heartbeat, mock_loop_detector):
        return {
            "llm": mock_llm_client,
            "hb": mock_heartbeat,
            "ld": mock_loop_detector,
        }

    async def test_replan_passes_artifact_context(self, planner_deps, sample_state):
        """On re-plan, _generate_plan receives artifact_context."""
        from src.agents.planner import PlannerAgent

        plan = {
            "phases": [
                {
                    "name": "Fix issues",
                    "tasks": [{"name": "Fix SQL", "agent": "dev", "estimated_hours": 2.0}],
                }
            ],
            "total_estimated_hours": 2.0,
            "summary": "Fix critical issues from review",
        }

        agent = PlannerAgent(
            llm_client=planner_deps["llm"],
            heartbeat=planner_deps["hb"],
            loop_detector=planner_deps["ld"],
        )

        # Simulate re-plan state: existing dev artifacts + critic feedback
        state = {
            **sample_state,
            "current_agent": "planner",
            "artifacts": {
                "dev": ["backend api code"],
                "critic": ['{"score": 0.5, "issues": ["security flaw"]}'],
            },
        }

        with (
            patch.object(agent, "_generate_plan", new_callable=AsyncMock, return_value=plan) as mock_gen,
            patch.object(agent, "_log_planning_action", new_callable=AsyncMock),
        ):
            await agent._execute(state)

        # Verify _generate_plan was called with artifact_context
        mock_gen.assert_called_once()
        call_args = mock_gen.call_args
        # Check that artifact_context kwarg was passed
        assert call_args.kwargs.get("artifact_context") is not None
        ctx = call_args.kwargs["artifact_context"]
        assert "dev" in ctx.lower()

    async def test_first_plan_no_artifact_context(self, planner_deps, sample_state):
        """First plan (no execution artifacts) — no artifact_context."""
        from src.agents.planner import PlannerAgent

        plan = {
            "phases": [
                {
                    "name": "Development",
                    "tasks": [{"name": "Build API", "agent": "dev", "estimated_hours": 5.0}],
                }
            ],
            "total_estimated_hours": 5.0,
            "summary": "Full project plan",
        }

        agent = PlannerAgent(
            llm_client=planner_deps["llm"],
            heartbeat=planner_deps["hb"],
            loop_detector=planner_deps["ld"],
        )

        state = {**sample_state, "current_agent": "planner", "artifacts": {}}

        with (
            patch.object(agent, "_generate_plan", new_callable=AsyncMock, return_value=plan) as mock_gen,
            patch.object(agent, "_log_planning_action", new_callable=AsyncMock),
        ):
            await agent._execute(state)

        mock_gen.assert_called_once()
        call_args = mock_gen.call_args
        # No artifact_context on first plan
        assert call_args.kwargs.get("artifact_context") is None

    async def test_replan_only_planner_artifacts_no_context(self, planner_deps, sample_state):
        """Re-plan count > 0 but no execution artifacts → no artifact_context."""
        from src.agents.planner import PlannerAgent

        plan = {
            "phases": [
                {
                    "name": "Development",
                    "tasks": [{"name": "Build", "agent": "dev", "estimated_hours": 3.0}],
                }
            ],
            "total_estimated_hours": 3.0,
            "summary": "Second attempt",
        }

        agent = PlannerAgent(
            llm_client=planner_deps["llm"],
            heartbeat=planner_deps["hb"],
            loop_detector=planner_deps["ld"],
        )

        state = {
            **sample_state,
            "current_agent": "planner",
            "artifacts": {"planner": ['{"phases": []}']},
        }

        with (
            patch.object(agent, "_generate_plan", new_callable=AsyncMock, return_value=plan) as mock_gen,
            patch.object(agent, "_log_planning_action", new_callable=AsyncMock),
        ):
            await agent._execute(state)

        mock_gen.assert_called_once()
        call_args = mock_gen.call_args
        assert call_args.kwargs.get("artifact_context") is None
