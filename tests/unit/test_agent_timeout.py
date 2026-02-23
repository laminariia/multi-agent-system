"""Tests for agent timeout enforcement (_DEFAULT_NODE_TIMEOUT_SECONDS=600).

Target: src/agents/base.py:293-309 — TimeoutError handler in invoke().
Pattern: tests/unit/test_base_agent_extended.py — _TestAgent(ConstrainedAgent).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.agents.base import ConstrainedAgent
from src.core.state import AgentState

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Concrete agent with injectable _execute
# ---------------------------------------------------------------------------


class _TimeoutAgent(ConstrainedAgent):
    """Thin concrete agent whose _execute hangs or succeeds as configured."""

    def __init__(self, execute_fn, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._fn = execute_fn

    async def _execute(self, state: AgentState) -> AgentState:
        return await self._fn(state)


def _agent(execute_fn, infra, **kw):
    return _TimeoutAgent(
        execute_fn=execute_fn,
        agent_name=kw.get("agent_name", "scout"),
        allowed_tools=["search_jobs"],
        llm_client=infra["llm"],
        heartbeat=infra["hb"],
        loop_detector=infra["ld"],
    )


@pytest.fixture()
def infra(mock_llm_client, mock_heartbeat, mock_loop_detector):
    return {"llm": mock_llm_client, "hb": mock_heartbeat, "ld": mock_loop_detector}


# ===== P1: Timeout enforcement ==============================================


@patch("src.agents.base._DEFAULT_NODE_TIMEOUT_SECONDS", 0.01)
async def test_invoke_timeout_returns_failed_state(sample_state, infra):
    """_execute hanging beyond timeout → status='failed', 'timed out' in errors."""

    async def hang(state):
        await asyncio.sleep(999)
        return state  # pragma: no cover

    result = await _agent(hang, infra).invoke(sample_state)
    assert result["status"] == "failed"
    assert any("timed out" in e for e in result["errors"])
    assert result["next_agent"] is None


@patch("src.agents.base._DEFAULT_NODE_TIMEOUT_SECONDS", 0.01)
async def test_invoke_timeout_records_metric(sample_state, infra):
    """Prometheus metric recorded with status='timeout'."""

    async def hang(state):
        await asyncio.sleep(999)
        return state  # pragma: no cover

    with patch("src.monitoring.metrics.get_metrics") as gm:
        mi = MagicMock()
        gm.return_value = mi
        await _agent(hang, infra).invoke(sample_state)
        mi.record_agent_run.assert_called_once()
        assert mi.record_agent_run.call_args[1]["status"] == "timeout"


@patch("src.agents.base._DEFAULT_NODE_TIMEOUT_SECONDS", 0.01)
async def test_invoke_timeout_does_not_retry(sample_state, infra):
    """TimeoutError returns immediately — no retry loop (unlike LLMException)."""
    call_count = 0

    async def hang(state):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(999)
        return state  # pragma: no cover

    await _agent(hang, infra).invoke(sample_state)
    assert call_count == 1, "Expected exactly one _execute call (no retry on timeout)"


@patch("src.agents.base._DEFAULT_NODE_TIMEOUT_SECONDS", 0.01)
async def test_invoke_timeout_preserves_thread_id(sample_state, infra):
    """thread_id and current_agent are preserved after timeout."""

    async def hang(state):
        await asyncio.sleep(999)
        return state  # pragma: no cover

    result = await _agent(hang, infra).invoke(sample_state)
    assert result["thread_id"] == sample_state["thread_id"]
    assert result["current_agent"] == "scout"
