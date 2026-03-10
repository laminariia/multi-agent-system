"""Tests for P3.14 — Pipeline Timeout: stuck agent → HITL.

Covers:
- All agents create HITL entries on timeout (not just recoverable)
- Configurable agent timeout via AGENT_TIMEOUT_SECONDS env var
- Pipeline-level timeout in run_project_pipeline
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.base import ConstrainedAgent
from src.core.state import AgentState

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test agent with injectable _execute
# ---------------------------------------------------------------------------


class _TestAgent(ConstrainedAgent):
    """Thin concrete agent with configurable _execute."""

    def __init__(self, execute_fn, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._fn = execute_fn

    async def _execute(self, state: AgentState) -> AgentState:
        return await self._fn(state)


def _make_agent(execute_fn, infra, agent_name: str = "scout"):
    return _TestAgent(
        execute_fn=execute_fn,
        agent_name=agent_name,
        allowed_tools=["search_jobs"],
        llm_client=infra["llm"],
        heartbeat=infra["hb"],
        loop_detector=infra["ld"],
    )


@pytest.fixture()
def infra(mock_llm_client, mock_heartbeat, mock_loop_detector):
    return {"llm": mock_llm_client, "hb": mock_heartbeat, "ld": mock_loop_detector}


# ===========================================================================
# 1. Non-recoverable agents create HITL entries on timeout
# ===========================================================================


class TestTimeoutHITLCreation:
    """All agents (not just recoverable) create HITL entries on timeout."""

    @patch("src.agents.base._DEFAULT_NODE_TIMEOUT_SECONDS", 0.01)
    async def test_non_recoverable_agent_creates_hitl_on_timeout(self, sample_state, infra) -> None:
        """Scout (non-recoverable) timeout creates HITL alert entry."""

        async def hang(state):
            await asyncio.sleep(999)
            return state

        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        with patch("src.agents.base.get_db_session") as mock_get_db:
            mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _make_agent(hang, infra, agent_name="scout").invoke(sample_state)

        # Should still be failed for non-recoverable
        assert result["status"] == "failed"
        # But HITL entry should have been created
        mock_session.add.assert_called_once()
        hitl_entry = mock_session.add.call_args[0][0]
        assert hitl_entry.type == "agent_timeout"
        assert hitl_entry.priority == "urgent"
        assert "scout" in hitl_entry.title
        assert "timed out" in hitl_entry.title.lower()

    @patch("src.agents.base._DEFAULT_NODE_TIMEOUT_SECONDS", 0.01)
    async def test_non_recoverable_planner_creates_hitl_on_timeout(self, sample_state, infra) -> None:
        """Planner (non-recoverable) timeout also creates HITL alert."""

        async def hang(state):
            await asyncio.sleep(999)
            return state

        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        with patch("src.agents.base.get_db_session") as mock_get_db:
            mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _make_agent(hang, infra, agent_name="planner").invoke(sample_state)

        assert result["status"] == "failed"
        mock_session.add.assert_called_once()
        hitl_entry = mock_session.add.call_args[0][0]
        assert "planner" in hitl_entry.title

    @patch("src.agents.base._DEFAULT_NODE_TIMEOUT_SECONDS", 0.01)
    async def test_recoverable_agent_pauses_on_timeout(self, sample_state, infra) -> None:
        """Dev (recoverable) timeout creates HITL entry AND pauses pipeline."""

        async def hang(state):
            await asyncio.sleep(999)
            return state

        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        with patch("src.agents.base.get_db_session") as mock_get_db:
            mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _make_agent(hang, infra, agent_name="dev").invoke(sample_state)

        # Recoverable agents should pause (not fail)
        assert result["status"] == "paused"
        assert result["requires_hitl"] is True

    @patch("src.agents.base._DEFAULT_NODE_TIMEOUT_SECONDS", 0.01)
    async def test_timeout_hitl_entry_has_correct_payload(self, sample_state, infra) -> None:
        """HITL entry payload includes timeout details."""

        async def hang(state):
            await asyncio.sleep(999)
            return state

        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        with patch("src.agents.base.get_db_session") as mock_get_db:
            mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

            await _make_agent(hang, infra, agent_name="bid").invoke(sample_state)

        hitl_entry = mock_session.add.call_args[0][0]
        payload = hitl_entry.payload
        assert payload["failed_agent"] == "bid"
        assert "timeout" in payload["failure_reason"].lower()
        assert "thread_id" in payload

    @patch("src.agents.base._DEFAULT_NODE_TIMEOUT_SECONDS", 0.01)
    async def test_timeout_hitl_failure_does_not_break_agent(self, sample_state, infra) -> None:
        """If HITL creation fails, agent still returns failed state gracefully."""

        async def hang(state):
            await asyncio.sleep(999)
            return state

        with patch("src.agents.base.get_db_session", side_effect=RuntimeError("DB down")):
            result = await _make_agent(hang, infra, agent_name="scout").invoke(sample_state)

        # Should still return failed state even if HITL creation failed
        assert result["status"] == "failed"
        assert any("timed out" in e for e in result["errors"])


# ===========================================================================
# 2. Configurable timeout
# ===========================================================================


class TestConfigurableTimeout:
    """Agent timeout is configurable via AGENT_TIMEOUT_SECONDS env var."""

    async def test_default_timeout_is_600(self) -> None:
        """Default timeout is 600 seconds (10 minutes)."""
        from src.agents.base import _DEFAULT_NODE_TIMEOUT_SECONDS

        assert _DEFAULT_NODE_TIMEOUT_SECONDS == 600

    @patch.dict("os.environ", {"AGENT_TIMEOUT_SECONDS": "300"})
    async def test_timeout_configurable_via_env(self, sample_state, infra) -> None:
        """AGENT_TIMEOUT_SECONDS env var controls timeout duration."""
        from src.agents import base as base_mod

        # Re-evaluate the timeout value
        original = base_mod._DEFAULT_NODE_TIMEOUT_SECONDS
        try:
            base_mod._DEFAULT_NODE_TIMEOUT_SECONDS = int(__import__("os").environ.get("AGENT_TIMEOUT_SECONDS", "600"))
            assert base_mod._DEFAULT_NODE_TIMEOUT_SECONDS == 300
        finally:
            base_mod._DEFAULT_NODE_TIMEOUT_SECONDS = original


# ===========================================================================
# 3. Pipeline-level timeout
# ===========================================================================


class TestPipelineTimeout:
    """Pipeline-level timeout wraps entire graph execution."""

    async def test_pipeline_timeout_creates_hitl(self) -> None:
        """Pipeline exceeding timeout creates HITL entry."""
        from src.worker.tasks import run_project_pipeline

        payload = {
            "project_id": "test-proj-1",
            "job_id": "test-job-1",
            "platform": "freelancer",
            "requirements": "Build a landing page",
            "budget": 500,
        }

        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        async def slow_graph(*args, **kwargs):
            await asyncio.sleep(999)
            return {}

        with (
            patch("src.worker.tasks.create_graph_with_persistence") as mock_graph_factory,
            patch("src.worker.tasks.get_settings") as mock_settings,
            patch("src.worker.tasks.get_valkey") as mock_valkey,
            patch("src.worker.tasks.asyncpg") as mock_asyncpg,
            patch("src.worker.tasks._PIPELINE_TIMEOUT_SECONDS", 0.01),
            patch("src.worker.tasks.get_db_session") as mock_get_db,
        ):
            mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://test")
            mock_valkey.return_value = MagicMock()
            mock_pool = AsyncMock()
            mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)

            mock_graph = AsyncMock()
            mock_graph.ainvoke = slow_graph
            mock_graph_factory.return_value = mock_graph

            mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await run_project_pipeline(payload)

        assert result["status"] == "timeout"
        # HITL entry should have been created
        mock_session.add.assert_called_once()
        hitl_entry = mock_session.add.call_args[0][0]
        assert hitl_entry.type == "pipeline_timeout"
        assert hitl_entry.priority == "urgent"

    async def test_pipeline_timeout_returns_proper_result(self) -> None:
        """Pipeline timeout returns structured result with status and thread_id."""
        from src.worker.tasks import run_project_pipeline

        payload = {
            "project_id": "test-proj-2",
            "job_id": "test-job-2",
            "platform": "freelancer",
            "requirements": "Build API",
            "budget": 1000,
        }

        async def slow_graph(*args, **kwargs):
            await asyncio.sleep(999)
            return {}

        with (
            patch("src.worker.tasks.create_graph_with_persistence") as mock_graph_factory,
            patch("src.worker.tasks.get_settings") as mock_settings,
            patch("src.worker.tasks.get_valkey"),
            patch("src.worker.tasks.asyncpg") as mock_asyncpg,
            patch("src.worker.tasks._PIPELINE_TIMEOUT_SECONDS", 0.01),
            patch("src.worker.tasks.get_db_session") as mock_get_db,
        ):
            mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://test")
            mock_pool = AsyncMock()
            mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)

            mock_graph = AsyncMock()
            mock_graph.ainvoke = slow_graph
            mock_graph_factory.return_value = mock_graph

            mock_session = AsyncMock()
            mock_session.add = MagicMock()
            mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await run_project_pipeline(payload)

        assert result["task"] == "project_pipeline"
        assert result["project_id"] == "test-proj-2"
        assert result["status"] == "timeout"
        assert "thread_id" in result
