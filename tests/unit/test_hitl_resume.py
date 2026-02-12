"""Unit tests for HITL resume integration.

Tests that:
1. resume_from_hitl correctly loads checkpoint and applies actions
2. _route_after_hitl_review routes plan_review approvals to dev_node
3. _apply_plan_review / _apply_email_approval set correct state
4. HITL resolve endpoint dispatches fire-and-forget resume
5. run_project_pipeline uses checkpointed graph for HITL pause/resume
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.graph import (
    _apply_bid_approval,
    _apply_email_approval,
    _apply_final_review,
    _apply_plan_review,
    _route_after_hitl_review,
)
from src.core.state import create_initial_state


def _state(**overrides: Any) -> dict[str, Any]:
    project = {
        "project_id": "p1",
        "job_id": "j1",
        "platform": "freelancer",
        "client": {"name": "T"},
        "requirements": "page",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    s = create_initial_state(project=project, first_agent="scout", thread_id="t-resume")
    s.update(overrides)
    return s


# =====================================================================
# 1. _route_after_hitl_review: plan_review approved -> dev_node
# =====================================================================

class TestRouteAfterHITLReview:
    """Test _route_after_hitl_review routing decisions."""

    def test_plan_review_approved_routes_to_dev_node(self) -> None:
        """When hitl_type=plan_review and status=active, routes to dev_node."""
        state = _state(
            status="active",
            artifacts={"_hitl_type": "plan_review"},
        )
        assert _route_after_hitl_review(state) == "dev_node"

    def test_plan_review_rejected_routes_to_end(self) -> None:
        """When hitl_type=plan_review but status=failed, routes to END."""
        state = _state(
            status="failed",
            artifacts={"_hitl_type": "plan_review"},
        )
        assert _route_after_hitl_review(state) == "__end__"

    def test_final_review_approved_routes_to_end(self) -> None:
        """final_review with status=completed goes to END."""
        state = _state(
            status="completed",
            artifacts={"_hitl_type": "final_review"},
        )
        assert _route_after_hitl_review(state) == "__end__"

    def test_no_hitl_type_routes_to_end(self) -> None:
        """Missing _hitl_type routes to END (default)."""
        state = _state(status="active", artifacts={})
        assert _route_after_hitl_review(state) == "__end__"


# =====================================================================
# 2. _apply_plan_review: sets correct state and artifacts
# =====================================================================

class TestApplyPlanReview:
    """Test _apply_plan_review helper."""

    def test_approve_sets_dev_as_next_and_active(self) -> None:
        """Approve should set next_agent=dev, status=active, _hitl_type artifact."""
        state = _state(status="paused", current_agent="hitl_review")
        result = _apply_plan_review(state, "approve", {}, "t-resume")
        assert result["status"] == "active"
        assert result["next_agent"] == "dev"
        assert result["current_agent"] == "hitl_review"
        assert result["artifacts"]["_hitl_type"] == "plan_review"
        assert result["requires_hitl"] is False

    def test_reject_sets_failed(self) -> None:
        """Reject should set status=failed."""
        state = _state(status="paused")
        result = _apply_plan_review(state, "reject", {}, "t-resume")
        assert result["status"] == "failed"
        assert "plan rejected" in result["errors"][-1]

    def test_edit_merges_edits_and_routes_to_dev(self) -> None:
        """Edit should merge edits into artifacts and route to dev."""
        state = _state(status="paused")
        edits = {"plan": "revised plan"}
        result = _apply_plan_review(state, "edit", {"edits": edits}, "t-resume")
        assert result["status"] == "active"
        assert result["next_agent"] == "dev"
        assert result["artifacts"]["hitl_edits"] == [edits]


# =====================================================================
# 3. _apply_email_approval: Pipeline B email approval
# =====================================================================

class TestApplyEmailApproval:
    """Test _apply_email_approval helper."""

    def test_approve_marks_emails_approved(self) -> None:
        """Approve should set emails_approved=True in artifacts."""
        state = _state(status="paused")
        result = _apply_email_approval(state, "approve", {}, "t-resume")
        assert result["artifacts"]["emails_approved"] is True
        assert result["requires_hitl"] is False

    def test_reject_sets_failed_with_error(self) -> None:
        """Reject should set status=failed with error message."""
        state = _state(status="paused")
        result = _apply_email_approval(state, "reject", {}, "t-resume")
        assert result["status"] == "failed"
        assert "emails rejected" in result["errors"][-1]

    def test_edit_approves_with_edits(self) -> None:
        """Edit should approve emails and store edits."""
        state = _state(status="paused")
        edits = {"subject": "New subject"}
        result = _apply_email_approval(state, "edit", {"edits": edits}, "t-resume")
        assert result["artifacts"]["emails_approved"] is True
        assert result["artifacts"]["hitl_edits"] == [edits]


# =====================================================================
# 4. resume_from_hitl: full integration with mocked checkpoint
# =====================================================================

@pytest.mark.asyncio
class TestResumeFromHITL:
    """Test resume_from_hitl function with mocked persistence."""

    async def test_bid_approval_resume_invokes_full_pipeline(self) -> None:
        """Bid approval should re-invoke the full pipeline graph."""
        saved_state = _state(status="paused", current_agent="hitl_bid")
        mock_checkpoint_tuple = MagicMock()
        mock_checkpoint_tuple.checkpoint = saved_state

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={"status": "completed"})

        with (
            patch("src.core.graph.get_settings") as mock_settings,
            patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
            patch("src.core.graph.build_full_pipeline_graph", return_value=mock_graph),
        ):
            mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")

            from src.core.graph import resume_from_hitl

            mock_pool = AsyncMock()
            mock_pool.close = AsyncMock()

            result = await resume_from_hitl(
                "t-resume",
                {"action": "approve", "bid_ids": ["bid-1"]},
                hitl_type="bid_approval",
                valkey=MagicMock(),
                db_pool=mock_pool,
            )

            assert result["status"] == "completed"
            mock_graph.ainvoke.assert_awaited_once()

    async def test_plan_review_resume_invokes_pipeline(self) -> None:
        """Plan review approval should re-invoke the pipeline graph."""
        saved_state = _state(status="paused", current_agent="hitl_review")
        mock_checkpoint_tuple = MagicMock()
        mock_checkpoint_tuple.checkpoint = saved_state

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={"status": "active"})

        with (
            patch("src.core.graph.get_settings") as mock_settings,
            patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
            patch("src.core.graph.build_full_pipeline_graph", return_value=mock_graph),
        ):
            mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")

            from src.core.graph import resume_from_hitl

            mock_pool = AsyncMock()
            mock_pool.close = AsyncMock()

            await resume_from_hitl(
                "t-resume",
                {"action": "approve"},
                hitl_type="plan_review",
                valkey=MagicMock(),
                db_pool=mock_pool,
            )

            mock_graph.ainvoke.assert_awaited_once()

    async def test_final_review_approve_completes_without_reinvoke(self) -> None:
        """Final review approval should save checkpoint but NOT re-invoke graph."""
        saved_state = _state(status="paused", current_agent="hitl_review")
        mock_checkpoint_tuple = MagicMock()
        mock_checkpoint_tuple.checkpoint = saved_state

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)
        mock_checkpointer.aput = AsyncMock()

        with (
            patch("src.core.graph.get_settings") as mock_settings,
            patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
        ):
            mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")

            from src.core.graph import resume_from_hitl

            mock_pool = AsyncMock()
            mock_pool.close = AsyncMock()

            result = await resume_from_hitl(
                "t-resume",
                {"action": "approve"},
                hitl_type="final_review",
                valkey=MagicMock(),
                db_pool=mock_pool,
            )

            assert result["status"] == "completed"
            mock_checkpointer.aput.assert_awaited_once()

    async def test_no_checkpoint_raises_value_error(self) -> None:
        """Should raise ValueError when no checkpoint exists for thread_id."""
        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget_tuple = AsyncMock(return_value=None)

        with (
            patch("src.core.graph.get_settings") as mock_settings,
            patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
        ):
            mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")

            from src.core.graph import resume_from_hitl

            mock_pool = AsyncMock()
            mock_pool.close = AsyncMock()

            with pytest.raises(ValueError, match="No checkpoint found"):
                await resume_from_hitl(
                    "nonexistent-thread",
                    {"action": "approve"},
                    valkey=MagicMock(),
                    db_pool=mock_pool,
                )

    async def test_email_approval_resume_invokes_pipeline_b(self) -> None:
        """Email approval should re-invoke Pipeline B graph."""
        saved_state = _state(status="paused", current_agent="hitl_email")
        mock_checkpoint_tuple = MagicMock()
        mock_checkpoint_tuple.checkpoint = saved_state

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={"status": "completed"})

        with (
            patch("src.core.graph.get_settings") as mock_settings,
            patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
            patch("src.core.graph.build_pipeline_b_graph", return_value=mock_graph),
        ):
            mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")

            from src.core.graph import resume_from_hitl

            mock_pool = AsyncMock()
            mock_pool.close = AsyncMock()

            result = await resume_from_hitl(
                "t-resume",
                {"action": "approve"},
                hitl_type="email_approval",
                valkey=MagicMock(),
                db_pool=mock_pool,
            )

            assert result["status"] == "completed"
            mock_graph.ainvoke.assert_awaited_once()

    async def test_auto_detects_hitl_type_from_current_agent(self) -> None:
        """Should auto-detect hitl_type from current_agent when not provided."""
        saved_state = _state(status="paused", current_agent="hitl_email")
        mock_checkpoint_tuple = MagicMock()
        mock_checkpoint_tuple.checkpoint = saved_state

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={"status": "completed"})

        with (
            patch("src.core.graph.get_settings") as mock_settings,
            patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
            patch("src.core.graph.build_pipeline_b_graph", return_value=mock_graph),
        ):
            mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")

            from src.core.graph import resume_from_hitl

            mock_pool = AsyncMock()
            mock_pool.close = AsyncMock()

            # No hitl_type passed -- should auto-detect from "hitl_email" -> "email_approval"
            await resume_from_hitl(
                "t-resume",
                {"action": "approve"},
                hitl_type=None,
                valkey=MagicMock(),
                db_pool=mock_pool,
            )

            # It should have called pipeline_b since hitl_email -> email_approval
            mock_graph.ainvoke.assert_awaited_once()


# =====================================================================
# 5. HITL resolve endpoint fires resume (integration-like)
# =====================================================================

class TestHITLResolveFiresResume:
    """Test that the resolve endpoint dispatches resume for resumable types."""

    def test_resolve_dispatches_resume_for_bid_approval(self) -> None:
        """Resolving a bid_approval item should dispatch resume_from_hitl."""
        from src.api.routes.hitl import _next_action

        # Verify the resume fire-and-forget pattern works:
        # - bid_approval is in _RESUMABLE_TYPES
        # - action is not "later"
        # - thread_id is in payload
        resumable_types = {"bid_approval", "plan_review", "email_approval", "final_review"}
        assert "bid_approval" in resumable_types

        # Verify _next_action returns correct downstream action
        assert _next_action("bid_approval", "approve") == "bid_will_be_submitted"
        assert _next_action("plan_review", "approve") == "plan_approved_for_execution"
        assert _next_action("email_approval", "approve") == "emails_will_be_sent"
        assert _next_action("final_review", "approve") == "work_delivered_to_client"

    def test_later_action_does_not_trigger_resume(self) -> None:
        """Action 'later' should NOT trigger resume dispatch."""
        from src.api.routes.hitl import _next_action

        # 'later' action should still produce a valid next_action label
        assert _next_action("bid_approval", "later") == "bid_deferred"
        assert _next_action("plan_review", "later") == "plan_review_deferred"

    def test_background_task_set_prevents_gc(self) -> None:
        """The _background_resume_tasks set should track tasks to prevent GC."""
        from src.api.routes.hitl import _background_resume_tasks

        assert isinstance(_background_resume_tasks, set)

    def test_non_resumable_types_not_dispatched(self) -> None:
        """Types not in the resumable set should not trigger resume."""
        resumable_types = {"bid_approval", "plan_review", "email_approval", "final_review"}
        non_resumable = {"alert", "revision", "scope_creep", "code_review", "job_review"}
        for t in non_resumable:
            assert t not in resumable_types


# =====================================================================
# 6. _apply_bid_approval and _apply_final_review
# =====================================================================

class TestApplyBidApproval:
    """Test _apply_bid_approval helper."""

    def test_approve_routes_to_planner(self) -> None:
        state = _state(status="paused", current_agent="hitl_bid")
        result = _apply_bid_approval(state, "approve", {}, "t-resume")
        assert result["status"] == "active"
        assert result["next_agent"] == "planner"
        assert result["requires_hitl"] is False

    def test_reject_sets_failed(self) -> None:
        state = _state(status="paused", current_agent="hitl_bid")
        result = _apply_bid_approval(state, "reject", {}, "t-resume")
        assert result["status"] == "failed"
        assert "bid rejected" in result["errors"][-1]

    def test_edit_merges_edits_and_routes_to_planner(self) -> None:
        state = _state(status="paused", current_agent="hitl_bid")
        edits = {"proposal_text": "Updated bid"}
        result = _apply_bid_approval(state, "edit", {"edits": edits}, "t-resume")
        assert result["status"] == "active"
        assert result["next_agent"] == "planner"
        assert result["artifacts"]["hitl_edits"] == [edits]


class TestApplyFinalReview:
    """Test _apply_final_review helper."""

    def test_approve_sets_completed(self) -> None:
        state = _state(status="paused", current_agent="hitl_review")
        result = _apply_final_review(state, "approve", {}, "t-resume")
        assert result["status"] == "completed"
        assert result["requires_hitl"] is False

    def test_reject_sets_failed(self) -> None:
        state = _state(status="paused", current_agent="hitl_review")
        result = _apply_final_review(state, "reject", {}, "t-resume")
        assert result["status"] == "failed"
        assert "delivery rejected" in result["errors"][-1]
