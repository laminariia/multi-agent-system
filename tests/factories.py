"""Factory pattern for test data generation.

Each factory produces plain ``dict`` objects matching the shapes that agents
produce and consume, without requiring a live database.
"""

from __future__ import annotations

import uuid
from typing import Any


class JobFactory:
    """Create job dicts matching the shape returned by the Scout Agent's scoring pipeline."""

    _counter: int = 0

    @classmethod
    def create(cls, **overrides: Any) -> dict[str, Any]:
        """Return a single job dict with sensible defaults.

        Override any field via keyword arguments.
        """
        cls._counter += 1
        defaults: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "platform": "freelancer",
            "external_id": f"ext-{cls._counter:05d}",
            "title": f"Build landing page #{cls._counter}",
            "description": "Create a responsive landing page with React and Tailwind CSS.",
            "budget_min": 200.0,
            "budget_max": 500.0,
            "currency": "USD",
            "skills_required": ["react", "tailwind", "html"],
            "match_score": 0.85,
            "recommendation": "bid",
            "reasoning": "Strong match -- clear scope, fair budget, core stack.",
            "client_info": {
                "name": "Test Client",
                "rating": 4.8,
                "reviews": 25,
                "hire_rate": 0.85,
            },
            "url": f"https://freelancer.com/projects/{cls._counter}",
            "score": 0.85,
        }
        defaults.update(overrides)
        return defaults

    @classmethod
    def create_batch(cls, count: int = 3, **overrides: Any) -> list[dict[str, Any]]:
        """Return a list of *count* job dicts."""
        return [cls.create(**overrides) for _ in range(count)]


class ProposalFactory:
    """Create proposal dicts matching the shape the Bid Agent's LLM returns."""

    _counter: int = 0

    @classmethod
    def create(cls, **overrides: Any) -> dict[str, Any]:
        """Return a single proposal dict with sensible defaults."""
        cls._counter += 1
        defaults: dict[str, Any] = {
            "proposal_text": (
                "I noticed you need a responsive landing page with React and Tailwind. "
                "I recently delivered a similar page that scored 98 on Lighthouse. "
                "I can have a polished first draft to you in 3 days with full revisions included. "
                "Happy to hop on a quick call to align on the details."
            ),
            "bid_amount": 350.0,
            "delivery_days": 7,
            "confidence_score": 0.82,
            "milestones": [
                {"description": "Design mockup", "amount": 100.0, "days": 2},
                {"description": "Development", "amount": 200.0, "days": 4},
                {"description": "Revisions & delivery", "amount": 50.0, "days": 1},
            ],
            "portfolio_links": ["https://portfolio.example.com/project-a"],
            "requires_hitl": True,
        }
        defaults.update(overrides)
        return defaults


class HITLFactory:
    """Create HITL queue entry dicts."""

    _counter: int = 0

    @classmethod
    def create(cls, **overrides: Any) -> dict[str, Any]:
        """Return a single HITL queue entry dict."""
        cls._counter += 1
        defaults: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "type": "bid_approval",
            "priority": "normal",
            "title": f"Approve bid: Landing page project #{cls._counter}",
            "description": "Platform: freelancer | Amount: $350.00 | Delivery: 7 days | Confidence: 82%",
            "payload": {
                "job": JobFactory.create(),
                "proposal": ProposalFactory.create(),
                "bid_id": str(uuid.uuid4()),
                "source_agent": "bid",
            },
            "available_actions": ["approve", "edit", "skip", "later"],
            "status": "pending",
        }
        defaults.update(overrides)
        return defaults
