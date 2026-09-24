"""Unit tests for RAG-aware Planner (L4).

Tests cover: _check_existing_artifacts skips agents when artifacts exist,
agent_sequence modification, no-skip when artifacts are empty.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.planner import PlannerAgent
from src.core.state import create_initial_state

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(**overrides: Any) -> dict[str, Any]:
    base = {
        "project_id": "test-proj-001",
        "job_id": "job-001",
        "platform": "freelancer",
        "client": {"name": "TestClient"},
        "requirements": "Build a responsive website with login",
        "budget": 1000.0,
        "deadline": "2026-04-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def _make_state(**overrides: Any) -> dict[str, Any]:
    state = create_initial_state(project=_make_project())
    state.update(overrides)
    return state


def _make_planner() -> PlannerAgent:
    return PlannerAgent(
        llm_client=MagicMock(),
        heartbeat=AsyncMock(),
        loop_detector=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# _check_existing_artifacts
# ---------------------------------------------------------------------------


class TestCheckExistingArtifacts:
    """PlannerAgent._check_existing_artifacts detects pre-existing work."""

    def test_returns_empty_set_when_no_artifacts(self):
        planner = _make_planner()
        state = _make_state(artifacts={})
        result = planner._check_existing_artifacts(state)
        assert result == set()

    def test_detects_existing_design_artifact(self):
        planner = _make_planner()
        state = _make_state(artifacts={"design": ["mockup.png"]})
        result = planner._check_existing_artifacts(state)
        assert "design" in result

    def test_detects_existing_code_artifact(self):
        planner = _make_planner()
        state = _make_state(artifacts={"dev": ["main.py"]})
        result = planner._check_existing_artifacts(state)
        assert "dev" in result

    def test_detects_existing_content_artifact(self):
        planner = _make_planner()
        state = _make_state(artifacts={"content": ["readme.md"]})
        result = planner._check_existing_artifacts(state)
        assert "content" in result

    def test_detects_multiple_existing_artifacts(self):
        planner = _make_planner()
        state = _make_state(
            artifacts={
                "dev": ["main.py"],
                "design": ["mockup.png"],
                "content": ["copy.txt"],
            }
        )
        result = planner._check_existing_artifacts(state)
        assert result == {"dev", "design", "content"}

    def test_ignores_non_execution_artifacts(self):
        """Artifacts from scout, bid, planner, critic are NOT skippable."""
        planner = _make_planner()
        state = _make_state(
            artifacts={
                "scout": ["job1"],
                "bid": ["proposal"],
                "planner": ["plan.json"],
                "critic": ["review"],
            }
        )
        result = planner._check_existing_artifacts(state)
        assert result == set()

    def test_ignores_empty_artifact_lists(self):
        planner = _make_planner()
        state = _make_state(artifacts={"dev": [], "design": []})
        result = planner._check_existing_artifacts(state)
        assert result == set()

    def test_ignores_internal_artifacts(self):
        """Artifacts prefixed with _ are internal metadata, not real work."""
        planner = _make_planner()
        state = _make_state(
            artifacts={
                "_critic_revision_count": [1],
                "_hitl_started_at": [12345],
            }
        )
        result = planner._check_existing_artifacts(state)
        assert result == set()


# ---------------------------------------------------------------------------
# Sequence modification based on existing artifacts
# ---------------------------------------------------------------------------


class TestSequenceModification:
    """Planner modifies agent_sequence to skip agents with existing work."""

    def test_skip_design_when_design_exists(self):
        planner = _make_planner()
        original_sequence = ["dev", "content", "design"]
        existing = {"design"}
        result = planner._filter_sequence_by_existing(original_sequence, existing)
        assert "design" not in result
        assert "dev" in result
        assert "content" in result

    def test_skip_dev_when_code_exists(self):
        planner = _make_planner()
        original_sequence = ["dev", "content", "design"]
        existing = {"dev"}
        result = planner._filter_sequence_by_existing(original_sequence, existing)
        assert "dev" not in result
        assert "content" in result
        assert "design" in result

    def test_skip_multiple_agents(self):
        planner = _make_planner()
        original_sequence = ["dev", "content", "design"]
        existing = {"dev", "design"}
        result = planner._filter_sequence_by_existing(original_sequence, existing)
        assert result == ["content"]

    def test_no_skip_when_empty_existing(self):
        planner = _make_planner()
        original_sequence = ["dev", "content", "design"]
        result = planner._filter_sequence_by_existing(original_sequence, set())
        assert result == ["dev", "content", "design"]

    def test_preserves_order(self):
        planner = _make_planner()
        original_sequence = ["dev", "content", "design"]
        existing = {"content"}
        result = planner._filter_sequence_by_existing(original_sequence, existing)
        assert result == ["dev", "design"]

    def test_all_skipped_returns_empty(self):
        """If all agents are already done, return empty (goes to packager)."""
        planner = _make_planner()
        original_sequence = ["dev", "content", "design"]
        existing = {"dev", "content", "design"}
        result = planner._filter_sequence_by_existing(original_sequence, existing)
        assert result == []


# ---------------------------------------------------------------------------
# Integration: _build_agent_sequence + existing artifacts
# ---------------------------------------------------------------------------


class TestBuildSequenceWithExistingArtifacts:
    """_build_agent_sequence produces the right sequence from plan tasks."""

    def test_full_sequence_from_plan(self):
        plan = {
            "phases": [
                {
                    "tasks": [
                        {"assigned_to": "dev"},
                        {"assigned_to": "content"},
                        {"assigned_to": "design"},
                    ]
                },
            ],
        }
        result = PlannerAgent._build_agent_sequence(plan)
        assert result == ["dev", "content", "design"]

    def test_partial_sequence_from_plan(self):
        plan = {
            "phases": [
                {"tasks": [{"assigned_to": "dev"}]},
            ],
        }
        result = PlannerAgent._build_agent_sequence(plan)
        assert result == ["dev"]

    def test_empty_plan_returns_default(self):
        plan = {"phases": []}
        result = PlannerAgent._build_agent_sequence(plan)
        assert result == ["dev", "content", "design"]
