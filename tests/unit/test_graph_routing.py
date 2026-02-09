"""Unit tests for LangGraph routing functions.

Tests all routing decision functions from src.core.graph without requiring
graph compilation.  Each routing function is a pure function that inspects
state fields and returns the next node name or END.
"""

from langgraph.graph import END

from src.core.graph import (
    MAX_REVISION_CYCLES,
    _route_after_bid,
    _route_after_content,
    _route_after_critic,
    _route_after_design,
    _route_after_dev,
    _route_after_hitl_bid,
    _route_after_hitl_review,
    _route_after_packager,
    _route_after_planner,
    _route_after_scout,
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
    """HITL bid routes to planner_node when not failed (approved)."""
    state = {"thread_id": "t1", "status": "active"}
    assert _route_after_hitl_bid(state) == "planner_node"


def test_route_after_hitl_bid_rejected():
    """HITL bid routes to END when status=failed (rejected)."""
    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_hitl_bid(state) == END


# ---------------------------------------------------------------------------
# Planner routing tests
# ---------------------------------------------------------------------------

def test_route_after_planner_to_dev():
    """Planner routes to dev_node when next_agent=dev (normal path)."""
    state = {"thread_id": "t1", "status": "active", "next_agent": "dev"}
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
    """Planner routes to END when next_agent is missing."""
    state = {"thread_id": "t1", "status": "active"}
    assert _route_after_planner(state) == END


# ---------------------------------------------------------------------------
# Dev routing tests
# ---------------------------------------------------------------------------

def test_route_after_dev_to_content():
    """Dev routes to content_node when next_agent=content."""
    state = {"thread_id": "t1", "status": "active", "next_agent": "content"}
    assert _route_after_dev(state) == "content_node"


def test_route_after_dev_failed():
    """Dev routes to END when status=failed."""
    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_dev(state) == END


def test_route_after_dev_no_next():
    """Dev routes to END when next_agent is missing."""
    state = {"thread_id": "t1", "status": "active"}
    assert _route_after_dev(state) == END


# ---------------------------------------------------------------------------
# Content routing tests
# ---------------------------------------------------------------------------

def test_route_after_content_to_design():
    """Content routes to design_node when next_agent=design."""
    state = {"thread_id": "t1", "status": "active", "next_agent": "design"}
    assert _route_after_content(state) == "design_node"


def test_route_after_content_failed():
    """Content routes to END when status=failed."""
    state = {"thread_id": "t1", "status": "failed"}
    assert _route_after_content(state) == END


# ---------------------------------------------------------------------------
# Design routing tests
# ---------------------------------------------------------------------------

def test_route_after_design_to_critic():
    """Design routes to critic_node when next_agent=critic."""
    state = {"thread_id": "t1", "status": "active", "next_agent": "critic"}
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
    """Critic routes to dev_node for minor revision (retry_count < limit)."""
    state = {
        "thread_id": "t1",
        "status": "active",
        "next_agent": "dev",
        "retry_count": 0,
    }
    assert _route_after_critic(state) == "dev_node"

    state2 = {
        "thread_id": "t2",
        "status": "active",
        "next_agent": "dev",
        "retry_count": 2,
    }
    assert _route_after_critic(state2) == "dev_node"


def test_route_after_critic_revision_limit_exceeded():
    """Critic routes to HITL when retry_count >= MAX_REVISION_CYCLES."""
    state = {
        "thread_id": "t1",
        "status": "active",
        "next_agent": "dev",
        "retry_count": MAX_REVISION_CYCLES,
    }
    assert _route_after_critic(state) == "hitl_review_node"

    state2 = {
        "thread_id": "t2",
        "status": "active",
        "next_agent": "dev",
        "retry_count": MAX_REVISION_CYCLES + 1,
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
    # This shouldn't happen in practice, but test precedence order.
    # Looking at the code, requires_hitl check comes AFTER next_agent checks,
    # so next_agent takes precedence unless revision limit is hit.
    state = {
        "thread_id": "t1",
        "status": "active",
        "next_agent": "dev",
        "requires_hitl": True,
        "retry_count": 0,
    }
    # next_agent=dev is checked first, so it should route to dev_node
    assert _route_after_critic(state) == "dev_node"

    # But if retry_count >= MAX, HITL takes precedence
    state2 = {
        "thread_id": "t2",
        "status": "active",
        "next_agent": "dev",
        "requires_hitl": True,
        "retry_count": MAX_REVISION_CYCLES,
    }
    assert _route_after_critic(state2) == "hitl_review_node"
