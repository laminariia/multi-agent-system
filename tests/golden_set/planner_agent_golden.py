"""Golden set data for the Planner Agent (3 cases)."""

from __future__ import annotations

import json

from tests.golden_set.evaluator import GoldenCase, ValidationRule

CASES: list[GoldenCase] = [
    GoldenCase(
        name="simple_website",
        agent="planner",
        input_data={
            "project_id": "proj_test_001",
            "job_title": "WordPress landing page for restaurant",
            "budget": 500,
            "requirements": "One-page responsive site with menu, gallery, contact form, Google Maps.",
        },
        expected_output='```json\n' + json.dumps({
            "phases": [
                {"name": "Setup", "tasks": [
                    {"id": "task_1", "description": "Create WP installation", "assigned_to": "dev",
                     "estimated_hours": 0.5, "dependencies": [], "deliverables": ["wp_site"]},
                ]},
                {"name": "Content", "tasks": [
                    {"id": "task_2", "description": "Write page copy", "assigned_to": "content",
                     "estimated_hours": 1.0, "dependencies": ["task_1"], "deliverables": ["copy.md"]},
                ]},
                {"name": "Build", "tasks": [
                    {"id": "task_3", "description": "Build landing page", "assigned_to": "dev",
                     "estimated_hours": 3.0, "dependencies": ["task_2"], "deliverables": ["page"]},
                ]},
            ],
            "total_estimated_hours": 4.5,
            "critical_path": ["task_1", "task_2", "task_3"],
            "risks": ["Client may change requirements after first draft."],
        }, indent=2) + '\n```',
        validators=[
            ValidationRule("list_non_empty", {"field": "phases"}),
            ValidationRule("list_non_empty", {"field": "risks"}),
            ValidationRule("list_non_empty", {"field": "critical_path"}),
            ValidationRule("score_range", {"field": "total_estimated_hours", "min": 2, "max": 20}),
            ValidationRule("has_keys", {"keys": ["phases", "total_estimated_hours", "risks"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="complex_fullstack",
        agent="planner",
        input_data={
            "project_id": "proj_test_002",
            "job_title": "Full e-commerce site with admin panel",
            "budget": 4000,
            "requirements": "Product catalog, cart, checkout, Stripe, admin dashboard, email notifications.",
        },
        expected_output=json.dumps({
            "phases": [
                {"name": "Discovery", "tasks": [
                    {"id": "task_1", "description": "Analyse requirements", "assigned_to": "dev",
                     "estimated_hours": 1.0, "dependencies": [], "deliverables": ["spec"]},
                ]},
                {"name": "Design", "tasks": [
                    {"id": "task_2", "description": "UI mockups", "assigned_to": "design",
                     "estimated_hours": 4.0, "dependencies": ["task_1"], "deliverables": ["figma"]},
                ]},
                {"name": "Backend", "tasks": [
                    {"id": "task_3", "description": "API + DB", "assigned_to": "dev",
                     "estimated_hours": 4.0, "dependencies": ["task_1"], "deliverables": ["api"]},
                    {"id": "task_4", "description": "Stripe integration", "assigned_to": "dev",
                     "estimated_hours": 3.0, "dependencies": ["task_3"], "deliverables": ["payments"]},
                ]},
                {"name": "Frontend", "tasks": [
                    {"id": "task_5", "description": "Store + cart", "assigned_to": "dev",
                     "estimated_hours": 4.0, "dependencies": ["task_2", "task_3"], "deliverables": ["store"]},
                    {"id": "task_6", "description": "Admin panel", "assigned_to": "dev",
                     "estimated_hours": 4.0, "dependencies": ["task_3"], "deliverables": ["admin"]},
                ]},
                {"name": "QA", "tasks": [
                    {"id": "task_7", "description": "Code review", "assigned_to": "critic",
                     "estimated_hours": 2.0, "dependencies": ["task_5", "task_6"], "deliverables": ["report"]},
                ]},
            ],
            "total_estimated_hours": 22.0,
            "critical_path": ["task_1", "task_3", "task_5", "task_7"],
            "risks": [
                "Complex project, recommend milestones.",
                "Stripe integration may need client sandbox credentials.",
            ],
        }),
        validators=[
            ValidationRule("list_non_empty", {"field": "phases"}),
            ValidationRule("list_non_empty", {"field": "risks"}),
            ValidationRule("score_range", {"field": "total_estimated_hours", "min": 10, "max": 60}),
            ValidationRule("has_keys", {"keys": ["phases", "total_estimated_hours", "critical_path", "risks"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="design_heavy",
        agent="planner",
        input_data={
            "project_id": "proj_test_003",
            "job_title": "Brand identity + website for fashion startup",
            "budget": 2000,
            "requirements": "Logo, brand colors, typography, 3-page website with lookbook gallery.",
        },
        expected_output='```json\n' + json.dumps({
            "phases": [
                {"name": "Brand Discovery", "tasks": [
                    {"id": "task_1", "description": "Mood board + brand brief", "assigned_to": "design",
                     "estimated_hours": 2.0, "dependencies": [], "deliverables": ["mood_board"]},
                ]},
                {"name": "Identity", "tasks": [
                    {"id": "task_2", "description": "Logo + palette + typography", "assigned_to": "design",
                     "estimated_hours": 4.0, "dependencies": ["task_1"], "deliverables": ["brand_kit"]},
                ]},
                {"name": "Build", "tasks": [
                    {"id": "task_3", "description": "3-page site + gallery", "assigned_to": "dev",
                     "estimated_hours": 4.0, "dependencies": ["task_2"], "deliverables": ["website"]},
                ]},
                {"name": "Review", "tasks": [
                    {"id": "task_4", "description": "Quality review", "assigned_to": "critic",
                     "estimated_hours": 1.0, "dependencies": ["task_3"], "deliverables": ["review"]},
                ]},
            ],
            "total_estimated_hours": 11.0,
            "critical_path": ["task_1", "task_2", "task_3", "task_4"],
            "risks": ["Design iterations may extend timeline."],
        }, indent=2) + '\n```',
        validators=[
            ValidationRule("list_non_empty", {"field": "phases"}),
            ValidationRule("list_non_empty", {"field": "risks"}),
            ValidationRule("score_range", {"field": "total_estimated_hours", "min": 5, "max": 30}),
            ValidationRule("has_keys", {"keys": ["phases", "total_estimated_hours", "risks"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
]
