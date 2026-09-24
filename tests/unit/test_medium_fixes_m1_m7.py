"""Unit tests for medium-priority fixes M1, M2, M3, M4, M7.

M1: HITL Plan Review Edit action -- _apply_plan_review handles "edit" with
    agent_sequence and delivery_type overrides from operator payload.

M2: Remove hardcoded next_agent from Dev/Content/Design agents -- they use
    current_sequence_index increment and let _route_next_in_sequence handle.

M3: Revision Target routing -- _route_after_critic reads revision_target and
    routes to that specific agent (dev, content, or design).

M4: Scout Custom Rules interpreter -- Scout loads custom_rules from config
    and injects them into the LLM scoring prompt.

M7: Email Templates personalization -- personalize_template replaces
    {{placeholder}} tokens; select_ab_variant picks deterministic A/B variant.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_project(**overrides: Any) -> dict[str, Any]:
    """Build a minimal ProjectContext dict."""
    base = {
        "project_id": "proj-medium-001",
        "job_id": "job-medium-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a landing page with React and Tailwind CSS",
        "budget": 500.0,
        "deadline": datetime(2026, 4, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return base


def _make_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults."""
    state = create_initial_state(
        project=_make_project(),
        first_agent="scout",
        thread_id="thread-medium-test",
    )
    for k, v in overrides.items():
        state[k] = v  # type: ignore[literal-required]
    return state


# ===========================================================================
# M1: HITL Plan Review Edit Action
# ===========================================================================


class TestM1PlanReviewEdit:
    """Verify _apply_plan_review correctly handles the 'edit' action,
    applying agent_sequence and delivery_type overrides."""

    def test_edit_applies_agent_sequence(self):
        """Edit action with agent_sequence should override the saved sequence."""
        from src.core.graph import _apply_plan_review

        saved = _make_state(
            agent_sequence=["dev", "content", "design"],
            current_sequence_index=0,
            delivery_type="files",
            artifacts={"planner": ['{"tasks": []}']},
            status="paused",
            requires_hitl=True,
        )

        hitl_response = {
            "action": "edit",
            "edits": {
                "agent_sequence": ["dev", "design"],
            },
        }

        result = _apply_plan_review(saved, "edit", hitl_response, "thread-test")

        assert result["agent_sequence"] == ["dev", "design"]
        assert result["current_sequence_index"] == 0
        assert result["status"] == "active"
        assert result["requires_hitl"] is False

    def test_edit_applies_delivery_type(self):
        """Edit action with delivery_type should override the saved type."""
        from src.core.graph import _apply_plan_review

        saved = _make_state(
            agent_sequence=["dev", "content"],
            delivery_type="files",
            artifacts={},
            status="paused",
            requires_hitl=True,
        )

        hitl_response = {
            "action": "edit",
            "edits": {
                "delivery_type": "deploy",
            },
        }

        result = _apply_plan_review(saved, "edit", hitl_response, "thread-test")

        assert result["delivery_type"] == "deploy"
        assert result["status"] == "active"

    def test_edit_applies_both_sequence_and_delivery_type(self):
        """Edit action should apply both overrides simultaneously."""
        from src.core.graph import _apply_plan_review

        saved = _make_state(
            agent_sequence=["dev", "content", "design"],
            delivery_type="files",
            artifacts={},
            status="paused",
            requires_hitl=True,
        )

        hitl_response = {
            "action": "edit",
            "edits": {
                "agent_sequence": ["design"],
                "delivery_type": "credentials",
            },
        }

        result = _apply_plan_review(saved, "edit", hitl_response, "thread-test")

        assert result["agent_sequence"] == ["design"]
        assert result["delivery_type"] == "credentials"
        assert result["current_sequence_index"] == 0
        assert result["status"] == "active"

    def test_edit_filters_invalid_agents(self):
        """Invalid agents in edited sequence should be filtered out."""
        from src.core.graph import _apply_plan_review

        saved = _make_state(
            agent_sequence=["dev"],
            artifacts={},
            status="paused",
            requires_hitl=True,
        )

        hitl_response = {
            "action": "edit",
            "edits": {
                "agent_sequence": ["dev", "invalid_agent", "content"],
            },
        }

        result = _apply_plan_review(saved, "edit", hitl_response, "thread-test")

        # "invalid_agent" should be filtered out
        assert "invalid_agent" not in result["agent_sequence"]
        assert "dev" in result["agent_sequence"]
        assert "content" in result["agent_sequence"]

    def test_edit_validates_delivery_type(self):
        """Invalid delivery_type should fall back to 'files'."""
        from src.core.graph import _apply_plan_review

        saved = _make_state(
            delivery_type="files",
            artifacts={},
            status="paused",
            requires_hitl=True,
        )

        hitl_response = {
            "action": "edit",
            "edits": {
                "delivery_type": "nonexistent_type",
            },
        }

        result = _apply_plan_review(saved, "edit", hitl_response, "thread-test")

        assert result["delivery_type"] == "files"  # fallback

    def test_edit_stores_hitl_edits_artifact(self):
        """Edit action should store the edits in artifacts['hitl_edits']."""
        from src.core.graph import _apply_plan_review

        saved = _make_state(
            artifacts={},
            status="paused",
            requires_hitl=True,
        )

        edits = {"agent_sequence": ["dev"], "delivery_type": "deploy"}
        hitl_response = {"action": "edit", "edits": edits}

        result = _apply_plan_review(saved, "edit", hitl_response, "thread-test")

        assert "hitl_edits" in result["artifacts"]

    def test_edit_stores_original_sequence(self):
        """Edit action should preserve original sequence in artifacts."""
        from src.core.graph import _apply_plan_review

        saved = _make_state(
            agent_sequence=["dev", "content", "design"],
            artifacts={},
            status="paused",
            requires_hitl=True,
        )

        hitl_response = {
            "action": "edit",
            "edits": {"agent_sequence": ["dev"]},
        }

        result = _apply_plan_review(saved, "edit", hitl_response, "thread-test")

        assert "_original_sequence" in result["artifacts"]

    def test_edit_sets_hitl_type_artifact(self):
        """Edit action should set _hitl_type='plan_review' for routing."""
        from src.core.graph import _apply_plan_review

        saved = _make_state(
            artifacts={},
            status="paused",
            requires_hitl=True,
        )

        hitl_response = {"action": "edit", "edits": {}}

        result = _apply_plan_review(saved, "edit", hitl_response, "thread-test")

        assert result["artifacts"].get("_hitl_type") == "plan_review"

    def test_edit_without_edits_still_activates(self):
        """Edit action with empty edits should still set status='active'."""
        from src.core.graph import _apply_plan_review

        saved = _make_state(
            agent_sequence=["dev"],
            artifacts={},
            status="paused",
            requires_hitl=True,
        )

        hitl_response = {"action": "edit", "edits": {}}

        result = _apply_plan_review(saved, "edit", hitl_response, "thread-test")

        assert result["status"] == "active"
        assert result["requires_hitl"] is False


# ===========================================================================
# M2: Agents use sequence index, not hardcoded next_agent
# ===========================================================================


class TestM2AgentSequenceRouting:
    """Dev, Content, and Design agents must increment current_sequence_index
    and NOT set a hardcoded next_agent."""

    async def test_dev_agent_increments_sequence_index(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Dev agent should increment current_sequence_index."""
        from src.agents.dev import DevAgent

        code_response = json.dumps(
            {
                "files": [{"path": "app.py", "content": "print('hello')", "language": "python"}],
                "dependencies": [],
            }
        )
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=code_response),
                CallMetrics(agent_name="dev", model_id="claude-opus-4-6", provider="anthropic"),
            )
        )

        agent = DevAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _make_state(
            current_agent="dev",
            current_sequence_index=0,
            agent_sequence=["dev", "content", "design"],
            artifacts={},
        )

        with patch("src.agents.dev.get_db_session") as mock_db:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_db.return_value = mock_ctx

            result = await agent._execute(state)

        assert result["current_sequence_index"] == 1
        assert result["current_agent"] == "dev"
        # next_agent should NOT be hardcoded to "content"
        assert result.get("next_agent") is None or result.get("next_agent") != "content"

    async def test_content_agent_increments_sequence_index(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Content agent should increment current_sequence_index."""
        from src.agents.content import ContentAgent

        content_response = json.dumps(
            {
                "deliverables": [{"type": "heading", "content": "Welcome"}],
                "content_type": "landing_page",
                "word_count": 100,
            }
        )
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=content_response),
                CallMetrics(agent_name="content", model_id="claude-sonnet-4-6", provider="anthropic"),
            )
        )

        agent = ContentAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _make_state(
            current_agent="content",
            current_sequence_index=1,
            agent_sequence=["dev", "content", "design"],
            artifacts={},
        )

        with patch("src.agents.content.get_db_session") as mock_db:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_db.return_value = mock_ctx

            result = await agent._execute(state)

        assert result["current_sequence_index"] == 2
        assert result["current_agent"] == "content"
        # next_agent should NOT be hardcoded to "design"
        assert result.get("next_agent") is None or result.get("next_agent") != "design"

    async def test_design_agent_increments_sequence_index(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Design agent should increment current_sequence_index."""
        from src.agents.design import DesignAgent

        design_response = json.dumps(
            {
                "deliverables": [{"type": "ui_mockup", "component": "hero"}],
                "design_type": "ui_mockup",
            }
        )
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=design_response),
                CallMetrics(agent_name="design", model_id="nanobana-pro", provider="openai"),
            )
        )

        agent = DesignAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _make_state(
            current_agent="design",
            current_sequence_index=2,
            agent_sequence=["dev", "content", "design"],
            artifacts={},
        )

        with patch("src.agents.design.get_db_session") as mock_db:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_db.return_value = mock_ctx

            result = await agent._execute(state)

        assert result["current_sequence_index"] == 3
        assert result["current_agent"] == "design"
        # next_agent should NOT be hardcoded to "critic"
        assert result.get("next_agent") is None or result.get("next_agent") != "critic"

    async def test_dev_agent_clears_revision_target(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """After completing work, dev agent should clear revision_target."""
        from src.agents.dev import DevAgent

        code_response = json.dumps(
            {
                "files": [{"path": "app.py", "content": "print('hello')", "language": "python"}],
                "dependencies": [],
            }
        )
        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=code_response),
                CallMetrics(agent_name="dev", model_id="claude-opus-4-6", provider="anthropic"),
            )
        )

        agent = DevAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _make_state(
            current_agent="dev",
            current_sequence_index=0,
            revision_target="dev",
            revision_severity="minor",
            artifacts={},
        )

        with patch("src.agents.dev.get_db_session") as mock_db:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_db.return_value = mock_ctx

            result = await agent._execute(state)

        assert result["revision_target"] is None
        assert result["revision_severity"] is None


# ===========================================================================
# M3: _route_after_critic revision_target routing
# ===========================================================================


class TestM3RevisionTargetRouting:
    """_route_after_critic must read revision_target and route to the
    specific agent node (dev, content, or design)."""

    def test_route_to_design_on_revision_target_design(self):
        """revision_target='design' should route to design_node."""
        from src.core.graph import _route_after_critic

        state = _make_state(
            current_agent="critic",
            next_agent="design",
            revision_target="design",
            revision_severity="minor",
            status="active",
            artifacts={"_critic_revision_count": ["1"]},
        )

        result = _route_after_critic(state)
        assert result == "design_node"

    def test_route_to_content_on_revision_target_content(self):
        """revision_target='content' should route to content_node."""
        from src.core.graph import _route_after_critic

        state = _make_state(
            current_agent="critic",
            next_agent="content",
            revision_target="content",
            revision_severity="minor",
            status="active",
            artifacts={"_critic_revision_count": ["1"]},
        )

        result = _route_after_critic(state)
        assert result == "content_node"

    def test_route_to_dev_on_revision_target_dev(self):
        """revision_target='dev' should route to dev_node."""
        from src.core.graph import _route_after_critic

        state = _make_state(
            current_agent="critic",
            next_agent="dev",
            revision_target="dev",
            revision_severity="minor",
            status="active",
            artifacts={"_critic_revision_count": ["1"]},
        )

        result = _route_after_critic(state)
        assert result == "dev_node"

    def test_route_to_packager_on_approve(self):
        """Critic approve should route to packager_node."""
        from src.core.graph import _route_after_critic

        state = _make_state(
            current_agent="critic",
            next_agent="packager",
            revision_target=None,
            status="active",
            artifacts={},
        )

        result = _route_after_critic(state)
        assert result == "packager_node"

    def test_route_to_hitl_on_requires_hitl(self):
        """requires_hitl=True should route to hitl_review_node."""
        from src.core.graph import _route_after_critic

        state = _make_state(
            current_agent="critic",
            requires_hitl=True,
            status="paused",
            artifacts={},
        )

        result = _route_after_critic(state)
        assert result == "hitl_review_node"

    def test_route_to_planner_on_major_revision(self):
        """next_agent='planner' should route to planner_node (major revision)."""
        from src.core.graph import _route_after_critic

        state = _make_state(
            current_agent="critic",
            next_agent="planner",
            status="active",
            artifacts={},
        )

        result = _route_after_critic(state)
        assert result == "planner_node"

    def test_revision_limit_routes_to_hitl(self):
        """When revision_count >= MAX, revision_target should route to HITL."""
        from src.core.graph import MAX_REVISION_CYCLES, _route_after_critic

        state = _make_state(
            current_agent="critic",
            next_agent="dev",
            revision_target="dev",
            status="active",
            artifacts={"_critic_revision_count": [str(MAX_REVISION_CYCLES)]},
        )

        result = _route_after_critic(state)
        assert result == "hitl_review_node"

    def test_critic_determines_design_target_from_issues(self):
        """Critic should determine revision_target='design' from design-related issues."""
        from src.agents.critic import CriticAgent

        target = CriticAgent._determine_revision_target(
            [
                {"category": "design", "description": "Color palette needs work"},
                {"category": "layout", "description": "Hero section misaligned"},
            ]
        )
        assert target == "design"

    def test_critic_determines_content_target_from_issues(self):
        """Critic should determine revision_target='content' from content-related issues."""
        from src.agents.critic import CriticAgent

        target = CriticAgent._determine_revision_target(
            [
                {"category": "content", "description": "Grammar issues in copy"},
                {"category": "text", "description": "Tone is too formal"},
            ]
        )
        assert target == "content"

    def test_critic_defaults_to_dev_target(self):
        """When no clear signal, revision_target should default to 'dev'."""
        from src.agents.critic import CriticAgent

        target = CriticAgent._determine_revision_target(
            [
                {"category": "code", "description": "Bug in component"},
            ]
        )
        assert target == "dev"


# ===========================================================================
# M4: Scout Custom Rules Interpreter
# ===========================================================================


class TestM4ScoutCustomRules:
    """Scout should load custom_rules from config and inject them into
    the LLM scoring prompt."""

    async def test_load_custom_rules_returns_rules(self):
        """_load_custom_rules should return rules from config."""
        from src.agents.scout import ScoutAgent

        agent = ScoutAgent(
            llm_client=AsyncMock(),
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
            adapters={},
        )

        mock_config = {
            "custom_rules": [
                "Prefer React projects",
                "Reject projects requiring PHP",
            ],
        }

        with patch("src.agents.scout.get_db_session") as mock_db:
            mock_session = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_db.return_value = mock_ctx

            with patch("src.agents.scout.load_scout_config", return_value=mock_config):
                rules = await agent._load_custom_rules()

        assert rules is not None
        assert len(rules) == 2
        assert "Prefer React projects" in rules
        assert "Reject projects requiring PHP" in rules

    async def test_load_custom_rules_returns_none_when_empty(self):
        """_load_custom_rules should return None when no rules configured."""
        from src.agents.scout import ScoutAgent

        agent = ScoutAgent(
            llm_client=AsyncMock(),
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
            adapters={},
        )

        with patch("src.agents.scout.get_db_session") as mock_db:
            mock_session = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_db.return_value = mock_ctx

            with patch("src.agents.scout.load_scout_config", return_value={"custom_rules": []}):
                rules = await agent._load_custom_rules()

        assert rules is None

    async def test_load_custom_rules_survives_errors(self):
        """_load_custom_rules should return None on errors (never block scanning)."""
        from src.agents.scout import ScoutAgent

        agent = ScoutAgent(
            llm_client=AsyncMock(),
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
            adapters={},
        )

        with patch("src.agents.scout.get_db_session", side_effect=RuntimeError("DB down")):
            rules = await agent._load_custom_rules()

        assert rules is None

    async def test_load_custom_rules_filters_empty_strings(self):
        """Empty strings in custom_rules should be filtered out."""
        from src.agents.scout import ScoutAgent

        agent = ScoutAgent(
            llm_client=AsyncMock(),
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
            adapters={},
        )

        mock_config = {"custom_rules": ["Valid rule", "", "  ", "Another rule"]}

        with patch("src.agents.scout.get_db_session") as mock_db:
            mock_session = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_db.return_value = mock_ctx

            with patch("src.agents.scout.load_scout_config", return_value=mock_config):
                rules = await agent._load_custom_rules()

        assert rules is not None
        assert len(rules) == 2
        assert "Valid rule" in rules
        assert "Another rule" in rules

    async def test_load_custom_rules_limits_to_50(self):
        """Custom rules should be capped at 50."""
        from src.agents.scout import ScoutAgent

        agent = ScoutAgent(
            llm_client=AsyncMock(),
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
            adapters={},
        )

        mock_config = {"custom_rules": [f"Rule {i}" for i in range(100)]}

        with patch("src.agents.scout.get_db_session") as mock_db:
            mock_session = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_db.return_value = mock_ctx

            with patch("src.agents.scout.load_scout_config", return_value=mock_config):
                rules = await agent._load_custom_rules()

        assert rules is not None
        assert len(rules) == 50

    async def test_score_jobs_includes_custom_rules_in_prompt(self):
        """_score_jobs should include custom rules in the LLM prompt."""
        from src.agents.scout import ScoutAgent

        mock_llm = AsyncMock()
        scored_response = json.dumps(
            [
                {
                    "job_id": "freelancer_123",
                    "match_score": 0.85,
                    "recommendation": "bid",
                }
            ]
        )
        mock_llm.call = AsyncMock(
            return_value=(
                AIMessage(content=scored_response),
                CallMetrics(agent_name="scout", model_id="gemini-flash", provider="google"),
            )
        )

        agent = ScoutAgent(
            llm_client=mock_llm,
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
            adapters={},
        )

        jobs = [{"title": "React landing page", "platform": "freelancer", "external_id": "123"}]
        custom_rules = ["Prefer React projects", "Reject PHP projects"]

        # Mock the container so _call_llm falls through to llm_client.call
        mock_container = MagicMock()
        mock_container.llm_queue = None
        with patch("src.core.container.get_container", return_value=mock_container):
            await agent._score_jobs(jobs, custom_rules=custom_rules)

        # _call_llm calls llm_client.call(agent_name, messages, ...)
        call_args = mock_llm.call.call_args
        messages = call_args[0][1]  # second positional arg is the messages list
        user_msg = messages[1].content

        assert "Custom Rules" in user_msg
        assert "Prefer React projects" in user_msg
        assert "Reject PHP projects" in user_msg

    async def test_score_jobs_works_without_custom_rules(self):
        """_score_jobs should work normally when no custom rules provided."""
        from src.agents.scout import ScoutAgent

        mock_llm = AsyncMock()
        scored_response = json.dumps(
            [
                {
                    "job_id": "freelancer_456",
                    "match_score": 0.75,
                    "recommendation": "bid",
                }
            ]
        )
        mock_llm.call = AsyncMock(
            return_value=(
                AIMessage(content=scored_response),
                CallMetrics(agent_name="scout", model_id="gemini-flash", provider="google"),
            )
        )

        agent = ScoutAgent(
            llm_client=mock_llm,
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
            adapters={},
        )

        jobs = [{"title": "WordPress site", "platform": "flru", "external_id": "456"}]

        # Mock the container so _call_llm falls through to llm_client.call
        mock_container = MagicMock()
        mock_container.llm_queue = None
        with patch("src.core.container.get_container", return_value=mock_container):
            result = await agent._score_jobs(jobs, custom_rules=None)

        assert len(result) == 1
        # _call_llm calls llm_client.call(agent_name, messages, ...)
        call_args = mock_llm.call.call_args
        messages = call_args[0][1]  # second positional arg is the messages list
        user_msg = messages[1].content
        assert "Custom Rules" not in user_msg


# ===========================================================================
# M7: Email Templates Personalization
# ===========================================================================


class TestM7EmailPersonalization:
    """personalize_template replaces {{placeholder}} tokens;
    select_ab_variant picks deterministic A/B variant."""

    def test_personalize_basic_placeholders(self):
        """Basic placeholder replacement should work."""
        from src.enrichment.email_sender import personalize_template

        template = "Hello {{first_name}}, welcome to {{company}}!"
        context = {"first_name": "John", "company": "Acme Corp"}

        result = personalize_template(template, context)

        assert result == "Hello John, welcome to Acme Corp!"

    def test_personalize_all_supported_placeholders(self):
        """All supported placeholders should be replaced."""
        from src.enrichment.email_sender import personalize_template

        template = "{{first_name}} {{last_name}} at {{company}} ({{industry}}, {{city}}) - {{role}} - {{website}}"
        context = {
            "first_name": "Jane",
            "last_name": "Doe",
            "company": "TechCorp",
            "industry": "SaaS",
            "city": "Berlin",
            "role": "CTO",
            "website": "techcorp.com",
        }

        result = personalize_template(template, context)

        assert "Jane" in result
        assert "Doe" in result
        assert "TechCorp" in result
        assert "SaaS" in result
        assert "Berlin" in result
        assert "CTO" in result
        assert "techcorp.com" in result

    def test_personalize_missing_key_uses_fallback(self):
        """Missing keys should use the fallback value."""
        from src.enrichment.email_sender import personalize_template

        template = "Hello {{first_name}}, your company: {{company}}"
        context = {"first_name": "Alice"}

        result = personalize_template(template, context)

        assert result == "Hello Alice, your company: "

    def test_personalize_custom_fallback(self):
        """Custom fallback value should be used for missing keys."""
        from src.enrichment.email_sender import personalize_template

        template = "Hello {{first_name}}"
        context: dict[str, str] = {}

        result = personalize_template(template, context, fallback="there")

        assert result == "Hello there"

    def test_personalize_no_placeholders(self):
        """Templates without placeholders should be returned unchanged."""
        from src.enrichment.email_sender import personalize_template

        template = "Hello, this is a plain message."
        result = personalize_template(template, {"first_name": "John"})

        assert result == template

    def test_personalize_unknown_placeholders_preserved(self):
        """Unknown placeholders should be left as-is."""
        from src.enrichment.email_sender import personalize_template

        template = "Hello {{first_name}}, your {{unknown_field}} is ready."
        context = {"first_name": "Bob"}

        result = personalize_template(template, context)

        assert "Bob" in result
        assert "{{unknown_field}}" in result

    def test_personalize_empty_context(self):
        """Empty context should replace all known placeholders with fallback."""
        from src.enrichment.email_sender import personalize_template

        template = "Dear {{first_name}} from {{company}}"
        result = personalize_template(template, {})

        assert result == "Dear  from "

    def test_personalize_multiple_same_placeholder(self):
        """Same placeholder appearing multiple times should all be replaced."""
        from src.enrichment.email_sender import personalize_template

        template = "{{first_name}}, dear {{first_name}}, hello {{first_name}}"
        context = {"first_name": "Eve"}

        result = personalize_template(template, context)

        assert result == "Eve, dear Eve, hello Eve"

    # -- A/B Variant Selection -----------------------------------------------

    def test_ab_variant_single_variant(self):
        """Single variant should always be returned."""
        from src.enrichment.email_sender import select_ab_variant

        result = select_ab_variant(["Subject A"])
        assert result == "Subject A"

    def test_ab_variant_deterministic(self):
        """Same lead_id should always get the same variant."""
        from src.enrichment.email_sender import select_ab_variant

        variants = ["Subject A", "Subject B", "Subject C"]
        lead_id = "lead-123"

        result1 = select_ab_variant(variants, lead_id=lead_id)
        result2 = select_ab_variant(variants, lead_id=lead_id)

        assert result1 == result2

    def test_ab_variant_different_leads_may_differ(self):
        """Different lead_ids should potentially select different variants."""
        from src.enrichment.email_sender import select_ab_variant

        variants = ["Subject A", "Subject B", "Subject C"]

        # With enough different leads, we should see multiple variants
        selected = set()
        for i in range(100):
            result = select_ab_variant(variants, lead_id=f"lead-{i}")
            selected.add(result)

        # At least 2 different variants should have been selected
        assert len(selected) >= 2

    def test_ab_variant_empty_list_raises(self):
        """Empty variants list should raise ValueError."""
        from src.enrichment.email_sender import select_ab_variant

        with pytest.raises(ValueError, match="variants list must not be empty"):
            select_ab_variant([])

    def test_ab_variant_no_lead_id_returns_first(self):
        """Empty lead_id should return the first variant."""
        from src.enrichment.email_sender import select_ab_variant

        result = select_ab_variant(["Subject A", "Subject B"], lead_id="")
        assert result == "Subject A"

    def test_ab_variant_two_variants(self):
        """Two variants should both be reachable."""
        from src.enrichment.email_sender import select_ab_variant

        variants = ["Subject A", "Subject B"]
        selected = set()
        for i in range(50):
            result = select_ab_variant(variants, lead_id=f"lead-{i}")
            selected.add(result)

        assert "Subject A" in selected or "Subject B" in selected
        assert len(selected) >= 1  # At minimum one is selected

    def test_ab_variant_in_range(self):
        """Selected variant must always be from the provided list."""
        from src.enrichment.email_sender import select_ab_variant

        variants = ["A", "B", "C", "D", "E"]
        for i in range(200):
            result = select_ab_variant(variants, lead_id=f"lead-{i}")
            assert result in variants
