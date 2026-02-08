"""Integration tests for the full multi-agent pipeline graph.

These tests exercise the graph routing logic by mocking every agent node
function to return predetermined states.  The tests verify that LangGraph
wires nodes correctly, conditional edges evaluate the right branch, and
the final state reflects the expected status, HITL flags, and artifacts.

All LLM calls and DB operations are mocked -- nothing hits real services.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

import pytest
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.core.state import AgentState, ProjectContext, create_initial_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NodeFn = Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]


def _make_project() -> ProjectContext:
    """Build a minimal ProjectContext for integration tests."""
    return ProjectContext(
        project_id="proj-integ-001",
        job_id="job-integ-001",
        platform="freelancer",
        client={"name": "Integration Client", "rating": 4.7, "reviews": 30, "hire_rate": 0.80},
        requirements="Build a responsive landing page with React and Tailwind CSS",
        budget=800.0,
        deadline=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )


def _make_initial_state(first_agent: str = "scout", thread_id: str | None = None) -> dict[str, Any]:
    """Build a fresh state dict for full-pipeline integration tests."""
    return dict(create_initial_state(
        project=_make_project(),
        first_agent=first_agent,
        thread_id=thread_id or f"thread-integ-{uuid.uuid4().hex[:8]}",
    ))


async def _passthrough(state: dict[str, Any]) -> dict[str, Any]:
    """Default passthrough node."""
    return state


def _build_pipeline_graph(
    nodes: dict[str, _NodeFn] | None = None,
) -> CompiledStateGraph:
    """Build a full pipeline graph, optionally with custom node functions.

    Args:
        nodes: A mapping of node_name -> async function. Any node not
            provided will use a passthrough that returns state unchanged.

    Returns:
        A compiled LangGraph ready for ``ainvoke``.
    """
    overrides = nodes or {}

    graph = StateGraph(dict)

    # Register nodes (use override if provided, otherwise passthrough)
    for name in (
        "scout_node", "bid_node", "hitl_bid_node", "planner_node",
        "dev_node", "content_node", "design_node", "critic_node",
        "packager_node", "hitl_review_node",
    ):
        graph.add_node(name, overrides.get(name, _passthrough))

    graph.set_entry_point("scout_node")

    # -- Routing functions -------------------------------------------------

    def route_after_scout(state: dict[str, Any]) -> str:
        if state.get("status") == "failed":
            return END
        if state.get("next_agent") == "bid":
            return "bid_node"
        return END

    def route_after_bid(state: dict[str, Any]) -> str:
        if state.get("status") == "failed":
            return END
        if state.get("requires_hitl"):
            return "hitl_bid_node"
        return END

    def route_after_hitl_bid(state: dict[str, Any]) -> str:
        if state.get("status") == "paused":
            return END
        if state.get("next_agent") == "planner":
            return "planner_node"
        return END

    def route_after_planner(state: dict[str, Any]) -> str:
        if state.get("status") == "failed":
            return END
        if state.get("next_agent") == "dev":
            return "dev_node"
        return END

    def route_after_dev(state: dict[str, Any]) -> str:
        if state.get("status") == "failed":
            return END
        if state.get("next_agent") == "content":
            return "content_node"
        return END

    def route_after_content(state: dict[str, Any]) -> str:
        if state.get("status") == "failed":
            return END
        if state.get("next_agent") == "design":
            return "design_node"
        return END

    def route_after_design(state: dict[str, Any]) -> str:
        if state.get("status") == "failed":
            return END
        if state.get("next_agent") == "critic":
            return "critic_node"
        return END

    def route_after_critic(state: dict[str, Any]) -> str:
        if state.get("status") == "failed":
            return END
        if state.get("requires_hitl"):
            return "hitl_review_node"
        if state.get("next_agent") == "packager":
            return "packager_node"
        if state.get("next_agent") == "dev":
            return "dev_node"
        return END

    def route_after_packager(state: dict[str, Any]) -> str:
        if state.get("requires_hitl"):
            return "hitl_review_node"
        return END

    def route_after_hitl_review(state: dict[str, Any]) -> str:
        return END

    # -- Conditional edges -------------------------------------------------

    graph.add_conditional_edges("scout_node", route_after_scout, {"bid_node": "bid_node", END: END})
    graph.add_conditional_edges("bid_node", route_after_bid, {"hitl_bid_node": "hitl_bid_node", END: END})
    graph.add_conditional_edges("hitl_bid_node", route_after_hitl_bid, {"planner_node": "planner_node", END: END})
    graph.add_conditional_edges("planner_node", route_after_planner, {"dev_node": "dev_node", END: END})
    graph.add_conditional_edges("dev_node", route_after_dev, {"content_node": "content_node", END: END})
    graph.add_conditional_edges("content_node", route_after_content, {"design_node": "design_node", END: END})
    graph.add_conditional_edges("design_node", route_after_design, {"critic_node": "critic_node", END: END})
    graph.add_conditional_edges(
        "critic_node",
        route_after_critic,
        {"packager_node": "packager_node", "hitl_review_node": "hitl_review_node", "dev_node": "dev_node", END: END},
    )
    graph.add_conditional_edges("packager_node", route_after_packager, {"hitl_review_node": "hitl_review_node", END: END})
    graph.add_conditional_edges("hitl_review_node", route_after_hitl_review, {END: END})

    return graph.compile()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_full_pipeline_scout_to_hitl_bid():
    """Full graph: Scout finds jobs -> Bid -> HITL bid approval pause."""

    async def mock_scout(state: dict[str, Any]) -> dict[str, Any]:
        return {
            **state,
            "next_agent": "bid",
            "current_agent": "scout",
            "status": "active",
            "artifacts": {"scout": ["job-id-1", "job-id-2"]},
        }

    async def mock_bid(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["bid"] = ["bid-id-1", "bid-id-2"]
        return {
            **state,
            "requires_hitl": True,
            "hitl_request_id": "hitl-bid-001",
            "status": "paused",
            "current_agent": "bid",
            "next_agent": None,
            "artifacts": artifacts,
        }

    async def mock_hitl_bid(state: dict[str, Any]) -> dict[str, Any]:
        return state

    graph = _build_pipeline_graph({
        "scout_node": mock_scout,
        "bid_node": mock_bid,
        "hitl_bid_node": mock_hitl_bid,
    })
    result = await graph.ainvoke(_make_initial_state())

    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["current_agent"] == "bid"
    assert result["next_agent"] is None
    assert "scout" in result["artifacts"]
    assert "bid" in result["artifacts"]
    assert len(result["artifacts"]["bid"]) == 2
    assert result["hitl_request_id"] == "hitl-bid-001"


async def test_full_pipeline_no_jobs_ends_early():
    """When Scout finds no jobs, the graph should terminate at the scout node."""

    bid_called = False

    async def mock_scout(state: dict[str, Any]) -> dict[str, Any]:
        return {
            **state,
            "next_agent": None,
            "current_agent": "scout",
            "status": "active",
            "artifacts": {},
        }

    async def mock_bid(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal bid_called
        bid_called = True
        return state

    graph = _build_pipeline_graph({
        "scout_node": mock_scout,
        "bid_node": mock_bid,
    })
    result = await graph.ainvoke(_make_initial_state())

    assert result["status"] == "active"
    assert result["next_agent"] is None
    assert result["requires_hitl"] is False
    assert bid_called is False, "Bid node should NOT have been called"


async def test_pipeline_planner_to_packager_happy_path():
    """Happy path: Scout -> Bid -> HITL(approve) -> Planner -> Dev -> Content -> Design -> Critic(APPROVE) -> Packager -> HITL review."""

    plan_json = json.dumps({"tasks": [{"id": "t1", "title": "Create landing page"}]})
    dev_json = json.dumps({"files": [{"path": "index.html", "content": "<h1>Hello</h1>"}]})
    content_json = json.dumps({"deliverables": [{"type": "heading", "content": "Welcome"}]})
    design_json = json.dumps({"specs": [{"component": "hero", "colors": {"primary": "#3B82F6"}}]})
    critic_json = json.dumps({"verdict": "APPROVE", "score": 0.92})
    packager_json = json.dumps({"archive_url": "https://storage.example.com/delivery.zip"})

    async def mock_scout(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "next_agent": "bid", "current_agent": "scout", "status": "active"}

    async def mock_bid(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "requires_hitl": True, "status": "paused", "current_agent": "bid", "next_agent": None}

    async def mock_hitl_bid(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "requires_hitl": False, "status": "active", "next_agent": "planner", "current_agent": "bid"}

    async def mock_planner(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["planner"] = [plan_json]
        return {**state, "next_agent": "dev", "current_agent": "planner", "status": "active", "artifacts": artifacts}

    async def mock_dev(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["dev"] = [dev_json]
        return {**state, "next_agent": "content", "current_agent": "dev", "status": "active", "artifacts": artifacts}

    async def mock_content(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["content"] = [content_json]
        return {**state, "next_agent": "design", "current_agent": "content", "status": "active", "artifacts": artifacts}

    async def mock_design(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["design"] = [design_json]
        return {**state, "next_agent": "critic", "current_agent": "design", "status": "active", "artifacts": artifacts}

    async def mock_critic(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["critic"] = [critic_json]
        return {**state, "next_agent": "packager", "current_agent": "critic", "status": "active", "requires_hitl": False, "artifacts": artifacts}

    async def mock_packager(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["packager"] = [packager_json]
        return {**state, "requires_hitl": True, "hitl_request_id": "hitl-review-final-001", "status": "paused", "current_agent": "packager", "next_agent": None, "artifacts": artifacts}

    graph = _build_pipeline_graph({
        "scout_node": mock_scout,
        "bid_node": mock_bid,
        "hitl_bid_node": mock_hitl_bid,
        "planner_node": mock_planner,
        "dev_node": mock_dev,
        "content_node": mock_content,
        "design_node": mock_design,
        "critic_node": mock_critic,
        "packager_node": mock_packager,
    })
    initial_state = _make_initial_state()
    initial_state["artifacts"] = {"scout": ["job-id-1"], "bid": ["bid-id-1"]}

    result = await graph.ainvoke(initial_state)

    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["current_agent"] == "packager"
    assert result["hitl_request_id"] == "hitl-review-final-001"

    for agent_name in ("planner", "dev", "content", "design", "critic", "packager"):
        assert agent_name in result["artifacts"], f"Missing artifacts from {agent_name}"


async def test_pipeline_critic_revision_loop():
    """Critic returns REVISE -> Dev -> Content -> Design -> Critic(APPROVE) -> Packager."""

    call_counts: dict[str, int] = {"dev": 0, "content": 0, "design": 0, "critic": 0}

    plan_json = json.dumps({"tasks": [{"id": "t1", "title": "Build page"}]})
    dev_json = json.dumps({"files": [{"path": "index.html", "content": "<h1>Hello</h1>"}]})
    content_json = json.dumps({"deliverables": [{"type": "heading", "content": "Welcome"}]})
    design_json = json.dumps({"specs": [{"component": "hero"}]})
    critic_revise_json = json.dumps({"verdict": "REVISE", "score": 0.68})
    critic_approve_json = json.dumps({"verdict": "APPROVE", "score": 0.91})
    packager_json = json.dumps({"archive_url": "https://storage.example.com/delivery.zip"})

    async def mock_scout(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "next_agent": "bid", "current_agent": "scout", "status": "active"}

    async def mock_bid(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "requires_hitl": True, "status": "paused", "current_agent": "bid", "next_agent": None}

    async def mock_hitl_bid(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "requires_hitl": False, "status": "active", "next_agent": "planner", "current_agent": "bid"}

    async def mock_planner(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["planner"] = [plan_json]
        return {**state, "next_agent": "dev", "current_agent": "planner", "status": "active", "artifacts": artifacts}

    async def mock_dev(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["dev"] += 1
        artifacts = dict(state.get("artifacts") or {})
        artifacts["dev"] = [dev_json]
        return {**state, "next_agent": "content", "current_agent": "dev", "status": "active", "artifacts": artifacts}

    async def mock_content(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["content"] += 1
        artifacts = dict(state.get("artifacts") or {})
        artifacts["content"] = [content_json]
        return {**state, "next_agent": "design", "current_agent": "content", "status": "active", "artifacts": artifacts}

    async def mock_design(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["design"] += 1
        artifacts = dict(state.get("artifacts") or {})
        artifacts["design"] = [design_json]
        return {**state, "next_agent": "critic", "current_agent": "design", "status": "active", "artifacts": artifacts}

    async def mock_critic(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["critic"] += 1
        artifacts = dict(state.get("artifacts") or {})
        if call_counts["critic"] == 1:
            artifacts["critic"] = [critic_revise_json]
            return {**state, "next_agent": "dev", "current_agent": "critic", "status": "active", "requires_hitl": False, "artifacts": artifacts}
        artifacts["critic"] = [critic_approve_json]
        return {**state, "next_agent": "packager", "current_agent": "critic", "status": "active", "requires_hitl": False, "artifacts": artifacts}

    async def mock_packager(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["packager"] = [packager_json]
        return {**state, "requires_hitl": True, "status": "paused", "current_agent": "packager", "next_agent": None, "artifacts": artifacts}

    graph = _build_pipeline_graph({
        "scout_node": mock_scout,
        "bid_node": mock_bid,
        "hitl_bid_node": mock_hitl_bid,
        "planner_node": mock_planner,
        "dev_node": mock_dev,
        "content_node": mock_content,
        "design_node": mock_design,
        "critic_node": mock_critic,
        "packager_node": mock_packager,
    })

    initial_state = _make_initial_state()
    initial_state["artifacts"] = {"scout": ["job-id-1"], "bid": ["bid-id-1"]}

    result = await graph.ainvoke(initial_state)

    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["current_agent"] == "packager"

    assert call_counts["dev"] == 2, f"Dev should be called 2x, was {call_counts['dev']}x"
    assert call_counts["content"] == 2, f"Content should be called 2x, was {call_counts['content']}x"
    assert call_counts["design"] == 2, f"Design should be called 2x, was {call_counts['design']}x"
    assert call_counts["critic"] == 2, f"Critic should be called 2x, was {call_counts['critic']}x"

    for agent_name in ("planner", "dev", "content", "design", "critic", "packager"):
        assert agent_name in result["artifacts"], f"Missing artifacts from {agent_name}"


async def test_pipeline_critic_reject_to_hitl():
    """Critic returns REJECT -> routes to HITL review node."""

    packager_called = False
    hitl_review_called = False

    critic_reject_json = json.dumps({"verdict": "REJECT", "score": 0.35, "issues": [{"desc": "Critical bugs"}]})

    async def mock_scout(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "next_agent": "bid", "current_agent": "scout", "status": "active"}

    async def mock_bid(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "requires_hitl": True, "status": "paused", "current_agent": "bid", "next_agent": None}

    async def mock_hitl_bid(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "requires_hitl": False, "status": "active", "next_agent": "planner", "current_agent": "bid"}

    async def mock_planner(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "next_agent": "dev", "current_agent": "planner", "status": "active"}

    async def mock_dev(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "next_agent": "content", "current_agent": "dev", "status": "active"}

    async def mock_content(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "next_agent": "design", "current_agent": "content", "status": "active"}

    async def mock_design(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "next_agent": "critic", "current_agent": "design", "status": "active"}

    async def mock_critic(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["critic"] = [critic_reject_json]
        return {**state, "next_agent": None, "current_agent": "critic", "status": "paused", "requires_hitl": True, "hitl_request_id": "hitl-critic-reject-001", "artifacts": artifacts}

    async def mock_packager(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal packager_called
        packager_called = True
        return state

    async def mock_hitl_review(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal hitl_review_called
        hitl_review_called = True
        return state

    graph = _build_pipeline_graph({
        "scout_node": mock_scout,
        "bid_node": mock_bid,
        "hitl_bid_node": mock_hitl_bid,
        "planner_node": mock_planner,
        "dev_node": mock_dev,
        "content_node": mock_content,
        "design_node": mock_design,
        "critic_node": mock_critic,
        "packager_node": mock_packager,
        "hitl_review_node": mock_hitl_review,
    })

    result = await graph.ainvoke(_make_initial_state())

    assert packager_called is False, "Packager should NOT be called when Critic rejects"
    assert hitl_review_called is True, "HITL review node should be called when Critic rejects"
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["hitl_request_id"] == "hitl-critic-reject-001"


async def test_pipeline_scout_failure_ends_graph():
    """When Scout fails, the graph should terminate immediately."""

    bid_called = False

    async def mock_scout(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "status": "failed", "current_agent": "scout", "next_agent": None, "errors": ["All adapters failed"]}

    async def mock_bid(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal bid_called
        bid_called = True
        return state

    graph = _build_pipeline_graph({
        "scout_node": mock_scout,
        "bid_node": mock_bid,
    })
    result = await graph.ainvoke(_make_initial_state())

    assert result["status"] == "failed"
    assert bid_called is False
    assert "All adapters failed" in result["errors"]


async def test_pipeline_artifacts_accumulate_across_agents():
    """Artifacts produced by each agent are preserved through the full pipeline."""

    async def mock_scout(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["scout"] = ["job-1"]
        return {**state, "next_agent": "bid", "current_agent": "scout", "status": "active", "artifacts": artifacts}

    async def mock_bid(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["bid"] = ["bid-1"]
        return {**state, "requires_hitl": True, "status": "paused", "current_agent": "bid", "next_agent": None, "artifacts": artifacts}

    async def mock_hitl_bid(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "requires_hitl": False, "status": "active", "next_agent": "planner"}

    async def mock_planner(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["planner"] = ["plan-1"]
        return {**state, "next_agent": "dev", "current_agent": "planner", "status": "active", "artifacts": artifacts}

    async def mock_dev(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["dev"] = ["code-1"]
        return {**state, "next_agent": "content", "current_agent": "dev", "status": "active", "artifacts": artifacts}

    async def mock_content(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["content"] = ["text-1"]
        return {**state, "next_agent": "design", "current_agent": "content", "status": "active", "artifacts": artifacts}

    async def mock_design(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["design"] = ["design-1"]
        return {**state, "next_agent": "critic", "current_agent": "design", "status": "active", "artifacts": artifacts}

    async def mock_critic(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["critic"] = ["review-1"]
        return {**state, "next_agent": "packager", "current_agent": "critic", "status": "active", "requires_hitl": False, "artifacts": artifacts}

    async def mock_packager(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["packager"] = ["package-1"]
        return {**state, "status": "completed", "current_agent": "packager", "next_agent": None, "requires_hitl": False, "artifacts": artifacts}

    graph = _build_pipeline_graph({
        "scout_node": mock_scout,
        "bid_node": mock_bid,
        "hitl_bid_node": mock_hitl_bid,
        "planner_node": mock_planner,
        "dev_node": mock_dev,
        "content_node": mock_content,
        "design_node": mock_design,
        "critic_node": mock_critic,
        "packager_node": mock_packager,
    })
    result = await graph.ainvoke(_make_initial_state())

    expected_agents = ["scout", "bid", "planner", "dev", "content", "design", "critic", "packager"]
    for agent_name in expected_agents:
        assert agent_name in result["artifacts"], (
            f"Artifacts from '{agent_name}' missing. Present: {list(result['artifacts'].keys())}"
        )
