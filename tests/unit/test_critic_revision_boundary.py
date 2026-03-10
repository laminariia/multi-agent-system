"""Boundary-condition tests for the Critic revision cycle routing.

Target: src/core/graph.py:621-642 — _route_after_critic revision logic.
Pattern: tests/unit/test_graph_routing.py — pure-function routing assertions.
"""

from __future__ import annotations

from typing import Any

from src.core.graph import MAX_REVISION_CYCLES, _route_after_critic


def _make_state(
    next_agent: str | None = None,
    status: str = "active",
    revision_count: int | None = None,
    requires_hitl: bool = False,
) -> dict[str, Any]:
    """Build a minimal state dict for _route_after_critic tests."""
    state: dict[str, Any] = {
        "thread_id": "t-critic",
        "status": status,
        "next_agent": next_agent,
        "requires_hitl": requires_hitl,
        "artifacts": {},
    }
    if revision_count is not None:
        state["artifacts"]["_critic_revision_count"] = revision_count
    return state


# ===== Revision count boundary (MAX_REVISION_CYCLES = 3) ====================


def test_count_0_routes_to_dev():
    """count=0 < 3 → dev_node (routing is pure, no state mutation)."""
    state = _make_state(next_agent="dev", revision_count=0)
    assert _route_after_critic(state) == "dev_node"
    # Routing is now pure — counter is NOT incremented by the routing function.
    assert state["artifacts"]["_critic_revision_count"] == 0


def test_count_1_routes_to_dev():
    """count=1 < 3 → dev_node (routing is pure, no state mutation)."""
    state = _make_state(next_agent="dev", revision_count=1)
    assert _route_after_critic(state) == "dev_node"
    assert state["artifacts"]["_critic_revision_count"] == 1


def test_count_2_routes_to_dev():
    """Boundary: count=2 is the LAST permitted revision (2 < 3)."""
    state = _make_state(next_agent="dev", revision_count=2)
    assert _route_after_critic(state) == "dev_node"
    assert state["artifacts"]["_critic_revision_count"] == 2


def test_count_3_escalates_to_hitl():
    """Boundary: count=3 → NOT < 3 → escalate to hitl_review_node."""
    state = _make_state(next_agent="dev", revision_count=3)
    assert _route_after_critic(state) == "hitl_review_node"
    assert MAX_REVISION_CYCLES == 3  # sanity check on the constant


def test_count_4_still_escalates():
    """Over-limit: count=4 → hitl_review_node (same as count=3)."""
    state = _make_state(next_agent="dev", revision_count=4)
    assert _route_after_critic(state) == "hitl_review_node"


def test_routing_does_not_mutate_artifacts():
    """Routing is now pure — it does NOT mutate state['artifacts']."""
    artifacts: dict[str, Any] = {"existing": "data", "_critic_revision_count": 0}
    state: dict[str, Any] = {
        "thread_id": "t-mutate",
        "status": "active",
        "next_agent": "dev",
        "artifacts": artifacts,
    }
    _route_after_critic(state)
    # Routing is pure — counter is unchanged
    assert artifacts["_critic_revision_count"] == 0
    assert artifacts is state["artifacts"]


def test_missing_count_defaults_zero():
    """No _critic_revision_count key → defaults to 0 → routes to dev_node."""
    state = _make_state(next_agent="dev")  # no revision_count
    assert _route_after_critic(state) == "dev_node"
    # Routing is pure — no key is added
    assert "_critic_revision_count" not in state["artifacts"]
