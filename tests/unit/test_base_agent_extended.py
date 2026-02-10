"""Extended unit tests for src.agents.base — ConstrainedAgent lifecycle.

Covers: invoke() with all exception paths, _validate_role_constraints,
_build_system_prompt, _assert_tool_allowed, _describe_task.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.base import ConstrainedAgent, _describe_task
from src.core.exceptions import (
    AgentException,
    HITLRequiredError,
    LLMException,
    LoopDetectedError,
    MASException,
)
from src.core.state import AgentState, update_state

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Concrete subclass for testing the abstract ConstrainedAgent
# ---------------------------------------------------------------------------

class _TestAgent(ConstrainedAgent):
    """Thin concrete agent whose _execute behaviour is injected via callable."""

    def __init__(self, execute_fn, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._execute_fn = execute_fn

    async def _execute(self, state: AgentState) -> AgentState:
        return await self._execute_fn(state)


def _agent(execute_fn, fixtures, *, agent_name="scout", allowed_tools=None, max_retries=3):
    return _TestAgent(
        execute_fn=execute_fn,
        agent_name=agent_name,
        allowed_tools=allowed_tools or ["search_jobs"],
        llm_client=fixtures["llm"],
        heartbeat=fixtures["hb"],
        loop_detector=fixtures["ld"],
        max_retries=max_retries,
    )


@pytest.fixture()
def infra(mock_llm_client, mock_heartbeat, mock_loop_detector):
    return {"llm": mock_llm_client, "hb": mock_heartbeat, "ld": mock_loop_detector}


# ===== invoke() success =====================================================

async def test_invoke_success(sample_state, infra):
    async def ok(state):
        return update_state(state, next_agent="bid", status="active")

    result = await _agent(ok, infra).invoke(sample_state)
    assert result["status"] == "active"
    assert result["next_agent"] == "bid"
    assert result["current_agent"] == "scout"


async def test_invoke_records_hitl_paused_metrics(sample_state, infra):
    async def hitl(state):
        return update_state(state, requires_hitl=True, status="paused")

    with patch("src.monitoring.metrics.get_metrics") as gm:
        mi = MagicMock()
        gm.return_value = mi
        await _agent(hitl, infra).invoke(sample_state)
        mi.record_agent_run.assert_called_once()
        assert mi.record_agent_run.call_args[1]["status"] == "hitl_paused"


async def test_invoke_metrics_error_does_not_break(sample_state, infra):
    async def ok(state):
        return update_state(state, next_agent="bid")

    with patch("src.monitoring.metrics.get_metrics", side_effect=RuntimeError("boom")):
        result = await _agent(ok, infra).invoke(sample_state)
    assert result["next_agent"] == "bid"


# ===== invoke() LoopDetectedError ===========================================

async def test_invoke_loop_detected(sample_state, infra):
    infra["ld"] = MagicMock()
    infra["ld"].check = AsyncMock(side_effect=LoopDetectedError(
        thread_id=sample_state["thread_id"], iteration_count=50,
    ))

    async def noop(s):
        return s  # pragma: no cover

    result = await _agent(noop, infra).invoke(sample_state)
    assert result["status"] == "failed"
    assert any("Loop detected" in e for e in result["errors"])


# ===== invoke() HITLRequiredError ============================================

async def test_invoke_hitl_required(sample_state, infra):
    async def raise_hitl(state):
        raise HITLRequiredError(
            message="Need approval",
            agent_name="bid",
            thread_id=state["thread_id"],
            hitl_request_id="hitl-999",
        )

    result = await _agent(raise_hitl, infra, agent_name="bid").invoke(sample_state)
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["hitl_request_id"] == "hitl-999"


# ===== invoke() LLMException with retries ====================================

async def test_invoke_llm_exception_retry_then_success(sample_state, infra):
    calls = 0

    async def flaky(state):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise LLMException(message="rate limit", agent_name="scout")
        return update_state(state, next_agent="bid")

    result = await _agent(flaky, infra).invoke(sample_state)
    assert result["next_agent"] == "bid"
    assert calls == 3


async def test_invoke_llm_exception_exhausts_retries(sample_state, infra):
    async def always_fail(state):
        raise LLMException(message="always fails", agent_name="scout")

    with patch("src.monitoring.metrics.get_metrics") as gm:
        mi = MagicMock()
        gm.return_value = mi
        result = await _agent(always_fail, infra, max_retries=2).invoke(sample_state)
    assert result["status"] == "failed"
    assert result["retry_count"] == 2
    mi.record_agent_run.assert_called_once()


# ===== invoke() AgentException ===============================================

async def test_invoke_agent_exception_retry_success(sample_state, infra):
    calls = 0

    async def flaky(state):
        nonlocal calls
        calls += 1
        if calls < 2:
            raise AgentException(message="temp", agent_name="dev", thread_id=state["thread_id"])
        return update_state(state, next_agent="critic")

    result = await _agent(flaky, infra, agent_name="dev", allowed_tools=["write_code"]).invoke(sample_state)
    assert result["next_agent"] == "critic"


async def test_invoke_agent_exception_exhausts_retries(sample_state, infra):
    async def always_fail(state):
        raise AgentException(message="stuck", agent_name="dev", thread_id=state["thread_id"])

    result = await _agent(always_fail, infra, agent_name="dev",
                          allowed_tools=["write_code"], max_retries=3).invoke(sample_state)
    assert result["status"] == "failed"
    assert result["retry_count"] == 3


# ===== invoke() MASException (no retry) ======================================

async def test_invoke_mas_exception(sample_state, infra):
    async def boom(state):
        raise MASException("critical")

    result = await _agent(boom, infra).invoke(sample_state)
    assert result["status"] == "failed"
    assert any("Unrecoverable" in e for e in result["errors"])
    assert result["retry_count"] == 0


# ===== invoke() unexpected Exception =========================================

async def test_invoke_unexpected_exception(sample_state, infra):
    async def boom(state):
        raise ValueError("oops")

    result = await _agent(boom, infra).invoke(sample_state)
    assert result["status"] == "failed"
    assert any("Unexpected: ValueError" in e for e in result["errors"])


# ===== _validate_role_constraints ============================================

def test_validate_forbidden_tools_overlap(sample_state, infra):
    agent = _TestAgent(
        execute_fn=AsyncMock(),
        agent_name="scout",
        allowed_tools=["search_jobs", "execute_code"],  # execute_code is forbidden
        llm_client=infra["llm"],
        heartbeat=infra["hb"],
        loop_detector=infra["ld"],
    )
    with pytest.raises(AgentException, match="forbidden tools"):
        agent._validate_role_constraints(sample_state)


def test_validate_iteration_exceeded(sample_state, infra):
    agent = _TestAgent(
        execute_fn=AsyncMock(),
        agent_name="dev",
        allowed_tools=["write_code"],
        llm_client=infra["llm"],
        heartbeat=infra["hb"],
        loop_detector=infra["ld"],
    )
    over = update_state(sample_state, retry_count=6)
    with pytest.raises(AgentException, match="exceeded max iterations"):
        agent._validate_role_constraints(over)


def test_validate_clean_pass(sample_state, infra):
    agent = _TestAgent(
        execute_fn=AsyncMock(),
        agent_name="scout",
        allowed_tools=["search_jobs"],
        llm_client=infra["llm"],
        heartbeat=infra["hb"],
        loop_detector=infra["ld"],
    )
    agent._validate_role_constraints(sample_state)  # no raise


# ===== _build_system_prompt ==================================================

def test_build_system_prompt_scout(infra):
    agent = _TestAgent(execute_fn=AsyncMock(), agent_name="scout",
                       allowed_tools=[], llm_client=infra["llm"],
                       heartbeat=infra["hb"], loop_detector=infra["ld"])
    prompt = agent._build_system_prompt()
    assert "Job Scout" in prompt
    assert "submit_proposal" in prompt
    assert "CRITICAL CONSTRAINTS" in prompt


def test_build_system_prompt_unknown_agent(infra):
    agent = _TestAgent(execute_fn=AsyncMock(), agent_name="unknown_xyz",
                       allowed_tools=[], llm_client=infra["llm"],
                       heartbeat=infra["hb"], loop_detector=infra["ld"])
    prompt = agent._build_system_prompt()
    assert "unknown_xyz" in prompt
    assert "- None" in prompt  # no forbidden tools


def test_build_system_prompt_no_forbidden(infra):
    agent = _TestAgent(execute_fn=AsyncMock(), agent_name="unknown_xyz",
                       allowed_tools=[], llm_client=infra["llm"],
                       heartbeat=infra["hb"], loop_detector=infra["ld"])
    prompt = agent._build_system_prompt()
    assert "FORBIDDEN ACTIONS:" in prompt
    assert "- None" in prompt


# ===== _assert_tool_allowed ==================================================

def test_assert_tool_forbidden(infra):
    agent = _TestAgent(execute_fn=AsyncMock(), agent_name="scout",
                       allowed_tools=["search_jobs"], llm_client=infra["llm"],
                       heartbeat=infra["hb"], loop_detector=infra["ld"])
    with pytest.raises(AgentException, match="forbidden"):
        agent._assert_tool_allowed("execute_code")


def test_assert_tool_not_in_allowed(infra):
    agent = _TestAgent(execute_fn=AsyncMock(), agent_name="scout",
                       allowed_tools=["search_jobs"], llm_client=infra["llm"],
                       heartbeat=infra["hb"], loop_detector=infra["ld"])
    with pytest.raises(AgentException, match="not permitted"):
        agent._assert_tool_allowed("unknown_tool")


def test_assert_tool_passes(infra):
    agent = _TestAgent(execute_fn=AsyncMock(), agent_name="scout",
                       allowed_tools=["search_jobs"], llm_client=infra["llm"],
                       heartbeat=infra["hb"], loop_detector=infra["ld"])
    agent._assert_tool_allowed("search_jobs")  # no raise


# ===== _describe_task ========================================================

def test_describe_task_none():
    assert _describe_task(None) is None


def test_describe_task_description():
    assert _describe_task({"description": "Build page"}) == "Build page"


def test_describe_task_name_fallback():
    assert _describe_task({"name": "Task A"}) == "Task A"


def test_describe_task_truncation():
    result = _describe_task({"data": "x" * 200})
    assert result is not None and len(result) <= 120


def test_describe_task_empty_dict():
    assert _describe_task({}) is not None


# ===== _record_metric =======================================================

def test_record_metric_success(infra):
    """_record_metric calls get_metrics().record_agent_run on success."""
    agent = _agent(AsyncMock(), infra)
    with patch("src.monitoring.metrics.get_metrics") as gm:
        mi = MagicMock()
        gm.return_value = mi
        agent._record_metric("success", 1.5)
        mi.record_agent_run.assert_called_once_with(
            "scout", status="success", duration_seconds=1.5,
        )


def test_record_metric_does_not_raise_on_error(infra):
    """_record_metric swallows exceptions without crashing."""
    agent = _agent(AsyncMock(), infra)
    with patch("src.monitoring.metrics.get_metrics", side_effect=RuntimeError("boom")):
        agent._record_metric("failed", 0)  # Should not raise


def test_record_metric_logs_failure(infra):
    """_record_metric logs debug when metrics fail."""
    agent = _agent(AsyncMock(), infra)
    with (
        patch("src.monitoring.metrics.get_metrics", side_effect=RuntimeError("boom")),
        patch.object(agent._log, "debug") as mock_debug,
    ):
        agent._record_metric("failed", 0)
        mock_debug.assert_called_once()
        assert mock_debug.call_args[0][0] == "metrics_recording_failed"


def test_record_metric_hitl_paused(infra):
    """_record_metric passes status correctly for hitl_paused."""
    agent = _agent(AsyncMock(), infra)
    with patch("src.monitoring.metrics.get_metrics") as gm:
        mi = MagicMock()
        gm.return_value = mi
        agent._record_metric("hitl_paused", 2.0)
        assert mi.record_agent_run.call_args[1]["status"] == "hitl_paused"
