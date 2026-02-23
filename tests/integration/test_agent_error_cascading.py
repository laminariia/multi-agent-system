"""Integration tests for error cascading between agents in the pipeline graph.

Target: src/core/graph.py — graph builders + routing functions.
Pattern: tests/integration/test_full_pipeline.py — mocked node functions in StateGraph(dict).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from langgraph.graph import END, StateGraph

from src.core.graph import (
    _route_after_bid,
    _route_after_critic,
    _route_after_scout,
)
from src.core.state import ProjectContext, create_initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project() -> ProjectContext:
    return ProjectContext(
        project_id="proj-cascade",
        job_id="job-cascade",
        platform="freelancer",
        client={"name": "Cascade Client", "rating": 4.5, "reviews": 10, "hire_rate": 0.7},
        requirements="Build a REST API",
        budget=600.0,
        deadline=datetime(2026, 6, 1, tzinfo=UTC),
    )


def _make_state(**overrides: Any) -> dict[str, Any]:
    state = dict(
        create_initial_state(
            project=_make_project(),
            first_agent="scout",
            thread_id=f"thread-cascade-{uuid.uuid4().hex[:8]}",
        )
    )
    state.update(overrides)
    return state


async def _passthrough(state: dict[str, Any]) -> dict[str, Any]:
    return state


def _build_scout_bid_graph(
    scout_fn=None,
    bid_fn=None,
) -> StateGraph:
    """Minimal scout → bid graph for error cascade tests."""
    graph = StateGraph(dict)
    graph.add_node("scout_node", scout_fn or _passthrough)
    graph.add_node("bid_node", bid_fn or _passthrough)

    graph.set_entry_point("scout_node")
    graph.add_conditional_edges(
        "scout_node",
        _route_after_scout,
        {
            "bid_node": "bid_node",
            END: END,
        },
    )
    graph.add_edge("bid_node", END)

    return graph.compile()


# ===== P3-1: Scout returns wrong status type ================================


def test_scout_returns_wrong_status_type():
    """status=123 (int) → routing treats as truthy but != 'failed', proceeds."""
    state: dict[str, Any] = {
        "thread_id": "t1",
        "status": 123,
        "next_agent": "bid",
    }
    result = _route_after_scout(state)
    # 123 != "failed", so it checks next_agent → "bid_node"
    assert result == "bid_node"


# ===== P3-2: Agent returns None as state ====================================


async def test_agent_returns_none_as_state():
    """Node returning None → LangGraph TypeError or similar error."""

    async def return_none(state: dict[str, Any]) -> None:
        return None  # noqa: RET501

    graph = StateGraph(dict)
    graph.add_node("scout_node", return_none)
    graph.set_entry_point("scout_node")
    graph.add_edge("scout_node", END)
    compiled = graph.compile()

    state = _make_state()

    # LangGraph should handle None return (either error or treat as empty update)
    try:
        result = await compiled.ainvoke(state)
        # If LangGraph accepts None, state passes through unchanged
        assert result["thread_id"] == state["thread_id"]
    except (TypeError, ValueError):
        # Expected — LangGraph rejects None state
        pass


# ===== P3-3: Bid sets invalid next_agent ====================================


def test_bid_sets_invalid_next_agent():
    """next_agent='nonexistent' → _route_after_bid falls through to END."""
    state: dict[str, Any] = {
        "thread_id": "t1",
        "status": "active",
        "next_agent": "nonexistent",
        "requires_hitl": False,
    }
    # _route_after_bid does NOT check next_agent — only requires_hitl
    result = _route_after_bid(state)
    assert result == END


# ===== P3-4: Artifacts wrong type (string) ==================================


def test_artifacts_wrong_type_string_in_critic():
    """artifacts='string' → truthy, `or {}` does NOT trigger → AttributeError."""
    state: dict[str, Any] = {
        "thread_id": "t1",
        "status": "active",
        "next_agent": "dev",
        "artifacts": "not a dict",
    }
    # String is truthy, so `or {}` won't fire → .get() raises AttributeError
    with pytest.raises(AttributeError):
        _route_after_critic(state)


# ===== P3-5: Unhandled exception in node ====================================


async def test_unhandled_exception_in_node():
    """Node raises unhandled Exception → graph propagates it to caller."""

    async def explode(state: dict[str, Any]) -> dict[str, Any]:
        msg = "kaboom"
        raise RuntimeError(msg)

    graph = StateGraph(dict)
    graph.add_node("scout_node", explode)
    graph.set_entry_point("scout_node")
    graph.add_edge("scout_node", END)
    compiled = graph.compile()

    with pytest.raises(RuntimeError, match="kaboom"):
        await compiled.ainvoke(_make_state())


# ===== P3-6: Extra state fields preserved through routing ====================


async def test_extra_state_fields_preserved():
    """Custom state fields survive full graph routing without loss."""

    async def add_custom(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "custom_flag": True, "next_agent": "bid"}

    async def read_custom(state: dict[str, Any]) -> dict[str, Any]:
        assert state.get("custom_flag") is True
        return state

    compiled = _build_scout_bid_graph(scout_fn=add_custom, bid_fn=read_custom)
    result = await compiled.ainvoke(_make_state())
    assert result.get("custom_flag") is True
