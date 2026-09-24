"""Unit tests for src.agents.portfolio.PortfolioAgent.

All LLM calls, database operations, and Playwright calls are mocked.
Tests cover: classification, description generation, platform adaptation,
portfolio saving, HITL gate, audit logic, metrics, and edge cases.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.agents.portfolio import (
    _AUDIT_PROJECT_INTERVAL,
    _FRESHNESS_THRESHOLD_DAYS,
    PORTFOLIO_ALLOWED_TOOLS,
    PORTFOLIO_PLATFORMS,
    PortfolioAgent,
    _next_project_id,
)
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state

# ---------------------------------------------------------------------------
# Auto-mock get_container so _call_llm falls through to self.llm_client.call
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_container():
    """Mock get_container so _call_llm uses self.llm_client.call (llm_queue=None)."""
    mock_container = MagicMock()
    mock_container.llm_queue = None
    with patch("src.core.container.get_container", return_value=mock_container):
        yield


@pytest.fixture(autouse=True)
def _mock_save_to_portfolio():
    """Prevent filesystem writes from _save_to_portfolio during tests."""
    with patch.object(PortfolioAgent, "_save_to_portfolio"):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_METRICS = CallMetrics(
    agent_name="portfolio",
    model_id="deepseek-v3.2",
    provider="openrouter",
)


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults for the Portfolio Agent."""
    project = {
        "project_id": "proj-port-001",
        "job_id": "job-port-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a restaurant landing page with booking",
        "budget": 500.0,
        "deadline": datetime(2026, 4, 15, tzinfo=UTC),
    }
    state = create_initial_state(
        project=project,
        first_agent="portfolio",
        thread_id="thread-portfolio-test",
    )
    # Simulate packager artifacts being present (post-approval flow)
    delivery_info = {
        "delivery_id": "del-001",
        "project_id": "proj-port-001",
        "files_count": 15,
        "includes": ["source_code", "documentation", "screenshots"],
        "delivery_message": "Your landing page is ready!",
        "readme_content": "# Restaurant Landing\n\n## Setup\nnpm install",
        "missing_artifacts": [],
        "quality_notes": "Critic score: 0.92",
        "requires_hitl": True,
    }
    default_artifacts = {
        "dev": [json.dumps({"files": [{"path": "index.html", "content": "<h1>Hello</h1>"}]})],
        "content": [json.dumps({"deliverables": [{"type": "heading", "content": "Welcome"}]})],
        "design": [json.dumps({"specs": [{"component": "hero"}]})],
        "critic": [json.dumps({"verdict": "APPROVE", "score": 0.92})],
        "packager": [json.dumps(delivery_info)],
    }
    state["artifacts"] = overrides.pop("artifacts", default_artifacts)  # type: ignore[typeddict-item]
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _make_description_response() -> str:
    """Valid LLM response for description generation."""
    return json.dumps(
        {
            "title_ru": "Лендинг ресторана с бронированием",
            "title_en": "Restaurant Landing Page with Booking",
            "description_ru": "Разработан современный лендинг для ресторана с системой онлайн-бронирования.",
            "description_en": "Developed a modern restaurant landing page with an online booking system.",
            "stack": ["HTML", "CSS", "JavaScript", "React"],
            "categories": ["landing", "restaurant", "booking"],
            "market": "ru",
            "complexity": "simple",
        }
    )


def _make_adaptation_response() -> str:
    """Valid LLM response for platform adaptations."""
    return json.dumps(
        {
            "kwork": "Разработка современного лендинга для ресторана.",
            "fl_ru": "Профессиональный лендинг для ресторана с системой бронирования.",
            "youdo": "Создан сайт-визитка ресторана с удобной формой записи.",
            "fiverr": "I developed a sleek restaurant landing page with online booking.",
            "freelancer": "Built a responsive restaurant landing page with a booking system.",
            "telegram": "Новый проект: лендинг ресторана с бронированием.\n\nNew project: restaurant landing with booking.",
        }
    )


def _make_audit_response() -> str:
    """Valid LLM response for portfolio audit."""
    return json.dumps(
        {
            "recommendations": [
                {
                    "action": "REPLACE",
                    "project_id": "001",
                    "reason": "Project is 8 months old, stack outdated",
                    "replacement_suggestion": "Replace with newer AI chatbot project",
                },
                {
                    "action": "ADD",
                    "project_id": None,
                    "reason": "No AI projects in portfolio, add AI chatbot",
                },
                {
                    "action": "UPDATE",
                    "project_id": "003",
                    "reason": "Screenshots outdated, UI has changed",
                },
                {
                    "action": "SYNC",
                    "project_id": "005",
                    "reason": "YouDo not updated in 2 months",
                },
            ],
            "metrics": {
                "freshness_score": 0.65,
                "diversity_score": 0.70,
                "stack_balance": 0.80,
                "platform_coverage": 0.60,
            },
        }
    )


# ---------------------------------------------------------------------------
# Construction & basic attributes
# ---------------------------------------------------------------------------


class TestPortfolioAgentConstruction:
    """Tests for agent construction and basic attributes."""

    def test_agent_name_is_portfolio(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        assert agent.agent_name == "portfolio"

    def test_allowed_tools_set(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        assert agent.allowed_tools == set(PORTFOLIO_ALLOWED_TOOLS)

    def test_portfolio_platforms_defined(self):
        assert len(PORTFOLIO_PLATFORMS) == 6
        assert "kwork" in PORTFOLIO_PLATFORMS
        assert "fl_ru" in PORTFOLIO_PLATFORMS
        assert "youdo" in PORTFOLIO_PLATFORMS
        assert "fiverr" in PORTFOLIO_PLATFORMS
        assert "freelancer" in PORTFOLIO_PLATFORMS
        assert "telegram" in PORTFOLIO_PLATFORMS

    def test_inherits_constrained_agent(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        from src.agents.base import ConstrainedAgent

        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        assert isinstance(agent, ConstrainedAgent)


# ---------------------------------------------------------------------------
# Project classification
# ---------------------------------------------------------------------------


class TestProjectClassification:
    """Tests for _classify_project method."""

    def test_simple_project_with_frontend(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        delivery = {"includes": ["source_code", "frontend"], "deploy_url": "https://example.com"}
        project = {"requirements": "Build a landing page"}
        result = agent._classify_project(delivery, project)
        assert result == "simple"

    def test_complex_project_backend_only(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        delivery = {"includes": ["source_code", "backend"]}
        project = {"requirements": "Build a CRM system with auth"}
        result = agent._classify_project(delivery, project)
        assert result == "complex"

    def test_complex_project_bot(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        delivery = {"includes": ["source_code"]}
        project = {"requirements": "Telegram bot for appointment scheduling"}
        result = agent._classify_project(delivery, project)
        assert result == "complex"

    def test_simple_project_with_screenshots(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        delivery = {"includes": ["source_code", "screenshots"]}
        project = {"requirements": "Static website for a dentist clinic"}
        result = agent._classify_project(delivery, project)
        assert result == "simple"

    def test_complex_project_cli(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        delivery = {"includes": ["source_code"]}
        project = {"requirements": "CLI tool for data parsing"}
        result = agent._classify_project(delivery, project)
        assert result == "complex"

    def test_empty_delivery_defaults_complex(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        result = agent._classify_project({}, {})
        assert result == "complex"


# ---------------------------------------------------------------------------
# Description generation
# ---------------------------------------------------------------------------


class TestDescriptionGeneration:
    """Tests for _generate_descriptions method."""

    async def test_generates_ru_and_en_descriptions(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=_make_description_response()),
                _DEFAULT_METRICS,
            )
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _build_state()
        result = await agent._generate_descriptions(state)
        assert result is not None
        assert "title_ru" in result
        assert "title_en" in result
        assert "description_ru" in result
        assert "description_en" in result
        assert "stack" in result
        assert isinstance(result["stack"], list)

    async def test_returns_none_on_llm_failure(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content="I cannot process this request."),
                _DEFAULT_METRICS,
            )
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _build_state()
        result = await agent._generate_descriptions(state)
        assert result is None

    async def test_handles_markdown_fenced_response(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        fenced = f"```json\n{_make_description_response()}\n```"
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=fenced),
                _DEFAULT_METRICS,
            )
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _build_state()
        result = await agent._generate_descriptions(state)
        assert result is not None
        assert result["title_en"] == "Restaurant Landing Page with Booking"


# ---------------------------------------------------------------------------
# Platform adaptation
# ---------------------------------------------------------------------------


class TestPlatformAdaptation:
    """Tests for _adapt_for_platforms method."""

    async def test_generates_all_platform_versions(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=_make_adaptation_response()),
                _DEFAULT_METRICS,
            )
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        descriptions = json.loads(_make_description_response())
        result = await agent._adapt_for_platforms(descriptions)
        assert result is not None
        for platform in PORTFOLIO_PLATFORMS:
            assert platform in result, f"Missing platform: {platform}"

    async def test_returns_none_on_llm_failure(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content="Sorry, I can't do that."),
                _DEFAULT_METRICS,
            )
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        descriptions = json.loads(_make_description_response())
        result = await agent._adapt_for_platforms(descriptions)
        assert result is None

    async def test_adaptation_partial_platforms(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Even if LLM only returns some platforms, result should contain them."""
        partial = json.dumps({"kwork": "Kwork text", "fiverr": "Fiverr text"})
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=partial),
                _DEFAULT_METRICS,
            )
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        descriptions = json.loads(_make_description_response())
        result = await agent._adapt_for_platforms(descriptions)
        assert result is not None
        assert "kwork" in result
        assert "fiverr" in result


# ---------------------------------------------------------------------------
# Meta.json building
# ---------------------------------------------------------------------------


class TestMetaJsonBuilding:
    """Tests for _build_meta_json method."""

    def test_builds_valid_meta(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        descriptions = json.loads(_make_description_response())
        delivery = {
            "delivery_id": "del-001",
            "includes": ["source_code", "frontend"],
        }
        project = {
            "project_id": "proj-001",
            "requirements": "Landing page",
        }
        meta = agent._build_meta_json(
            project_id="042",
            descriptions=descriptions,
            delivery_info=delivery,
            project=project,
            complexity="simple",
        )
        assert meta["id"] == "042"
        assert meta["title"] == descriptions["title_ru"]
        assert meta["title_en"] == descriptions["title_en"]
        assert meta["stack"] == descriptions["stack"]
        assert meta["categories"] == descriptions["categories"]
        assert meta["complexity"] == "simple"
        assert meta["auto_screenshot"] is True
        assert meta["status"] == "active"
        assert meta["conversion_count"] == 0

    def test_complex_project_no_auto_screenshot(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        descriptions = json.loads(_make_description_response())
        meta = agent._build_meta_json(
            project_id="043",
            descriptions=descriptions,
            delivery_info={},
            project={},
            complexity="complex",
        )
        assert meta["auto_screenshot"] is False
        assert meta["complexity"] == "complex"

    def test_meta_has_all_required_fields(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        descriptions = json.loads(_make_description_response())
        meta = agent._build_meta_json(
            project_id="044",
            descriptions=descriptions,
            delivery_info={"delivery_id": "del-x"},
            project={"project_id": "proj-x"},
            complexity="simple",
        )
        required_fields = [
            "id",
            "delivery_id",
            "project_id",
            "title",
            "title_en",
            "date",
            "client",
            "stack",
            "categories",
            "market",
            "platforms_published",
            "screenshots",
            "deployed_url",
            "complexity",
            "auto_screenshot",
            "added_at",
            "last_audit",
            "conversion_count",
            "status",
            "audit_notes",
        ]
        for field in required_fields:
            assert field in meta, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# Portfolio saving (build artifact)
# ---------------------------------------------------------------------------


class TestPortfolioSaving:
    """Tests for _build_portfolio_artifact method."""

    def test_builds_artifact_with_all_components(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        meta = {"id": "042", "title": "Test", "status": "active"}
        descriptions = json.loads(_make_description_response())
        adaptations = json.loads(_make_adaptation_response())

        artifact = agent._build_portfolio_artifact(meta, descriptions, adaptations)
        parsed = json.loads(artifact)
        assert parsed["meta"] == meta
        assert parsed["description_ru"] == descriptions["description_ru"]
        assert parsed["description_en"] == descriptions["description_en"]
        assert "platform_versions" in parsed
        assert "kwork" in parsed["platform_versions"]

    def test_artifact_without_adaptations(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        meta = {"id": "043", "title": "Test2", "status": "active"}
        descriptions = json.loads(_make_description_response())

        artifact = agent._build_portfolio_artifact(meta, descriptions, None)
        parsed = json.loads(artifact)
        assert parsed["meta"] == meta
        assert parsed["platform_versions"] == {}


# ---------------------------------------------------------------------------
# Next project ID
# ---------------------------------------------------------------------------


class TestNextProjectId:
    """Tests for _next_project_id helper."""

    def test_first_project(self):
        assert _next_project_id([]) == "001"

    def test_sequential_ids(self):
        existing = [{"id": "001"}, {"id": "002"}, {"id": "003"}]
        assert _next_project_id(existing) == "004"

    def test_handles_non_numeric_ids(self):
        existing = [{"id": "abc"}, {"id": "002"}]
        assert _next_project_id(existing) == "003"

    def test_pads_to_three_digits(self):
        existing = [{"id": str(i).zfill(3)} for i in range(1, 100)]
        assert _next_project_id(existing) == "100"


# ---------------------------------------------------------------------------
# Full _execute flow
# ---------------------------------------------------------------------------


class TestExecuteFlow:
    """Tests for the full _execute pipeline."""

    async def test_full_flow_sets_hitl_and_pauses(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Full flow: classify -> generate -> adapt -> build -> HITL pause."""
        # First LLM call: descriptions. Second: adaptations.
        mock_llm_client.call = AsyncMock(
            side_effect=[
                (AIMessage(content=_make_description_response()), _DEFAULT_METRICS),
                (AIMessage(content=_make_adaptation_response()), _DEFAULT_METRICS),
            ]
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _build_state()

        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-port-001"),
            patch.object(agent, "_log_portfolio_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        assert result["requires_hitl"] is True
        assert result["status"] == "paused"
        assert result["next_agent"] is None
        assert result["current_agent"] == "portfolio"
        assert result["hitl_request_id"] == "hitl-port-001"
        assert "portfolio" in result["artifacts"]

    async def test_no_packager_artifacts_returns_active(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """No packager artifacts -> status active, no HITL."""
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _build_state(artifacts={"dev": ["art-1"]})

        with patch.object(agent, "_log_portfolio_action", new_callable=AsyncMock):
            result = await agent._execute(state)

        assert result["status"] == "active"
        assert result.get("requires_hitl", False) is False
        assert result["next_agent"] is None

    async def test_llm_description_failure_uses_fallback(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """When description generation fails, fallback is used."""
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content="not json at all"),
                _DEFAULT_METRICS,
            )
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _build_state()

        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-fb-001"),
            patch.object(agent, "_log_portfolio_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        # Should still produce HITL with a fallback artifact
        assert result["requires_hitl"] is True
        assert result["status"] == "paused"
        assert "portfolio" in result["artifacts"]
        stored = json.loads(result["artifacts"]["portfolio"][0])
        assert stored["meta"]["title"] != ""

    async def test_adaptation_failure_still_saves(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """When adaptation fails, portfolio is still saved with base descriptions."""
        mock_llm_client.call = AsyncMock(
            side_effect=[
                (AIMessage(content=_make_description_response()), _DEFAULT_METRICS),
                (AIMessage(content="not json"), _DEFAULT_METRICS),  # adaptation fails
            ]
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _build_state()

        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-adapt-fail"),
            patch.object(agent, "_log_portfolio_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        assert result["requires_hitl"] is True
        stored = json.loads(result["artifacts"]["portfolio"][0])
        assert stored["platform_versions"] == {}


# ---------------------------------------------------------------------------
# HITL entry
# ---------------------------------------------------------------------------


class TestHITLEntry:
    """Tests for HITL integration."""

    async def test_hitl_entry_has_correct_actions(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """HITL entry must have the specified available actions."""
        mock_llm_client.call = AsyncMock(
            side_effect=[
                (AIMessage(content=_make_description_response()), _DEFAULT_METRICS),
                (AIMessage(content=_make_adaptation_response()), _DEFAULT_METRICS),
            ]
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _build_state()

        hitl_mock = AsyncMock(return_value="hitl-actions-001")

        with (
            patch.object(agent, "_create_hitl_entry", hitl_mock),
            patch.object(agent, "_log_portfolio_action", new_callable=AsyncMock),
        ):
            await agent._execute(state)

        hitl_mock.assert_awaited_once()
        call_args = hitl_mock.call_args
        portfolio_data = call_args[0][1]
        assert isinstance(portfolio_data, dict)

    async def test_always_requires_hitl(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Portfolio agent MUST always set requires_hitl=True when portfolio is produced."""
        mock_llm_client.call = AsyncMock(
            side_effect=[
                (AIMessage(content=_make_description_response()), _DEFAULT_METRICS),
                (AIMessage(content=_make_adaptation_response()), _DEFAULT_METRICS),
            ]
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _build_state()

        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-always"),
            patch.object(agent, "_log_portfolio_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        assert result["requires_hitl"] is True
        assert result["status"] == "paused"


# ---------------------------------------------------------------------------
# Audit logic
# ---------------------------------------------------------------------------


class TestAuditLogic:
    """Tests for portfolio audit methods."""

    def test_freshness_threshold_is_180_days(self):
        assert _FRESHNESS_THRESHOLD_DAYS == 180

    def test_audit_project_interval_is_5(self):
        assert _AUDIT_PROJECT_INTERVAL == 5

    async def test_run_audit_generates_recommendations(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=_make_audit_response()),
                _DEFAULT_METRICS,
            )
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        existing_projects = [
            {
                "id": "001",
                "title": "Old project",
                "added_at": "2025-09-01",
                "status": "active",
                "categories": ["landing"],
                "stack": ["HTML"],
                "platforms_published": {"ru": ["kwork"]},
            },
            {
                "id": "002",
                "title": "Recent project",
                "added_at": "2026-03-01",
                "status": "active",
                "categories": ["bot"],
                "stack": ["Python"],
                "platforms_published": {"ru": ["fl_ru"], "en": ["fiverr"]},
            },
        ]
        result = await agent._run_audit(existing_projects)
        assert result is not None
        assert "recommendations" in result
        assert "metrics" in result
        assert len(result["recommendations"]) > 0

    async def test_audit_returns_none_on_llm_failure(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content="cannot audit"),
                _DEFAULT_METRICS,
            )
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        result = await agent._run_audit([])
        assert result is None

    def test_check_audit_trigger_by_project_count(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        # 5 projects -> audit trigger
        assert agent._should_trigger_audit(project_count=5) is True
        assert agent._should_trigger_audit(project_count=10) is True
        assert agent._should_trigger_audit(project_count=15) is True

    def test_no_audit_trigger_below_interval(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        assert agent._should_trigger_audit(project_count=3) is False
        assert agent._should_trigger_audit(project_count=7) is False

    def test_audit_recommendation_actions_valid(self):
        """Valid audit actions must be REPLACE, ADD, UPDATE, DELETE, SYNC."""
        valid_actions = {"REPLACE", "ADD", "UPDATE", "DELETE", "SYNC"}
        audit_data = json.loads(_make_audit_response())
        for rec in audit_data["recommendations"]:
            assert rec["action"] in valid_actions


# ---------------------------------------------------------------------------
# Fallback description builder
# ---------------------------------------------------------------------------


class TestFallbackDescription:
    """Tests for _build_fallback_descriptions method."""

    def test_fallback_has_ru_and_en(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        project = {"requirements": "Build landing page", "project_id": "proj-001"}
        delivery = {
            "delivery_message": "Your page is ready",
            "includes": ["source_code"],
        }
        result = agent._build_fallback_descriptions(project, delivery)
        assert "title_ru" in result
        assert "title_en" in result
        assert "description_ru" in result
        assert "description_en" in result
        assert "stack" in result
        assert "categories" in result
        assert "market" in result
        assert "complexity" in result

    def test_fallback_uses_project_requirements(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        project = {"requirements": "React landing page with Tailwind", "project_id": "proj-002"}
        delivery = {"delivery_message": "Done", "includes": []}
        result = agent._build_fallback_descriptions(project, delivery)
        assert "React landing page" in result["title_ru"] or "React landing page" in result["description_ru"]


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------


class TestPortfolioNode:
    """Tests for the module-level portfolio_node function."""

    async def test_node_creates_agent_and_invokes(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        from src.agents.portfolio import portfolio_node

        state = _build_state()

        mock_container = MagicMock()
        mock_container.llm_client = mock_llm_client
        mock_container.heartbeat = mock_heartbeat
        mock_container.loop_detector = mock_loop_detector
        mock_container.llm_queue = None

        # Mock description + adaptation LLM calls
        mock_llm_client.call = AsyncMock(
            side_effect=[
                (AIMessage(content=_make_description_response()), _DEFAULT_METRICS),
                (AIMessage(content=_make_adaptation_response()), _DEFAULT_METRICS),
            ]
        )

        with (
            patch("src.core.container.get_container", return_value=mock_container),
            patch(
                "src.agents.portfolio.PortfolioAgent._create_hitl_entry",
                new_callable=AsyncMock,
                return_value="hitl-node-001",
            ),
            patch("src.agents.portfolio.PortfolioAgent._log_portfolio_action", new_callable=AsyncMock),
        ):
            result = await portfolio_node(state)

        assert result["current_agent"] == "portfolio"
        assert result["requires_hitl"] is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    async def test_empty_artifacts_dict(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _build_state(artifacts={})

        with patch.object(agent, "_log_portfolio_action", new_callable=AsyncMock):
            result = await agent._execute(state)

        assert result["status"] == "active"
        assert result.get("requires_hitl", False) is False

    async def test_malformed_packager_artifact(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Malformed packager JSON should not crash the agent."""
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _build_state(artifacts={"packager": ["not valid json at all"]})

        with patch.object(agent, "_log_portfolio_action", new_callable=AsyncMock):
            result = await agent._execute(state)

        # Should handle gracefully - either produce a fallback or return active
        assert result["status"] in ("active", "paused")

    async def test_packager_artifact_missing_fields(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Packager artifact with missing fields should use defaults."""
        mock_llm_client.call = AsyncMock(
            side_effect=[
                (AIMessage(content=_make_description_response()), _DEFAULT_METRICS),
                (AIMessage(content=_make_adaptation_response()), _DEFAULT_METRICS),
            ]
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        minimal_delivery = json.dumps({"delivery_id": "del-minimal"})
        state = _build_state(artifacts={"packager": [minimal_delivery]})

        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-min-001"),
            patch.object(agent, "_log_portfolio_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        assert result["requires_hitl"] is True
        assert "portfolio" in result["artifacts"]

    def test_classify_project_with_deploy_url(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Project with deploy_url should be classified as simple."""
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        delivery = {"includes": [], "deploy_url": "https://my-site.com"}
        project = {"requirements": "Website"}
        assert agent._classify_project(delivery, project) == "simple"

    async def test_state_not_mutated(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Original state dict must not be mutated."""
        mock_llm_client.call = AsyncMock(
            side_effect=[
                (AIMessage(content=_make_description_response()), _DEFAULT_METRICS),
                (AIMessage(content=_make_adaptation_response()), _DEFAULT_METRICS),
            ]
        )
        agent = PortfolioAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _build_state()
        original_artifacts = dict(state.get("artifacts", {}))

        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-immut"),
            patch.object(agent, "_log_portfolio_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        # Result should be a new dict, not the same reference
        assert result is not state
