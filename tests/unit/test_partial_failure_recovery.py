"""Unit tests for Partial Failure Recovery HITL (L3).

Tests cover: agent failure → HITL entry creation, retry/skip/manual_override
actions, graph routing on agent exception, state preservation during recovery.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.base import _RECOVERABLE_AGENTS, ConstrainedAgent
from src.core.state import create_initial_state

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project() -> dict[str, Any]:
    return {
        "project_id": "test-proj-001",
        "job_id": "job-001",
        "platform": "freelancer",
        "client": {"name": "TestClient"},
        "requirements": "Build a website",
        "budget": 500.0,
        "deadline": "2026-04-01T00:00:00Z",
    }


def _make_state(**overrides: Any) -> dict[str, Any]:
    state = create_initial_state(project=_make_project())
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Recoverable agents set
# ---------------------------------------------------------------------------


class TestRecoverableAgents:
    """Only execution agents support partial failure recovery."""

    def test_dev_is_recoverable(self):
        assert "dev" in _RECOVERABLE_AGENTS

    def test_content_is_recoverable(self):
        assert "content" in _RECOVERABLE_AGENTS

    def test_design_is_recoverable(self):
        assert "design" in _RECOVERABLE_AGENTS

    def test_scout_is_not_recoverable(self):
        assert "scout" not in _RECOVERABLE_AGENTS

    def test_bid_is_not_recoverable(self):
        assert "bid" not in _RECOVERABLE_AGENTS

    def test_planner_is_not_recoverable(self):
        assert "planner" not in _RECOVERABLE_AGENTS

    def test_packager_is_not_recoverable(self):
        assert "packager" not in _RECOVERABLE_AGENTS


# ---------------------------------------------------------------------------
# _pause_for_recovery method
# ---------------------------------------------------------------------------


class TestPauseForRecovery:
    """ConstrainedAgent._pause_for_recovery creates HITL and pauses state."""

    @patch("src.core.database.get_db_session")
    async def test_pause_sets_status_paused(self, mock_db):
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

        # Create a concrete subclass for testing
        class TestAgent(ConstrainedAgent):
            async def _execute(self, state):
                return state

        agent = TestAgent(
            agent_name="dev",
            allowed_tools=[],
            llm_client=MagicMock(),
            heartbeat=AsyncMock(),
            loop_detector=AsyncMock(),
        )

        state = _make_state()
        result = await agent._pause_for_recovery(state, "Test failure reason")

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True
        assert result["failed_agent"] == "dev"
        assert "Test failure" in result["failure_reason"]

    @patch("src.core.database.get_db_session")
    async def test_pause_creates_hitl_entry(self, mock_db):
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

        class TestAgent(ConstrainedAgent):
            async def _execute(self, state):
                return state

        agent = TestAgent(
            agent_name="content",
            allowed_tools=[],
            llm_client=MagicMock(),
            heartbeat=AsyncMock(),
            loop_detector=AsyncMock(),
        )

        state = _make_state()
        await agent._pause_for_recovery(state, "Content agent crashed")

        # HITL entry should have been added to session
        mock_session.add.assert_called_once()
        hitl_entry = mock_session.add.call_args[0][0]
        assert hitl_entry.type == "agent_failure"
        assert hitl_entry.priority == "urgent"
        assert "content" in hitl_entry.title
        assert "resume" in hitl_entry.available_actions
        assert "skip" in hitl_entry.available_actions
        assert "manual" in hitl_entry.available_actions

    @patch("src.core.database.get_db_session")
    async def test_pause_preserves_thread_id(self, mock_db):
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

        class TestAgent(ConstrainedAgent):
            async def _execute(self, state):
                return state

        agent = TestAgent(
            agent_name="dev",
            allowed_tools=[],
            llm_client=MagicMock(),
            heartbeat=AsyncMock(),
            loop_detector=AsyncMock(),
        )

        state = _make_state()
        original_thread_id = state["thread_id"]
        result = await agent._pause_for_recovery(state, "error")

        assert result["thread_id"] == original_thread_id

    @patch("src.core.database.get_db_session")
    async def test_pause_sets_hitl_request_id(self, mock_db):
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

        class TestAgent(ConstrainedAgent):
            async def _execute(self, state):
                return state

        agent = TestAgent(
            agent_name="design",
            allowed_tools=[],
            llm_client=MagicMock(),
            heartbeat=AsyncMock(),
            loop_detector=AsyncMock(),
        )

        state = _make_state()
        result = await agent._pause_for_recovery(state, "error")

        # hitl_request_id should be a valid UUID string
        assert result["hitl_request_id"] is not None
        uuid.UUID(result["hitl_request_id"])  # should not raise

    @patch("src.core.database.get_db_session")
    async def test_pause_truncates_long_reason(self, mock_db):
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

        class TestAgent(ConstrainedAgent):
            async def _execute(self, state):
                return state

        agent = TestAgent(
            agent_name="dev",
            allowed_tools=[],
            llm_client=MagicMock(),
            heartbeat=AsyncMock(),
            loop_detector=AsyncMock(),
        )

        state = _make_state()
        long_reason = "x" * 1000
        result = await agent._pause_for_recovery(state, long_reason)

        assert len(result["failure_reason"]) <= 500

    @patch("src.core.database.get_db_session")
    async def test_pause_includes_recovery_metadata_in_payload(self, mock_db):
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

        class TestAgent(ConstrainedAgent):
            async def _execute(self, state):
                return state

        agent = TestAgent(
            agent_name="dev",
            allowed_tools=[],
            llm_client=MagicMock(),
            heartbeat=AsyncMock(),
            loop_detector=AsyncMock(),
        )

        state = _make_state(
            agent_sequence=["dev", "content", "design"],
            current_sequence_index=1,
            artifacts={"dev": ["code.py"]},
        )
        await agent._pause_for_recovery(state, "test error")

        hitl_entry = mock_session.add.call_args[0][0]
        payload = hitl_entry.payload
        assert payload["failed_agent"] == "dev"
        assert payload["thread_id"] == state["thread_id"]
        assert payload["agent_sequence"] == ["dev", "content", "design"]
        assert "dev" in payload["completed_artifacts"]


# ---------------------------------------------------------------------------
# Agent invoke with exception → recovery path
# ---------------------------------------------------------------------------


class TestInvokeRecoveryPath:
    """When a recoverable agent raises, invoke() pauses for HITL recovery."""

    @patch("src.core.database.get_db_session")
    async def test_recoverable_agent_pauses_on_exception(self, mock_db):
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

        class FailingDevAgent(ConstrainedAgent):
            async def _execute(self, state):
                raise RuntimeError("Unexpected crash in dev agent")

        agent = FailingDevAgent(
            agent_name="dev",
            allowed_tools=[],
            llm_client=MagicMock(),
            heartbeat=AsyncMock(),
            loop_detector=AsyncMock(),
        )

        state = _make_state()
        result = await agent.invoke(state)

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True
        assert result["failed_agent"] == "dev"

    @patch("src.core.database.get_db_session")
    async def test_non_recoverable_agent_fails_on_exception(self, mock_db):
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

        class FailingScoutAgent(ConstrainedAgent):
            async def _execute(self, state):
                raise RuntimeError("Unexpected crash in scout")

        agent = FailingScoutAgent(
            agent_name="scout",
            allowed_tools=[],
            llm_client=MagicMock(),
            heartbeat=AsyncMock(),
            loop_detector=AsyncMock(),
        )

        state = _make_state()
        result = await agent.invoke(state)

        # Scout is NOT recoverable — should fail, not pause
        assert result["status"] == "failed"
        assert result.get("requires_hitl") is not True


# ---------------------------------------------------------------------------
# HITL actions for agent_failure
# ---------------------------------------------------------------------------


class TestAgentFailureHITLActions:
    """Verify the HITL queue entry has correct available_actions."""

    @patch("src.core.database.get_db_session")
    async def test_available_actions_include_retry_skip_manual(self, mock_db):
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

        class TestAgent(ConstrainedAgent):
            async def _execute(self, state):
                return state

        agent = TestAgent(
            agent_name="dev",
            allowed_tools=[],
            llm_client=MagicMock(),
            heartbeat=AsyncMock(),
            loop_detector=AsyncMock(),
        )

        state = _make_state()
        await agent._pause_for_recovery(state, "test")

        hitl_entry = mock_session.add.call_args[0][0]
        actions = hitl_entry.available_actions
        assert "resume" in actions
        assert "skip" in actions
        assert "manual" in actions

    @patch("src.core.database.get_db_session")
    async def test_hitl_entry_type_is_agent_failure(self, mock_db):
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

        class TestAgent(ConstrainedAgent):
            async def _execute(self, state):
                return state

        agent = TestAgent(
            agent_name="dev",
            allowed_tools=[],
            llm_client=MagicMock(),
            heartbeat=AsyncMock(),
            loop_detector=AsyncMock(),
        )

        state = _make_state()
        await agent._pause_for_recovery(state, "test")

        hitl_entry = mock_session.add.call_args[0][0]
        assert hitl_entry.type == "agent_failure"
        assert hitl_entry.priority == "urgent"
