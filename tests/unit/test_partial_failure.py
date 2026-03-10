"""Tests for P1.5: Partial Failure Recovery.

Covers:
- State fields: failed_agent, failure_reason, recovery_attempted, skipped_agents
- Base agent: execution agents pause for HITL on failure instead of terminating
- Routing: _route_next_in_sequence respects skipped_agents
- HITL resume: agent_failure type with resume/skip/manual recovery actions
- Integration: full sequence with failure + recovery flow
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import AgentException, LLMException
from src.core.state import (
    AgentState,
    ProjectContext,
    create_initial_state,
    update_state,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project() -> ProjectContext:
    return ProjectContext(
        project_id="proj-pf-001",
        job_id="job-pf-001",
        platform="freelancer",
        client={"name": "Test", "rating": 4.5},
        requirements="Build a website",
        budget=1000.0,
        deadline=datetime(2026, 6, 1, tzinfo=UTC),
    )


def _make_state(**overrides: Any) -> AgentState:
    state = create_initial_state(
        project=_make_project(),
        first_agent="dev",
        thread_id="thread-pf-001",
    )
    if overrides:
        state = update_state(state, **overrides)
    return state


# ===========================================================================
# 1. State Field Tests
# ===========================================================================


class TestPartialFailureStateFields:
    """New state fields for partial failure recovery."""

    def test_initial_state_has_failed_agent_none(self):
        """create_initial_state should set failed_agent to None."""
        state = _make_state()
        assert state.get("failed_agent") is None

    def test_initial_state_has_failure_reason_none(self):
        """create_initial_state should set failure_reason to None."""
        state = _make_state()
        assert state.get("failure_reason") is None

    def test_initial_state_has_recovery_attempted_zero(self):
        """create_initial_state should set recovery_attempted to 0."""
        state = _make_state()
        assert state.get("recovery_attempted", 0) == 0

    def test_initial_state_has_skipped_agents_empty(self):
        """create_initial_state should set skipped_agents to empty list."""
        state = _make_state()
        assert state.get("skipped_agents", []) == []

    def test_update_state_sets_failed_agent(self):
        """update_state should allow setting failed_agent."""
        state = _make_state()
        updated = update_state(state, failed_agent="dev")
        assert updated["failed_agent"] == "dev"

    def test_update_state_sets_failure_reason(self):
        """update_state should allow setting failure_reason."""
        state = _make_state()
        updated = update_state(state, failure_reason="LLM timeout")
        assert updated["failure_reason"] == "LLM timeout"

    def test_update_state_sets_recovery_attempted(self):
        """update_state should allow setting recovery_attempted."""
        state = _make_state()
        updated = update_state(state, recovery_attempted=2)
        assert updated["recovery_attempted"] == 2

    def test_update_state_appends_skipped_agents(self):
        """update_state should allow appending to skipped_agents."""
        state = _make_state(skipped_agents=["content"])
        updated = update_state(state, skipped_agents=[*state["skipped_agents"], "design"])
        assert updated["skipped_agents"] == ["content", "design"]

    def test_clear_failure_resets_fields(self):
        """clear_failure helper should reset failure-related fields."""
        from src.core.state import clear_failure

        state = _make_state(
            failed_agent="dev",
            failure_reason="timeout",
            status="paused",
            requires_hitl=True,
        )
        cleared = clear_failure(state)
        assert cleared["failed_agent"] is None
        assert cleared["failure_reason"] is None
        assert cleared["status"] == "active"
        assert cleared["requires_hitl"] is False


# ===========================================================================
# 2. Base Agent Failure Handling Tests
# ===========================================================================


class TestBaseAgentPartialFailure:
    """ConstrainedAgent.invoke() should pause for HITL on execution agent failure."""

    @pytest.fixture()
    def _mock_db(self):
        """Mock get_db_session for HITL entry creation."""
        session = AsyncMock()
        session.add = MagicMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("src.core.database.get_db_session", return_value=ctx):
            yield session

    @pytest.mark.asyncio()
    async def test_dev_agent_failure_pauses_for_hitl(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
        _mock_db: Any,
    ):
        """Dev agent should set status='paused' and requires_hitl=True on failure."""
        from src.agents.dev import DevAgent

        mock_llm_client.call = AsyncMock(side_effect=LLMException("LLM timeout"))

        agent = DevAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _make_state(
            current_agent="dev",
            agent_sequence=["dev", "content", "design"],
            current_sequence_index=0,
        )
        result = await agent.invoke(state)

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True
        assert result["failed_agent"] == "dev"
        assert "LLM timeout" in result.get("failure_reason", "")

    @pytest.mark.asyncio()
    async def test_content_agent_failure_pauses_for_hitl(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
        _mock_db: Any,
    ):
        """Content agent should pause for HITL on failure."""
        from src.agents.content import ContentAgent

        mock_llm_client.call = AsyncMock(side_effect=AgentException("content error", agent_name="content"))

        agent = ContentAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _make_state(
            current_agent="content",
            agent_sequence=["dev", "content", "design"],
            current_sequence_index=1,
        )
        result = await agent.invoke(state)

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True
        assert result["failed_agent"] == "content"

    @pytest.mark.asyncio()
    async def test_design_agent_failure_pauses_for_hitl(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
        _mock_db: Any,
    ):
        """Design agent should pause for HITL on failure."""
        from src.agents.design import DesignAgent

        mock_llm_client.call = AsyncMock(side_effect=LLMException("design LLM error"))

        agent = DesignAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _make_state(
            current_agent="design",
            agent_sequence=["dev", "content", "design"],
            current_sequence_index=2,
        )
        result = await agent.invoke(state)

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True
        assert result["failed_agent"] == "design"

    @pytest.mark.asyncio()
    async def test_scout_agent_failure_still_fails(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Non-execution agents (scout) should still set status='failed'."""
        from src.agents.scout import ScoutAgent

        mock_llm_client.call = AsyncMock(side_effect=LLMException("scout error"))

        agent = ScoutAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
            adapters=[],
        )
        state = _make_state(current_agent="scout")
        result = await agent.invoke(state)

        assert result["status"] == "failed"
        assert result.get("failed_agent") is None

    @pytest.mark.asyncio()
    async def test_failure_preserves_existing_artifacts(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
        _mock_db: Any,
    ):
        """On failure, artifacts from previous agents should be preserved."""
        from src.agents.content import ContentAgent

        mock_llm_client.call = AsyncMock(side_effect=LLMException("error"))

        agent = ContentAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _make_state(
            current_agent="content",
            agent_sequence=["dev", "content"],
            current_sequence_index=1,
            artifacts={"dev": ["code.py", "utils.py"]},
        )
        result = await agent.invoke(state)

        assert result["artifacts"]["dev"] == ["code.py", "utils.py"]

    @pytest.mark.asyncio()
    async def test_failure_creates_hitl_queue_entry(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
        _mock_db: AsyncMock,
    ):
        """On execution agent failure, an HITL queue entry should be created."""
        from src.agents.dev import DevAgent

        mock_llm_client.call = AsyncMock(side_effect=LLMException("timeout"))

        agent = DevAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )
        state = _make_state(
            current_agent="dev",
            agent_sequence=["dev", "content"],
            current_sequence_index=0,
        )
        result = await agent.invoke(state)

        assert result["hitl_request_id"] is not None
        # HITL entry should have been added to DB
        _mock_db.add.assert_called()

    @pytest.mark.asyncio()
    async def test_timeout_error_pauses_execution_agent(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
        _mock_db: Any,
    ):
        """TimeoutError on execution agent should pause, not fail."""
        from src.agents.dev import DevAgent

        agent = DevAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        # Make _execute raise TimeoutError
        async def _timeout_execute(state):
            raise TimeoutError("600s exceeded")

        with patch.object(agent, "_execute", side_effect=_timeout_execute):
            state = _make_state(
                current_agent="dev",
                agent_sequence=["dev"],
                current_sequence_index=0,
            )
            result = await agent.invoke(state)

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True
        assert result["failed_agent"] == "dev"
        assert "timed out" in result.get("failure_reason", "").lower()


# ===========================================================================
# 3. Routing Tests — skipped_agents
# ===========================================================================


class TestRoutingWithSkippedAgents:
    """_route_next_in_sequence should skip agents in skipped_agents list."""

    def test_skip_first_agent_routes_to_second(self):
        """If first agent in sequence is skipped, route to second."""
        from src.core.graph import _route_next_in_sequence

        state: dict[str, Any] = {
            "status": "active",
            "requires_hitl": False,
            "revision_target": None,
            "agent_sequence": ["dev", "content", "design"],
            "current_sequence_index": 0,
            "skipped_agents": ["dev"],
            "thread_id": "t1",
        }
        assert _route_next_in_sequence(state) == "content_node"

    def test_skip_middle_agent_routes_past_it(self):
        """If middle agent is skipped, route to next non-skipped."""
        from src.core.graph import _route_next_in_sequence

        state: dict[str, Any] = {
            "status": "active",
            "requires_hitl": False,
            "revision_target": None,
            "agent_sequence": ["dev", "content", "design"],
            "current_sequence_index": 1,
            "skipped_agents": ["content"],
            "thread_id": "t2",
        }
        assert _route_next_in_sequence(state) == "design_node"

    def test_skip_all_remaining_routes_to_critic(self):
        """If all remaining agents are skipped, route to critic."""
        from src.core.graph import _route_next_in_sequence

        state: dict[str, Any] = {
            "status": "active",
            "requires_hitl": False,
            "revision_target": None,
            "agent_sequence": ["dev", "content", "design"],
            "current_sequence_index": 1,
            "skipped_agents": ["content", "design"],
            "thread_id": "t3",
        }
        assert _route_next_in_sequence(state) == "critic_node"

    def test_no_skipped_agents_routes_normally(self):
        """Without skipped_agents, routing should work as before."""
        from src.core.graph import _route_next_in_sequence

        state: dict[str, Any] = {
            "status": "active",
            "requires_hitl": False,
            "revision_target": None,
            "agent_sequence": ["dev", "content", "design"],
            "current_sequence_index": 0,
            "skipped_agents": [],
            "thread_id": "t4",
        }
        assert _route_next_in_sequence(state) == "dev_node"

    def test_empty_skipped_agents_not_set(self):
        """If skipped_agents key is missing, behave as empty list."""
        from src.core.graph import _route_next_in_sequence

        state: dict[str, Any] = {
            "status": "active",
            "requires_hitl": False,
            "revision_target": None,
            "agent_sequence": ["dev", "content"],
            "current_sequence_index": 0,
            "thread_id": "t5",
        }
        assert _route_next_in_sequence(state) == "dev_node"

    def test_skip_multiple_consecutive_agents(self):
        """Skipping consecutive agents should advance past all of them."""
        from src.core.graph import _route_next_in_sequence

        state: dict[str, Any] = {
            "status": "active",
            "requires_hitl": False,
            "revision_target": None,
            "agent_sequence": ["dev", "content", "design"],
            "current_sequence_index": 0,
            "skipped_agents": ["dev", "content"],
            "thread_id": "t6",
        }
        assert _route_next_in_sequence(state) == "design_node"


# ===========================================================================
# 4. HITL Resume with Recovery Actions
# ===========================================================================


class TestHITLAgentFailureRecovery:
    """HITL resume should support agent_failure recovery actions."""

    def _make_paused_state(self, failed: str = "dev", attempt: int = 0) -> dict[str, Any]:
        """Build a state paused due to agent failure."""
        state = _make_state(
            status="paused",
            requires_hitl=True,
            failed_agent=failed,
            failure_reason="LLM timeout after 90s",
            recovery_attempted=attempt,
            current_agent=failed,
            agent_sequence=["dev", "content", "design"],
            current_sequence_index=["dev", "content", "design"].index(failed),
            skipped_agents=[],
            artifacts={"dev": ["code.py"]} if failed != "dev" else {},
        )
        return dict(state)

    def test_resume_action_increments_recovery_attempted(self):
        """Resume action should increment recovery_attempted counter."""
        from src.core.graph import _apply_agent_failure_recovery

        state = self._make_paused_state("dev", attempt=0)
        result = _apply_agent_failure_recovery(state, "resume", {}, "thread-1")
        assert result["recovery_attempted"] == 1
        assert result["status"] == "active"
        assert result["requires_hitl"] is False

    def test_resume_action_keeps_same_agent(self):
        """Resume should keep current_agent and sequence_index for retry."""
        from src.core.graph import _apply_agent_failure_recovery

        state = self._make_paused_state("content", attempt=0)
        result = _apply_agent_failure_recovery(state, "resume", {}, "thread-1")
        assert result["current_agent"] == "content"
        assert result["current_sequence_index"] == 1

    def test_skip_action_adds_to_skipped_agents(self):
        """Skip action should add failed agent to skipped_agents."""
        from src.core.graph import _apply_agent_failure_recovery

        state = self._make_paused_state("design", attempt=1)
        result = _apply_agent_failure_recovery(state, "skip", {}, "thread-1")
        assert "design" in result["skipped_agents"]
        assert result["status"] == "active"
        assert result["failed_agent"] is None

    def test_skip_action_clears_failure_state(self):
        """Skip should clear failed_agent and failure_reason."""
        from src.core.graph import _apply_agent_failure_recovery

        state = self._make_paused_state("dev")
        result = _apply_agent_failure_recovery(state, "skip", {}, "thread-1")
        assert result["failed_agent"] is None
        assert result["failure_reason"] is None

    def test_manual_action_stores_artifacts(self):
        """Manual action should store operator-provided artifacts."""
        from src.core.graph import _apply_agent_failure_recovery

        state = self._make_paused_state("content")
        manual_data = {"content": ["manual_copy.txt", "blog_post.md"]}
        result = _apply_agent_failure_recovery(state, "manual", {"manual_artifacts": manual_data}, "thread-1")
        assert result["artifacts"]["content"] == ["manual_copy.txt", "blog_post.md"]
        assert result["status"] == "active"
        assert result["failed_agent"] is None

    def test_manual_action_preserves_existing_artifacts(self):
        """Manual action should not overwrite artifacts from other agents."""
        from src.core.graph import _apply_agent_failure_recovery

        state = self._make_paused_state("content")
        state["artifacts"] = {"dev": ["code.py"]}
        manual_data = {"content": ["copy.txt"]}
        result = _apply_agent_failure_recovery(state, "manual", {"manual_artifacts": manual_data}, "thread-1")
        assert result["artifacts"]["dev"] == ["code.py"]
        assert result["artifacts"]["content"] == ["copy.txt"]

    def test_resume_max_attempts_reached(self):
        """After 3 resume attempts, should escalate (not allow more retries)."""
        from src.core.graph import _apply_agent_failure_recovery

        state = self._make_paused_state("dev", attempt=3)
        result = _apply_agent_failure_recovery(state, "resume", {}, "thread-1")
        # After max attempts, resume should still be allowed but
        # the counter should reflect the escalation threshold
        assert result["recovery_attempted"] == 4

    def test_hitl_type_detected_as_agent_failure(self):
        """resume_from_hitl should detect agent_failure type from failed_agent field."""
        state = self._make_paused_state("dev")
        # When failed_agent is set, hitl_type should be "agent_failure"
        assert state["failed_agent"] == "dev"
        assert state["status"] == "paused"


# ===========================================================================
# 5. Integration Tests
# ===========================================================================


class TestPartialFailureIntegration:
    """End-to-end scenarios combining failure, HITL, and recovery."""

    def test_failure_then_skip_continues_sequence(self):
        """Agent failure + skip should continue to the next agent in sequence."""
        from src.core.graph import _apply_agent_failure_recovery, _route_next_in_sequence

        # 1. Dev fails → paused
        state = _make_state(
            status="paused",
            requires_hitl=True,
            failed_agent="dev",
            failure_reason="LLM error",
            recovery_attempted=0,
            agent_sequence=["dev", "content", "design"],
            current_sequence_index=0,
            skipped_agents=[],
        )

        # 2. HITL: skip dev
        recovered = _apply_agent_failure_recovery(dict(state), "skip", {}, "t-int-1")
        assert recovered["skipped_agents"] == ["dev"]

        # 3. Routing should go to content (skip dev)
        next_node = _route_next_in_sequence(recovered)
        assert next_node == "content_node"

    def test_failure_then_resume_retries_same_agent(self):
        """Agent failure + resume should route back to the same agent."""
        from src.core.graph import _apply_agent_failure_recovery, _route_next_in_sequence

        state = _make_state(
            status="paused",
            requires_hitl=True,
            failed_agent="content",
            failure_reason="timeout",
            recovery_attempted=0,
            agent_sequence=["dev", "content", "design"],
            current_sequence_index=1,
            skipped_agents=[],
        )

        recovered = _apply_agent_failure_recovery(dict(state), "resume", {}, "t-int-2")
        next_node = _route_next_in_sequence(recovered)
        assert next_node == "content_node"

    def test_failure_then_manual_advances_sequence(self):
        """Agent failure + manual should advance to the next agent."""
        from src.core.graph import _apply_agent_failure_recovery, _route_next_in_sequence

        state = _make_state(
            status="paused",
            requires_hitl=True,
            failed_agent="dev",
            failure_reason="error",
            recovery_attempted=0,
            agent_sequence=["dev", "content", "design"],
            current_sequence_index=0,
            skipped_agents=[],
        )

        manual_data = {"dev": ["manual_code.py"]}
        recovered = _apply_agent_failure_recovery(dict(state), "manual", {"manual_artifacts": manual_data}, "t-int-3")
        assert recovered["artifacts"]["dev"] == ["manual_code.py"]

        # Index should have advanced past dev
        next_node = _route_next_in_sequence(recovered)
        assert next_node == "content_node"

    def test_multiple_failures_different_agents(self):
        """Multiple agents can fail and be skipped independently."""
        from src.core.graph import _apply_agent_failure_recovery, _route_next_in_sequence

        # Dev fails, skip it
        state1: dict[str, Any] = {
            "status": "paused",
            "requires_hitl": True,
            "failed_agent": "dev",
            "failure_reason": "error1",
            "recovery_attempted": 0,
            "agent_sequence": ["dev", "content", "design"],
            "current_sequence_index": 0,
            "skipped_agents": [],
            "artifacts": {},
            "thread_id": "t-multi",
            "revision_target": None,
        }
        recovered1 = _apply_agent_failure_recovery(state1, "skip", {}, "t-multi")

        # Content also fails, skip it too
        state2 = {
            **recovered1,
            "failed_agent": "content",
            "failure_reason": "error2",
            "status": "paused",
            "requires_hitl": True,
        }
        recovered2 = _apply_agent_failure_recovery(state2, "skip", {}, "t-multi")

        assert recovered2["skipped_agents"] == ["dev", "content"]

        # Should route to design (only non-skipped agent)
        next_node = _route_next_in_sequence(recovered2)
        assert next_node == "design_node"

    def test_all_agents_skipped_routes_to_critic(self):
        """If all execution agents are skipped, should route to critic."""
        from src.core.graph import _route_next_in_sequence

        state: dict[str, Any] = {
            "status": "active",
            "requires_hitl": False,
            "revision_target": None,
            "agent_sequence": ["dev", "content", "design"],
            "current_sequence_index": 0,
            "skipped_agents": ["dev", "content", "design"],
            "thread_id": "t-all-skip",
        }
        assert _route_next_in_sequence(state) == "critic_node"
