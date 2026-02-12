"""Golden set regression tests for LLM agent outputs.

These tests validate that agent prompt templates produce outputs matching
structural quality requirements. Each golden case stores the expected LLM
response as a **raw string** (possibly with markdown fences). Tests parse
it through ``json_repair.extract_json()`` and validate with Pydantic schemas.

Run:
    pytest tests/golden_set/ -m golden -v
"""

from __future__ import annotations

import pytest

from src.agents.schemas import (
    BidProposal,
    ContentDeliverable,
    CriticVerdict,
    OutreachEmail,
    PlannerPlan,
    ScoutScoredJob,
)
from src.core.json_repair import extract_and_validate, extract_json
from tests.golden_set.evaluator import GoldenCase, evaluate_case

# ---------------------------------------------------------------------------
# Agent -> Pydantic model mapping
# ---------------------------------------------------------------------------

_AGENT_MODEL_MAP = {
    "scout": ScoutScoredJob,
    "bid": BidProposal,
    "planner": PlannerPlan,
    "content": ContentDeliverable,
    "critic": CriticVerdict,
    "outreach": OutreachEmail,
}

pytestmark = pytest.mark.golden


# ---------------------------------------------------------------------------
# Core tests (use the parametrized ``golden_case`` fixture from conftest.py)
# ---------------------------------------------------------------------------


def test_golden_case_parses(golden_case: GoldenCase) -> None:
    """Each golden case output must parse as valid JSON via json_repair."""
    result = extract_json(golden_case.expected_output)
    assert result is not None
    assert isinstance(result, dict)


def test_golden_case_pydantic(golden_case: GoldenCase) -> None:
    """Each golden case output must validate against its Pydantic schema."""
    model = _AGENT_MODEL_MAP.get(golden_case.agent)
    assert model is not None, f"Unknown agent: {golden_case.agent}"
    instance = extract_and_validate(golden_case.expected_output, model)
    assert instance is not None


def test_golden_case_validates(golden_case: GoldenCase) -> None:
    """Each golden case must pass all validation rules."""
    parsed = extract_json(golden_case.expected_output)
    eval_result = evaluate_case(golden_case, parsed)
    assert eval_result.passed, f"Failed: {eval_result.details}"
