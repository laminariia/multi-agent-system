"""Unit tests for src.negotiations.scope_change -- ScopeChangeHandler.

Tests cover: analyze returns ScopeChangePayload, LLM-based analysis, heuristic
fallback when LLM unavailable or fails, 3 options (include/phase_2/revise),
and cost/time deltas.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage

from src.negotiations.scope_change import ScopeChangeHandler, ScopeChangePayload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm(response_dict: dict) -> AsyncMock:
    client = AsyncMock()
    content = json.dumps(response_dict)
    ai_msg = AIMessage(content=content)
    metrics = MagicMock()
    client.call = AsyncMock(return_value=(ai_msg, metrics))
    return client


def _scope_change_response(**overrides) -> dict:
    base = {
        "is_scope_change": True,
        "new_requirements": "Client wants mobile app support",
        "estimated_additional_cost": 300,
        "estimated_additional_days": 5,
        "options": [
            {"name": "include", "description": "Include in current project", "cost_delta": 300, "time_delta_days": 5},
            {"name": "phase_2", "description": "Defer to Phase 2", "cost_delta": 0, "time_delta_days": 0},
            {"name": "revise", "description": "Replace existing feature", "cost_delta": 0, "time_delta_days": 0},
        ],
    }
    base.update(overrides)
    return base


def _not_scope_change_response() -> dict:
    return {
        "is_scope_change": False,
        "new_requirements": "",
        "estimated_additional_cost": 0,
        "estimated_additional_days": 0,
        "options": [],
    }


# ---------------------------------------------------------------------------
# Tests: LLM-based analysis
# ---------------------------------------------------------------------------


class TestLLMAnalysis:
    """Test LLM-powered scope change analysis."""

    async def test_analyze_returns_scope_change_payload(self):
        llm = _make_llm(_scope_change_response())
        handler = ScopeChangeHandler(llm_client=llm)

        result = await handler.analyze(
            original_requirements="Build a web app",
            message_content="Can you also make a mobile version?",
            current_amount=1000.0,
            current_days=14,
        )

        assert isinstance(result, ScopeChangePayload)
        assert result.requested_changes == "Client wants mobile app support"
        assert result.estimated_additional_cost == 300.0
        assert result.estimated_additional_time_days == 5

    async def test_analyze_has_three_options(self):
        llm = _make_llm(_scope_change_response())
        handler = ScopeChangeHandler(llm_client=llm)

        result = await handler.analyze(
            original_requirements="Build a web app",
            message_content="Add mobile support",
            current_amount=1000.0,
            current_days=14,
        )

        assert len(result.options) == 3

    async def test_options_contain_include_phase2_revise(self):
        llm = _make_llm(_scope_change_response())
        handler = ScopeChangeHandler(llm_client=llm)

        result = await handler.analyze(
            original_requirements="Build a web app",
            message_content="Add mobile support",
            current_amount=1000.0,
            current_days=14,
        )

        option_names = [o["name"] for o in result.options]
        assert "include" in option_names
        assert "phase_2" in option_names
        assert "revise" in option_names

    async def test_not_scope_change_returns_empty_options(self):
        llm = _make_llm(_not_scope_change_response())
        handler = ScopeChangeHandler(llm_client=llm)

        result = await handler.analyze(
            original_requirements="Build a web app",
            message_content="When can you start?",
            current_amount=1000.0,
            current_days=14,
        )

        assert result.estimated_additional_cost == 0.0
        assert result.estimated_additional_time_days == 0
        assert result.options == []

    async def test_conversation_summary_passed_through(self):
        llm = _make_llm(_scope_change_response())
        handler = ScopeChangeHandler(llm_client=llm)

        result = await handler.analyze(
            original_requirements="Build a web app",
            message_content="Add mobile",
            current_amount=1000.0,
            current_days=14,
            conversation_summary="Client discussed mobile needs",
        )

        assert result.conversation_summary == "Client discussed mobile needs"

    async def test_negative_cost_clamped_to_zero(self):
        resp = _scope_change_response(estimated_additional_cost=-100)
        llm = _make_llm(resp)
        handler = ScopeChangeHandler(llm_client=llm)

        result = await handler.analyze(
            original_requirements="Build a web app",
            message_content="Remove a feature",
            current_amount=1000.0,
            current_days=14,
        )

        assert result.estimated_additional_cost == 0.0

    async def test_negative_days_clamped_to_zero(self):
        resp = _scope_change_response(estimated_additional_days=-3)
        llm = _make_llm(resp)
        handler = ScopeChangeHandler(llm_client=llm)

        result = await handler.analyze(
            original_requirements="Build a web app",
            message_content="Simplify scope",
            current_amount=1000.0,
            current_days=14,
        )

        assert result.estimated_additional_time_days == 0


# ---------------------------------------------------------------------------
# Tests: Heuristic fallback when LLM unavailable
# ---------------------------------------------------------------------------


class TestHeuristicFallbackNoLLM:
    """Test heuristic fallback when no LLM client is provided."""

    async def test_heuristic_when_llm_none(self):
        handler = ScopeChangeHandler(llm_client=None)

        result = await handler.analyze(
            original_requirements="Build a web app",
            message_content="Can you add a chat feature?",
            current_amount=1000.0,
            current_days=15,
        )

        assert isinstance(result, ScopeChangePayload)
        # Heuristic: 20% of current amount
        assert result.estimated_additional_cost == 200.0
        # Heuristic: current_days // 5 = 3
        assert result.estimated_additional_time_days == 3
        assert len(result.options) == 3

    async def test_heuristic_with_zero_days(self):
        handler = ScopeChangeHandler(llm_client=None)

        result = await handler.analyze(
            original_requirements="Quick fix",
            message_content="Add extra feature",
            current_amount=500.0,
            current_days=0,
        )

        assert result.estimated_additional_time_days == 3  # fallback default


# ---------------------------------------------------------------------------
# Tests: Heuristic fallback when LLM fails
# ---------------------------------------------------------------------------


class TestHeuristicFallbackLLMFails:
    """Test heuristic fallback when LLM call fails."""

    async def test_llm_exception_falls_back_to_heuristic(self):
        llm = AsyncMock()
        llm.call = AsyncMock(side_effect=RuntimeError("LLM down"))
        handler = ScopeChangeHandler(llm_client=llm)

        result = await handler.analyze(
            original_requirements="Build a web app",
            message_content="Add a dashboard",
            current_amount=2000.0,
            current_days=20,
        )

        assert isinstance(result, ScopeChangePayload)
        assert result.estimated_additional_cost == 400.0  # 20% of 2000
        assert len(result.options) == 3

    async def test_malformed_json_falls_back_to_heuristic(self):
        llm = AsyncMock()
        ai_msg = AIMessage(content="This is not valid JSON at all")
        metrics = MagicMock()
        llm.call = AsyncMock(return_value=(ai_msg, metrics))
        handler = ScopeChangeHandler(llm_client=llm)

        result = await handler.analyze(
            original_requirements="Build a site",
            message_content="Add blog",
            current_amount=1000.0,
            current_days=10,
        )

        assert isinstance(result, ScopeChangePayload)
        assert result.estimated_additional_cost == 200.0
        assert len(result.options) == 3

    async def test_non_dict_json_falls_back_to_heuristic(self):
        llm = AsyncMock()
        ai_msg = AIMessage(content="[1, 2, 3]")
        metrics = MagicMock()
        llm.call = AsyncMock(return_value=(ai_msg, metrics))
        handler = ScopeChangeHandler(llm_client=llm)

        result = await handler.analyze(
            original_requirements="Build a site",
            message_content="Add blog",
            current_amount=800.0,
            current_days=10,
        )

        assert isinstance(result, ScopeChangePayload)
        assert result.estimated_additional_cost == 160.0


# ---------------------------------------------------------------------------
# Tests: Default options structure
# ---------------------------------------------------------------------------


class TestDefaultOptions:
    """Test _generate_default_options produces correct structure."""

    def test_default_options_structure(self):
        options = ScopeChangeHandler._generate_default_options(200.0, 3)

        assert len(options) == 3
        assert options[0]["name"] == "include"
        assert options[0]["cost_delta"] == 200.0
        assert options[0]["time_delta_days"] == 3
        assert options[1]["name"] == "phase_2"
        assert options[1]["cost_delta"] == 0.0
        assert options[1]["time_delta_days"] == 0
        assert options[2]["name"] == "revise"
        assert options[2]["cost_delta"] == 0.0
        assert options[2]["time_delta_days"] == 0

    def test_default_options_descriptions_not_empty(self):
        options = ScopeChangeHandler._generate_default_options(100.0, 2)
        for opt in options:
            assert len(opt["description"]) > 0


# ---------------------------------------------------------------------------
# Tests: LLM with fewer than 3 options fills defaults
# ---------------------------------------------------------------------------


class TestIncompleteOptions:
    """When LLM returns fewer than 3 options, defaults fill in."""

    async def test_fewer_than_3_options_replaced_with_defaults(self):
        resp = _scope_change_response()
        resp["options"] = [
            {"name": "include", "description": "Include it", "cost_delta": 100, "time_delta_days": 2},
        ]
        llm = _make_llm(resp)
        handler = ScopeChangeHandler(llm_client=llm)

        result = await handler.analyze(
            original_requirements="Build a site",
            message_content="Add analytics",
            current_amount=1000.0,
            current_days=10,
        )

        assert len(result.options) == 3
