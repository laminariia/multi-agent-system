"""E2E test fixtures for the Multi-Agent Service.

These fixtures provide mock LLM responses that simulate realistic agent behavior
while still running through the actual graph routing and agent node logic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from src.core.state import ProjectContext


@pytest.fixture()
def e2e_project() -> ProjectContext:
    """A realistic project for E2E testing."""
    return ProjectContext(
        project_id="e2e-proj-001",
        job_id="e2e-job-001",
        platform="freelancer",
        client={
            "name": "E2E Test Client",
            "rating": 4.9,
            "reviews": 50,
            "hire_rate": 0.90,
        },
        requirements=(
            "Build a responsive landing page with React and Tailwind CSS. "
            "Include hero section, features grid, testimonials, and contact form."
        ),
        budget=750.0,
        deadline=datetime(2026, 6, 1, tzinfo=UTC),
    )


@pytest.fixture()
def mock_llm_responses() -> dict[str, str]:
    """Canned LLM responses for each agent, keyed by agent name.

    Each value is a JSON string that the agent's ``_execute`` method would parse.
    """
    return {
        "scout": json.dumps({
            "jobs": [{"id": "job-e2e-1", "title": "Landing page needed", "budget": 750}],
        }),
        "bid": json.dumps({
            "proposal": "I will build your landing page with React and Tailwind CSS...",
            "price": 700,
            "timeline_days": 5,
        }),
        "planner": json.dumps({
            "tasks": [
                {"id": "t1", "title": "Create React project structure", "type": "code", "estimated_hours": 1},
                {"id": "t2", "title": "Build hero component", "type": "code", "estimated_hours": 2},
                {"id": "t3", "title": "Style with Tailwind CSS", "type": "code", "estimated_hours": 1},
            ],
            "total_estimated_hours": 4,
            "phases": [{"name": "Build", "tasks": ["t1", "t2", "t3"]}],
        }),
        "dev": json.dumps({
            "files": [
                {"path": "src/App.tsx", "content": "export default function App() { return <div>Hello</div> }"},
                {"path": "src/components/Hero.tsx", "content": "<section>Hero</section>"},
            ],
        }),
        "content": json.dumps({
            "deliverables": [
                {"type": "heading", "content": "Welcome to our platform"},
                {"type": "body", "content": "We provide the best solutions..."},
            ],
        }),
        "design": json.dumps({
            "specs": [
                {"component": "hero", "colors": {"primary": "#3B82F6", "secondary": "#1E40AF"}},
                {"component": "features", "layout": "grid-3-cols"},
            ],
        }),
        "critic_approve": json.dumps({
            "verdict": "APPROVE",
            "score": 0.91,
            "feedback": "Good quality work.",
        }),
        "critic_revise": json.dumps({
            "verdict": "REVISE",
            "score": 0.65,
            "feedback": "Hero section needs improvement.",
            "issues": [{"severity": "medium", "description": "Missing responsive breakpoints"}],
        }),
        "packager": json.dumps({
            "archive_url": "https://storage.example.com/e2e-delivery.zip",
            "deliverables": ["src/App.tsx", "src/components/Hero.tsx"],
        }),
    }
