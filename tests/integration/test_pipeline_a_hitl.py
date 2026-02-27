"""Pipeline A HITL scenario integration tests.

Tests the four HITL decision points:
    1. Bid approval  — approve / reject / edit
    2. Plan review   — approve / reject / edit
    3. Final review  — approve / reject / edit
    4. resume_from_hitl() with mocked checkpoint persistence

Each ``_apply_*`` helper is a pure function that transforms saved state
into the next state based on the human's action.  ``resume_from_hitl``
integrates checkpoint loading + graph re-invocation.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.graph import (
    _apply_bid_approval,
    _apply_final_review,
    _apply_plan_review,
    resume_from_hitl,
)
from src.core.state import ProjectContext, create_initial_state

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_project() -> ProjectContext:
    return ProjectContext(
        project_id="proj-hitl-001",
        job_id="freelancer_react_landing_001",
        platform="freelancer",
        client={"name": "HITL Test Client", "rating": 4.5, "reviews": 20},
        requirements="Build a responsive React landing page",
        budget=500.0,
        deadline=datetime(2026, 4, 1, tzinfo=UTC),
    )


def _make_paused_bid_state() -> dict[str, Any]:
    """State paused after Bid agent requests HITL approval.

    Note: ``_enrich_project_from_bid`` expects ``artifacts["bid"]`` to be a
    dict (not a LangGraph channel list).  In real graph execution the channel
    reducer keeps it as a list, but the HITL helpers are called on a
    *checkpoint snapshot* where bid is already unwrapped.
    """
    state = dict(create_initial_state(
        project=_make_project(),
        first_agent="scout",
        thread_id=f"thread-hitl-{uuid.uuid4().hex[:8]}",
    ))
    state["current_agent"] = "hitl_bid"
    state["status"] = "paused"
    state["requires_hitl"] = True
    state["hitl_request_id"] = "hitl-bid-test-001"
    state["artifacts"] = {
        "scout": {"job_id": "j1", "score": 0.92},
        "bid": {
            "proposal": "I can build this for you.",
            "amount": 450,
            "delivery_days": 3,
        },
    }
    return state


def _make_paused_plan_state() -> dict[str, Any]:
    """State paused after Planner for plan review HITL."""
    state = dict(create_initial_state(
        project=_make_project(),
        first_agent="scout",
        thread_id=f"thread-hitl-{uuid.uuid4().hex[:8]}",
    ))
    state["current_agent"] = "hitl_review"
    state["status"] = "paused"
    state["requires_hitl"] = True
    state["hitl_request_id"] = "hitl-plan-test-001"
    state["artifacts"] = {
        "scout": [json.dumps({"job_id": "j1", "score": 0.92})],
        "bid": [json.dumps({"proposal": "I can build this.", "amount": 450})],
        "planner": [json.dumps({
            "phases": [{"name": "Setup", "tasks": [{"id": "t1"}]}],
            "total_estimated_hours": 3.5,
        })],
    }
    return state


def _make_paused_final_state() -> dict[str, Any]:
    """State paused after Packager for final delivery review."""
    state = dict(create_initial_state(
        project=_make_project(),
        first_agent="scout",
        thread_id=f"thread-hitl-{uuid.uuid4().hex[:8]}",
    ))
    state["current_agent"] = "packager"
    state["status"] = "paused"
    state["requires_hitl"] = True
    state["hitl_request_id"] = "hitl-final-test-001"
    state["artifacts"] = {
        "scout": [json.dumps({"job_id": "j1"})],
        "bid": [json.dumps({"proposal": "Bid.", "amount": 450})],
        "planner": [json.dumps({"phases": []})],
        "dev": [json.dumps({"files": [{"path": "a.tsx", "content": "x"}]})],
        "content": [json.dumps({"deliverables": [{"type": "heading"}]})],
        "design": [json.dumps({"specs": []})],
        "critic": [json.dumps({"verdict": "approve", "score": 0.91})],
        "packager": [json.dumps({
            "archive_url": "https://storage.example.com/d.zip",
        })],
    }
    return state


# ===========================================================================
# 1. Bid Approval scenarios
# ===========================================================================


def test_bid_approval_approve_routes_to_planner() -> None:
    """Approving a bid should set next_agent=planner and status=active."""
    state = _make_paused_bid_state()
    result = _apply_bid_approval(
        state, "approve", {"action": "approve"}, state["thread_id"],
    )
    assert result["status"] == "active"
    assert result["next_agent"] == "planner"
    assert result["current_agent"] == "planner"
    assert result["requires_hitl"] is False
    assert result["hitl_request_id"] is None


def test_bid_approval_reject_fails_state() -> None:
    """Rejecting a bid should set status=failed and append error."""
    state = _make_paused_bid_state()
    result = _apply_bid_approval(
        state, "reject", {"action": "reject"}, state["thread_id"],
    )
    assert result["status"] == "failed"
    assert result["next_agent"] is None
    assert result["requires_hitl"] is False
    assert any("rejected" in e for e in result["errors"])


def test_bid_approval_edit_preserves_edits_and_continues() -> None:
    """Editing a bid should store edits in artifacts and continue."""
    state = _make_paused_bid_state()
    edits = {"amount": 400, "delivery_days": 5}
    result = _apply_bid_approval(
        state, "edit", {"action": "edit", "edits": edits}, state["thread_id"],
    )
    assert result["status"] == "active"
    assert result["next_agent"] == "planner"
    assert result["artifacts"]["hitl_edits"] == [edits]


# ===========================================================================
# 2. Plan Review scenarios
# ===========================================================================


def test_plan_review_approve_routes_to_dev() -> None:
    """Approving a plan should set next_agent=dev."""
    state = _make_paused_plan_state()
    result = _apply_plan_review(
        state, "approve", {"action": "approve"}, state["thread_id"],
    )
    assert result["status"] == "active"
    assert result["next_agent"] == "dev"
    assert result["requires_hitl"] is False
    assert result["artifacts"]["_hitl_type"] == "plan_review"


def test_plan_review_reject_fails_with_error() -> None:
    """Rejecting a plan should set status=failed."""
    state = _make_paused_plan_state()
    result = _apply_plan_review(
        state, "reject", {"action": "reject"}, state["thread_id"],
    )
    assert result["status"] == "failed"
    assert result["next_agent"] is None
    assert any("plan rejected" in e for e in result["errors"])


def test_plan_review_edit_stores_edits_and_continues() -> None:
    """Editing a plan should store edits and route to dev."""
    state = _make_paused_plan_state()
    edits = {"phases": [{"name": "Revised Setup", "tasks": []}]}
    result = _apply_plan_review(
        state, "edit", {"action": "edit", "edits": edits}, state["thread_id"],
    )
    assert result["status"] == "active"
    assert result["next_agent"] == "dev"
    assert result["artifacts"]["hitl_edits"] == [edits]
    assert result["artifacts"]["_hitl_type"] == "plan_review"


# ===========================================================================
# 3. Final Review scenarios
# ===========================================================================


def test_final_review_approve_completes_project() -> None:
    """Approving final review should set status=completed."""
    state = _make_paused_final_state()
    result = _apply_final_review(
        state, "approve", {"action": "approve"}, state["thread_id"],
    )
    assert result["status"] == "completed"
    assert result["next_agent"] is None
    assert result["requires_hitl"] is False


def test_final_review_reject_fails_state() -> None:
    """Rejecting final review should fail the project."""
    state = _make_paused_final_state()
    result = _apply_final_review(
        state, "reject", {"action": "reject"}, state["thread_id"],
    )
    assert result["status"] == "failed"
    assert result["next_agent"] is None
    assert any("final delivery rejected" in e for e in result["errors"])


def test_final_review_edit_stores_edits_and_completes() -> None:
    """Editing final review should store edits and complete."""
    state = _make_paused_final_state()
    edits = {"note": "Looks good, just update the footer text."}
    result = _apply_final_review(
        state, "edit", {"action": "edit", "edits": edits}, state["thread_id"],
    )
    assert result["status"] == "completed"
    assert result["artifacts"]["hitl_edits"] == [edits]


# ===========================================================================
# 4. resume_from_hitl() with mocked checkpoint
# ===========================================================================


async def test_resume_from_hitl_bid_approval_with_checkpoint() -> None:
    """resume_from_hitl loads checkpoint, applies bid approval, returns state."""
    saved = _make_paused_bid_state()
    thread_id = saved["thread_id"]

    mock_checkpoint_tuple = MagicMock()
    mock_checkpoint_tuple.checkpoint = saved

    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)
    mock_checkpointer.aput = AsyncMock()

    mock_graph = AsyncMock()
    # Simulate the pipeline running to completion after bid approval
    resumed_result = dict(saved)
    resumed_result["status"] = "paused"
    resumed_result["current_agent"] = "packager"
    resumed_result["requires_hitl"] = True
    mock_graph.ainvoke = AsyncMock(return_value=resumed_result)

    mock_valkey = AsyncMock()
    mock_db_pool = AsyncMock()

    with (
        patch(
            "src.core.graph.HybridCheckpointSaver",
            return_value=mock_checkpointer,
        ),
        patch(
            "src.core.graph.build_full_pipeline_graph",
            return_value=mock_graph,
        ),
        patch("src.core.graph.get_settings"),
    ):
        result = await resume_from_hitl(
            thread_id=thread_id,
            hitl_response={"action": "approve"},
            hitl_type="bid_approval",
            valkey=mock_valkey,
            db_pool=mock_db_pool,
        )

    assert result["status"] == "paused"
    assert result["current_agent"] == "packager"
    mock_graph.ainvoke.assert_called_once()


async def test_resume_from_hitl_bid_rejection_saves_checkpoint() -> None:
    """resume_from_hitl with bid rejection saves state without graph invoke."""
    saved = _make_paused_bid_state()
    thread_id = saved["thread_id"]

    mock_checkpoint_tuple = MagicMock()
    mock_checkpoint_tuple.checkpoint = saved

    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)
    mock_checkpointer.aput = AsyncMock()

    mock_valkey = AsyncMock()
    mock_db_pool = AsyncMock()

    with (
        patch(
            "src.core.graph.HybridCheckpointSaver",
            return_value=mock_checkpointer,
        ),
        patch("src.core.graph.get_settings"),
    ):
        result = await resume_from_hitl(
            thread_id=thread_id,
            hitl_response={"action": "reject"},
            hitl_type="bid_approval",
            valkey=mock_valkey,
            db_pool=mock_db_pool,
        )

    assert result["status"] == "failed"
    assert any("rejected" in e for e in result.get("errors", []))
    mock_checkpointer.aput.assert_called_once()


async def test_resume_from_hitl_final_review_approve() -> None:
    """resume_from_hitl with final review approval completes the project."""
    saved = _make_paused_final_state()
    # Adjust current_agent so auto-detect works for final_review
    saved["current_agent"] = "hitl_review"
    thread_id = saved["thread_id"]

    mock_checkpoint_tuple = MagicMock()
    mock_checkpoint_tuple.checkpoint = saved

    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)
    mock_checkpointer.aput = AsyncMock()

    mock_valkey = AsyncMock()
    mock_db_pool = AsyncMock()

    with (
        patch(
            "src.core.graph.HybridCheckpointSaver",
            return_value=mock_checkpointer,
        ),
        patch("src.core.graph.get_settings"),
    ):
        result = await resume_from_hitl(
            thread_id=thread_id,
            hitl_response={"action": "approve"},
            hitl_type="final_review",
            valkey=mock_valkey,
            db_pool=mock_db_pool,
        )

    assert result["status"] == "completed"
    assert result["requires_hitl"] is False
    mock_checkpointer.aput.assert_called_once()


async def test_resume_from_hitl_auto_detects_bid_type() -> None:
    """resume_from_hitl auto-detects bid_approval from current_agent=hitl_bid."""
    saved = _make_paused_bid_state()
    thread_id = saved["thread_id"]

    mock_checkpoint_tuple = MagicMock()
    mock_checkpoint_tuple.checkpoint = saved

    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)
    mock_checkpointer.aput = AsyncMock()

    mock_valkey = AsyncMock()
    mock_db_pool = AsyncMock()

    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(return_value=dict(saved, status="completed"))

    with (
        patch(
            "src.core.graph.HybridCheckpointSaver",
            return_value=mock_checkpointer,
        ),
        patch(
            "src.core.graph.build_full_pipeline_graph",
            return_value=mock_graph,
        ),
        patch("src.core.graph.get_settings"),
    ):
        # Note: hitl_type=None, should auto-detect from current_agent="hitl_bid"
        await resume_from_hitl(
            thread_id=thread_id,
            hitl_response={"action": "approve"},
            hitl_type=None,
            valkey=mock_valkey,
            db_pool=mock_db_pool,
        )

    # Should have continued pipeline (approve triggers graph.ainvoke)
    mock_graph.ainvoke.assert_called_once()


async def test_resume_from_hitl_no_checkpoint_raises() -> None:
    """resume_from_hitl raises ValueError when no checkpoint exists."""
    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(return_value=None)

    mock_valkey = AsyncMock()
    mock_db_pool = AsyncMock()

    import pytest

    with (
        patch(
            "src.core.graph.HybridCheckpointSaver",
            return_value=mock_checkpointer,
        ),
        patch("src.core.graph.get_settings"),
        pytest.raises(ValueError, match="No checkpoint found"),
    ):
        await resume_from_hitl(
            thread_id="nonexistent-thread",
            hitl_response={"action": "approve"},
            valkey=mock_valkey,
            db_pool=mock_db_pool,
        )
