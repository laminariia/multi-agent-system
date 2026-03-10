"""Unit tests for delivery_type feature across Planner + Packager.

Tests cover:
- State validation (VALID_DELIVERY_TYPES, validate_delivery_type)
- Planner delivery_type inference from requirements
- Planner prompt delivery_type documentation
- Packager delivery_type-aware packaging
- Packager response parsing with type-specific fields
- Integration: Planner->Packager delivery_type flow

All LLM calls and database operations are mocked.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from src.agents.packager import PackagerAgent
from src.agents.planner import PlannerAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_planner_state(**overrides: Any) -> AgentState:
    """Build a test AgentState for the Planner Agent."""
    project = {
        "project_id": "proj-dt-001",
        "job_id": "job-dt-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a responsive landing page with React",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    state = create_initial_state(
        project=project,
        first_agent="planner",
        thread_id="thread-dt-planner",
    )
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _build_packager_state(
    delivery_type: str = "files",
    **overrides: Any,
) -> AgentState:
    """Build a test AgentState for the Packager Agent."""
    project = {
        "project_id": "proj-dt-pkg-001",
        "job_id": "job-dt-pkg-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a landing page",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    state = create_initial_state(
        project=project,
        first_agent="packager",
        thread_id="thread-dt-packager",
    )
    state["delivery_type"] = delivery_type  # type: ignore[typeddict-item]
    if "artifacts" not in overrides:
        overrides["artifacts"] = {
            "dev": [json.dumps({"files": [{"path": "index.html", "content": "<h1>Hello</h1>"}]})],
            "content": [json.dumps({"deliverables": [{"type": "heading", "content": "Welcome"}]})],
        }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _make_plan_json(
    delivery_type: str = "files",
    agents: list[str] | None = None,
) -> str:
    """Build a valid plan JSON response with delivery_type."""
    if agents is None:
        agents = ["dev", "content"]
    tasks = []
    for i, agent in enumerate(agents):
        tasks.append(
            {
                "id": f"task_{i + 1}",
                "description": f"Task for {agent}",
                "assigned_to": agent,
                "estimated_hours": 2.0,
                "dependencies": [],
                "deliverables": [f"{agent}_output"],
            }
        )
    return json.dumps(
        {
            "project_id": "proj-dt-001",
            "delivery_type": delivery_type,
            "phases": [{"name": "Implementation", "tasks": tasks}],
            "total_estimated_hours": len(tasks) * 2.0,
            "critical_path": [t["id"] for t in tasks],
            "risks": ["Requirements may change"],
        }
    )


def _make_delivery_json(
    delivery_type: str = "files",
    **extra_fields: Any,
) -> str:
    """Create a valid delivery package JSON string with delivery_type."""
    delivery: dict[str, Any] = {
        "delivery_id": "del-dt-001",
        "project_id": "proj-dt-pkg-001",
        "delivery_type": delivery_type,
        "files_count": 15,
        "includes": ["source_code", "documentation"],
        "delivery_message": "Your project is ready!",
        "readme_content": "# Project\n\n## Setup\nnpm install",
        "missing_artifacts": [],
        "quality_notes": "All passed review.",
        "requires_hitl": True,
    }
    delivery.update(extra_fields)
    return json.dumps(delivery, default=str)


# ===========================================================================
# 1. State Validation Tests
# ===========================================================================


class TestDeliveryTypeStateValidation:
    """Tests for VALID_DELIVERY_TYPES and validate_delivery_type."""

    def test_valid_delivery_types_constant_exists(self):
        """VALID_DELIVERY_TYPES should be a frozenset with exactly 5 values."""
        from src.core.state import VALID_DELIVERY_TYPES

        assert isinstance(VALID_DELIVERY_TYPES, frozenset)
        assert VALID_DELIVERY_TYPES == frozenset(
            {
                "files",
                "credentials",
                "deploy",
                "instructions",
                "mixed",
            }
        )

    def test_validate_delivery_type_accepts_valid_values(self):
        """validate_delivery_type should return the same value for all valid types."""
        from src.core.state import VALID_DELIVERY_TYPES, validate_delivery_type

        for dt in VALID_DELIVERY_TYPES:
            assert validate_delivery_type(dt) == dt

    def test_validate_delivery_type_invalid_returns_files(self):
        """validate_delivery_type should return 'files' for unknown values."""
        from src.core.state import validate_delivery_type

        assert validate_delivery_type("pdf") == "files"
        assert validate_delivery_type("archive") == "files"
        assert validate_delivery_type("unknown") == "files"

    def test_validate_delivery_type_empty_string_returns_files(self):
        """validate_delivery_type should return 'files' for empty string."""
        from src.core.state import validate_delivery_type

        assert validate_delivery_type("") == "files"

    def test_create_initial_state_default_delivery_type(self):
        """create_initial_state should default delivery_type to 'files'."""
        state = create_initial_state(
            project={
                "project_id": "p1",
                "job_id": "j1",
                "platform": "freelancer",
                "client": {},
                "requirements": "test",
                "budget": 100.0,
                "deadline": "2026-04-01",
            },
        )
        assert state["delivery_type"] == "files"


# ===========================================================================
# 2. Planner Inference Tests
# ===========================================================================


class TestPlannerDeliveryTypeInference:
    """Tests for PlannerAgent._infer_delivery_type."""

    def test_infer_deploy_keywords(self):
        """Requirements mentioning deploy/hosting/site should return 'deploy'."""
        assert PlannerAgent._infer_delivery_type("Создать лендинг и задеплоить на хостинг") == "deploy"
        assert PlannerAgent._infer_delivery_type("Build and deploy a website") == "deploy"
        assert PlannerAgent._infer_delivery_type("Лендинг для стоматологии с доменом") == "deploy"

    def test_infer_credentials_keywords(self):
        """Requirements mentioning bot/token/service should return 'credentials'."""
        assert PlannerAgent._infer_delivery_type("Telegram бот для записи клиентов") == "credentials"
        assert PlannerAgent._infer_delivery_type("Create API service with authentication") == "credentials"
        assert PlannerAgent._infer_delivery_type("Настроить сервис с доступами") == "credentials"

    def test_infer_instructions_keywords(self):
        """Requirements mentioning audit/consulting should return 'instructions'."""
        assert PlannerAgent._infer_delivery_type("Аудит безопасности и рекомендации") == "instructions"
        assert PlannerAgent._infer_delivery_type("Consulting on architecture and strategy") == "instructions"
        assert PlannerAgent._infer_delivery_type("Провести анализ и составить план") == "instructions"

    def test_infer_files_default(self):
        """Generic requirements should return 'files' (default)."""
        assert PlannerAgent._infer_delivery_type("Build a responsive landing page") == "files"
        assert PlannerAgent._infer_delivery_type("Create a logo and brand identity") == "files"
        assert PlannerAgent._infer_delivery_type("Write content for website") == "files"

    def test_infer_mixed_multiple_signals(self):
        """Requirements with multiple delivery signals should return 'mixed'."""
        assert (
            PlannerAgent._infer_delivery_type(
                "Создать CRM: код + настроить сервер + написать документацию + выдать доступы"
            )
            == "mixed"
        )
        assert (
            PlannerAgent._infer_delivery_type("Build and deploy app with API credentials and documentation") == "mixed"
        )

    def test_infer_case_insensitive(self):
        """Inference should be case-insensitive."""
        assert PlannerAgent._infer_delivery_type("DEPLOY to HOSTING") == "deploy"
        assert PlannerAgent._infer_delivery_type("TELEGRAM BOT") == "credentials"
        assert PlannerAgent._infer_delivery_type("АУДИТ САЙТА") == "instructions"

    def test_infer_russian_keywords(self):
        """Russian keywords should be recognized correctly."""
        assert PlannerAgent._infer_delivery_type("Разработать сайт и разместить на хостинге") == "deploy"
        assert PlannerAgent._infer_delivery_type("Создать бота для телеграм") == "credentials"
        assert PlannerAgent._infer_delivery_type("Консалтинг по архитектуре") == "instructions"
        assert PlannerAgent._infer_delivery_type("Создать логотип") == "files"

    async def test_planner_sets_delivery_type_from_plan(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Planner should set delivery_type from the LLM plan response."""
        plan_json = _make_plan_json(delivery_type="deploy", agents=["design", "dev"])
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=plan_json),
                CallMetrics(agent_name="planner", model_id="claude-opus-4-6", provider="anthropic"),
            )
        )

        agent = PlannerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_planner_state()
        with patch.object(agent, "_log_planning_action", new_callable=AsyncMock):
            result = await agent._execute(state)

        assert result["delivery_type"] == "deploy"
        assert result["agent_sequence"] == ["design", "dev"]
        assert result["status"] == "active"

    async def test_planner_validates_invalid_delivery_type(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Planner should fallback to 'files' if LLM returns invalid delivery_type."""
        plan_json = _make_plan_json(delivery_type="pdf", agents=["dev"])
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=plan_json),
                CallMetrics(agent_name="planner", model_id="claude-opus-4-6", provider="anthropic"),
            )
        )

        agent = PlannerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_planner_state()
        with patch.object(agent, "_log_planning_action", new_callable=AsyncMock):
            result = await agent._execute(state)

        # Invalid "pdf" should be corrected to "files"
        assert result["delivery_type"] == "files"

    async def test_planner_infers_delivery_type_when_missing(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """When LLM plan has no delivery_type, Planner should infer from requirements."""
        # Plan without delivery_type field
        plan = {
            "project_id": "proj-dt-001",
            "phases": [
                {
                    "name": "Build",
                    "tasks": [
                        {
                            "id": "t1",
                            "description": "Build bot",
                            "assigned_to": "dev",
                            "estimated_hours": 2.0,
                            "dependencies": [],
                            "deliverables": ["bot"],
                        },
                    ],
                }
            ],
            "total_estimated_hours": 2.0,
            "critical_path": ["t1"],
            "risks": [],
        }
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=json.dumps(plan)),
                CallMetrics(agent_name="planner", model_id="claude-opus-4-6", provider="anthropic"),
            )
        )

        agent = PlannerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        # Requirements mention "Telegram бот" -> should infer "credentials"
        state = _build_planner_state()
        state["project"]["requirements"] = "Создать Telegram бот для записи"  # type: ignore[typeddict-item]

        with patch.object(agent, "_log_planning_action", new_callable=AsyncMock):
            result = await agent._execute(state)

        assert result["delivery_type"] == "credentials"


# ===========================================================================
# 3. Planner Prompt Tests
# ===========================================================================


class TestPlannerPromptDeliveryType:
    """Tests that Planner prompt includes delivery_type documentation."""

    def test_planner_prompt_contains_delivery_type(self):
        """Planner system prompt should mention delivery_type field."""
        from src.prompts.planner import PLANNER_SYSTEM_PROMPT

        assert "delivery_type" in PLANNER_SYSTEM_PROMPT

    def test_planner_prompt_lists_valid_values(self):
        """Planner system prompt should list all valid delivery_type values."""
        from src.prompts.planner import PLANNER_SYSTEM_PROMPT

        for dt in ("files", "credentials", "deploy", "instructions", "mixed"):
            assert dt in PLANNER_SYSTEM_PROMPT


# ===========================================================================
# 4. Packager Delivery Type Handling Tests
# ===========================================================================


class TestPackagerDeliveryTypeHandling:
    """Tests for PackagerAgent delivery_type-aware packaging."""

    async def test_packager_files_delivery(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """delivery_type='files' -> standard file-based delivery."""
        delivery_json = _make_delivery_json("files")
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=delivery_json),
                CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
            )
        )

        agent = PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_packager_state(delivery_type="files")
        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-f"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        assert result["requires_hitl"] is True
        stored = json.loads(result["artifacts"]["packager"][0])
        assert stored["delivery_type"] == "files"

    async def test_packager_credentials_delivery(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """delivery_type='credentials' -> includes credentials + setup instructions."""
        delivery_json = _make_delivery_json(
            "credentials",
            credentials={"api_key": "***masked***", "bot_token": "***masked***"},
            setup_instructions="1. Set BOT_TOKEN env var\n2. Run: python bot.py",
        )
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=delivery_json),
                CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
            )
        )

        agent = PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_packager_state(delivery_type="credentials")
        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-c"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        stored = json.loads(result["artifacts"]["packager"][0])
        assert stored["delivery_type"] == "credentials"
        assert "credentials" in stored
        assert "setup_instructions" in stored

    async def test_packager_deploy_delivery(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """delivery_type='deploy' -> includes deploy_url + setup_instructions."""
        delivery_json = _make_delivery_json(
            "deploy",
            deploy_url="https://example.com",
            setup_instructions="DNS: point A record to 1.2.3.4",
        )
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=delivery_json),
                CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
            )
        )

        agent = PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_packager_state(delivery_type="deploy")
        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-d"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        stored = json.loads(result["artifacts"]["packager"][0])
        assert stored["delivery_type"] == "deploy"
        assert "deploy_url" in stored
        assert "setup_instructions" in stored

    async def test_packager_instructions_delivery(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """delivery_type='instructions' -> documentation-focused delivery."""
        delivery_json = _make_delivery_json(
            "instructions",
            setup_instructions="Full audit report with recommendations",
            includes=["audit_report", "recommendations", "action_plan"],
        )
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=delivery_json),
                CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
            )
        )

        agent = PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_packager_state(delivery_type="instructions")
        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-i"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        stored = json.loads(result["artifacts"]["packager"][0])
        assert stored["delivery_type"] == "instructions"

    async def test_packager_mixed_delivery(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """delivery_type='mixed' -> combination of files + credentials + instructions."""
        delivery_json = _make_delivery_json(
            "mixed",
            credentials={"admin_url": "https://admin.example.com", "password": "***masked***"},
            deploy_url="https://example.com",
            setup_instructions="1. Login at admin URL\n2. Configure settings",
        )
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=delivery_json),
                CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
            )
        )

        agent = PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_packager_state(delivery_type="mixed")
        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-m"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        stored = json.loads(result["artifacts"]["packager"][0])
        assert stored["delivery_type"] == "mixed"
        assert "credentials" in stored
        assert "deploy_url" in stored
        assert "setup_instructions" in stored

    async def test_packager_unknown_delivery_type_fallback(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Unknown delivery_type in state should be treated as 'files'."""
        delivery_json = _make_delivery_json("files")
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=delivery_json),
                CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
            )
        )

        agent = PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_packager_state(delivery_type="invalid_type")
        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-u"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        assert result["requires_hitl"] is True

    async def test_packager_delivery_context_includes_type(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """LLM call should include delivery_type context in the prompt."""
        delivery_json = _make_delivery_json("deploy")
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=delivery_json),
                CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
            )
        )

        agent = PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_packager_state(delivery_type="deploy")
        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-ctx"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            await agent._execute(state)

        # Verify the LLM was called with delivery_type in the prompt
        call_args = mock_llm_client.call.call_args
        messages = call_args[0][1]  # Second positional arg (after agent_name)
        # The HumanMessage should contain delivery_type context
        human_msg_content = str(messages[-1].content)
        assert "deploy" in human_msg_content.lower()

    async def test_packager_validates_completeness_files(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """delivery_type='files' with dev artifacts -> completeness OK."""
        delivery_json = _make_delivery_json("files")
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=delivery_json),
                CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
            )
        )

        agent = PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_packager_state(
            delivery_type="files",
            artifacts={"dev": ["code-1"]},
        )
        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-vc"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        assert result["requires_hitl"] is True
        stored = json.loads(result["artifacts"]["packager"][0])
        # Files delivery with dev artifacts should have no missing items
        assert stored.get("missing_artifacts", []) == [] or len(stored.get("missing_artifacts", [])) == 0

    async def test_packager_validates_completeness_credentials_missing(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """delivery_type='credentials' with no credentials data -> flag missing."""
        # LLM response without credentials field
        delivery = {
            "delivery_id": "del-miss",
            "project_id": "proj-dt-pkg-001",
            "delivery_type": "credentials",
            "files_count": 5,
            "includes": ["source_code"],
            "delivery_message": "Bot ready.",
            "readme_content": "# Bot",
            "missing_artifacts": [],
            "quality_notes": "",
            "requires_hitl": True,
            # No "credentials" field!
        }
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=json.dumps(delivery)),
                CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
            )
        )

        agent = PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_packager_state(delivery_type="credentials")
        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-mc"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        stored = json.loads(result["artifacts"]["packager"][0])
        # Should flag missing credentials
        missing = stored.get("missing_artifacts", [])
        assert any("credentials" in str(m).lower() for m in missing)

    async def test_packager_validates_completeness_deploy_missing(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """delivery_type='deploy' without deploy_url -> flag missing."""
        delivery = {
            "delivery_id": "del-miss-d",
            "project_id": "proj-dt-pkg-001",
            "delivery_type": "deploy",
            "files_count": 10,
            "includes": ["source_code"],
            "delivery_message": "Site ready.",
            "readme_content": "# Site",
            "missing_artifacts": [],
            "quality_notes": "",
            "requires_hitl": True,
            # No "deploy_url" field!
        }
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=json.dumps(delivery)),
                CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
            )
        )

        agent = PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_packager_state(delivery_type="deploy")
        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-md"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        stored = json.loads(result["artifacts"]["packager"][0])
        missing = stored.get("missing_artifacts", [])
        assert any("deploy" in str(m).lower() for m in missing)

    async def test_packager_hitl_entry_includes_delivery_type(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """HITL queue entry should include delivery_type for operator visibility."""
        delivery_json = _make_delivery_json("deploy")
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=delivery_json),
                CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
            )
        )

        agent = PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_packager_state(delivery_type="deploy")
        hitl_mock = AsyncMock(return_value="hitl-dt")

        with (
            patch.object(agent, "_create_hitl_entry", hitl_mock),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            await agent._execute(state)

        hitl_mock.assert_awaited_once()
        call_args = hitl_mock.call_args
        delivery_info = call_args[0][1]  # second positional arg
        assert delivery_info.get("delivery_type") == "deploy"

    async def test_packager_fallback_delivery_type_aware(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Fallback delivery should include delivery_type from state."""
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content="Garbage response"),
                CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
            )
        )

        agent = PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_packager_state(delivery_type="credentials")
        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-fb"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        stored = json.loads(result["artifacts"]["packager"][0])
        assert stored["delivery_type"] == "credentials"


# ===========================================================================
# 5. Packager Response Parsing Tests
# ===========================================================================


class TestPackagerResponseParsing:
    """Tests for PackagerAgent._parse_delivery_response with type-specific fields."""

    def _make_agent(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ) -> PackagerAgent:
        return PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

    def test_parse_response_with_credentials_field(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Parser should preserve credentials field from LLM response."""
        agent = self._make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
        raw = json.dumps(
            {
                "delivery_id": "del-cred",
                "project_id": "proj-1",
                "delivery_type": "credentials",
                "files_count": 5,
                "includes": ["source_code"],
                "delivery_message": "Bot ready.",
                "credentials": {"bot_token": "***", "api_key": "***"},
                "requires_hitl": True,
            }
        )
        result = agent._parse_delivery_response(raw)
        assert result is not None
        assert "credentials" in result
        assert result["credentials"]["bot_token"] == "***"

    def test_parse_response_with_deploy_url(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Parser should preserve deploy_url field from LLM response."""
        agent = self._make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
        raw = json.dumps(
            {
                "delivery_id": "del-dep",
                "project_id": "proj-1",
                "delivery_type": "deploy",
                "files_count": 10,
                "includes": ["source_code", "deployment"],
                "delivery_message": "Site deployed.",
                "deploy_url": "https://client-site.com",
                "requires_hitl": True,
            }
        )
        result = agent._parse_delivery_response(raw)
        assert result is not None
        assert result["deploy_url"] == "https://client-site.com"

    def test_parse_response_with_setup_instructions(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Parser should preserve setup_instructions field."""
        agent = self._make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
        raw = json.dumps(
            {
                "delivery_id": "del-inst",
                "project_id": "proj-1",
                "delivery_type": "instructions",
                "files_count": 3,
                "includes": ["documentation"],
                "delivery_message": "Audit complete.",
                "setup_instructions": "Step 1: Review report\nStep 2: Implement changes",
                "requires_hitl": True,
            }
        )
        result = agent._parse_delivery_response(raw)
        assert result is not None
        assert "setup_instructions" in result
        assert "Step 1" in result["setup_instructions"]

    def test_parse_response_delivery_type_echo(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Parser should preserve delivery_type from LLM response."""
        agent = self._make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
        raw = json.dumps(
            {
                "delivery_id": "del-echo",
                "project_id": "proj-1",
                "delivery_type": "mixed",
                "files_count": 20,
                "includes": ["source_code", "credentials", "documentation"],
                "delivery_message": "CRM ready.",
                "requires_hitl": True,
            }
        )
        result = agent._parse_delivery_response(raw)
        assert result is not None
        assert result.get("delivery_type") == "mixed"

    def test_parse_response_missing_type_specific_fields_defaults(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Parser should set sensible defaults for missing type-specific fields."""
        agent = self._make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
        raw = json.dumps(
            {
                "delivery_id": "del-min",
                "project_id": "proj-1",
                "files_count": 1,
                "delivery_message": "Done.",
                "requires_hitl": True,
            }
        )
        result = agent._parse_delivery_response(raw)
        assert result is not None
        # Default type-specific fields
        assert result.get("credentials") is None or result.get("credentials") == {}
        assert result.get("deploy_url") is None or result.get("deploy_url") == ""
        assert result.get("setup_instructions") is None or result.get("setup_instructions") == ""


# ===========================================================================
# 6. Integration Tests
# ===========================================================================


class TestDeliveryTypeIntegration:
    """Integration tests for delivery_type flow across agents."""

    async def test_planner_to_packager_delivery_type_flow(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """delivery_type set by Planner should be readable by Packager."""
        # Step 1: Planner generates plan with delivery_type="deploy"
        plan_json = _make_plan_json(delivery_type="deploy", agents=["dev"])
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=plan_json),
                CallMetrics(agent_name="planner", model_id="claude-opus-4-6", provider="anthropic"),
            )
        )

        planner = PlannerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        planner_state = _build_planner_state()
        with patch.object(planner, "_log_planning_action", new_callable=AsyncMock):
            after_planner = await planner._execute(planner_state)

        assert after_planner["delivery_type"] == "deploy"

        # Step 2: Packager reads delivery_type from state
        delivery_json = _make_delivery_json("deploy", deploy_url="https://example.com")
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=delivery_json),
                CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
            )
        )

        packager = PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        # Build packager state from planner output
        packager_state = dict(after_planner)
        packager_state["current_agent"] = "packager"
        packager_state["artifacts"]["dev"] = [json.dumps({"files": [{"path": "app.py"}]})]

        with (
            patch.object(packager, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-int"),
            patch.object(packager, "_log_packaging_action", new_callable=AsyncMock),
        ):
            after_packager = await packager._execute(packager_state)

        stored = json.loads(after_packager["artifacts"]["packager"][0])
        assert stored["delivery_type"] == "deploy"
        assert after_packager["requires_hitl"] is True

    async def test_delivery_type_preserved_after_critic_revision(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """delivery_type should survive through critic revision cycle."""
        # Simulate state after critic minor revision -> back to dev -> back to critic -> packager
        state = _build_packager_state(delivery_type="credentials")
        # Simulate revision cycle history in artifacts
        state["artifacts"]["_critic_revision_count"] = ["1"]  # type: ignore[typeddict-item]

        delivery_json = _make_delivery_json("credentials", credentials={"token": "***"})
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=delivery_json),
                CallMetrics(agent_name="packager", model_id="claude-haiku-4-5", provider="anthropic"),
            )
        )

        agent = PackagerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        with (
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-rev"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        # delivery_type should still be "credentials" after revision cycle
        assert result["delivery_type"] == "credentials"
        stored = json.loads(result["artifacts"]["packager"][0])
        assert stored["delivery_type"] == "credentials"

    async def test_consulting_project_instructions_delivery(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Consulting project (empty agent_sequence) -> instructions delivery."""
        # Planner produces empty sequence + instructions delivery_type
        plan_json = json.dumps(
            {
                "project_id": "proj-consult",
                "delivery_type": "instructions",
                "phases": [
                    {
                        "name": "Analysis",
                        "tasks": [
                            {
                                "id": "t1",
                                "description": "Analyze architecture",
                                "assigned_to": "critic",
                                "estimated_hours": 3.0,
                                "dependencies": [],
                                "deliverables": ["audit_report"],
                            },
                        ],
                    }
                ],
                "total_estimated_hours": 3.0,
                "critical_path": ["t1"],
                "risks": [],
            }
        )
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=plan_json),
                CallMetrics(agent_name="planner", model_id="claude-opus-4-6", provider="anthropic"),
            )
        )

        planner = PlannerAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_planner_state()
        state["project"]["requirements"] = "Аудит безопасности и рекомендации"  # type: ignore[typeddict-item]

        with patch.object(planner, "_log_planning_action", new_callable=AsyncMock):
            result = await planner._execute(state)

        assert result["delivery_type"] == "instructions"
        # Consulting: no execution agents, only critic task
        # _build_agent_sequence excludes critic -> empty or fallback
        # Empty sequence means routing goes straight to packager
        assert result["agent_sequence"] == [] or result["agent_sequence"] == ["dev", "content", "design"]
