"""Tests for P3.13 Integration — Decision Memory wired into Planner and Worker.

Covers:
- AgentContainer.decision_memory property
- PlannerAgent queries decision memory before planning
- Similar patterns included in LLM prompt
- run_project_pipeline records outcome on completion
- run_project_pipeline records outcome on failure
- Recording failures handled gracefully
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ===========================================================================
# 1. AgentContainer.decision_memory
# ===========================================================================


class TestContainerDecisionMemory:
    """AgentContainer provides decision_memory property."""

    def test_decision_memory_property_exists(self) -> None:
        """Container should have decision_memory property."""
        from src.core.container import AgentContainer

        container = AgentContainer()
        assert hasattr(container, "decision_memory")

    def test_decision_memory_is_none_before_init(self) -> None:
        """decision_memory should be None before initialization."""
        from src.core.container import AgentContainer

        container = AgentContainer()
        assert container.decision_memory is None

    def test_decision_memory_setter(self) -> None:
        """Should accept setting decision_memory externally."""
        from src.core.container import AgentContainer

        container = AgentContainer()
        mock_dm = MagicMock()
        container.decision_memory = mock_dm
        assert container.decision_memory is mock_dm


# ===========================================================================
# 2. Planner queries decision memory
# ===========================================================================


class TestPlannerDecisionMemoryIntegration:
    """PlannerAgent._generate_plan queries decision memory."""

    async def test_planner_queries_similar_patterns(self) -> None:
        """Planner should call find_similar before generating plan."""
        from src.agents.planner import PlannerAgent

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps(
            {
                "phases": [
                    {
                        "name": "Dev",
                        "tasks": [{"id": "t1", "description": "Build", "assigned_to": "dev", "estimated_hours": 2}],
                    }
                ],
                "total_estimated_hours": 2,
            }
        )
        mock_llm.call = AsyncMock(return_value=(mock_response, MagicMock()))

        mock_dm = AsyncMock()
        mock_pattern = MagicMock()
        mock_pattern.content = "Project type: web\nOutcome: success\nTimeline: 3 days"
        mock_pattern.similarity = 0.85
        mock_pattern.title = "Past web project"
        mock_dm.find_similar = AsyncMock(return_value=[mock_pattern])

        agent = PlannerAgent(
            llm_client=mock_llm,
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
        )

        with patch("src.core.container.get_container") as mock_get:
            mock_container = MagicMock()
            mock_container.decision_memory = mock_dm
            mock_container.llm_queue = None
            mock_get.return_value = mock_container

            project = {"project_id": "test-1", "requirements": "Build a React landing page"}
            _plan = await agent._generate_plan(project)

            mock_dm.find_similar.assert_awaited_once()
            # Requirements should be passed to find_similar
            call_args = mock_dm.find_similar.call_args
            assert "React landing page" in call_args[0][0]

    async def test_similar_patterns_included_in_prompt(self) -> None:
        """Similar patterns should be added to the LLM prompt."""
        from src.agents.planner import PlannerAgent

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps(
            {
                "phases": [
                    {
                        "name": "Dev",
                        "tasks": [{"id": "t1", "description": "Build", "assigned_to": "dev", "estimated_hours": 2}],
                    }
                ],
                "total_estimated_hours": 2,
            }
        )
        mock_llm.call = AsyncMock(return_value=(mock_response, MagicMock()))

        mock_dm = AsyncMock()
        mock_pattern = MagicMock()
        mock_pattern.content = "Project type: web\nOutcome: success\nTimeline: 3 days"
        mock_pattern.similarity = 0.85
        mock_pattern.title = "Past web project"
        mock_dm.find_similar = AsyncMock(return_value=[mock_pattern])

        agent = PlannerAgent(
            llm_client=mock_llm,
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
        )

        with patch("src.core.container.get_container") as mock_get:
            mock_container = MagicMock()
            mock_container.decision_memory = mock_dm
            mock_container.llm_queue = None
            mock_get.return_value = mock_container

            project = {"project_id": "test-2", "requirements": "REST API with auth"}
            await agent._generate_plan(project)

            # Check that the LLM prompt includes pattern data
            call_args = mock_llm.call.call_args
            messages = call_args[0][1]
            user_msg_content = str(messages[-1].content)
            assert "Past web project" in user_msg_content or "success" in user_msg_content

    async def test_planner_works_without_decision_memory(self) -> None:
        """Planner should work normally when decision_memory is None."""
        from src.agents.planner import PlannerAgent

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps(
            {
                "phases": [
                    {
                        "name": "Dev",
                        "tasks": [{"id": "t1", "description": "Build", "assigned_to": "dev", "estimated_hours": 1}],
                    }
                ],
                "total_estimated_hours": 1,
            }
        )
        mock_llm.call = AsyncMock(return_value=(mock_response, MagicMock()))

        agent = PlannerAgent(
            llm_client=mock_llm,
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
        )

        with patch("src.core.container.get_container") as mock_get:
            mock_container = MagicMock()
            mock_container.decision_memory = None
            mock_container.llm_queue = None
            mock_get.return_value = mock_container

            project = {"project_id": "test-3", "requirements": "Simple task"}
            plan = await agent._generate_plan(project)

            assert plan is not None

    async def test_decision_memory_error_does_not_break_planner(self) -> None:
        """Decision memory errors should not break plan generation."""
        from src.agents.planner import PlannerAgent

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps(
            {
                "phases": [
                    {
                        "name": "Dev",
                        "tasks": [{"id": "t1", "description": "Build", "assigned_to": "dev", "estimated_hours": 1}],
                    }
                ],
                "total_estimated_hours": 1,
            }
        )
        mock_llm.call = AsyncMock(return_value=(mock_response, MagicMock()))

        mock_dm = AsyncMock()
        mock_dm.find_similar = AsyncMock(side_effect=RuntimeError("KB unavailable"))

        agent = PlannerAgent(
            llm_client=mock_llm,
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
        )

        with patch("src.core.container.get_container") as mock_get:
            mock_container = MagicMock()
            mock_container.decision_memory = mock_dm
            mock_container.llm_queue = None
            mock_get.return_value = mock_container

            project = {"project_id": "test-4", "requirements": "Build something"}
            plan = await agent._generate_plan(project)

            # Should still produce a plan despite memory error
            assert plan is not None


# ===========================================================================
# 3. Worker records outcome
# ===========================================================================


class TestWorkerRecordsOutcome:
    """run_project_pipeline records decision patterns after completion."""

    def _pipeline_patches(self):
        """Return a tuple of patch contexts for run_project_pipeline deps."""
        mock_settings = MagicMock()
        mock_settings.DATABASE_URL = "postgresql://test:test@localhost/test"

        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()

        mock_graph = AsyncMock()

        patches = {
            "settings": patch("src.worker.tasks.get_settings", return_value=mock_settings),
            "valkey": patch("src.worker.tasks.get_valkey", return_value=MagicMock()),
            "pool": patch("src.worker.tasks.asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool),
            "graph": patch("src.worker.tasks.create_graph_with_persistence", return_value=mock_graph),
        }
        return patches, mock_graph, mock_pool

    async def test_records_pattern_on_success(self) -> None:
        """Should record decision pattern when pipeline completes successfully."""
        from src.worker.tasks import run_project_pipeline

        patches, mock_graph, mock_pool = self._pipeline_patches()
        mock_graph.ainvoke = AsyncMock(
            return_value={
                "status": "completed",
                "project": {"requirements": "Build a website"},
                "agent_sequence": ["dev", "content"],
                "errors": [],
            }
        )

        mock_dm = AsyncMock()
        mock_dm.record_outcome = AsyncMock(return_value="doc-123")

        with (
            patches["settings"],
            patches["valkey"],
            patches["pool"],
            patches["graph"],
            patch("src.core.container.get_container") as mock_get,
        ):
            mock_container = MagicMock()
            mock_container.decision_memory = mock_dm
            mock_container.llm_queue = None
            mock_get.return_value = mock_container

            payload = {"project_id": "proj-rec", "requirements": "Build a website", "budget": 500}
            result = await run_project_pipeline(payload)

            assert result["status"] == "completed"
            mock_dm.record_outcome.assert_awaited_once()

    async def test_records_pattern_on_failure(self) -> None:
        """Should record decision pattern when pipeline fails."""
        from src.worker.tasks import run_project_pipeline

        patches, mock_graph, mock_pool = self._pipeline_patches()
        mock_graph.ainvoke = AsyncMock(
            return_value={
                "status": "failed",
                "project": {"requirements": "Complex ML pipeline"},
                "agent_sequence": ["dev"],
                "errors": ["LLM timeout"],
            }
        )

        mock_dm = AsyncMock()
        mock_dm.record_outcome = AsyncMock(return_value="doc-456")

        with (
            patches["settings"],
            patches["valkey"],
            patches["pool"],
            patches["graph"],
            patch("src.core.container.get_container") as mock_get,
        ):
            mock_container = MagicMock()
            mock_container.decision_memory = mock_dm
            mock_container.llm_queue = None
            mock_get.return_value = mock_container

            payload = {"project_id": "proj-fail", "requirements": "Complex ML pipeline", "budget": 2000}
            result = await run_project_pipeline(payload)

            assert result["status"] == "failed"
            mock_dm.record_outcome.assert_awaited_once()

    async def test_recording_failure_does_not_break_pipeline(self) -> None:
        """Decision memory recording errors should not affect pipeline result."""
        from src.worker.tasks import run_project_pipeline

        patches, mock_graph, mock_pool = self._pipeline_patches()
        mock_graph.ainvoke = AsyncMock(
            return_value={
                "status": "completed",
                "project": {"requirements": "Test"},
                "agent_sequence": ["dev"],
                "errors": [],
            }
        )

        mock_dm = AsyncMock()
        mock_dm.record_outcome = AsyncMock(side_effect=RuntimeError("DB down"))

        with (
            patches["settings"],
            patches["valkey"],
            patches["pool"],
            patches["graph"],
            patch("src.core.container.get_container") as mock_get,
        ):
            mock_container = MagicMock()
            mock_container.decision_memory = mock_dm
            mock_container.llm_queue = None
            mock_get.return_value = mock_container

            payload = {"project_id": "proj-err", "requirements": "Test", "budget": 100}
            result = await run_project_pipeline(payload)

            # Should still return successfully
            assert result["status"] == "completed"

    async def test_skips_recording_for_non_terminal_status(self) -> None:
        """Should not record pattern for paused/active pipelines."""
        from src.worker.tasks import run_project_pipeline

        patches, mock_graph, mock_pool = self._pipeline_patches()
        mock_graph.ainvoke = AsyncMock(
            return_value={
                "status": "paused",
                "project": {"requirements": "Test"},
                "agent_sequence": ["dev"],
                "errors": [],
            }
        )

        mock_dm = AsyncMock()
        mock_dm.record_outcome = AsyncMock()

        with (
            patches["settings"],
            patches["valkey"],
            patches["pool"],
            patches["graph"],
            patch("src.core.container.get_container") as mock_get,
        ):
            mock_container = MagicMock()
            mock_container.decision_memory = mock_dm
            mock_container.llm_queue = None
            mock_get.return_value = mock_container

            payload = {"project_id": "proj-pause", "requirements": "Test", "budget": 100}
            _result = await run_project_pipeline(payload)

            mock_dm.record_outcome.assert_not_awaited()
