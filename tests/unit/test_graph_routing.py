"""Unit tests for LangGraph routing functions.

Tests all routing decision functions from src.core.graph without requiring
graph compilation.  Each routing function is a pure function that inspects
state fields and returns the next node name or END.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph import END

from src.core.graph import (
    MAX_REVISION_CYCLES,
    _apply_email_approval,
    _route_after_bid,
    _route_after_bid_submission,
    _route_after_content,
    _route_after_critic,
    _route_after_design,
    _route_after_dev,
    _route_after_hitl_bid,
    _route_after_hitl_email,
    _route_after_hitl_review,
    _route_after_packager,
    _route_after_planner,
    _route_after_scout,
    bid_submission_node,
    build_planner_pipeline_graph,
    create_graph_with_persistence,
)

# ---------------------------------------------------------------------------
# Scout routing tests
# ---------------------------------------------------------------------------


def test_route_after_scout_to_bid():
    """Scout routes to bid_node when next_agent=bid."""
    state = {"thread_id": "t1", "status": "active", "next_agent": "bid"}
    assert _route_after_scout(state) == "bid_node"


def test_route_after_scout_failed():
    """Scout routes to END when status=failed."""
    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_scout(state) == END


def test_route_after_scout_no_jobs():
    """Scout routes to END when next_agent is missing or not bid."""
    state = {"thread_id": "t1", "status": "active", "next_agent": None}
    assert _route_after_scout(state) == END

    state2 = {"thread_id": "t2", "status": "active"}
    assert _route_after_scout(state2) == END


# ---------------------------------------------------------------------------
# Bid routing tests
# ---------------------------------------------------------------------------


def test_route_after_bid_to_hitl():
    """Bid routes to hitl_bid_node when requires_hitl=True (normal path)."""
    state = {"thread_id": "t1", "status": "active", "requires_hitl": True}
    assert _route_after_bid(state) == "hitl_bid_node"


def test_route_after_bid_failed():
    """Bid routes to END when status=failed."""
    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_bid(state) == END


def test_route_after_bid_no_hitl():
    """Bid routes to END when HITL not required (defensive edge case)."""
    state = {"thread_id": "t1", "status": "active", "requires_hitl": False}
    assert _route_after_bid(state) == END


# ---------------------------------------------------------------------------
# HITL Bid routing tests
# ---------------------------------------------------------------------------


def test_route_after_hitl_bid_approved():
    """HITL bid routes to bid_submission_node when not failed (approved)."""
    state = {"thread_id": "t1", "status": "active"}
    assert _route_after_hitl_bid(state) == "bid_submission_node"


def test_route_after_hitl_bid_rejected():
    """HITL bid routes to END when status=failed (rejected)."""
    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_hitl_bid(state) == END


# ---------------------------------------------------------------------------
# Planner routing tests
# ---------------------------------------------------------------------------


def test_route_after_planner_to_dev():
    """Planner routes to dev_node via dynamic sequence (normal path)."""
    state = {
        "thread_id": "t1",
        "status": "active",
        "agent_sequence": ["dev", "content", "design"],
        "current_sequence_index": 0,
    }
    assert _route_after_planner(state) == "dev_node"


def test_route_after_planner_to_hitl_review():
    """Planner routes to HITL review when requires_hitl=True (plan review)."""
    state = {"thread_id": "t1", "status": "active", "requires_hitl": True}
    assert _route_after_planner(state) == "hitl_review_node"


def test_route_after_planner_failed():
    """Planner routes to END when status=failed."""
    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_planner(state) == END


def test_route_after_planner_no_next():
    """Planner routes to packager when no sequence (consulting)."""
    state = {"thread_id": "t1", "status": "active"}
    assert _route_after_planner(state) == "packager_node"


# ---------------------------------------------------------------------------
# Dev routing tests
# ---------------------------------------------------------------------------


def test_route_after_dev_to_content():
    """Dev routes to content_node via dynamic sequence routing."""
    state = {
        "thread_id": "t1",
        "status": "active",
        "agent_sequence": ["dev", "content", "design"],
        "current_sequence_index": 1,
    }
    assert _route_after_dev(state) == "content_node"


def test_route_after_dev_failed():
    """Dev routes to END when status=failed."""
    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_dev(state) == END


def test_route_after_dev_no_next():
    """Dev routes to packager when sequence is empty (no next agent)."""
    state = {"thread_id": "t1", "status": "active", "agent_sequence": [], "current_sequence_index": 0}
    assert _route_after_dev(state) == "packager_node"


# ---------------------------------------------------------------------------
# Content routing tests
# ---------------------------------------------------------------------------


def test_route_after_content_to_design():
    """Content routes to design_node via dynamic sequence routing."""
    state = {
        "thread_id": "t1",
        "status": "active",
        "agent_sequence": ["dev", "content", "design"],
        "current_sequence_index": 2,
    }
    assert _route_after_content(state) == "design_node"


def test_route_after_content_failed():
    """Content routes to END when status=failed."""
    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_content(state) == END


# ---------------------------------------------------------------------------
# Design routing tests
# ---------------------------------------------------------------------------


def test_route_after_design_to_critic():
    """Design routes to critic_node when sequence exhausted."""
    state = {
        "thread_id": "t1",
        "status": "active",
        "agent_sequence": ["dev", "content", "design"],
        "current_sequence_index": 3,
    }
    assert _route_after_design(state) == "critic_node"


def test_route_after_design_failed():
    """Design routes to END when status=failed."""
    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_design(state) == END


# ---------------------------------------------------------------------------
# Critic routing tests (most complex routing point)
# ---------------------------------------------------------------------------


def test_route_after_critic_approved():
    """Critic routes to packager_node when next_agent=packager (approved)."""
    state = {"thread_id": "t1", "status": "active", "next_agent": "packager"}
    assert _route_after_critic(state) == "packager_node"


def test_route_after_critic_minor_revision():
    """Critic routes to dev_node for minor revision (revision count < limit)."""
    # No artifacts yet — defaults to count=0
    state = {
        "thread_id": "t1",
        "status": "active",
        "next_agent": "dev",
    }
    assert _route_after_critic(state) == "dev_node"

    # Explicit count below limit
    state2 = {
        "thread_id": "t2",
        "status": "active",
        "next_agent": "dev",
        "artifacts": {"_critic_revision_count": 2},
    }
    assert _route_after_critic(state2) == "dev_node"


def test_route_after_critic_revision_limit_exceeded():
    """Critic routes to HITL when artifacts._critic_revision_count >= MAX_REVISION_CYCLES."""
    state = {
        "thread_id": "t1",
        "status": "active",
        "next_agent": "dev",
        "artifacts": {"_critic_revision_count": MAX_REVISION_CYCLES},
    }
    assert _route_after_critic(state) == "hitl_review_node"

    state2 = {
        "thread_id": "t2",
        "status": "active",
        "next_agent": "dev",
        "artifacts": {"_critic_revision_count": MAX_REVISION_CYCLES + 1},
    }
    assert _route_after_critic(state2) == "hitl_review_node"


def test_route_after_critic_major_revision():
    """Critic routes to planner_node for major revision (re-decomposition)."""
    state = {"thread_id": "t1", "status": "active", "next_agent": "planner"}
    assert _route_after_critic(state) == "planner_node"


def test_route_after_critic_scope_creep_hitl():
    """Critic routes to HITL when requires_hitl=True (scope creep/reject)."""
    state = {
        "thread_id": "t1",
        "status": "active",
        "requires_hitl": True,
    }
    assert _route_after_critic(state) == "hitl_review_node"


def test_route_after_critic_failed():
    """Critic routes to END when status=failed."""
    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_critic(state) == END


def test_route_after_critic_no_next():
    """Critic routes to END when next_agent is missing and no HITL."""
    state = {"thread_id": "t1", "status": "active"}
    assert _route_after_critic(state) == END


# ---------------------------------------------------------------------------
# Packager routing tests
# ---------------------------------------------------------------------------


def test_route_after_packager_to_hitl():
    """Packager routes to hitl_review_node when requires_hitl=True (normal)."""
    state = {"thread_id": "t1", "status": "active", "requires_hitl": True}
    assert _route_after_packager(state) == "hitl_review_node"


def test_route_after_packager_failed():
    """Packager routes to END when status=failed."""
    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_packager(state) == END


def test_route_after_packager_no_hitl():
    """Packager routes to END when HITL not required (defensive edge case)."""
    state = {"thread_id": "t1", "status": "active", "requires_hitl": False}
    assert _route_after_packager(state) == END


# ---------------------------------------------------------------------------
# HITL Review routing tests
# ---------------------------------------------------------------------------


def test_route_after_hitl_review_always_end():
    """HITL review always routes to END (Phase 1)."""
    state = {"thread_id": "t1", "status": "completed"}
    assert _route_after_hitl_review(state) == END

    state2 = {"thread_id": "t2", "status": "failed"}
    assert _route_after_hitl_review(state2) == END

    state3 = {"thread_id": "t3", "status": "active"}
    assert _route_after_hitl_review(state3) == END


# ---------------------------------------------------------------------------
# Edge cases and complex scenarios
# ---------------------------------------------------------------------------


def test_route_after_critic_revision_count_missing():
    """Critic treats missing retry_count as 0 (first revision)."""
    state = {
        "thread_id": "t1",
        "status": "active",
        "next_agent": "dev",
        # retry_count not present
    }
    assert _route_after_critic(state) == "dev_node"


def test_route_after_critic_hitl_takes_precedence_over_dev():
    """Critic routes to HITL when both requires_hitl and next_agent=dev."""
    # HITL escalation is checked BEFORE next_agent — safety-first priority.
    state = {
        "thread_id": "t1",
        "status": "active",
        "next_agent": "dev",
        "requires_hitl": True,
        "retry_count": 0,
    }
    # HITL always takes precedence over next_agent
    assert _route_after_critic(state) == "hitl_review_node"

    # Same with revision count >= MAX
    state2 = {
        "thread_id": "t2",
        "status": "active",
        "next_agent": "dev",
        "requires_hitl": True,
        "artifacts": {"_critic_revision_count": MAX_REVISION_CYCLES},
    }
    assert _route_after_critic(state2) == "hitl_review_node"


# ---------------------------------------------------------------------------
# Pipeline B: email HITL routing
# ---------------------------------------------------------------------------


def test_route_after_hitl_email_always_ends():
    """hitl_email always routes to END (emails sent after resume, not continuation)."""
    state = {"thread_id": "t1", "status": "paused"}
    assert _route_after_hitl_email(state) == END


# ---------------------------------------------------------------------------
# _apply_email_approval tests
# ---------------------------------------------------------------------------


def test_apply_email_approval_approve():
    """Approve sets emails_approved=True and status=active for pipeline re-invocation."""
    saved = {"thread_id": "t1", "status": "paused", "artifacts": {"outreach": []}}
    result = _apply_email_approval(saved, "approve", {}, "t1")
    assert result["status"] == "active"
    assert result["artifacts"]["emails_approved"] is True
    assert result["requires_hitl"] is False


def test_apply_email_approval_reject():
    """Reject sets status=failed with error message."""
    saved = {"thread_id": "t1", "status": "paused", "errors": []}
    result = _apply_email_approval(saved, "reject", {}, "t1")
    assert result["status"] == "failed"
    assert "rejected" in result["errors"][-1].lower()


def test_apply_email_approval_edit():
    """Edit merges edits into artifacts and marks approved."""
    saved = {"thread_id": "t1", "status": "paused", "artifacts": {}}
    edits = {"subject": "Updated subject"}
    result = _apply_email_approval(saved, "edit", {"edits": edits}, "t1")
    assert result["status"] == "active"
    assert result["artifacts"]["emails_approved"] is True
    assert result["artifacts"]["hitl_edits"] == [edits]


def test_apply_email_approval_unknown_action():
    """Unknown action fails closed — sets status='failed' with error message."""
    saved = {"thread_id": "t1", "status": "paused", "artifacts": {}, "errors": []}
    result = _apply_email_approval(saved, "later", {}, "t1")
    assert result["status"] == "failed"
    assert any("unknown outreach approval action" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# create_graph_with_persistence Pipeline B option
# ---------------------------------------------------------------------------


@patch("src.core.graph.HybridCheckpointSaver")
@patch("src.core.graph.build_pipeline_b_graph")
def test_create_graph_with_persistence_pipeline_b(mock_build, _mock_cp):
    """create_graph_with_persistence(pipeline_b=True) builds Pipeline B graph."""
    create_graph_with_persistence(MagicMock(), MagicMock(), pipeline_b=True)
    mock_build.assert_called_once()


@patch("src.core.graph.HybridCheckpointSaver")
@patch("src.core.graph.build_full_pipeline_graph")
def test_create_graph_with_persistence_full_pipeline(mock_build, _mock_cp):
    """create_graph_with_persistence(full_pipeline=True) builds Pipeline A graph."""
    create_graph_with_persistence(MagicMock(), MagicMock(), full_pipeline=True)
    mock_build.assert_called_once()


@patch("src.core.graph.HybridCheckpointSaver")
@patch("src.core.graph.build_full_pipeline_graph")
@patch("src.core.graph.build_pipeline_b_graph")
def test_create_graph_with_persistence_pipeline_b_overrides_full(mock_build_b, mock_build_a, _mock_cp):
    """pipeline_b=True takes precedence over full_pipeline=True."""
    create_graph_with_persistence(MagicMock(), MagicMock(), full_pipeline=True, pipeline_b=True)
    mock_build_b.assert_called_once()
    mock_build_a.assert_not_called()


# ---------------------------------------------------------------------------
# Bid submission routing tests
# ---------------------------------------------------------------------------


def test_route_after_bid_submission_to_dev_launch():
    """Bid submission routes to hitl_dev_launch_node (safety gate before planner)."""
    state = {"thread_id": "t1", "status": "active"}
    assert _route_after_bid_submission(state) == "hitl_dev_launch_node"


def test_route_after_bid_submission_failed():
    """Bid submission routes to END when status=failed."""
    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_bid_submission(state) == END


# ---------------------------------------------------------------------------
# bid_submission_node tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bid_submission_node_freelancer():
    """bid_submission_node calls FreelancerClient.submit_bid for freelancer platform."""
    state = {
        "thread_id": "t1",
        "status": "active",
        "project": {"platform": "freelancer", "job_id": "proj-123"},
        "artifacts": {"bid": {"proposal": "I can do this", "amount": 500}},
        "current_agent": "hitl_bid",
        "next_agent": None,
    }

    mock_client = MagicMock()
    mock_client.submit_bid = AsyncMock(return_value={"bid_id": "b-1"})

    with patch(
        "src.adapters.freelancer.FreelancerClient",
        return_value=mock_client,
    ):
        result = await bid_submission_node(state)

    assert result["artifacts"]["bid_submitted"] is True
    assert result["artifacts"]["bid_submission_result"] == {"bid_id": "b-1"}
    assert result["next_agent"] == "planner"
    mock_client.submit_bid.assert_called_once()


@pytest.mark.asyncio
async def test_bid_submission_node_non_freelancer():
    """bid_submission_node marks non-freelancer platforms as manual_submit_required."""
    state = {
        "thread_id": "t1",
        "status": "active",
        "project": {"platform": "kwork"},
        "artifacts": {"bid": {"proposal": "I can do this"}},
        "current_agent": "hitl_bid",
        "next_agent": None,
    }

    mock_session = AsyncMock()
    mock_session.add = MagicMock()  # add() is sync in SQLAlchemy
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("src.core.database.get_db_session", return_value=mock_ctx):
        result = await bid_submission_node(state)

    assert result["artifacts"]["bid_submitted"] is False
    assert result["artifacts"]["manual_submit_required"] is True
    assert result["next_agent"] == "planner"


@pytest.mark.asyncio
async def test_bid_submission_node_freelancer_failure():
    """bid_submission_node handles FreelancerClient failure gracefully."""
    state = {
        "thread_id": "t1",
        "status": "active",
        "project": {"platform": "freelancer", "job_id": "proj-123"},
        "artifacts": {"bid": {}},
        "current_agent": "hitl_bid",
        "next_agent": None,
    }

    with patch(
        "src.adapters.freelancer.FreelancerClient",
        side_effect=ConnectionError("API down"),
    ):
        result = await bid_submission_node(state)

    assert result["artifacts"]["bid_submitted"] is False
    assert "API down" in result["artifacts"]["bid_submission_error"]
    assert result["next_agent"] == "planner"


@pytest.mark.asyncio
async def test_bid_submission_node_missing_project():
    """bid_submission_node handles missing project gracefully (non-freelancer path)."""
    state = {
        "thread_id": "t1",
        "status": "active",
        "artifacts": {},
        "current_agent": "hitl_bid",
        "next_agent": None,
    }

    mock_session = AsyncMock()
    mock_session.add = MagicMock()  # add() is sync in SQLAlchemy
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("src.core.database.get_db_session", return_value=mock_ctx):
        result = await bid_submission_node(state)

    assert result["artifacts"]["manual_submit_required"] is True
    assert result["next_agent"] == "planner"


# ---------------------------------------------------------------------------
# build_planner_pipeline_graph tests
# ---------------------------------------------------------------------------


def test_build_planner_pipeline_graph_compiles():
    """build_planner_pipeline_graph returns a compiled graph starting at planner."""
    graph = build_planner_pipeline_graph()
    assert graph is not None


# ---------------------------------------------------------------------------
# Design HITL routing tests
# ---------------------------------------------------------------------------


def test_route_after_design_for_review_normal():
    """Design routes to hitl_design_review_node when not failed."""
    from src.core.graph import _route_after_design_for_review

    state = {"thread_id": "t1", "status": "active"}
    assert _route_after_design_for_review(state) == "hitl_design_review_node"


def test_route_after_design_for_review_failed():
    """Design routes to END when status=failed."""
    from src.core.graph import _route_after_design_for_review

    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_design_for_review(state) == END


def test_route_after_hitl_design_review_operator_approved():
    """Design review routes to client approval when operator approved."""
    from src.core.graph import _route_after_hitl_design_review

    state = {
        "thread_id": "t1",
        "status": "active",
        "artifacts": {"_design_operator_approved": True},
    }
    assert _route_after_hitl_design_review(state) == "hitl_design_approval_node"


def test_route_after_hitl_design_review_request_changes():
    """Design review routes to design_node when feedback provided (revision)."""
    from src.core.graph import _route_after_hitl_design_review

    state = {
        "thread_id": "t1",
        "status": "active",
        "design_feedback": "Make the hero section bigger",
        "design_revision": 1,
    }
    assert _route_after_hitl_design_review(state) == "design_node"


def test_route_after_hitl_design_review_revision_limit():
    """Design review routes to planner when revision limit exceeded."""
    from src.core.graph import MAX_DESIGN_REVISIONS, _route_after_hitl_design_review

    state = {
        "thread_id": "t1",
        "status": "active",
        "design_feedback": "Still not right",
        "design_revision": MAX_DESIGN_REVISIONS,
    }
    assert _route_after_hitl_design_review(state) == "planner_node"


def test_route_after_hitl_design_review_failed():
    """Design review routes to END when status=failed."""
    from src.core.graph import _route_after_hitl_design_review

    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_hitl_design_review(state) == END


def test_route_after_hitl_design_review_still_paused():
    """Design review routes to END when still paused."""
    from src.core.graph import _route_after_hitl_design_review

    state = {"thread_id": "t1", "status": "paused"}
    assert _route_after_hitl_design_review(state) == END


def test_route_after_hitl_design_review_default_approve():
    """Design review defaults to client approval when active with no flags."""
    from src.core.graph import _route_after_hitl_design_review

    state = {"thread_id": "t1", "status": "active"}
    assert _route_after_hitl_design_review(state) == "hitl_design_approval_node"


def test_route_after_hitl_design_approval_client_approved():
    """Client approval routes to next in sequence when design_approved=True."""
    from src.core.graph import _route_after_hitl_design_approval

    state = {
        "thread_id": "t1",
        "status": "active",
        "design_approved": True,
        "agent_sequence": ["dev", "content", "design"],
        "current_sequence_index": 3,  # past design
    }
    # Index 3 >= len(sequence) -> critic_node
    assert _route_after_hitl_design_approval(state) == "critic_node"


def test_route_after_hitl_design_approval_client_changes():
    """Client approval routes to design_node when client requests changes."""
    from src.core.graph import _route_after_hitl_design_approval

    state = {
        "thread_id": "t1",
        "status": "active",
        "design_feedback": "Change the color scheme",
    }
    assert _route_after_hitl_design_approval(state) == "design_node"


def test_route_after_hitl_design_approval_failed():
    """Client approval routes to END when status=failed."""
    from src.core.graph import _route_after_hitl_design_approval

    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_hitl_design_approval(state) == END


def test_route_after_hitl_design_approval_still_paused():
    """Client approval routes to END when still paused."""
    from src.core.graph import _route_after_hitl_design_approval

    state = {"thread_id": "t1", "status": "paused"}
    assert _route_after_hitl_design_approval(state) == END


def test_route_after_hitl_design_approval_default_next():
    """Client approval defaults to next in sequence when active, no flags."""
    from src.core.graph import _route_after_hitl_design_approval

    state = {
        "thread_id": "t1",
        "status": "active",
        "agent_sequence": ["dev"],
        "current_sequence_index": 1,  # past all agents
    }
    assert _route_after_hitl_design_approval(state) == "critic_node"


# ---------------------------------------------------------------------------
# _apply_design_review tests
# ---------------------------------------------------------------------------


def test_apply_design_review_approve():
    """Approve sets _design_operator_approved flag and status=active."""
    from src.core.graph import _apply_design_review

    saved = {"thread_id": "t1", "status": "paused", "artifacts": {}, "errors": []}
    result = _apply_design_review(saved, "approve", {}, "t1")
    assert result["status"] == "active"
    assert result["artifacts"]["_design_operator_approved"] is True
    assert result["requires_hitl"] is False


def test_apply_design_review_request_changes():
    """Request changes increments design_revision and sets feedback."""
    from src.core.graph import _apply_design_review

    saved = {
        "thread_id": "t1",
        "status": "paused",
        "artifacts": {},
        "errors": [],
        "design_revision": 0,
    }
    result = _apply_design_review(saved, "request_changes", {"feedback": "Fix colors"}, "t1")
    assert result["status"] == "active"
    assert result["design_revision"] == 1
    assert result["design_feedback"] == "Fix colors"


def test_apply_design_review_request_changes_max_revision():
    """Request changes still works at max revisions (routing handles escalation)."""
    from src.core.graph import MAX_DESIGN_REVISIONS, _apply_design_review

    saved = {
        "thread_id": "t1",
        "status": "paused",
        "artifacts": {},
        "errors": [],
        "design_revision": MAX_DESIGN_REVISIONS - 1,
    }
    result = _apply_design_review(saved, "request_changes", {"feedback": "Last try"}, "t1")
    assert result["status"] == "active"
    assert result["design_revision"] == MAX_DESIGN_REVISIONS


def test_apply_design_review_reject():
    """Reject escalates to Planner for re-decomposition (status=active, next_agent=planner)."""
    from src.core.graph import _apply_design_review

    saved = {"thread_id": "t1", "status": "paused", "artifacts": {}, "errors": []}
    result = _apply_design_review(saved, "reject", {}, "t1")
    assert result["status"] == "active"
    assert result["next_agent"] == "planner"
    assert result["requires_hitl"] is False


def test_apply_design_review_unknown_action():
    """Unknown action fails closed."""
    from src.core.graph import _apply_design_review

    saved = {"thread_id": "t1", "status": "paused", "artifacts": {}, "errors": []}
    result = _apply_design_review(saved, "magic", {}, "t1")
    assert result["status"] == "failed"
    assert "unknown" in result["errors"][-1].lower()


# ---------------------------------------------------------------------------
# _apply_design_client_approval tests
# ---------------------------------------------------------------------------


def test_apply_design_client_approval_approve():
    """Client approve sets design_approved=True and status=active."""
    from src.core.graph import _apply_design_client_approval

    saved = {"thread_id": "t1", "status": "paused", "artifacts": {}, "errors": []}
    result = _apply_design_client_approval(saved, "approve", {}, "t1")
    assert result["status"] == "active"
    assert result["design_approved"] is True
    assert result["requires_hitl"] is False


def test_apply_design_client_approval_request_changes():
    """Client request_changes sets design_feedback and design_approved=False."""
    from src.core.graph import _apply_design_client_approval

    saved = {"thread_id": "t1", "status": "paused", "artifacts": {}, "errors": []}
    result = _apply_design_client_approval(saved, "request_changes", {"feedback": "Too dark"}, "t1")
    assert result["status"] == "active"
    assert result["design_approved"] is False
    assert result["design_feedback"] == "Too dark"


def test_apply_design_client_approval_reject():
    """Client reject escalates to Planner for re-decomposition (status=active, next_agent=planner)."""
    from src.core.graph import _apply_design_client_approval

    saved = {"thread_id": "t1", "status": "paused", "artifacts": {}, "errors": []}
    result = _apply_design_client_approval(saved, "reject", {}, "t1")
    assert result["status"] == "active"
    assert result["next_agent"] == "planner"
    assert result["requires_hitl"] is False


def test_apply_design_client_approval_unknown():
    """Unknown action fails closed."""
    from src.core.graph import _apply_design_client_approval

    saved = {"thread_id": "t1", "status": "paused", "artifacts": {}, "errors": []}
    result = _apply_design_client_approval(saved, "maybe", {}, "t1")
    assert result["status"] == "failed"
    assert "unknown" in result["errors"][-1].lower()
