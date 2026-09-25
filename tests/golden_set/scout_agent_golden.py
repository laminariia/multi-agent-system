"""Golden set data for the Scout Agent (5 cases)."""

from __future__ import annotations

from tests.golden_set.evaluator import GoldenCase, ValidationRule

CASES: list[GoldenCase] = [
    GoldenCase(
        name="web_dev_high_match",
        agent="scout",
        input_data={
            "job_title": "React landing page for SaaS product",
            "budget": 800,
            "skills": ["React", "Tailwind", "Next.js"],
            "client_rating": 4.9,
            "description": "Modern responsive landing with animations, pricing table, testimonials.",
        },
        expected_output=(
            "```json\n"
            '{"job_id": "freelancer_react_landi", "platform": "freelancer",'
            ' "title": "React landing page for SaaS product",'
            ' "match_score": 0.92, "recommendation": "bid",'
            ' "reasoning": "Strong match: React + Tailwind in our core stack, fair budget, proven client.",'
            ' "skills_match": ["React", "Tailwind", "Next.js"]}\n'
            "```"
        ),
        validators=[
            ValidationRule("value_in", {"field": "recommendation", "values": ["bid"]}),
            ValidationRule("score_range", {"field": "match_score", "min": 0.7, "max": 1.0}),
            ValidationRule("has_keys", {"keys": ["recommendation", "match_score", "reasoning"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="mobile_app_medium",
        agent="scout",
        input_data={
            "job_title": "Cross-platform mobile app with React Native",
            "budget": 3000,
            "skills": ["React Native", "TypeScript", "Firebase"],
            "client_rating": 4.2,
            "description": "Simple inventory tracking app for iOS and Android.",
        },
        expected_output=(
            '{"match_score": 0.55, "recommendation": "review",'
            ' "reasoning": "Mobile app is outside primary capabilities; may require HITL review."}'
        ),
        validators=[
            ValidationRule("value_in", {"field": "recommendation", "values": ["review", "skip"]}),
            ValidationRule("score_range", {"field": "match_score", "min": 0.0, "max": 0.70}),
            ValidationRule("has_keys", {"keys": ["recommendation", "reasoning"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="data_entry_low_match",
        agent="scout",
        input_data={
            "job_title": "Data entry from PDF to spreadsheet",
            "budget": 50,
            "skills": ["Data Entry", "Excel"],
            "client_rating": 3.0,
            "description": "Need someone to copy data from 200 PDF invoices into Excel.",
        },
        expected_output=(
            "```json\n"
            '{"match_score": 0.15, "recommendation": "skip",'
            ' "reasoning": "Data entry is not in our capability matrix."}\n'
            "```"
        ),
        validators=[
            ValidationRule("value_equals", {"field": "recommendation", "value": "skip"}),
            ValidationRule("score_range", {"field": "match_score", "min": 0.0, "max": 0.30}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="missing_fields",
        agent="scout",
        input_data={
            "job_title": "Build something",
            "budget": 0,
            "skills": [],
            "client_rating": 0,
            "description": "",
        },
        expected_output=(
            '{"match_score": 0.1, "recommendation": "skip",'
            ' "reasoning": "Vague requirements, missing budget and skills."}'
        ),
        validators=[
            ValidationRule("value_equals", {"field": "recommendation", "value": "skip"}),
            ValidationRule("score_range", {"field": "match_score", "min": 0.0, "max": 0.40}),
            ValidationRule("has_keys", {"keys": ["recommendation", "reasoning"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="wordpress_bid",
        agent="scout",
        input_data={
            "job_title": "Simple WordPress blog setup with SEO",
            "budget": 300,
            "skills": ["WordPress", "SEO"],
            "client_rating": 4.5,
            "description": "Need a WordPress blog with a clean theme, basic SEO setup, contact page.",
        },
        expected_output=(
            '{"match_score": 0.85, "recommendation": "bid",'
            ' "reasoning": "WordPress is our core stack, budget fair, client reputable.",'
            ' "skills_match": ["WordPress"]}'
        ),
        validators=[
            ValidationRule("value_equals", {"field": "recommendation", "value": "bid"}),
            ValidationRule("score_range", {"field": "match_score", "min": 0.7, "max": 1.0}),
            ValidationRule("has_keys", {"keys": ["recommendation", "match_score", "reasoning"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
]
