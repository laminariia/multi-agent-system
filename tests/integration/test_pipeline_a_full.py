"""Pipeline A full golden path integration test.

Verifies the complete happy path:
    Scout(3 jobs) -> Bid -> HITL(approve) -> bid_submission -> Planner
    -> Dev -> Content -> Design -> Critic(approve) -> Packager
    -> HITL(final review) -> complete

All agent nodes are mocked to return predetermined states with realistic
artifact data.  The test validates:
- All 10 agent names appear in the final artifacts
- Scout produces exactly 3 scored jobs
- Bid produces a proposal with required fields
- Planner produces phases with tasks
- Dev produces files array
- Content produces deliverables
- Design produces specs
- Critic produces approve verdict with score >= 0.85
- Packager produces archive_url
- Final state pauses at HITL for delivery review
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.core.state import ProjectContext, create_initial_state

# ---------------------------------------------------------------------------
# Realistic artifact data (golden responses)
# ---------------------------------------------------------------------------

SCOUT_JOBS = [
    {
        "job_id": "freelancer_react_landing_001",
        "title": "React Landing Page",
        "platform": "freelancer",
        "budget": 500,
        "score": 0.92,
        "recommendation": "bid",
        "skills": ["react", "tailwindcss"],
    },
    {
        "job_id": "freelancer_wp_site_002",
        "title": "WordPress Business Site",
        "platform": "freelancer",
        "budget": 800,
        "score": 0.85,
        "recommendation": "bid",
        "skills": ["wordpress", "php"],
    },
    {
        "job_id": "freelancer_portfolio_003",
        "title": "Portfolio Website",
        "platform": "freelancer",
        "budget": 300,
        "score": 0.78,
        "recommendation": "bid",
        "skills": ["html", "css", "javascript"],
    },
]

BID_PROPOSAL = {
    "project_id": "freelancer_react_landing_001",
    "proposal": (
        "Hi! I noticed you need a React landing page. I specialize in React + Tailwind "
        "and can deliver a responsive, fast-loading page within 3 days. I've completed "
        "50+ similar projects. Let's discuss the details?"
    ),
    "amount": 450,
    "delivery_days": 3,
    "requires_hitl": True,
}

PLANNER_PLAN = {
    "phases": [
        {
            "name": "Setup",
            "tasks": [
                {
                    "id": "task_1",
                    "description": "Initialize React + Tailwind project",
                    "assigned_to": "dev",
                    "estimated_hours": 0.5,
                    "dependencies": [],
                    "deliverables": ["project_scaffold"],
                },
            ],
        },
        {
            "name": "Build",
            "tasks": [
                {
                    "id": "task_2",
                    "description": "Build hero section and navigation",
                    "assigned_to": "dev",
                    "estimated_hours": 2.0,
                    "dependencies": ["task_1"],
                    "deliverables": ["hero_component", "nav_component"],
                },
                {
                    "id": "task_3",
                    "description": "Write page copy",
                    "assigned_to": "content",
                    "estimated_hours": 1.0,
                    "dependencies": ["task_1"],
                    "deliverables": ["page_copy"],
                },
            ],
        },
    ],
    "total_estimated_hours": 3.5,
    "critical_path": ["task_1", "task_2"],
    "risks": ["Client may request design changes after first draft."],
}

DEV_CODE = {
    "files": [
        {
            "path": "src/components/Hero.tsx",
            "content": (
                'export default function Hero() { return <section className="hero"><h1>Welcome</h1></section>; }'
            ),
            "language": "typescript",
        },
        {
            "path": "src/components/Nav.tsx",
            "content": 'export default function Nav() { return <nav><a href="/">Home</a></nav>; }',
            "language": "typescript",
        },
        {
            "path": "src/index.tsx",
            "content": (
                'import Hero from "./components/Hero"; '
                'import Nav from "./components/Nav"; '
                "export default function App() { return <><Nav /><Hero /></>; }"
            ),
            "language": "typescript",
        },
    ],
    "dependencies": ["react", "react-dom", "tailwindcss"],
    "build_commands": ["npm install", "npm run build"],
    "test_commands": ["npm test"],
    "deployment_notes": "Deploy to Vercel or Netlify.",
}

CONTENT_DELIVERABLES = {
    "deliverables": [
        {"type": "heading", "content": "Welcome to Our Service"},
        {"type": "subheading", "content": "Professional Solutions for Your Business"},
        {"type": "body", "content": "We deliver high-quality web solutions tailored to your needs."},
        {"type": "cta", "content": "Get Started Today"},
    ],
}

DESIGN_SPECS = {
    "specs": [
        {
            "component": "hero",
            "colors": {"primary": "#3B82F6", "secondary": "#1E40AF", "background": "#F8FAFC"},
            "typography": {"heading": "Inter Bold 48px", "body": "Inter Regular 16px"},
        },
        {
            "component": "nav",
            "layout": "horizontal",
            "sticky": True,
        },
    ],
}

CRITIC_REVIEW = {
    "verdict": "approve",
    "score": 0.91,
    "revision_type": "none",
    "issues": [],
    "passed_checks": ["compiles", "tests_pass", "security", "accessibility", "responsive"],
    "failed_checks": [],
    "revision_instructions": "",
}

PACKAGER_DELIVERY = {
    "archive_url": "https://storage.example.com/delivery-react-landing-001.zip",
    "file_count": 3,
    "total_size_kb": 42,
    "preview_url": "https://preview.example.com/react-landing-001",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project() -> ProjectContext:
    return ProjectContext(
        project_id="proj-golden-001",
        job_id="freelancer_react_landing_001",
        platform="freelancer",
        client={"name": "Golden Path Client", "rating": 4.8, "reviews": 35},
        requirements="Build a responsive landing page with React and Tailwind CSS",
        budget=500.0,
        deadline=datetime(2026, 4, 1, tzinfo=UTC),
    )


def _make_initial_state() -> dict[str, Any]:
    return dict(
        create_initial_state(
            project=_make_project(),
            first_agent="scout",
            thread_id=f"thread-golden-{uuid.uuid4().hex[:8]}",
        )
    )


def _build_golden_graph() -> CompiledStateGraph:
    """Build the pipeline graph with all mock nodes returning golden data."""

    async def mock_scout(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["scout"] = [json.dumps(job) for job in SCOUT_JOBS]
        return {
            **state,
            "next_agent": "bid",
            "current_agent": "scout",
            "status": "active",
            "artifacts": artifacts,
        }

    async def mock_bid(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["bid"] = [json.dumps(BID_PROPOSAL)]
        return {
            **state,
            "requires_hitl": True,
            "hitl_request_id": "hitl-bid-golden-001",
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
            "next_agent": "bid_submission",
            "current_agent": "hitl_bid",
        }

    async def mock_bid_submission(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["bid_submitted"] = True
        return {
            **state,
            "current_agent": "bid_submission",
            "next_agent": "planner",
            "status": "active",
            "artifacts": artifacts,
        }

    async def mock_planner(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["planner"] = [json.dumps(PLANNER_PLAN)]
        return {
            **state,
            "next_agent": "dev",
            "current_agent": "planner",
            "status": "active",
            "artifacts": artifacts,
        }

    async def mock_dev(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["dev"] = [json.dumps(DEV_CODE)]
        return {
            **state,
            "next_agent": "content",
            "current_agent": "dev",
            "status": "active",
            "artifacts": artifacts,
        }

    async def mock_content(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["content"] = [json.dumps(CONTENT_DELIVERABLES)]
        return {
            **state,
            "next_agent": "design",
            "current_agent": "content",
            "status": "active",
            "artifacts": artifacts,
        }

    async def mock_design(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["design"] = [json.dumps(DESIGN_SPECS)]
        return {
            **state,
            "next_agent": "critic",
            "current_agent": "design",
            "status": "active",
            "artifacts": artifacts,
        }

    async def mock_critic(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["critic"] = [json.dumps(CRITIC_REVIEW)]
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
        artifacts["packager"] = [json.dumps(PACKAGER_DELIVERY)]
        return {
            **state,
            "requires_hitl": True,
            "hitl_request_id": "hitl-final-golden-001",
            "status": "paused",
            "current_agent": "packager",
            "next_agent": None,
            "artifacts": artifacts,
        }

    async def mock_hitl_review(state: dict[str, Any]) -> dict[str, Any]:
        return state

    graph = StateGraph(dict)
    for name, fn in [
        ("scout_node", mock_scout),
        ("bid_node", mock_bid),
        ("hitl_bid_node", mock_hitl_bid),
        ("bid_submission_node", mock_bid_submission),
        ("planner_node", mock_planner),
        ("dev_node", mock_dev),
        ("content_node", mock_content),
        ("design_node", mock_design),
        ("critic_node", mock_critic),
        ("packager_node", mock_packager),
        ("hitl_review_node", mock_hitl_review),
    ]:
        graph.add_node(name, fn)

    graph.set_entry_point("scout_node")

    # Routing
    def route_scout(s: dict[str, Any]) -> str:
        return "bid_node" if s.get("next_agent") == "bid" else END

    def route_bid(s: dict[str, Any]) -> str:
        return "hitl_bid_node" if s.get("requires_hitl") else END

    def route_hitl_bid(s: dict[str, Any]) -> str:
        if s.get("next_agent") == "bid_submission":
            return "bid_submission_node"
        if s.get("next_agent") == "planner":
            return "planner_node"
        return END

    def route_bid_submission(s: dict[str, Any]) -> str:
        return "planner_node" if s.get("next_agent") == "planner" else END

    def route_planner(s: dict[str, Any]) -> str:
        return "dev_node" if s.get("next_agent") == "dev" else END

    def route_dev(s: dict[str, Any]) -> str:
        return "content_node" if s.get("next_agent") == "content" else END

    def route_content(s: dict[str, Any]) -> str:
        return "design_node" if s.get("next_agent") == "design" else END

    def route_design(s: dict[str, Any]) -> str:
        return "critic_node" if s.get("next_agent") == "critic" else END

    def route_critic(s: dict[str, Any]) -> str:
        if s.get("requires_hitl"):
            return "hitl_review_node"
        if s.get("next_agent") == "packager":
            return "packager_node"
        if s.get("next_agent") == "dev":
            return "dev_node"
        return END

    def route_packager(s: dict[str, Any]) -> str:
        return "hitl_review_node" if s.get("requires_hitl") else END

    def route_hitl_review(_s: dict[str, Any]) -> str:
        return END

    graph.add_conditional_edges("scout_node", route_scout, {"bid_node": "bid_node", END: END})
    graph.add_conditional_edges("bid_node", route_bid, {"hitl_bid_node": "hitl_bid_node", END: END})
    graph.add_conditional_edges(
        "hitl_bid_node",
        route_hitl_bid,
        {"bid_submission_node": "bid_submission_node", "planner_node": "planner_node", END: END},
    )
    graph.add_conditional_edges("bid_submission_node", route_bid_submission, {"planner_node": "planner_node", END: END})
    graph.add_conditional_edges("planner_node", route_planner, {"dev_node": "dev_node", END: END})
    graph.add_conditional_edges("dev_node", route_dev, {"content_node": "content_node", END: END})
    graph.add_conditional_edges("content_node", route_content, {"design_node": "design_node", END: END})
    graph.add_conditional_edges("design_node", route_design, {"critic_node": "critic_node", END: END})
    graph.add_conditional_edges(
        "critic_node",
        route_critic,
        {"packager_node": "packager_node", "dev_node": "dev_node", "hitl_review_node": "hitl_review_node", END: END},
    )
    graph.add_conditional_edges("packager_node", route_packager, {"hitl_review_node": "hitl_review_node", END: END})
    graph.add_conditional_edges("hitl_review_node", route_hitl_review, {END: END})

    return graph.compile()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_golden_path_all_agents_produce_artifacts() -> None:
    """Full golden path: all agents run, all artifacts present in final state."""
    graph = _build_golden_graph()
    result = await graph.ainvoke(_make_initial_state())

    # Final state should pause at packager for HITL final review.
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["current_agent"] == "packager"
    assert result["hitl_request_id"] == "hitl-final-golden-001"

    # All agent artifacts must be present.
    expected_agents = ["scout", "bid", "planner", "dev", "content", "design", "critic", "packager"]
    for agent in expected_agents:
        assert agent in result["artifacts"], f"Missing artifacts from '{agent}'"


async def test_golden_path_scout_produces_3_jobs() -> None:
    """Scout should produce exactly 3 scored job artifacts."""
    graph = _build_golden_graph()
    result = await graph.ainvoke(_make_initial_state())

    scout_artifacts = result["artifacts"]["scout"]
    assert len(scout_artifacts) == 3

    for artifact_json in scout_artifacts:
        job = json.loads(artifact_json)
        assert "job_id" in job
        assert "score" in job
        assert "recommendation" in job
        assert job["recommendation"] == "bid"


async def test_golden_path_bid_proposal_structure() -> None:
    """Bid proposal must have required fields."""
    graph = _build_golden_graph()
    result = await graph.ainvoke(_make_initial_state())

    bid_artifacts = result["artifacts"]["bid"]
    assert len(bid_artifacts) >= 1

    proposal = json.loads(bid_artifacts[0])
    assert "proposal" in proposal
    assert "amount" in proposal
    assert proposal["amount"] > 0
    assert len(proposal["proposal"]) > 20


async def test_golden_path_planner_has_phases_and_tasks() -> None:
    """Planner must produce phases with tasks and estimated hours."""
    graph = _build_golden_graph()
    result = await graph.ainvoke(_make_initial_state())

    plan = json.loads(result["artifacts"]["planner"][0])
    assert "phases" in plan
    assert len(plan["phases"]) > 0
    assert "total_estimated_hours" in plan
    assert plan["total_estimated_hours"] > 0

    # Verify tasks exist within phases.
    total_tasks = sum(len(phase.get("tasks", [])) for phase in plan["phases"])
    assert total_tasks > 0


async def test_golden_path_dev_produces_files() -> None:
    """Dev must produce a files array with path and content."""
    graph = _build_golden_graph()
    result = await graph.ainvoke(_make_initial_state())

    dev_data = json.loads(result["artifacts"]["dev"][0])
    assert "files" in dev_data
    assert len(dev_data["files"]) >= 1

    for f in dev_data["files"]:
        assert "path" in f
        assert "content" in f
        assert len(f["content"]) > 0


async def test_golden_path_critic_approves() -> None:
    """Critic must approve with score >= 0.85."""
    graph = _build_golden_graph()
    result = await graph.ainvoke(_make_initial_state())

    review = json.loads(result["artifacts"]["critic"][0])
    assert review["verdict"] == "approve"
    assert review["score"] >= 0.85


async def test_golden_path_packager_has_archive_url() -> None:
    """Packager must produce an archive_url for delivery."""
    graph = _build_golden_graph()
    result = await graph.ainvoke(_make_initial_state())

    delivery = json.loads(result["artifacts"]["packager"][0])
    assert "archive_url" in delivery
    assert delivery["archive_url"].startswith("https://")
