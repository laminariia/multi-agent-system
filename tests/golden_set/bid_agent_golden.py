"""Golden set data for the Bid Agent (5 cases)."""

from __future__ import annotations

import json

from tests.golden_set.evaluator import GoldenCase, ValidationRule


def _raw(obj: dict) -> str:
    """Serialize dict to compact JSON string (simulates clean LLM output)."""
    return json.dumps(obj)


def _fenced(obj: dict) -> str:
    """Serialize dict to JSON wrapped in markdown code fences."""
    return "```json\n" + json.dumps(obj, indent=2) + "\n```"


CASES: list[GoldenCase] = [
    GoldenCase(
        name="standard_web_dev",
        agent="bid",
        input_data={
            "job_title": "Build a React dashboard with charts",
            "budget": 2500,
            "skills": ["React", "TypeScript", "Chart.js"],
            "client_rating": 4.8,
            "description": "Need a modern analytics dashboard with 5 chart types, responsive layout.",
        },
        expected_output=_fenced({
            "proposal_text": (
                "I noticed your project requires React and Chart.js expertise -- "
                "that's exactly what I specialize in. Recently I built a similar "
                "analytics dashboard that improved client data visibility by 40%. "
                "For your project, I'll deliver a responsive React + TypeScript dashboard "
                "with all 5 chart types, dark/light theme support, and clean code. "
                "Happy to share my portfolio on a quick call."
            ),
            "bid_amount": 2000,
            "delivery_days": 7,
            "confidence_score": 0.85,
            "milestones": [
                {"description": "Design mockup", "amount": 600, "days": 2},
                {"description": "Development", "amount": 1000, "days": 4},
                {"description": "Revisions & delivery", "amount": 400, "days": 1},
            ],
            "requires_hitl": True,
        }),
        validators=[
            ValidationRule("must_mention", {"keywords": ["React", "dashboard"]}),
            ValidationRule("must_not_mention", {"keywords": ["Angular", "Vue"]}),
            ValidationRule("length_range", {"field": "proposal_text", "min": 50, "max": 350}),
            ValidationRule("score_range", {"field": "bid_amount", "min": 1500, "max": 2500}),
            ValidationRule("score_range", {"field": "confidence_score", "min": 0.7, "max": 1.0}),
            ValidationRule("value_equals", {"field": "requires_hitl", "value": True}),
            ValidationRule("list_non_empty", {"field": "milestones"}),
            ValidationRule("has_keys", {"keys": ["proposal_text", "bid_amount", "delivery_days"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="urgent_premium",
        agent="bid",
        input_data={
            "job_title": "URGENT: Redesign company website in 48 hours",
            "budget": 3000,
            "skills": ["HTML", "CSS", "JavaScript", "Figma"],
            "client_rating": 4.5,
            "description": "Complete redesign of a 5-page corporate site. Figma designs will be provided.",
        },
        expected_output=_raw({
            "proposal_text": (
                "I see you need a fast turnaround on a corporate redesign -- I've delivered "
                "similar projects in under 48 hours. With Figma designs provided, I can "
                "start implementation immediately and deliver pixel-perfect responsive pages. "
                "I can have a draft ready within 24 hours."
            ),
            "bid_amount": 2500,
            "delivery_days": 2,
            "confidence_score": 0.80,
            "requires_hitl": True,
        }),
        validators=[
            ValidationRule("length_range", {"field": "proposal_text", "min": 40, "max": 350}),
            ValidationRule("score_range", {"field": "bid_amount", "min": 2000, "max": 3000}),
            ValidationRule("value_equals", {"field": "requires_hitl", "value": True}),
            ValidationRule("has_keys", {"keys": ["proposal_text", "bid_amount", "delivery_days"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="budget_conscious",
        agent="bid",
        input_data={
            "job_title": "Fix CSS bugs on e-commerce site",
            "budget": 100,
            "skills": ["CSS", "HTML"],
            "client_rating": 3.5,
            "description": "Mobile layout broken on product pages, need responsive fix.",
        },
        expected_output=_fenced({
            "proposal_text": (
                "I can see the mobile layout issues on your product pages -- "
                "CSS responsive fixes are something I handle daily. "
                "I'll audit the breakpoints, fix the layout, and test across devices."
            ),
            "bid_amount": 80,
            "delivery_days": 2,
            "confidence_score": 0.90,
            "requires_hitl": True,
        }),
        validators=[
            ValidationRule("must_mention", {"keywords": ["CSS"]}),
            ValidationRule("length_range", {"field": "proposal_text", "min": 20, "max": 200}),
            ValidationRule("score_range", {"field": "bid_amount", "min": 50, "max": 100}),
            ValidationRule("value_equals", {"field": "requires_hitl", "value": True}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="specific_tech",
        agent="bid",
        input_data={
            "job_title": "Shopify store with custom Liquid theme",
            "budget": 1200,
            "skills": ["Shopify", "Liquid", "CSS"],
            "client_rating": 4.6,
            "description": "Custom Shopify theme, 50 products from CSV, Stripe integration.",
        },
        expected_output=_raw({
            "proposal_text": (
                "Your Shopify project is right in my wheelhouse -- I've built custom Liquid "
                "themes for multiple stores. I'll handle the theme build, CSV product import, "
                "and Stripe setup as separate milestones."
            ),
            "bid_amount": 1000,
            "delivery_days": 10,
            "confidence_score": 0.80,
            "milestones": [
                {"description": "Theme setup", "amount": 400, "days": 3},
                {"description": "Product import", "amount": 300, "days": 3},
                {"description": "Stripe + polish", "amount": 300, "days": 4},
            ],
            "requires_hitl": True,
        }),
        validators=[
            ValidationRule("must_mention", {"keywords": ["Shopify"]}),
            ValidationRule("score_range", {"field": "bid_amount", "min": 800, "max": 1200}),
            ValidationRule("list_non_empty", {"field": "milestones"}),
            ValidationRule("value_equals", {"field": "requires_hitl", "value": True}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="short_description",
        agent="bid",
        input_data={
            "job_title": "Landing page",
            "budget": 200,
            "skills": ["HTML"],
            "client_rating": 4.0,
            "description": "One page site.",
        },
        expected_output=_fenced({
            "proposal_text": (
                "I can build a clean, responsive landing page for you. "
                "Even with a brief description, I'll deliver a polished result. "
                "Happy to discuss the details."
            ),
            "bid_amount": 180,
            "delivery_days": 3,
            "confidence_score": 0.75,
            "requires_hitl": True,
        }),
        validators=[
            ValidationRule("length_range", {"field": "proposal_text", "min": 15, "max": 250}),
            ValidationRule("score_range", {"field": "bid_amount", "min": 100, "max": 200}),
            ValidationRule("value_equals", {"field": "requires_hitl", "value": True}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
]
