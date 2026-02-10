"""Unit tests for HITL action map additions and Pipeline B worker task.

Tests the three new HITL types (email_approval, final_review, job_review)
added to ``_NEXT_ACTION_MAP`` and the ``run_pipeline_b_scan`` worker task.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.api.routes.hitl import _next_action
from src.worker.tasks import (
    TASK_REGISTRY,
    dispatch_task,
    run_pipeline_b_scan,
)

# ---------------------------------------------------------------------------
# _next_action — email_approval
# ---------------------------------------------------------------------------


class TestNextActionEmailApproval:
    """Test _next_action for the email_approval HITL type."""

    def test_approve(self) -> None:
        assert _next_action("email_approval", "approve") == "emails_will_be_sent"

    def test_reject(self) -> None:
        assert _next_action("email_approval", "reject") == "emails_discarded"

    def test_edit(self) -> None:
        assert _next_action("email_approval", "edit") == "emails_revised_and_sent"

    def test_skip(self) -> None:
        assert _next_action("email_approval", "skip") == "emails_skipped"

    def test_later(self) -> None:
        assert _next_action("email_approval", "later") == "emails_deferred"


# ---------------------------------------------------------------------------
# _next_action — final_review
# ---------------------------------------------------------------------------


class TestNextActionFinalReview:
    """Test _next_action for the final_review HITL type."""

    def test_approve(self) -> None:
        assert _next_action("final_review", "approve") == "work_delivered_to_client"

    def test_reject(self) -> None:
        assert _next_action("final_review", "reject") == "work_rejected_for_rework"

    def test_edit(self) -> None:
        assert _next_action("final_review", "edit") == "work_revised_and_delivered"

    def test_skip(self) -> None:
        assert _next_action("final_review", "skip") == "delivery_skipped"

    def test_later(self) -> None:
        assert _next_action("final_review", "later") == "delivery_deferred"


# ---------------------------------------------------------------------------
# _next_action — job_review
# ---------------------------------------------------------------------------


class TestNextActionJobReview:
    """Test _next_action for the job_review HITL type."""

    def test_approve(self) -> None:
        assert _next_action("job_review", "approve") == "job_accepted_for_bidding"

    def test_reject(self) -> None:
        assert _next_action("job_review", "reject") == "job_rejected"

    def test_edit(self) -> None:
        assert _next_action("job_review", "edit") == "job_criteria_modified"

    def test_skip(self) -> None:
        assert _next_action("job_review", "skip") == "job_skipped"

    def test_later(self) -> None:
        assert _next_action("job_review", "later") == "job_review_deferred"


# ---------------------------------------------------------------------------
# _next_action — unknown type fallback
# ---------------------------------------------------------------------------


class TestNextActionUnknownFallback:
    """Test _next_action fallback for unknown HITL types."""

    def test_unknown_type_returns_generic(self) -> None:
        assert _next_action("nonexistent_type", "approve") == "approve_processed"

    def test_unknown_resolution_returns_generic(self) -> None:
        assert _next_action("email_approval", "custom_action") == "custom_action_processed"


# ---------------------------------------------------------------------------
# run_pipeline_b_scan
# ---------------------------------------------------------------------------


class TestRunPipelineBScan:
    """Test the run_pipeline_b_scan worker task."""

    @pytest.mark.asyncio
    async def test_valid_city_invokes_graph(self) -> None:
        """Should build Pipeline B graph and invoke with city in artifacts."""
        with patch("src.core.graph.build_pipeline_b_graph") as mock_build:
            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value={"status": "completed"})
            mock_build.return_value = mock_graph

            await run_pipeline_b_scan({"city": "Berlin"})

            mock_build.assert_called_once()
            mock_graph.ainvoke.assert_awaited_once()
            # Verify artifacts contain the city
            call_state = mock_graph.ainvoke.call_args[0][0]
            assert call_state["artifacts"]["_scan_city"] == "Berlin"

    @pytest.mark.asyncio
    async def test_returns_task_city_and_status(self) -> None:
        """Should return result dict with task type, city, and status."""
        with patch("src.core.graph.build_pipeline_b_graph") as mock_build:
            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value={"status": "active"})
            mock_build.return_value = mock_graph

            result = await run_pipeline_b_scan({"city": "Munich"})

            assert result["task"] == "pipeline_b_scan"
            assert result["city"] == "Munich"
            assert result["status"] == "active"

    @pytest.mark.asyncio
    async def test_missing_city_raises_value_error(self) -> None:
        """Should raise ValueError when city is missing from payload."""
        with pytest.raises(ValueError, match="pipeline_b_scan requires 'city'"):
            await run_pipeline_b_scan({})

    @pytest.mark.asyncio
    async def test_empty_city_raises_value_error(self) -> None:
        """Should raise ValueError when city is an empty string."""
        with pytest.raises(ValueError, match="pipeline_b_scan requires 'city'"):
            await run_pipeline_b_scan({"city": ""})

    @pytest.mark.asyncio
    async def test_custom_thread_id_passed_to_state(self) -> None:
        """Should pass thread_id from payload to initial state."""
        with patch("src.core.graph.build_pipeline_b_graph") as mock_build:
            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value={"status": "completed"})
            mock_build.return_value = mock_graph

            await run_pipeline_b_scan({"city": "Berlin", "thread_id": "custom-123"})

            call_state = mock_graph.ainvoke.call_args[0][0]
            assert call_state["thread_id"] == "custom-123"

    @pytest.mark.asyncio
    async def test_project_context_uses_city_in_requirements(self) -> None:
        """Should set city as project requirements."""
        with patch("src.core.graph.build_pipeline_b_graph") as mock_build:
            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value={"status": "completed"})
            mock_build.return_value = mock_graph

            await run_pipeline_b_scan({"city": "Hamburg"})

            call_state = mock_graph.ainvoke.call_args[0][0]
            assert call_state["project"]["requirements"] == "Hamburg"
            assert call_state["project"]["platform"] == "outreach"
            assert call_state["current_agent"] == "geoscout"

    @pytest.mark.asyncio
    async def test_defaults_status_to_unknown_when_missing(self) -> None:
        """Should default status to 'unknown' when graph returns no status."""
        with patch("src.core.graph.build_pipeline_b_graph") as mock_build:
            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value={})
            mock_build.return_value = mock_graph

            result = await run_pipeline_b_scan({"city": "Berlin"})

            assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# TASK_REGISTRY and dispatch_task
# ---------------------------------------------------------------------------


class TestPipelineBScanRegistry:
    """Test that pipeline_b_scan is in TASK_REGISTRY and dispatchable."""

    def test_pipeline_b_scan_in_registry(self) -> None:
        """TASK_REGISTRY should contain pipeline_b_scan."""
        assert "pipeline_b_scan" in TASK_REGISTRY

    def test_registry_handler_is_callable(self) -> None:
        """The registered handler should be callable."""
        assert callable(TASK_REGISTRY["pipeline_b_scan"])

    def test_registry_handler_is_run_pipeline_b_scan(self) -> None:
        """The registered handler should be the run_pipeline_b_scan function."""
        assert TASK_REGISTRY["pipeline_b_scan"] is run_pipeline_b_scan

    @pytest.mark.asyncio
    async def test_dispatch_task_pipeline_b_scan(self) -> None:
        """dispatch_task should correctly route pipeline_b_scan."""
        with patch("src.core.graph.build_pipeline_b_graph") as mock_build:
            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value={"status": "completed"})
            mock_build.return_value = mock_graph

            result = await dispatch_task("pipeline_b_scan", {"city": "Berlin"})

            assert result["task"] == "pipeline_b_scan"
            assert result["city"] == "Berlin"
            assert result["status"] == "completed"

    def test_registry_now_has_four_entries(self) -> None:
        """Registry should now contain 4 task handlers."""
        assert len(TASK_REGISTRY) == 4
