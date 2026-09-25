"""Golden set data for the Critic Agent (4 cases)."""

from __future__ import annotations

import json

from tests.golden_set.evaluator import GoldenCase, ValidationRule

CASES: list[GoldenCase] = [
    GoldenCase(
        name="clean_code_approve",
        agent="critic",
        input_data={
            "artifact_type": "code",
            "code": "function Hero({title}) { return <h1 aria-label={title}>{title}</h1>; }",
            "requirements": "Accessible hero component",
        },
        expected_output=json.dumps(
            {
                "verdict": "approve",
                "score": 0.95,
                "revision_type": "none",
                "issues": [],
                "passed_checks": ["compiles", "accessibility", "responsive"],
                "failed_checks": [],
                "revision_instructions": "",
            }
        ),
        validators=[
            ValidationRule("value_in", {"field": "verdict", "values": ["approve"]}),
            ValidationRule("score_range", {"field": "score", "min": 0.85, "max": 1.0}),
            ValidationRule("has_keys", {"keys": ["verdict", "score", "passed_checks"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="minor_issues",
        agent="critic",
        input_data={
            "artifact_type": "code",
            "code": "<img src={img} /><button>Add</button>",
            "requirements": "Accessible product card",
        },
        expected_output="```json\n"
        + json.dumps(
            {
                "verdict": "revise",
                "score": 0.82,
                "revision_type": "minor",
                "issues": [
                    {
                        "type": "accessibility",
                        "severity": "minor",
                        "description": "Missing alt on img",
                        "location": "line 1",
                    }
                ],
                "passed_checks": ["compiles"],
                "failed_checks": ["accessibility"],
                "revision_instructions": "Add alt attribute to <img> tag.",
            },
            indent=2,
        )
        + "\n```",
        validators=[
            ValidationRule("value_in", {"field": "verdict", "values": ["approve", "revise"]}),
            ValidationRule("score_range", {"field": "score", "min": 0.60, "max": 1.0}),
            ValidationRule("has_keys", {"keys": ["verdict", "score"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="security_vuln",
        agent="critic",
        input_data={
            "artifact_type": "code",
            "code": 'import os\ndef run(cmd): os.system(cmd)\ndef q(uid): f"SELECT * FROM u WHERE id={uid}"',
            "requirements": "User management",
        },
        expected_output=json.dumps(
            {
                "verdict": "reject",
                "score": 0.30,
                "revision_type": "none",
                "issues": [
                    {"type": "security", "severity": "critical", "description": "os.system() command injection"},
                    {"type": "security", "severity": "critical", "description": "SQL injection via f-string"},
                ],
                "passed_checks": ["compiles"],
                "failed_checks": ["security"],
                "revision_instructions": "Remove os.system, use parameterized SQL.",
            }
        ),
        validators=[
            ValidationRule("value_in", {"field": "verdict", "values": ["revise", "reject"]}),
            ValidationRule("score_range", {"field": "score", "min": 0.0, "max": 0.50}),
            ValidationRule("list_non_empty", {"field": "issues"}),
            ValidationRule("has_keys", {"keys": ["verdict", "score", "issues"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="missing_tests",
        agent="critic",
        input_data={
            "artifact_type": "code",
            "code": "function add(a, b) { return a + b; }\nexport default add;",
            "requirements": "Utility function with tests",
        },
        expected_output="```json\n"
        + json.dumps(
            {
                "verdict": "revise",
                "score": 0.75,
                "revision_type": "minor",
                "issues": [{"type": "testing", "severity": "medium", "description": "No tests included"}],
                "passed_checks": ["compiles", "security"],
                "failed_checks": ["tests_included"],
                "revision_instructions": "Add unit tests for the add function.",
            },
            indent=2,
        )
        + "\n```",
        validators=[
            ValidationRule("value_in", {"field": "verdict", "values": ["revise"]}),
            ValidationRule("score_range", {"field": "score", "min": 0.60, "max": 0.85}),
            ValidationRule("has_keys", {"keys": ["verdict", "score", "revision_instructions"]}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
]
