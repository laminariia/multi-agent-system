"""Golden set data for the Content Agent (3 cases)."""

from __future__ import annotations

import json

from tests.golden_set.evaluator import GoldenCase, ValidationRule

CASES: list[GoldenCase] = [
    GoldenCase(
        name="blog_post",
        agent="content",
        input_data={
            "content_type": "blog_post",
            "business": "SaaS analytics platform",
            "audience": "Product managers and data analysts",
            "language": "en",
            "topic": "5 KPI dashboards every PM needs",
        },
        expected_output='```json\n' + json.dumps({
            "content_type": "blog_post",
            "deliverables": [
                {
                    "name": "blog_headline",
                    "content": "5 KPI Dashboards Every Product Manager Needs in 2026",
                    "alternatives": [
                        "The PM's Guide to KPI Dashboards",
                        "Build Better Products with These 5 Dashboards",
                    ],
                    "notes": "Numbers in headline for click-through.",
                },
            ],
            "word_count": 120,
            "reading_time_seconds": 36,
        }, indent=2) + '\n```',
        validators=[
            ValidationRule("has_keys", {"keys": ["content_type", "deliverables", "word_count"]}),
            ValidationRule("list_non_empty", {"field": "deliverables"}),
            ValidationRule("must_not_mention", {"keywords": ["Lorem ipsum", "click here"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="landing_page",
        agent="content",
        input_data={
            "content_type": "landing_page",
            "business": "Dental clinic",
            "audience": "Local patients aged 25-55",
            "language": "en",
        },
        expected_output=json.dumps({
            "content_type": "landing_page",
            "deliverables": [
                {
                    "name": "hero_headline",
                    "content": "Transform Your Smile Today",
                    "alternatives": ["Your Smile, Our Priority", "Confident Smiles Start Here"],
                    "notes": "Action verb + benefit.",
                },
                {
                    "name": "cta_button",
                    "content": "Book Your Free Consultation",
                    "alternatives": ["Schedule a Visit", "Get Started"],
                    "notes": "Low-friction CTA.",
                },
                {
                    "name": "meta_description",
                    "content": "Professional dental care with a personal touch. Book your free consultation today.",
                    "alternatives": ["Trusted dental clinic.", "Smile with confidence."],
                    "notes": "Under 155 chars for SEO.",
                },
            ],
            "word_count": 35,
            "reading_time_seconds": 10,
        }),
        validators=[
            ValidationRule("has_keys", {"keys": ["content_type", "deliverables", "word_count"]}),
            ValidationRule("list_non_empty", {"field": "deliverables"}),
            ValidationRule("must_not_mention", {"keywords": ["Lorem ipsum", "innovative", "cutting-edge"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="technical_docs",
        agent="content",
        input_data={
            "content_type": "documentation",
            "business": "REST API service",
            "audience": "Backend developers",
            "language": "en",
            "topic": "API authentication guide",
        },
        expected_output='```json\n' + json.dumps({
            "content_type": "documentation",
            "deliverables": [
                {
                    "name": "auth_guide_section",
                    "content": "Authentication uses JWT Bearer tokens. Send a POST to /auth/login.",
                    "alternatives": [
                        "Getting started with API authentication.",
                        "All requests require a valid JWT token.",
                    ],
                    "notes": "Direct, code-oriented language for developers.",
                },
            ],
            "word_count": 20,
            "reading_time_seconds": 6,
        }, indent=2) + '\n```',
        validators=[
            ValidationRule("has_keys", {"keys": ["content_type", "deliverables"]}),
            ValidationRule("list_non_empty", {"field": "deliverables"}),
            ValidationRule("must_not_mention", {"keywords": ["Lorem ipsum", "game-changer"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
]
