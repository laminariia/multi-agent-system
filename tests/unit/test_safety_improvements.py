"""Tests for safety improvements: HITL locking, Critic HITL creation, node timeout.

Covers:
1. HITL resolve uses SELECT FOR UPDATE (row-level locking)
2. HITL bulk-resolve uses SELECT FOR UPDATE
3. Critic creates HITLQueue entry on revision limit exhaustion
4. Critic creates HITLQueue entry on scope_creep
5. Critic creates HITLQueue entry on rejection
6. ConstrainedAgent times out after _DEFAULT_NODE_TIMEOUT_SECONDS
7. ConstrainedAgent timeout returns failed status with error message
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.agents.base import _DEFAULT_NODE_TIMEOUT_SECONDS, ConstrainedAgent
from src.agents.critic import _MAX_REVISION_CYCLES, CriticAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults."""
    project = {
        "project_id": "proj-test-001",
        "job_id": "job-test-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a landing page",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    state = create_initial_state(
        project=project,
        first_agent="critic",
        thread_id="thread-safety-test",
    )
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _make_dev_artifacts() -> dict[str, list[str]]:
    """Build a minimal set of dev artifacts."""
    code_data = json.dumps(
        {
            "files": [{"path": "src/Hero.tsx", "content": "export default function Hero() {}"}],
        }
    )
    return {"dev": ["artifact-id", code_data]}


def _make_review_response(
    verdict: str = "approve",
    score: float = 0.92,
    revision_type: str = "none",
    issues: list[dict[str, Any]] | None = None,
) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "score": score,
            "revision_type": revision_type,
            "issues": issues or [],
            "passed_checks": ["compiles"],
            "failed_checks": [],
            "revision_instructions": "",
        }
    )


# ---------------------------------------------------------------------------
# 1. HITL resolve: row-level locking (SELECT FOR UPDATE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hitl_resolve_uses_for_update():
    """resolve() should use SELECT ... FOR UPDATE to prevent race conditions."""
    from src.api.routes.hitl import HITLController

    controller = HITLController(owner=MagicMock())

    mock_item = MagicMock()
    mock_item.id = uuid.uuid4()
    mock_item.status = "pending"
    mock_item.type = "bid_approval"
    mock_item.title = "Test bid"
    mock_item.payload = {"thread_id": "t1"}
    mock_item.available_actions = ["approve", "reject"]
    mock_item.expires_at = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_item

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.flush = AsyncMock()

    mock_request = MagicMock()
    mock_request.user = MagicMock()
    mock_request.user.id = uuid.uuid4()
    mock_request.user.email = "test@test.com"

    mock_data = MagicMock()
    mock_data.action = "approve"
    mock_data.note = None
    mock_data.edited_payload = None

    mock_valkey = AsyncMock()
    mock_channels = AsyncMock()

    await HITLController.resolve.fn(
        controller,
        hitl_id=mock_item.id,
        data=mock_data,
        request=mock_request,
        db_session=mock_session,
        valkey=mock_valkey,
        channels=mock_channels,
    )

    # Verify the SELECT statement used with_for_update()
    call_args = mock_session.execute.call_args
    stmt = call_args[0][0]
    # SQLAlchemy compiled statement should include FOR UPDATE
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "FOR UPDATE" in compiled


@pytest.mark.asyncio
async def test_hitl_bulk_resolve_uses_for_update():
    """bulk_resolve() should use SELECT ... FOR UPDATE to prevent race conditions."""
    from src.api.routes.hitl import HITLController

    controller = HITLController(owner=MagicMock())

    item_id = uuid.uuid4()
    mock_item = MagicMock()
    mock_item.id = item_id
    mock_item.status = "pending"
    mock_item.type = "bid_approval"
    mock_item.title = "Test bid"
    mock_item.payload = {}
    mock_item.available_actions = ["approve", "reject"]
    mock_item.expires_at = None

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_item]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.flush = AsyncMock()

    mock_request = MagicMock()
    mock_request.user = MagicMock()
    mock_request.user.id = uuid.uuid4()
    mock_request.user.email = "test@test.com"

    mock_data = MagicMock()
    mock_data.ids = [item_id]
    mock_data.action = "approve"
    mock_data.note = None

    mock_valkey = AsyncMock()
    mock_channels = AsyncMock()

    await HITLController.bulk_resolve.fn(
        controller,
        data=mock_data,
        request=mock_request,
        db_session=mock_session,
        valkey=mock_valkey,
        channels=mock_channels,
    )

    call_args = mock_session.execute.call_args
    stmt = call_args[0][0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "FOR UPDATE" in compiled


# ---------------------------------------------------------------------------
# 2. Critic HITL escalation creates HITLQueue entry
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm_client():
    mock = MagicMock(spec=["call"])
    mock.call = AsyncMock()
    return mock


@pytest.fixture
def mock_heartbeat():
    mock = MagicMock()
    mock.ping = AsyncMock()
    return mock


@pytest.fixture
def mock_loop_detector():
    mock = MagicMock()
    mock.check = AsyncMock()
    return mock


@pytest.mark.asyncio
async def test_critic_creates_hitl_entry_on_revision_limit(
    mock_llm_client,
    mock_heartbeat,
    mock_loop_detector,
):
    """Critic should create an HITLQueue DB entry when revision limit is exhausted."""
    review_response = _make_review_response(verdict="revise", score=0.75)
    mock_llm_client.call = AsyncMock(
        return_value=(
            AIMessage(content=review_response),
            CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
        )
    )

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    artifacts = _make_dev_artifacts()
    artifacts["_critic_revision_count"] = [str(_MAX_REVISION_CYCLES)]
    state = _build_state(artifacts=artifacts)

    mock_session = MagicMock()

    with (
        patch.object(agent, "_log_review_decision", new_callable=AsyncMock),
        patch("src.agents.critic.get_db_session") as mock_get_session,
    ):
        mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await agent._execute(state)

    assert result["requires_hitl"] is True
    assert result["status"] == "paused"
    # session.add should have been called with an HITLQueue instance
    mock_session.add.assert_called()
    hitl_obj = mock_session.add.call_args[0][0]
    assert hitl_obj.type == "code_review"
    assert hitl_obj.priority == "urgent"
    assert "revision_limit_exceeded" in hitl_obj.description


@pytest.mark.asyncio
async def test_critic_creates_hitl_entry_on_scope_creep(
    mock_llm_client,
    mock_heartbeat,
    mock_loop_detector,
):
    """Critic should create an HITLQueue DB entry when scope creep is detected."""
    review_response = _make_review_response(
        verdict="revise",
        score=0.70,
        revision_type="scope_creep",
    )
    mock_llm_client.call = AsyncMock(
        return_value=(
            AIMessage(content=review_response),
            CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
        )
    )

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts=_make_dev_artifacts())
    mock_session = MagicMock()

    with (
        patch.object(agent, "_log_review_decision", new_callable=AsyncMock),
        patch("src.agents.critic.get_db_session") as mock_get_session,
    ):
        mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await agent._execute(state)

    assert result["requires_hitl"] is True
    mock_session.add.assert_called()
    hitl_obj = mock_session.add.call_args[0][0]
    assert hitl_obj.type == "code_review"
    assert "scope_creep" in hitl_obj.description


@pytest.mark.asyncio
async def test_critic_creates_hitl_entry_on_rejection(
    mock_llm_client,
    mock_heartbeat,
    mock_loop_detector,
):
    """Critic should create an HITLQueue DB entry when it rejects the work."""
    review_response = _make_review_response(verdict="reject", score=0.40)
    mock_llm_client.call = AsyncMock(
        return_value=(
            AIMessage(content=review_response),
            CallMetrics(agent_name="critic", model_id="gpt-4o", provider="openai"),
        )
    )

    agent = CriticAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state(artifacts=_make_dev_artifacts())
    mock_session = MagicMock()

    with (
        patch.object(agent, "_log_review_decision", new_callable=AsyncMock),
        patch("src.agents.critic.get_db_session") as mock_get_session,
    ):
        mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await agent._execute(state)

    assert result["requires_hitl"] is True
    mock_session.add.assert_called()
    hitl_obj = mock_session.add.call_args[0][0]
    assert hitl_obj.type == "code_review"
    assert "rejected" in hitl_obj.description


# ---------------------------------------------------------------------------
# 3. Node timeout
# ---------------------------------------------------------------------------


class _SlowAgent(ConstrainedAgent):
    """Test agent that sleeps beyond the timeout."""

    def __init__(self, sleep_time: float, **kwargs: Any):
        super().__init__(**kwargs)
        self._sleep_time = sleep_time

    async def _execute(self, state: AgentState) -> AgentState:
        await asyncio.sleep(self._sleep_time)
        return state


@pytest.mark.asyncio
async def test_agent_timeout_returns_failed():
    """Non-recoverable agent should return failed status when _execute exceeds timeout."""
    mock_llm = MagicMock(spec=["call"])
    mock_heartbeat = MagicMock()
    mock_heartbeat.ping = AsyncMock()
    mock_loop_detector = MagicMock()
    mock_loop_detector.check = AsyncMock()

    agent = _SlowAgent(
        sleep_time=2.0,  # Will sleep 2 seconds
        agent_name="scout",
        allowed_tools=[],
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state()

    # Temporarily reduce timeout for test speed
    with patch("src.agents.base._DEFAULT_NODE_TIMEOUT_SECONDS", 0.1):
        result = await agent.invoke(state)

    assert result["status"] == "failed"
    assert any("timed out" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_agent_no_timeout_on_fast_execution():
    """Agent should complete normally when _execute finishes within timeout."""
    mock_llm = MagicMock(spec=["call"])
    mock_heartbeat = MagicMock()
    mock_heartbeat.ping = AsyncMock()
    mock_loop_detector = MagicMock()
    mock_loop_detector.check = AsyncMock()

    class _FastAgent(ConstrainedAgent):
        async def _execute(self, state: AgentState) -> AgentState:
            from src.core.state import update_state

            return update_state(state, next_agent="packager", status="active")

    agent = _FastAgent(
        agent_name="dev",
        allowed_tools=[],
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _build_state()
    result = await agent.invoke(state)

    assert result["status"] != "failed"
    assert not any("timed out" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_timeout_constant_is_reasonable():
    """The default timeout should be between 1 and 30 minutes."""
    assert 60 <= _DEFAULT_NODE_TIMEOUT_SECONDS <= 1800
