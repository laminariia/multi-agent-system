"""Fixtures for golden set regression tests.

Loads all per-agent golden cases into a unified fixture that can be
parametrized across the entire test suite.
"""

from __future__ import annotations

import pytest

from tests.golden_set.bid_agent_golden import CASES as bid_cases
from tests.golden_set.content_agent_golden import CASES as content_cases
from tests.golden_set.critic_agent_golden import CASES as critic_cases
from tests.golden_set.evaluator import GoldenCase
from tests.golden_set.outreach_agent_golden import CASES as outreach_cases
from tests.golden_set.planner_agent_golden import CASES as planner_cases
from tests.golden_set.scout_agent_golden import CASES as scout_cases

ALL_CASES: list[GoldenCase] = scout_cases + bid_cases + content_cases + planner_cases + critic_cases + outreach_cases


@pytest.fixture(params=ALL_CASES, ids=lambda c: f"{c.agent}-{c.name}")
def golden_case(request: pytest.FixtureRequest) -> GoldenCase:
    """Parametrized fixture yielding each golden case one at a time."""
    return request.param
