"""Unit tests for Scout Category Pre-Filter (L5).

Tests cover: category filtering before LLM scoring, config loading,
token cost reduction by filtering jobs, edge cases.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.scout import ScoutAgent, apply_category_modifier

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scout(adapters: dict[str, Any] | None = None) -> ScoutAgent:
    return ScoutAgent(
        llm_client=MagicMock(),
        heartbeat=AsyncMock(),
        loop_detector=AsyncMock(),
        adapters=adapters or {},
    )


def _make_jobs(categories: list[str]) -> list[dict[str, Any]]:
    """Create test jobs with specified categories."""
    jobs = []
    for i, cat in enumerate(categories):
        jobs.append(
            {
                "platform": "freelancer",
                "external_id": f"job-{i}",
                "title": f"Test job {i}",
                "description": f"Description for category {cat}",
                "category": cat,
                "budget_min": 100,
                "budget_max": 500,
                "currency": "USD",
            }
        )
    return jobs


# ---------------------------------------------------------------------------
# Category pre-filter
# ---------------------------------------------------------------------------


class TestCategoryPreFilter:
    """ScoutAgent._filter_by_categories removes non-matching jobs."""

    def test_filter_keeps_matching_categories(self):
        scout = _make_scout()
        jobs = _make_jobs(["web_development", "mobile_apps", "data_science"])
        config = {
            "categories_auto": ["web_development", "mobile_apps"],
            "categories_suggest": [],
        }
        result = scout._filter_by_categories(jobs, config)
        assert len(result) == 2
        categories = [j["category"] for j in result]
        assert "web_development" in categories
        assert "mobile_apps" in categories

    def test_filter_removes_non_matching(self):
        scout = _make_scout()
        jobs = _make_jobs(["web_development", "accounting", "legal"])
        config = {
            "categories_auto": ["web_development"],
            "categories_suggest": [],
        }
        result = scout._filter_by_categories(jobs, config)
        assert len(result) == 1
        assert result[0]["category"] == "web_development"

    def test_filter_includes_suggest_categories(self):
        """Suggest categories should still pass filter (scored lower by modifier)."""
        scout = _make_scout()
        jobs = _make_jobs(["web_development", "graphic_design"])
        config = {
            "categories_auto": ["web_development"],
            "categories_suggest": ["graphic_design"],
        }
        result = scout._filter_by_categories(jobs, config)
        assert len(result) == 2

    def test_filter_returns_all_when_no_config(self):
        """When no categories configured, all jobs pass through."""
        scout = _make_scout()
        jobs = _make_jobs(["web_development", "accounting", "legal"])
        config = {}
        result = scout._filter_by_categories(jobs, config)
        assert len(result) == 3

    def test_filter_returns_all_when_empty_categories(self):
        scout = _make_scout()
        jobs = _make_jobs(["web_development", "accounting"])
        config = {
            "categories_auto": [],
            "categories_suggest": [],
        }
        result = scout._filter_by_categories(jobs, config)
        assert len(result) == 2

    def test_filter_handles_missing_category_field(self):
        """Jobs without a category field should pass through (not filtered)."""
        scout = _make_scout()
        jobs = [
            {"platform": "freelancer", "external_id": "1", "title": "No cat job"},
            {"platform": "freelancer", "external_id": "2", "title": "Cat job", "category": "web_development"},
        ]
        config = {
            "categories_auto": ["web_development"],
            "categories_suggest": [],
        }
        result = scout._filter_by_categories(jobs, config)
        # Job without category passes through + matching job
        assert len(result) == 2

    def test_filter_empty_jobs_list(self):
        scout = _make_scout()
        config = {"categories_auto": ["web_development"]}
        result = scout._filter_by_categories([], config)
        assert result == []

    def test_filter_case_insensitive(self):
        """Category matching should be case-insensitive."""
        scout = _make_scout()
        jobs = _make_jobs(["Web_Development", "MOBILE_APPS"])
        config = {
            "categories_auto": ["web_development", "mobile_apps"],
            "categories_suggest": [],
        }
        result = scout._filter_by_categories(jobs, config)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# apply_category_modifier
# ---------------------------------------------------------------------------


class TestApplyCategoryModifier:
    """Score modifier based on category classification."""

    def test_auto_category_no_modifier(self):
        config = {"categories_auto": ["web_development"], "categories_suggest": []}
        result = apply_category_modifier("web_development", config)
        assert result == 0.0

    def test_suggest_category_negative_modifier(self):
        config = {"categories_auto": [], "categories_suggest": ["graphic_design"]}
        result = apply_category_modifier("graphic_design", config)
        assert result == -0.2

    def test_unknown_category_no_modifier(self):
        config = {"categories_auto": ["web_development"], "categories_suggest": ["graphic_design"]}
        result = apply_category_modifier("accounting", config)
        assert result == 0.0

    def test_empty_config_no_modifier(self):
        config = {}
        result = apply_category_modifier("web_development", config)
        assert result == 0.0


# ---------------------------------------------------------------------------
# Token cost reduction verification
# ---------------------------------------------------------------------------


class TestTokenCostReduction:
    """Verify that pre-filtering reduces the number of jobs sent to LLM."""

    def test_filter_reduces_batch_size(self):
        scout = _make_scout()
        # 10 jobs but only 3 match categories
        categories = ["web_development"] * 3 + ["accounting"] * 7
        jobs = _make_jobs(categories)
        config = {
            "categories_auto": ["web_development"],
            "categories_suggest": [],
        }
        result = scout._filter_by_categories(jobs, config)
        assert len(result) == 3
        # 70% reduction in LLM calls
        assert len(result) < len(jobs)

    def test_all_matching_no_reduction(self):
        scout = _make_scout()
        jobs = _make_jobs(["web_development"] * 5)
        config = {
            "categories_auto": ["web_development"],
            "categories_suggest": [],
        }
        result = scout._filter_by_categories(jobs, config)
        assert len(result) == 5
