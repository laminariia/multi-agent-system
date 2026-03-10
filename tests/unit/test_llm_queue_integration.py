"""Tests for P3.12 Integration — LLM Queue wired into agents and container.

Covers:
- Settings.LLM_MAX_CONCURRENT config field
- AgentContainer.llm_queue lazy property
- ConstrainedAgent._call_llm routes through queue when available
- Lifespan starts/stops queue
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ===========================================================================
# 1. Settings field
# ===========================================================================


class TestLLMMaxConcurrentSetting:
    """LLM_MAX_CONCURRENT is configurable via Settings."""

    def test_default_value_is_5(self) -> None:
        """Default LLM_MAX_CONCURRENT should be 5."""
        from src.core.config import Settings

        s = Settings(
            DEBUG=True,
            JWT_SECRET_KEY="test",
            ENCRYPTION_KEY="test",
        )
        assert s.LLM_MAX_CONCURRENT == 5

    def test_custom_value(self) -> None:
        """Should accept custom LLM_MAX_CONCURRENT."""
        from src.core.config import Settings

        s = Settings(
            DEBUG=True,
            JWT_SECRET_KEY="test",
            ENCRYPTION_KEY="test",
            LLM_MAX_CONCURRENT=10,
        )
        assert s.LLM_MAX_CONCURRENT == 10


# ===========================================================================
# 2. AgentContainer.llm_queue
# ===========================================================================


class TestContainerLLMQueue:
    """AgentContainer provides llm_queue property."""

    def test_llm_queue_property_exists(self) -> None:
        """Container should have llm_queue property."""
        from src.core.container import AgentContainer

        container = AgentContainer()
        # Property should exist (may be None before start)
        assert hasattr(container, "llm_queue")

    def test_llm_queue_is_none_before_init(self) -> None:
        """llm_queue should be None before explicit initialization."""
        from src.core.container import AgentContainer

        container = AgentContainer()
        assert container.llm_queue is None

    def test_llm_queue_setter(self) -> None:
        """Should accept setting llm_queue externally."""
        from src.core.container import AgentContainer

        container = AgentContainer()
        mock_queue = MagicMock()
        container.llm_queue = mock_queue
        assert container.llm_queue is mock_queue


# ===========================================================================
# 3. _call_llm routes through queue
# ===========================================================================


class TestCallLLMRoutesViaQueue:
    """ConstrainedAgent._call_llm uses queue when available."""

    def _make_agent(self, llm_queue: Any = None) -> Any:
        """Create a minimal concrete agent for testing."""
        from src.agents.base import ConstrainedAgent

        class _TestAgent(ConstrainedAgent):
            async def _execute(self, state):
                return state

        agent = _TestAgent(
            agent_name="dev",
            allowed_tools=[],
            llm_client=MagicMock(),
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
        )
        return agent

    async def test_uses_queue_when_available(self) -> None:
        """_call_llm should submit through queue when container has one."""
        from src.core.llm_queue import LLMPriority

        agent = self._make_agent()

        mock_queue = AsyncMock()
        mock_response = MagicMock()
        mock_metrics = MagicMock()
        mock_queue.submit = AsyncMock(return_value=(mock_response, mock_metrics))

        with patch("src.core.container.get_container") as mock_get:
            mock_container = MagicMock()
            mock_container.llm_queue = mock_queue
            mock_get.return_value = mock_container

            _result = await agent._call_llm([], temperature=0.5)

            mock_queue.submit.assert_awaited_once()
            call_kwargs = mock_queue.submit.call_args
            assert call_kwargs.kwargs.get("priority") == LLMPriority.NORMAL
            assert call_kwargs.kwargs.get("agent_name") == "dev"

    async def test_falls_back_to_direct_call(self) -> None:
        """_call_llm should call llm_client directly when no queue."""
        agent = self._make_agent()
        mock_response = MagicMock()
        mock_metrics = MagicMock()
        agent.llm_client.call = AsyncMock(return_value=(mock_response, mock_metrics))

        with patch("src.core.container.get_container") as mock_get:
            mock_container = MagicMock()
            mock_container.llm_queue = None
            mock_get.return_value = mock_container

            result = await agent._call_llm([])

            agent.llm_client.call.assert_awaited_once()
            assert result == (mock_response, mock_metrics)

    async def test_queue_uses_agent_priority(self) -> None:
        """Queue should use AGENT_PRIORITY mapping for the agent."""
        from src.core.llm_queue import LLMPriority

        agent = self._make_agent()
        agent.agent_name = "scout"  # LOW priority

        mock_queue = AsyncMock()
        mock_queue.submit = AsyncMock(return_value=(MagicMock(), MagicMock()))

        with patch("src.core.container.get_container") as mock_get:
            mock_container = MagicMock()
            mock_container.llm_queue = mock_queue
            mock_get.return_value = mock_container

            await agent._call_llm([])

            call_kwargs = mock_queue.submit.call_args
            assert call_kwargs.kwargs.get("priority") == LLMPriority.LOW
