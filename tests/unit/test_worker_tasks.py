"""Unit tests for src/worker/tasks.py - Background task handlers."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.worker.tasks import (
    TASK_REGISTRY,
    dispatch_task,
    run_bid_generation,
    run_project_pipeline,
    run_scout_cycle,
)


class TestRunScoutCycle:
    """Test run_scout_cycle task."""

    @pytest.mark.asyncio
    async def test_calls_scout_node_with_correct_initial_state(self) -> None:
        """Should call scout_node with properly initialized state."""
        with patch("src.agents.scout.scout_node", new_callable=AsyncMock) as mock_scout:
            mock_scout.return_value = {"artifacts": {"scout": []}}

            await run_scout_cycle({"platform": "freelancer"})

            mock_scout.assert_awaited_once()
            call_args = mock_scout.call_args[0][0]
            assert call_args["project"]["platform"] == "freelancer"
            assert call_args["current_agent"] == "scout"

    @pytest.mark.asyncio
    async def test_returns_summary_with_jobs_found_count(self) -> None:
        """Should return summary dict with job count."""
        with patch("src.agents.scout.scout_node", new_callable=AsyncMock) as mock_scout:
            mock_scout.return_value = {
                "artifacts": {"scout": ["job-1", "job-2", "job-3"]},
            }

            result = await run_scout_cycle({})

            assert result["task"] == "scout_cycle"
            assert result["jobs_found"] == 3

    @pytest.mark.asyncio
    async def test_default_platform_is_all(self) -> None:
        """Should default to 'all' platform when not specified."""
        with patch("src.agents.scout.scout_node", new_callable=AsyncMock) as mock_scout:
            mock_scout.return_value = {"artifacts": {}}

            await run_scout_cycle(None)

            call_args = mock_scout.call_args[0][0]
            assert call_args["project"]["platform"] == "all"

    @pytest.mark.asyncio
    async def test_custom_platform_from_payload(self) -> None:
        """Should use platform from payload."""
        with patch("src.agents.scout.scout_node", new_callable=AsyncMock) as mock_scout:
            mock_scout.return_value = {"artifacts": {}}

            await run_scout_cycle({"platform": "upwork"})

            call_args = mock_scout.call_args[0][0]
            assert call_args["project"]["platform"] == "upwork"

    @pytest.mark.asyncio
    async def test_handles_empty_artifacts(self) -> None:
        """Should handle missing scout artifacts gracefully."""
        with patch("src.agents.scout.scout_node", new_callable=AsyncMock) as mock_scout:
            mock_scout.return_value = {}

            result = await run_scout_cycle({})

            assert result["jobs_found"] == 0
            assert result["job_ids"] == []


class TestRunProjectPipeline:
    """Test run_project_pipeline task."""

    @pytest.mark.asyncio
    async def test_creates_project_context_from_payload(self) -> None:
        """Should create ProjectContext with payload data."""
        # Create a mock object that supports both dict and attribute access
        from unittest.mock import MagicMock
        mock_project = MagicMock()
        mock_project.__getitem__ = lambda self, key: {
            "project_id": "proj-123",
            "job_id": "job-456",
            "platform": "freelancer",
            "client": {},
            "requirements": "Build a website",
            "budget": 1000,
            "deadline": None,
        }[key]
        mock_project.project_id = "proj-123"

        with (
            patch("src.worker.tasks.ProjectContext", return_value=mock_project),
            patch("src.core.graph.build_full_pipeline_graph") as mock_build,
        ):
            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value={"status": "completed"})
            mock_build.return_value = mock_graph

            payload = {
                "project_id": "proj-123",
                "job_id": "job-456",
                "platform": "freelancer",
                "requirements": "Build a website",
                "budget": 1000,
            }

            result = await run_project_pipeline(payload)

            assert result["task"] == "project_pipeline"
            assert result["project_id"] == "proj-123"

    @pytest.mark.asyncio
    async def test_calls_build_full_pipeline_graph_and_ainvoke(self) -> None:
        """Should build graph and invoke it."""
        from unittest.mock import MagicMock
        mock_project = MagicMock()
        mock_project.project_id = "proj-123"

        with (
            patch("src.worker.tasks.ProjectContext", return_value=mock_project),
            patch("src.core.graph.build_full_pipeline_graph") as mock_build,
        ):
            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value={"status": "in_progress"})
            mock_build.return_value = mock_graph

            payload = {
                "project_id": "proj-123",
                "requirements": "Test task",
                "budget": 500,
            }

            await run_project_pipeline(payload)

            mock_build.assert_called_once()
            mock_graph.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_project_id_and_final_status(self) -> None:
        """Should return project ID and final status."""
        mock_project = {
            "project_id": "proj-abc",
            "job_id": "",
            "platform": "freelancer",
            "client": {},
            "requirements": "Test",
            "budget": 200,
            "deadline": None,
        }

        with (
            patch("src.worker.tasks.ProjectContext", return_value=mock_project),
            patch("src.core.graph.build_full_pipeline_graph") as mock_build,
        ):
            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value={"status": "delivered"})
            mock_build.return_value = mock_graph

            payload = {
                "project_id": "proj-abc",
                "requirements": "Test",
                "budget": 200,
            }

            result = await run_project_pipeline(payload)

            assert result["task"] == "project_pipeline"
            assert result["project_id"] == "proj-abc"
            assert result["status"] == "delivered"


class TestRunBidGeneration:
    """Test run_bid_generation task."""

    @pytest.mark.asyncio
    async def test_calls_bid_node_with_job_ids_in_artifacts(self) -> None:
        """Should call bid_node with job_ids in scout artifacts."""
        with patch("src.agents.bid.bid_node", new_callable=AsyncMock) as mock_bid:
            mock_bid.return_value = {"artifacts": {"bid": []}}

            await run_bid_generation({"job_ids": ["job-1", "job-2"]})

            mock_bid.assert_awaited_once()
            call_args = mock_bid.call_args[0][0]
            assert call_args["artifacts"]["scout"] == ["job-1", "job-2"]

    @pytest.mark.asyncio
    async def test_returns_bids_created_count(self) -> None:
        """Should return count of bids created."""
        with patch("src.agents.bid.bid_node", new_callable=AsyncMock) as mock_bid:
            mock_bid.return_value = {
                "artifacts": {"bid": [{"id": "b1"}, {"id": "b2"}, {"id": "b3"}]},
            }

            result = await run_bid_generation({"job_ids": ["j1", "j2", "j3"]})

            assert result["task"] == "bid_generation"
            assert result["bids_created"] == 3

    @pytest.mark.asyncio
    async def test_empty_job_ids_payload(self) -> None:
        """Should handle empty job_ids gracefully."""
        with patch("src.agents.bid.bid_node", new_callable=AsyncMock) as mock_bid:
            mock_bid.return_value = {"artifacts": {}}

            result = await run_bid_generation({})

            assert result["bids_created"] == 0


class TestDispatchTask:
    """Test dispatch_task function."""

    @pytest.mark.asyncio
    async def test_dispatches_scout_cycle_correctly(self) -> None:
        """Should dispatch scout_cycle to correct handler."""
        with (
            patch("src.agents.scout.scout_node", new_callable=AsyncMock) as mock_scout,
        ):
            mock_scout.return_value = {"artifacts": {"scout": ["job-1", "job-2"]}}

            result = await dispatch_task("scout_cycle", {"platform": "all"})

            assert result["task"] == "scout_cycle"
            assert result["jobs_found"] == 2

    @pytest.mark.asyncio
    async def test_dispatches_project_pipeline_correctly(self) -> None:
        """Should dispatch project_pipeline to correct handler."""
        from unittest.mock import MagicMock
        mock_project = MagicMock()
        mock_project.project_id = "p1"

        with (
            patch("src.worker.tasks.ProjectContext", return_value=mock_project),
            patch("src.core.graph.build_full_pipeline_graph") as mock_build,
        ):
            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value={"status": "completed"})
            mock_build.return_value = mock_graph

            payload = {"project_id": "p1", "requirements": "Test", "budget": 100}
            result = await dispatch_task("project_pipeline", payload)

            assert result["task"] == "project_pipeline"
            assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_dispatches_bid_generation_correctly(self) -> None:
        """Should dispatch bid_generation to correct handler."""
        with (
            patch("src.agents.bid.bid_node", new_callable=AsyncMock) as mock_bid,
        ):
            mock_bid.return_value = {"artifacts": {"bid": [{"id": "b1"}, {"id": "b2"}]}}

            result = await dispatch_task("bid_generation", {"job_ids": ["j1"]})

            assert result["task"] == "bid_generation"
            assert result["bids_created"] == 2

    @pytest.mark.asyncio
    async def test_raises_value_error_for_unknown_task_type(self) -> None:
        """Should raise ValueError for unregistered task type."""
        with pytest.raises(ValueError, match="Unknown task type: invalid_task"):
            await dispatch_task("invalid_task", {})

    @pytest.mark.asyncio
    async def test_empty_payload_defaults_to_empty_dict(self) -> None:
        """None payload should default to empty dict."""
        with (
            patch("src.agents.scout.scout_node", new_callable=AsyncMock) as mock_scout,
        ):
            mock_scout.return_value = {"artifacts": {}}

            result = await dispatch_task("scout_cycle", None)

            assert result["jobs_found"] == 0


class TestTaskRegistry:
    """Test TASK_REGISTRY constant."""

    def test_contains_all_registered_tasks(self) -> None:
        """Registry should contain all 4 task handlers."""
        assert len(TASK_REGISTRY) == 4
        assert "scout_cycle" in TASK_REGISTRY
        assert "project_pipeline" in TASK_REGISTRY
        assert "bid_generation" in TASK_REGISTRY
        assert "pipeline_b_scan" in TASK_REGISTRY

    def test_keys_match_expected_task_type_names(self) -> None:
        """Registry keys should match expected task types."""
        expected_keys = {"scout_cycle", "project_pipeline", "bid_generation", "pipeline_b_scan"}
        assert set(TASK_REGISTRY.keys()) == expected_keys

    def test_handlers_are_callable(self) -> None:
        """All registered handlers should be callable."""
        for handler in TASK_REGISTRY.values():
            assert callable(handler)
