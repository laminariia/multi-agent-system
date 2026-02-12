"""Integration tests for the PRODUCTION build_full_pipeline_graph().

Unlike test_full_pipeline.py which builds its own graph with local routing,
these tests invoke the actual production graph builder from src/core/graph.py
with patched agent node functions.  This validates that:

1. The production graph is wired correctly (nodes + conditional edges)
2. Production routing functions route state through the correct agent sequence
3. HITL gates, revision loops, and scope creep escalation work end-to-end

Phase 2 M5: Full Pipeline A E2E dry run validation.

Important LangGraph 1.0.8 behaviour notes:
- Without a checkpointer, HITL nodes are pass-through when status is already
  "paused" (set by the previous agent).  They do NOT update current_agent.
- ``ainvoke`` may return initial state for complex ``StateGraph(dict)`` graphs.
  Use ``astream(stream_mode="values")`` as a workaround.
- LangGraph inspects routing function type hints.  Using ``AgentState``
  (TypedDict) as the ``state`` parameter annotation causes LangGraph to
  reconstruct the state from the schema, losing accumulated values.  Always
  annotate routing functions with ``dict[str, Any]``.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from src.core.state import ProjectContext, create_initial_state


async def _run_graph(graph, state: dict[str, Any]) -> dict[str, Any]:
    """Run a compiled graph and return the final state.

    Uses ``astream(stream_mode="values")`` instead of ``ainvoke`` to work
    around a LangGraph 1.0.8 issue where ``ainvoke`` returns the initial
    state instead of the final state for complex ``StateGraph(dict)`` graphs.
    """
    last_state = state
    async for chunk in graph.astream(state, stream_mode="values"):
        last_state = chunk
    return last_state


def _make_initial_state(thread_id: str | None = None) -> dict[str, Any]:
    """Build a fresh state dict for production graph integration tests."""
    project = ProjectContext(
        project_id="proj-prod-001",
        job_id="job-prod-001",
        platform="freelancer",
        client={"name": "Production Test Client", "rating": 4.7, "reviews": 30, "hire_rate": 0.80},
        requirements="Build a responsive landing page with React and Tailwind CSS",
        budget=800.0,
        deadline=datetime(2026, 5, 1, tzinfo=UTC),
    )
    return dict(create_initial_state(
        project=project,
        first_agent="scout",
        thread_id=thread_id or f"thread-prod-{uuid.uuid4().hex[:8]}",
    ))


# ---------------------------------------------------------------------------
# Helpers: reusable mock agent functions
#
# Each mock explicitly sets ALL control-flow fields (status, requires_hitl,
# next_agent, current_agent) to avoid inheriting stale values from {**state}.
# ---------------------------------------------------------------------------

async def _scout_finds_jobs(state: dict[str, Any]) -> dict[str, Any]:
    artifacts = dict(state.get("artifacts") or {})
    artifacts["scout"] = ["job-001", "job-002"]
    return {
        **state, "next_agent": "bid", "current_agent": "scout",
        "status": "active", "requires_hitl": False, "artifacts": artifacts,
    }


async def _scout_no_jobs(state: dict[str, Any]) -> dict[str, Any]:
    return {
        **state, "next_agent": None, "current_agent": "scout",
        "status": "active", "requires_hitl": False, "artifacts": state.get("artifacts", {}),
    }


async def _scout_fails(state: dict[str, Any]) -> dict[str, Any]:
    return {
        **state, "status": "failed", "current_agent": "scout",
        "next_agent": None, "requires_hitl": False, "errors": ["All adapters failed"],
    }


async def _bid_paused(state: dict[str, Any]) -> dict[str, Any]:
    """Bid generates a proposal and requests HITL approval (status=paused)."""
    artifacts = dict(state.get("artifacts") or {})
    artifacts["bid"] = [json.dumps({"proposal": "We can build your landing page..."})]
    return {
        **state, "requires_hitl": True, "hitl_request_id": "hitl-bid-001",
        "status": "paused", "current_agent": "bid", "next_agent": None, "artifacts": artifacts,
    }


async def _planner_simple(state: dict[str, Any]) -> dict[str, Any]:
    artifacts = dict(state.get("artifacts") or {})
    artifacts["planner"] = [json.dumps({"tasks": [{"id": "t1", "title": "Create landing page", "estimated_hours": 3}]})]
    return {
        **state, "next_agent": "dev", "current_agent": "planner",
        "status": "active", "requires_hitl": False, "artifacts": artifacts,
    }


async def _planner_complex_hitl(state: dict[str, Any]) -> dict[str, Any]:
    artifacts = dict(state.get("artifacts") or {})
    artifacts["planner"] = [json.dumps({"tasks": [], "total_estimated_hours": 30})]
    return {
        **state, "next_agent": "dev", "current_agent": "planner",
        "status": "paused", "requires_hitl": True, "hitl_request_id": "hitl-plan-001",
        "artifacts": artifacts,
    }


async def _dev_generates(state: dict[str, Any]) -> dict[str, Any]:
    artifacts = dict(state.get("artifacts") or {})
    artifacts["dev"] = [json.dumps({"files": [{"path": "index.html", "content": "<h1>Hello</h1>"}]})]
    return {
        **state, "next_agent": "content", "current_agent": "dev",
        "status": "active", "requires_hitl": False, "artifacts": artifacts,
    }


async def _content_generates(state: dict[str, Any]) -> dict[str, Any]:
    artifacts = dict(state.get("artifacts") or {})
    artifacts["content"] = [json.dumps({"deliverables": [{"type": "heading", "content": "Welcome"}]})]
    return {
        **state, "next_agent": "design", "current_agent": "content",
        "status": "active", "requires_hitl": False, "artifacts": artifacts,
    }


async def _design_generates(state: dict[str, Any]) -> dict[str, Any]:
    artifacts = dict(state.get("artifacts") or {})
    artifacts["design"] = [json.dumps({"specs": [{"component": "hero", "colors": {"primary": "#3B82F6"}}]})]
    return {
        **state, "next_agent": "critic", "current_agent": "design",
        "status": "active", "requires_hitl": False, "artifacts": artifacts,
    }


async def _critic_approves(state: dict[str, Any]) -> dict[str, Any]:
    artifacts = dict(state.get("artifacts") or {})
    artifacts["critic"] = [json.dumps({"verdict": "APPROVE", "score": 0.92})]
    return {
        **state, "next_agent": "packager", "current_agent": "critic",
        "status": "active", "requires_hitl": False, "artifacts": artifacts,
    }


async def _critic_scope_creep(state: dict[str, Any]) -> dict[str, Any]:
    artifacts = dict(state.get("artifacts") or {})
    artifacts["critic"] = [json.dumps({
        "verdict": "REVISE", "score": 0.60, "revision_type": "scope_creep",
        "issues": [{"desc": "Client wants admin panel — not in spec"}],
    })]
    return {
        **state, "next_agent": None, "current_agent": "critic",
        "status": "paused", "requires_hitl": True,
        "hitl_request_id": "hitl-scope-001", "artifacts": artifacts,
    }


async def _packager_delivers(state: dict[str, Any]) -> dict[str, Any]:
    artifacts = dict(state.get("artifacts") or {})
    artifacts["packager"] = [json.dumps({"archive_url": "https://storage.example.com/delivery.zip"})]
    return {
        **state, "requires_hitl": True, "hitl_request_id": "hitl-review-final",
        "status": "paused", "current_agent": "packager", "next_agent": None, "artifacts": artifacts,
    }


def _make_patches(**overrides):
    """Create a dict of patch targets for all 8 agent node functions."""
    defaults = {
        "scout_node": _scout_finds_jobs,
        "bid_node": _bid_paused,
        "planner_node": _planner_simple,
        "dev_node": _dev_generates,
        "content_node": _content_generates,
        "design_node": _design_generates,
        "critic_node": _critic_approves,
        "packager_node": _packager_delivers,
    }
    defaults.update(overrides)
    return {f"src.core.graph.{k}": v for k, v in defaults.items()}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_production_graph_bid_hitl_pause():
    """Production graph: Bid with status=active → HITL bid node pauses.

    When bid returns status=active + requires_hitl=True, the production
    hitl_bid_node sets status=paused and current_agent=hitl_bid.  Since
    _route_after_hitl_bid only checks for failed, the graph continues to
    planner and beyond.
    """

    async def bid_active_hitl(state: dict[str, Any]) -> dict[str, Any]:
        """Bid returns active status — hitl_bid_node will set paused."""
        artifacts = dict(state.get("artifacts") or {})
        artifacts["bid"] = [json.dumps({"proposal": "We can build your landing page..."})]
        return {
            **state, "requires_hitl": True, "hitl_request_id": "hitl-bid-001",
            "status": "active", "current_agent": "bid", "next_agent": None,
            "artifacts": artifacts,
        }

    patches = _make_patches(bid_node=bid_active_hitl)
    with patch("src.core.graph.scout_node", side_effect=patches["src.core.graph.scout_node"]), \
         patch("src.core.graph.bid_node", side_effect=patches["src.core.graph.bid_node"]), \
         patch("src.core.graph.planner_node", side_effect=patches["src.core.graph.planner_node"]), \
         patch("src.core.graph.dev_node", side_effect=patches["src.core.graph.dev_node"]), \
         patch("src.core.graph.content_node", side_effect=patches["src.core.graph.content_node"]), \
         patch("src.core.graph.design_node", side_effect=patches["src.core.graph.design_node"]), \
         patch("src.core.graph.critic_node", side_effect=patches["src.core.graph.critic_node"]), \
         patch("src.core.graph.packager_node", side_effect=patches["src.core.graph.packager_node"]):
        from src.core.graph import build_full_pipeline_graph
        graph = build_full_pipeline_graph()
        result = await _run_graph(graph, _make_initial_state())

    # Full pipeline completes (hitl_bid sets paused then routes to planner)
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    # Artifacts from at least scout and bid should be present
    assert "scout" in result["artifacts"]
    assert "bid" in result["artifacts"]


async def test_production_graph_full_happy_path():
    """Production graph: Full Pipeline A happy path.

    Scout → Bid(paused) → HITL_bid(passthrough) → Planner → Dev → Content
    → Design → Critic(approve) → Packager(paused) → HITL_review(passthrough) → END.

    Without a checkpointer, HITL nodes pass through when status is already
    paused.  The final current_agent is "packager" (set by packager, not
    overwritten by hitl_review_node passthrough).
    """
    patches = _make_patches()
    with patch("src.core.graph.scout_node", side_effect=patches["src.core.graph.scout_node"]), \
         patch("src.core.graph.bid_node", side_effect=patches["src.core.graph.bid_node"]), \
         patch("src.core.graph.planner_node", side_effect=patches["src.core.graph.planner_node"]), \
         patch("src.core.graph.dev_node", side_effect=patches["src.core.graph.dev_node"]), \
         patch("src.core.graph.content_node", side_effect=patches["src.core.graph.content_node"]), \
         patch("src.core.graph.design_node", side_effect=patches["src.core.graph.design_node"]), \
         patch("src.core.graph.critic_node", side_effect=patches["src.core.graph.critic_node"]), \
         patch("src.core.graph.packager_node", side_effect=patches["src.core.graph.packager_node"]):
        from src.core.graph import build_full_pipeline_graph
        graph = build_full_pipeline_graph()
        result = await _run_graph(graph, _make_initial_state())

    # Final state: packager pauses for HITL review
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True

    # All agents should have produced artifacts
    for agent in ("scout", "bid", "planner", "dev", "content", "design", "critic", "packager"):
        assert agent in result["artifacts"], (
            f"Artifacts from '{agent}' missing. Present: {list(result['artifacts'].keys())}"
        )


async def test_production_graph_scout_no_jobs():
    """Production graph: Scout finds no jobs → graph ends immediately."""
    bid_called = False

    async def tracking_bid(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal bid_called
        bid_called = True
        return await _bid_paused(state)

    patches = _make_patches(scout_node=_scout_no_jobs, bid_node=tracking_bid)
    with patch("src.core.graph.scout_node", side_effect=patches["src.core.graph.scout_node"]), \
         patch("src.core.graph.bid_node", side_effect=patches["src.core.graph.bid_node"]), \
         patch("src.core.graph.planner_node", side_effect=patches["src.core.graph.planner_node"]), \
         patch("src.core.graph.dev_node", side_effect=patches["src.core.graph.dev_node"]), \
         patch("src.core.graph.content_node", side_effect=patches["src.core.graph.content_node"]), \
         patch("src.core.graph.design_node", side_effect=patches["src.core.graph.design_node"]), \
         patch("src.core.graph.critic_node", side_effect=patches["src.core.graph.critic_node"]), \
         patch("src.core.graph.packager_node", side_effect=patches["src.core.graph.packager_node"]):
        from src.core.graph import build_full_pipeline_graph
        graph = build_full_pipeline_graph()
        result = await _run_graph(graph, _make_initial_state())

    assert result["status"] == "active"
    assert result["next_agent"] is None
    assert bid_called is False, "Bid should NOT be called when Scout finds no jobs"


async def test_production_graph_scout_failure():
    """Production graph: Scout fails → graph ends, bid NOT called."""
    bid_called = False

    async def tracking_bid(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal bid_called
        bid_called = True
        return state

    patches = _make_patches(scout_node=_scout_fails, bid_node=tracking_bid)
    with patch("src.core.graph.scout_node", side_effect=patches["src.core.graph.scout_node"]), \
         patch("src.core.graph.bid_node", side_effect=patches["src.core.graph.bid_node"]), \
         patch("src.core.graph.planner_node", side_effect=patches["src.core.graph.planner_node"]), \
         patch("src.core.graph.dev_node", side_effect=patches["src.core.graph.dev_node"]), \
         patch("src.core.graph.content_node", side_effect=patches["src.core.graph.content_node"]), \
         patch("src.core.graph.design_node", side_effect=patches["src.core.graph.design_node"]), \
         patch("src.core.graph.critic_node", side_effect=patches["src.core.graph.critic_node"]), \
         patch("src.core.graph.packager_node", side_effect=patches["src.core.graph.packager_node"]):
        from src.core.graph import build_full_pipeline_graph
        graph = build_full_pipeline_graph()
        result = await _run_graph(graph, _make_initial_state())

    assert result["status"] == "failed"
    assert "All adapters failed" in result["errors"]
    assert bid_called is False


async def test_production_graph_critic_minor_revision_loop():
    """Production graph: Critic returns minor revision → Dev cycle repeats.

    Flow: Scout→Bid(paused)→HITL_bid→Planner→Dev→Content→Design→Critic(revise)
    →Dev(2nd)→Content(2nd)→Design(2nd)→Critic(approve)→Packager→HITL_review.
    """
    call_counts: dict[str, int] = {"dev": 0, "content": 0, "design": 0, "critic": 0}

    async def counting_dev(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["dev"] += 1
        return await _dev_generates(state)

    async def counting_content(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["content"] += 1
        return await _content_generates(state)

    async def counting_design(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["design"] += 1
        return await _design_generates(state)

    async def revise_then_approve(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["critic"] += 1
        artifacts = dict(state.get("artifacts") or {})
        if call_counts["critic"] == 1:
            # First pass: minor revision → route to dev
            artifacts["critic"] = [json.dumps({"verdict": "REVISE", "score": 0.68, "revision_type": "minor"})]
            return {
                **state, "next_agent": "dev", "current_agent": "critic",
                "status": "active", "requires_hitl": False, "artifacts": artifacts,
            }
        # Second pass: approve
        artifacts["critic"] = [json.dumps({"verdict": "APPROVE", "score": 0.91})]
        return {
            **state, "next_agent": "packager", "current_agent": "critic",
            "status": "active", "requires_hitl": False, "artifacts": artifacts,
        }

    patches = _make_patches(
        dev_node=counting_dev, content_node=counting_content,
        design_node=counting_design, critic_node=revise_then_approve,
    )
    with patch("src.core.graph.scout_node", side_effect=patches["src.core.graph.scout_node"]), \
         patch("src.core.graph.bid_node", side_effect=patches["src.core.graph.bid_node"]), \
         patch("src.core.graph.planner_node", side_effect=patches["src.core.graph.planner_node"]), \
         patch("src.core.graph.dev_node", side_effect=patches["src.core.graph.dev_node"]), \
         patch("src.core.graph.content_node", side_effect=patches["src.core.graph.content_node"]), \
         patch("src.core.graph.design_node", side_effect=patches["src.core.graph.design_node"]), \
         patch("src.core.graph.critic_node", side_effect=patches["src.core.graph.critic_node"]), \
         patch("src.core.graph.packager_node", side_effect=patches["src.core.graph.packager_node"]):
        from src.core.graph import build_full_pipeline_graph
        graph = build_full_pipeline_graph()
        result = await _run_graph(graph, _make_initial_state())

    assert result["status"] == "paused"
    assert call_counts["dev"] == 2, f"Dev expected 2x, got {call_counts['dev']}x"
    assert call_counts["content"] == 2
    assert call_counts["design"] == 2
    assert call_counts["critic"] == 2


async def test_production_graph_critic_major_revision_to_planner():
    """Production graph: Critic major revision → Planner re-decomposes.

    Flow: ...→Critic(major)→Planner(2nd)→Dev(2nd)→...→Critic(approve)→Packager.
    """
    call_counts: dict[str, int] = {"planner": 0, "dev": 0, "critic": 0}

    async def counting_planner(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["planner"] += 1
        return await _planner_simple(state)

    async def counting_dev(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["dev"] += 1
        return await _dev_generates(state)

    async def major_then_approve(state: dict[str, Any]) -> dict[str, Any]:
        call_counts["critic"] += 1
        artifacts = dict(state.get("artifacts") or {})
        if call_counts["critic"] == 1:
            artifacts["critic"] = [json.dumps({"verdict": "REVISE", "score": 0.55, "revision_type": "major"})]
            return {
                **state, "next_agent": "planner", "current_agent": "critic",
                "status": "active", "requires_hitl": False, "artifacts": artifacts,
            }
        artifacts["critic"] = [json.dumps({"verdict": "APPROVE", "score": 0.90})]
        return {
            **state, "next_agent": "packager", "current_agent": "critic",
            "status": "active", "requires_hitl": False, "artifacts": artifacts,
        }

    patches = _make_patches(
        planner_node=counting_planner, dev_node=counting_dev,
        critic_node=major_then_approve,
    )
    with patch("src.core.graph.scout_node", side_effect=patches["src.core.graph.scout_node"]), \
         patch("src.core.graph.bid_node", side_effect=patches["src.core.graph.bid_node"]), \
         patch("src.core.graph.planner_node", side_effect=patches["src.core.graph.planner_node"]), \
         patch("src.core.graph.dev_node", side_effect=patches["src.core.graph.dev_node"]), \
         patch("src.core.graph.content_node", side_effect=patches["src.core.graph.content_node"]), \
         patch("src.core.graph.design_node", side_effect=patches["src.core.graph.design_node"]), \
         patch("src.core.graph.critic_node", side_effect=patches["src.core.graph.critic_node"]), \
         patch("src.core.graph.packager_node", side_effect=patches["src.core.graph.packager_node"]):
        from src.core.graph import build_full_pipeline_graph
        graph = build_full_pipeline_graph()
        result = await _run_graph(graph, _make_initial_state())

    assert result["status"] == "paused"
    assert call_counts["planner"] == 2, f"Planner expected 2x, got {call_counts['planner']}x"
    assert call_counts["dev"] == 2, f"Dev expected 2x, got {call_counts['dev']}x"
    assert call_counts["critic"] == 2


async def test_production_graph_scope_creep_hitl():
    """Production graph: Critic detects scope creep → HITL review, packager NOT called."""
    packager_called = False

    async def tracking_packager(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal packager_called
        packager_called = True
        return state

    patches = _make_patches(critic_node=_critic_scope_creep, packager_node=tracking_packager)
    with patch("src.core.graph.scout_node", side_effect=patches["src.core.graph.scout_node"]), \
         patch("src.core.graph.bid_node", side_effect=patches["src.core.graph.bid_node"]), \
         patch("src.core.graph.planner_node", side_effect=patches["src.core.graph.planner_node"]), \
         patch("src.core.graph.dev_node", side_effect=patches["src.core.graph.dev_node"]), \
         patch("src.core.graph.content_node", side_effect=patches["src.core.graph.content_node"]), \
         patch("src.core.graph.design_node", side_effect=patches["src.core.graph.design_node"]), \
         patch("src.core.graph.critic_node", side_effect=patches["src.core.graph.critic_node"]), \
         patch("src.core.graph.packager_node", side_effect=patches["src.core.graph.packager_node"]):
        from src.core.graph import build_full_pipeline_graph
        graph = build_full_pipeline_graph()
        result = await _run_graph(graph, _make_initial_state())

    assert packager_called is False, "Packager should NOT be called on scope creep"
    assert result["requires_hitl"] is True
    assert result["status"] == "paused"


async def test_production_graph_planner_hitl_review():
    """Production graph: Planner requests HITL review → dev NOT called."""
    dev_called = False

    async def tracking_dev(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal dev_called
        dev_called = True
        return state

    patches = _make_patches(planner_node=_planner_complex_hitl, dev_node=tracking_dev)
    with patch("src.core.graph.scout_node", side_effect=patches["src.core.graph.scout_node"]), \
         patch("src.core.graph.bid_node", side_effect=patches["src.core.graph.bid_node"]), \
         patch("src.core.graph.planner_node", side_effect=patches["src.core.graph.planner_node"]), \
         patch("src.core.graph.dev_node", side_effect=patches["src.core.graph.dev_node"]), \
         patch("src.core.graph.content_node", side_effect=patches["src.core.graph.content_node"]), \
         patch("src.core.graph.design_node", side_effect=patches["src.core.graph.design_node"]), \
         patch("src.core.graph.critic_node", side_effect=patches["src.core.graph.critic_node"]), \
         patch("src.core.graph.packager_node", side_effect=patches["src.core.graph.packager_node"]):
        from src.core.graph import build_full_pipeline_graph
        graph = build_full_pipeline_graph()
        result = await _run_graph(graph, _make_initial_state())

    assert dev_called is False, "Dev should NOT be called when Planner requests HITL review"
    assert result["requires_hitl"] is True


async def test_production_graph_artifacts_accumulate():
    """Production graph: Artifacts from all agents are preserved through the pipeline."""
    patches = _make_patches()
    with patch("src.core.graph.scout_node", side_effect=patches["src.core.graph.scout_node"]), \
         patch("src.core.graph.bid_node", side_effect=patches["src.core.graph.bid_node"]), \
         patch("src.core.graph.planner_node", side_effect=patches["src.core.graph.planner_node"]), \
         patch("src.core.graph.dev_node", side_effect=patches["src.core.graph.dev_node"]), \
         patch("src.core.graph.content_node", side_effect=patches["src.core.graph.content_node"]), \
         patch("src.core.graph.design_node", side_effect=patches["src.core.graph.design_node"]), \
         patch("src.core.graph.critic_node", side_effect=patches["src.core.graph.critic_node"]), \
         patch("src.core.graph.packager_node", side_effect=patches["src.core.graph.packager_node"]):
        from src.core.graph import build_full_pipeline_graph
        graph = build_full_pipeline_graph()
        result = await _run_graph(graph, _make_initial_state())

    expected_agents = ["scout", "bid", "planner", "dev", "content", "design", "critic", "packager"]
    for agent_name in expected_agents:
        assert agent_name in result["artifacts"], (
            f"Artifacts from '{agent_name}' missing. Present: {list(result['artifacts'].keys())}"
        )
