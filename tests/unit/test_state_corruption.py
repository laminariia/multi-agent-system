"""Tests for robustness of state handling against malformed / missing fields.

Target: src/core/state.py, routing functions in src/core/graph.py.
Pattern: tests/unit/test_graph_routing.py — minimal state dicts.
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END

from src.core.graph import (
    _route_after_critic,
    _route_after_scout,
)
from src.core.state import append_error, create_initial_state, update_state

# ---------------------------------------------------------------------------
# Routing resilience to missing / malformed fields
# ---------------------------------------------------------------------------


def test_routing_with_missing_status():
    """State with no 'status' key → routing does not crash."""
    state: dict[str, Any] = {"thread_id": "t1", "next_agent": "bid"}
    result = _route_after_scout(state)
    assert result == "bid_node"


def test_routing_with_missing_next_agent():
    """State with no 'next_agent' → routes to END (no crash)."""
    state: dict[str, Any] = {"thread_id": "t1", "status": "active"}
    result = _route_after_scout(state)
    assert result == END


def test_routing_with_none_artifacts():
    """artifacts=None → `or {}` fallback works in _route_after_critic."""
    state: dict[str, Any] = {
        "thread_id": "t1",
        "status": "active",
        "next_agent": "dev",
        "artifacts": None,
    }
    result = _route_after_critic(state)
    assert result == "dev_node"
    # Routing is pure — does not mutate state; artifacts stays None
    assert state["artifacts"] is None


def test_routing_with_artifacts_as_string():
    """artifacts='not a dict' → truthy, `or {}` does NOT trigger → AttributeError."""
    state: dict[str, Any] = {
        "thread_id": "t1",
        "status": "active",
        "next_agent": "dev",
        "artifacts": "not a dict",
    }
    with pytest.raises(AttributeError):
        _route_after_critic(state)


# ---------------------------------------------------------------------------
# State manipulation edge cases
# ---------------------------------------------------------------------------


def test_update_state_with_extra_keys(sample_state):
    """Extra keys (not in AgentState TypedDict) are preserved through update_state."""
    result = update_state(sample_state, custom_field="hello", another=42)
    assert result["custom_field"] == "hello"  # type: ignore[typeddict-item]
    assert result["another"] == 42  # type: ignore[typeddict-item]
    assert result["thread_id"] == sample_state["thread_id"]


def test_agent_invoke_with_missing_errors_key():
    """append_error with missing 'errors' key → KeyError."""
    state: dict[str, Any] = {"thread_id": "t1", "status": "active"}
    with pytest.raises(KeyError):
        append_error(state, "some error")  # type: ignore[arg-type]


def test_create_initial_state_has_all_required_fields(sample_project):
    """All 15 AgentState fields present in freshly created state."""
    state = create_initial_state(project=sample_project, first_agent="scout")
    required = {
        "thread_id",
        "mas_checkpoint_id",
        "project",
        "current_agent",
        "current_task",
        "artifacts",
        "messages",
        "next_agent",
        "requires_hitl",
        "hitl_request_id",
        "retry_count",
        "errors",
        "created_at",
        "updated_at",
        "status",
    }
    assert required.issubset(set(state.keys()))


def test_agent_returns_minimal_state_merge(sample_state):
    """Agent returning minimal overrides → update_state preserves the rest."""
    merged = update_state(sample_state, status="completed")
    assert merged["status"] == "completed"
    assert merged["thread_id"] == sample_state["thread_id"]
    assert merged["errors"] == sample_state["errors"]
    assert merged["project"] == sample_state["project"]
