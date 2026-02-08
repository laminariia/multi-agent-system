"""End-to-end tests for the full multi-agent pipeline graph.

These tests are more comprehensive than the integration tests: they exercise
the entire graph flow from Scout to delivery/HITL, including revision loops,
artifact accumulation, and edge cases like empty results and bid rejection.

All LLM calls are mocked via node-level mock functions.  The graph routing
logic (conditional edges) is the real LangGraph implementation.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.core.state import ProjectContext, create_initial_state

# ---------------------------------------------------------------------------
# Helpers (copied from integration tests -- intentionally NOT imported)
# ---------------------------------------------------------------------------

_NodeFn = Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]


def _make_project() -> ProjectContext:
    """Build a minimal ProjectContext for E2E tests."""
    return ProjectContext(
        project_id="e2e-proj-001",
        job_id="e2e-job-001",
        platform="freelancer",
        client={"name": "E2E Test Client", "rating": 4.9, "reviews": 50, "hire_rate": 0.90},
        requirements=(
            "Build a responsive landing page with React and Tailwind CSS. "
            "Include hero section, features grid, testimonials, and contact form."
        ),
        budget=750.0,
        deadline=datetime(2026, 6, 1, tzinfo=UTC),
    )


def _make_initial_state(first_agent: str = "scout", thread_id: str | None = None) -> dict[str, Any]:
    """Build a fresh state dict for E2E tests."""
    return dict(create_initial_state(
        project=_make_project(),
        first_agent=first_agent,
        thread_id=thread_id or f"thread-e2e-{uuid.uuid4().hex[:8]}",
    ))


async def _passthrough(state: dict[str, Any]) -> dict[str, Any]:
    """Default passthrough node -- returns state unchanged."""
    return state


def _build_pipeline_graph(nodes: dict[str, _NodeFn] | None = None) -> CompiledStateGraph:
    """Build a full pipeline graph with optional custom node functions.

    Any node not explicitly provided will use a passthrough that returns
    state unchanged.
    """
    overrides = nodes or {}

    graph = StateGraph(dict)

    for name in (
        "scout_node", "bid_node", "hitl_bid_node", "planner_node",
        "dev_node", "content_node", "design_node", "critic_node",
        "packager_node", "hitl_review_node",
    ):
        graph.add_node(name, overrides.get(name, _passthrough))

    graph.set_entry_point("scout_node")

    # -- Routing functions -----------------------------------------------------

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

    # -- Conditional edges -----------------------------------------------------

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
    graph.add_conditional_edges(
        "packager_node", route_after_packager, {"hitl_review_node": "hitl_review_node", END: END},
    )
    graph.add_conditional_edges("hitl_review_node", route_after_hitl_review, {END: END})

    return graph.compile()


# ---------------------------------------------------------------------------
# JSON payloads used across tests
# ---------------------------------------------------------------------------

_SCOUT_ARTIFACTS = ["job-e2e-1"]
_BID_ARTIFACTS = ["bid-e2e-1"]
_PLAN_JSON = json.dumps({
    "tasks": [
        {"id": "t1", "title": "Create React project structure", "type": "code", "estimated_hours": 1},
        {"id": "t2", "title": "Build hero component", "type": "code", "estimated_hours": 2},
        {"id": "t3", "title": "Style with Tailwind CSS", "type": "code", "estimated_hours": 1},
    ],
    "total_estimated_hours": 4,
    "phases": [{"name": "Build", "tasks": ["t1", "t2", "t3"]}],
})
_DEV_JSON = json.dumps({
    "files": [
        {"path": "src/App.tsx", "content": "export default function App() { return <div>Hello</div> }"},
        {"path": "src/components/Hero.tsx", "content": "<section>Hero</section>"},
    ],
})
_CONTENT_JSON = json.dumps({
    "deliverables": [
        {"type": "heading", "content": "Welcome to our platform"},
        {"type": "body", "content": "We provide the best solutions..."},
    ],
})
_DESIGN_JSON = json.dumps({
    "specs": [
        {"component": "hero", "colors": {"primary": "#3B82F6", "secondary": "#1E40AF"}},
        {"component": "features", "layout": "grid-3-cols"},
    ],
})
_CRITIC_APPROVE_JSON = json.dumps({"verdict": "APPROVE", "score": 0.91, "feedback": "Good quality work."})
_CRITIC_REVISE_JSON = json.dumps({
    "verdict": "REVISE",
    "score": 0.65,
    "feedback": "Hero section needs improvement.",
    "issues": [{"severity": "medium", "description": "Missing responsive breakpoints"}],
})
_PACKAGER_JSON = json.dumps({
    "archive_url": "https://storage.example.com/e2e-delivery.zip",
    "deliverables": ["src/App.tsx", "src/components/Hero.tsx"],
})

# All 8 agent names that produce artifacts in the happy path.
_ALL_AGENT_NAMES = ("scout", "bid", "planner", "dev", "content", "design", "critic", "packager")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_e2e_happy_path_scout_to_delivery():
    """Full happy path: Scout -> Bid -> HITL(approve) -> Planner -> Dev ->
    Content -> Design -> Critic(APPROVE) -> Packager -> HITL review.

    Verifies all 8 agent artifacts exist and correct status transitions.
    """

    async def mock_scout(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["scout"] = _SCOUT_ARTIFACTS
        return {**state, "next_agent": "bid", "current_agent": "scout", "status": "active", "artifacts": artifacts}

    async def mock_bid(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["bid"] = _BID_ARTIFACTS
        return {
            **state,
            "requires_hitl": True,
            "hitl_request_id": "hitl-bid-e2e-001",
            "status": "paused",
            "current_agent": "bid",
            "next_agent": None,
            "artifacts": artifacts,
        }

    async def mock_hitl_bid(state: dict[str, Any]) -> dict[str, Any]:
        return {
            **state,
            "requires_hitl": False,
            "hitl_request_id": None,
            "status": "active",
            "next_agent": "planner",
            "current_agent": "bid",
        }

    async def mock_planner(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["planner"] = [_PLAN_JSON]
        return {**state, "next_agent": "dev", "current_agent": "planner", "status": "active", "artifacts": artifacts}

    async def mock_dev(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["dev"] = [_DEV_JSON]
        return {**state, "next_agent": "content", "current_agent": "dev", "status": "active", "artifacts": artifacts}

    async def mock_content(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["content"] = [_CONTENT_JSON]
        return {
            **state, "next_agent": "design", "current_agent": "content", "status": "active", "artifacts": artifacts,
        }

    async def mock_design(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["design"] = [_DESIGN_JSON]
        return {**state, "next_agent": "critic", "current_agent": "design", "status": "active", "artifacts": artifacts}

    async def mock_critic(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["critic"] = [_CRITIC_APPROVE_JSON]
        return {
            **state,
            "next_agent": "packager",
            "current_agent": "critic",
            "status": "active",
            "requires_hitl": False,
            "artifacts": artifacts,
        }

    async def mock_packager(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["packager"] = [_PACKAGER_JSON]
        return {
            **state,
            "requires_hitl": True,
            "hitl_request_id": "hitl-review-e2e-001",
            "status": "paused",
            "current_agent": "packager",
            "next_agent": None,
            "artifacts": artifacts,
        }

    async def mock_hitl_review(state: dict[str, Any]) -> dict[str, Any]:
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

    # Status assertions
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["hitl_request_id"] == "hitl-review-e2e-001"
    assert result["current_agent"] == "packager"

    # All 8 agents produced artifacts
    for agent_name in _ALL_AGENT_NAMES:
        assert agent_name in result["artifacts"], (
            f"Missing artifacts from '{agent_name}'. Present: {list(result['artifacts'].keys())}"
        )

    # Verify artifact content integrity
    assert result["artifacts"]["scout"] == _SCOUT_ARTIFACTS
    assert result["artifacts"]["bid"] == _BID_ARTIFACTS
    assert json.loads(result["artifacts"]["planner"][0])["total_estimated_hours"] == 4
    assert json.loads(result["artifacts"]["critic"][0])["verdict"] == "APPROVE"
    assert json.loads(result["artifacts"]["packager"][0])["archive_url"] == "https://storage.example.com/e2e-delivery.zip"


async def test_e2e_critic_revision_then_approve():
    """Critic revises once then approves: Scout -> Bid -> HITL -> Planner ->
    Dev -> Content -> Design -> Critic(REVISE) -> Dev(retry) -> Content ->
    Design -> Critic(APPROVE) -> Packager -> HITL review.

    Verifies revision count is tracked and dev is called 2x.
    """
    call_counts: dict[str, int] = {"dev": 0, "content": 0, "design": 0, "critic": 0}

    async def mock_scout(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["scout"] = _SCOUT_ARTIFACTS
        return {**state, "next_agent": "bid", "current_agent": "scout", "status": "active", "artifacts": artifacts}

    async def mock_bid(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["bid"] = _BID_ARTIFACTS
        return {
            **state, "requires_hitl": True, "status": "paused", "current_agent": "bid",
            "next_agent": None, "artifacts": artifacts,
        }

    async def mock_hitl_bid(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "requires_hitl": False, "status": "active", "next_agent": "planner", "current_agent": "bid"}

    async def mock_planner(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["planner"] = [_PLAN_JSON]
        return {**state, "next_agent": "dev", "current_agent": "planner", "status": "active", "artifacts": artifacts}

    async def mock_dev(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["dev"] += 1
        artifacts = dict(state.get("artifacts") or {})
        artifacts["dev"] = [_DEV_JSON]
        return {**state, "next_agent": "content", "current_agent": "dev", "status": "active", "artifacts": artifacts}

    async def mock_content(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["content"] += 1
        artifacts = dict(state.get("artifacts") or {})
        artifacts["content"] = [_CONTENT_JSON]
        return {
            **state, "next_agent": "design", "current_agent": "content", "status": "active", "artifacts": artifacts,
        }

    async def mock_design(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["design"] += 1
        artifacts = dict(state.get("artifacts") or {})
        artifacts["design"] = [_DESIGN_JSON]
        return {**state, "next_agent": "critic", "current_agent": "design", "status": "active", "artifacts": artifacts}

    async def mock_critic(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["critic"] += 1
        artifacts = dict(state.get("artifacts") or {})
        if call_counts["critic"] == 1:
            # First call: REVISE
            artifacts["critic"] = [_CRITIC_REVISE_JSON]
            return {
                **state, "next_agent": "dev", "current_agent": "critic", "status": "active",
                "requires_hitl": False, "artifacts": artifacts,
            }
        # Second call: APPROVE
        artifacts["critic"] = [_CRITIC_APPROVE_JSON]
        return {
            **state, "next_agent": "packager", "current_agent": "critic", "status": "active",
            "requires_hitl": False, "artifacts": artifacts,
        }

    async def mock_packager(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["packager"] = [_PACKAGER_JSON]
        return {
            **state, "requires_hitl": True, "hitl_request_id": "hitl-review-e2e-002",
            "status": "paused", "current_agent": "packager", "next_agent": None, "artifacts": artifacts,
        }

    async def mock_hitl_review(state: dict[str, Any]) -> dict[str, Any]:
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

    # Final state
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["current_agent"] == "packager"

    # Call counts verify the revision loop
    assert call_counts["dev"] == 2, f"Dev should be called 2x, was {call_counts['dev']}x"
    assert call_counts["content"] == 2, f"Content should be called 2x, was {call_counts['content']}x"
    assert call_counts["design"] == 2, f"Design should be called 2x, was {call_counts['design']}x"
    assert call_counts["critic"] == 2, f"Critic should be called 2x, was {call_counts['critic']}x"

    # Final critic verdict should be APPROVE
    assert json.loads(result["artifacts"]["critic"][0])["verdict"] == "APPROVE"

    # All agents still have artifacts
    for agent_name in _ALL_AGENT_NAMES:
        assert agent_name in result["artifacts"], f"Missing artifacts from '{agent_name}'"


async def test_e2e_hitl_bid_rejection_ends_pipeline():
    """Bid rejection: Scout -> Bid -> HITL(reject) -- graph ends at hitl_bid
    with status='paused', no planner/dev called.
    """
    planner_called = False
    dev_called = False

    async def mock_scout(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["scout"] = _SCOUT_ARTIFACTS
        return {**state, "next_agent": "bid", "current_agent": "scout", "status": "active", "artifacts": artifacts}

    async def mock_bid(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["bid"] = _BID_ARTIFACTS
        return {
            **state, "requires_hitl": True, "hitl_request_id": "hitl-bid-e2e-reject",
            "status": "paused", "current_agent": "bid", "next_agent": None, "artifacts": artifacts,
        }

    async def mock_hitl_bid(state: dict[str, Any]) -> dict[str, Any]:
        # Simulate rejection: status stays "paused", no next_agent
        return {**state, "requires_hitl": False, "status": "paused", "next_agent": None, "current_agent": "hitl_bid"}

    async def mock_planner(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal planner_called
        planner_called = True
        return state

    async def mock_dev(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal dev_called
        dev_called = True
        return state

    graph = _build_pipeline_graph({
        "scout_node": mock_scout,
        "bid_node": mock_bid,
        "hitl_bid_node": mock_hitl_bid,
        "planner_node": mock_planner,
        "dev_node": mock_dev,
    })
    result = await graph.ainvoke(_make_initial_state())

    assert result["status"] == "paused"
    assert result["current_agent"] == "hitl_bid"
    assert planner_called is False, "Planner should NOT be called after bid rejection"
    assert dev_called is False, "Dev should NOT be called after bid rejection"

    # Only scout and bid artifacts should be present
    assert "scout" in result["artifacts"]
    assert "bid" in result["artifacts"]
    assert "planner" not in result["artifacts"]
    assert "dev" not in result["artifacts"]


async def test_e2e_scout_empty_results_no_bid():
    """Scout returns empty results -- graph ends, bid never called."""
    bid_called = False
    planner_called = False

    async def mock_scout(state: dict[str, Any]) -> dict[str, Any]:
        return {
            **state, "next_agent": None, "current_agent": "scout",
            "status": "active", "artifacts": {},
        }

    async def mock_bid(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal bid_called
        bid_called = True
        return state

    async def mock_planner(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal planner_called
        planner_called = True
        return state

    graph = _build_pipeline_graph({
        "scout_node": mock_scout,
        "bid_node": mock_bid,
        "planner_node": mock_planner,
    })
    result = await graph.ainvoke(_make_initial_state())

    assert result["status"] == "active"
    assert result["next_agent"] is None
    assert result["current_agent"] == "scout"
    assert bid_called is False, "Bid node should NOT have been called when Scout finds no jobs"
    assert planner_called is False, "Planner node should NOT have been called when Scout finds no jobs"
    assert result["requires_hitl"] is False


async def test_e2e_all_artifacts_preserved_through_revision():
    """After a revision loop, verify ALL artifacts from ALL agents are still
    present in the final state.  This guards against state overwrites during
    the Dev -> Content -> Design -> Critic cycle.
    """

    async def mock_scout(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["scout"] = _SCOUT_ARTIFACTS
        return {**state, "next_agent": "bid", "current_agent": "scout", "status": "active", "artifacts": artifacts}

    async def mock_bid(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["bid"] = _BID_ARTIFACTS
        return {
            **state, "requires_hitl": True, "status": "paused", "current_agent": "bid",
            "next_agent": None, "artifacts": artifacts,
        }

    async def mock_hitl_bid(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "requires_hitl": False, "status": "active", "next_agent": "planner", "current_agent": "bid"}

    async def mock_planner(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["planner"] = [_PLAN_JSON]
        return {**state, "next_agent": "dev", "current_agent": "planner", "status": "active", "artifacts": artifacts}

    revision_count = {"value": 0}

    async def mock_dev(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        # Append revision index to track which call produced the artifact
        dev_payload = json.dumps({"files": [{"path": "src/App.tsx", "revision": revision_count["value"]}]})
        artifacts["dev"] = [dev_payload]
        return {**state, "next_agent": "content", "current_agent": "dev", "status": "active", "artifacts": artifacts}

    async def mock_content(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["content"] = [_CONTENT_JSON]
        return {
            **state, "next_agent": "design", "current_agent": "content", "status": "active", "artifacts": artifacts,
        }

    async def mock_design(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["design"] = [_DESIGN_JSON]
        return {**state, "next_agent": "critic", "current_agent": "design", "status": "active", "artifacts": artifacts}

    async def mock_critic(state: dict[str, Any]) -> dict[str, Any]:
        revision_count["value"] += 1
        artifacts = dict(state.get("artifacts") or {})
        if revision_count["value"] == 1:
            artifacts["critic"] = [_CRITIC_REVISE_JSON]
            return {
                **state, "next_agent": "dev", "current_agent": "critic", "status": "active",
                "requires_hitl": False, "artifacts": artifacts,
            }
        artifacts["critic"] = [_CRITIC_APPROVE_JSON]
        return {
            **state, "next_agent": "packager", "current_agent": "critic", "status": "active",
            "requires_hitl": False, "artifacts": artifacts,
        }

    async def mock_packager(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["packager"] = [_PACKAGER_JSON]
        return {
            **state, "requires_hitl": True, "status": "paused", "current_agent": "packager",
            "next_agent": None, "artifacts": artifacts,
        }

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

    # ALL eight agents must have artifacts in the final state
    for agent_name in _ALL_AGENT_NAMES:
        assert agent_name in result["artifacts"], (
            f"Artifacts from '{agent_name}' missing after revision loop. "
            f"Present: {list(result['artifacts'].keys())}"
        )

    # Early pipeline artifacts (scout, bid, planner) must survive the revision loop
    assert result["artifacts"]["scout"] == _SCOUT_ARTIFACTS
    assert result["artifacts"]["bid"] == _BID_ARTIFACTS
    assert json.loads(result["artifacts"]["planner"][0])["total_estimated_hours"] == 4

    # Dev artifact should reflect the LATEST revision (revision index 1)
    dev_data = json.loads(result["artifacts"]["dev"][0])
    assert dev_data["files"][0]["revision"] == 1, (
        f"Dev artifact should reflect the second revision (index 1), got {dev_data['files'][0]['revision']}"
    )

    # Critic final verdict should be APPROVE
    assert json.loads(result["artifacts"]["critic"][0])["verdict"] == "APPROVE"


async def test_e2e_multiple_revision_cycles():
    """Critic revises 2 times before approving.  Dev/Content/Design called
    3x each, Critic called 3x.
    """
    call_counts: dict[str, int] = {"dev": 0, "content": 0, "design": 0, "critic": 0, "packager": 0}

    async def mock_scout(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["scout"] = _SCOUT_ARTIFACTS
        return {**state, "next_agent": "bid", "current_agent": "scout", "status": "active", "artifacts": artifacts}

    async def mock_bid(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["bid"] = _BID_ARTIFACTS
        return {
            **state, "requires_hitl": True, "status": "paused", "current_agent": "bid",
            "next_agent": None, "artifacts": artifacts,
        }

    async def mock_hitl_bid(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "requires_hitl": False, "status": "active", "next_agent": "planner", "current_agent": "bid"}

    async def mock_planner(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["planner"] = [_PLAN_JSON]
        return {**state, "next_agent": "dev", "current_agent": "planner", "status": "active", "artifacts": artifacts}

    async def mock_dev(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["dev"] += 1
        artifacts = dict(state.get("artifacts") or {})
        dev_payload = json.dumps({"files": [{"path": "src/App.tsx", "revision": call_counts["dev"]}]})
        artifacts["dev"] = [dev_payload]
        return {**state, "next_agent": "content", "current_agent": "dev", "status": "active", "artifacts": artifacts}

    async def mock_content(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["content"] += 1
        artifacts = dict(state.get("artifacts") or {})
        artifacts["content"] = [_CONTENT_JSON]
        return {
            **state, "next_agent": "design", "current_agent": "content", "status": "active", "artifacts": artifacts,
        }

    async def mock_design(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["design"] += 1
        artifacts = dict(state.get("artifacts") or {})
        artifacts["design"] = [_DESIGN_JSON]
        return {**state, "next_agent": "critic", "current_agent": "design", "status": "active", "artifacts": artifacts}

    async def mock_critic(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["critic"] += 1
        artifacts = dict(state.get("artifacts") or {})
        if call_counts["critic"] <= 2:
            # First two calls: REVISE
            artifacts["critic"] = [_CRITIC_REVISE_JSON]
            return {
                **state, "next_agent": "dev", "current_agent": "critic", "status": "active",
                "requires_hitl": False, "artifacts": artifacts,
            }
        # Third call: APPROVE
        artifacts["critic"] = [_CRITIC_APPROVE_JSON]
        return {
            **state, "next_agent": "packager", "current_agent": "critic", "status": "active",
            "requires_hitl": False, "artifacts": artifacts,
        }

    async def mock_packager(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["packager"] += 1
        artifacts = dict(state.get("artifacts") or {})
        artifacts["packager"] = [_PACKAGER_JSON]
        return {
            **state, "requires_hitl": True, "hitl_request_id": "hitl-review-e2e-multi",
            "status": "paused", "current_agent": "packager", "next_agent": None, "artifacts": artifacts,
        }

    async def mock_hitl_review(state: dict[str, Any]) -> dict[str, Any]:
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

    # Final state
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["current_agent"] == "packager"

    # Call counts: 2 revisions + 1 approval = 3 cycles through dev/content/design/critic
    assert call_counts["dev"] == 3, f"Dev should be called 3x, was {call_counts['dev']}x"
    assert call_counts["content"] == 3, f"Content should be called 3x, was {call_counts['content']}x"
    assert call_counts["design"] == 3, f"Design should be called 3x, was {call_counts['design']}x"
    assert call_counts["critic"] == 3, f"Critic should be called 3x, was {call_counts['critic']}x"
    assert call_counts["packager"] == 1, f"Packager should be called 1x, was {call_counts['packager']}x"

    # All agents have artifacts
    for agent_name in _ALL_AGENT_NAMES:
        assert agent_name in result["artifacts"], f"Missing artifacts from '{agent_name}'"

    # Dev artifact reflects the final (3rd) revision
    dev_data = json.loads(result["artifacts"]["dev"][0])
    assert dev_data["files"][0]["revision"] == 3

    # Critic final verdict should be APPROVE
    assert json.loads(result["artifacts"]["critic"][0])["verdict"] == "APPROVE"
