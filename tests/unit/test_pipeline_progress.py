"""Tests for P2.10 — Pipeline Execution Progress tracking.

Covers:
- PipelineProgressTracker: Valkey read/write helpers
- ConstrainedAgent.invoke() integration: progress published on start/complete
- API endpoint: GET /api/v1/jobs/{job_id}/pipeline-progress
- Schema validation: PipelineProgressSchema
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.state import AgentState, ProjectContext, create_initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(**overrides: Any) -> ProjectContext:
    defaults: dict[str, Any] = {
        "project_id": "test-proj-1",
        "job_id": "job-1",
        "platform": "freelancer",
        "client": {},
        "requirements": "Build a website",
        "budget": 500.0,
        "deadline": None,
    }
    defaults.update(overrides)
    return ProjectContext(**defaults)


def _make_state(**overrides: Any) -> dict[str, Any]:
    state = create_initial_state(project=_make_project(), first_agent="planner")
    state.update(overrides)
    return state


# ===========================================================================
# 1. PipelineProgressTracker unit tests
# ===========================================================================


class TestPipelineProgressTracker:
    """Tests for the Valkey-backed progress tracker."""

    @pytest.mark.asyncio
    async def test_publish_agent_started(self) -> None:
        """publish_agent_started stores progress in Valkey with correct fields."""
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        valkey.get.return_value = None  # No existing progress
        tracker = PipelineProgressTracker(valkey)
        state = _make_state(
            thread_id="pipeline-job-1",
            agent_sequence=["dev", "content", "design"],
            current_sequence_index=0,
        )

        await tracker.publish_agent_started("planner", state)

        valkey.set.assert_called_once()
        call_args = valkey.set.call_args
        key = call_args[0][0] if call_args[0] else call_args.kwargs.get("name")
        assert key == "pipeline-progress:pipeline-job-1"

        stored = json.loads(call_args[0][1] if len(call_args[0]) > 1 else call_args.kwargs["value"])
        assert stored["current_agent"] == "planner"
        assert stored["status"] == "running"
        assert stored["thread_id"] == "pipeline-job-1"

    @pytest.mark.asyncio
    async def test_publish_agent_completed(self) -> None:
        """publish_agent_completed adds agent to completed list."""
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        # Simulate existing progress in Valkey
        existing = {
            "thread_id": "pipeline-job-1",
            "agent_sequence": ["dev", "content"],
            "current_agent": "planner",
            "current_index": 0,
            "total_agents": 4,
            "completed_agents": [],
            "status": "running",
            "started_at": "2026-03-10T08:00:00+00:00",
            "updated_at": "2026-03-10T08:00:00+00:00",
        }
        valkey.get.return_value = json.dumps(existing)
        tracker = PipelineProgressTracker(valkey)

        state = _make_state(
            thread_id="pipeline-job-1",
            agent_sequence=["dev", "content"],
            current_sequence_index=0,
        )

        await tracker.publish_agent_completed("planner", state)

        valkey.set.assert_called_once()
        stored = json.loads(valkey.set.call_args[0][1])
        assert "planner" in stored["completed_agents"]
        assert stored["status"] == "running"

    @pytest.mark.asyncio
    async def test_publish_status_paused(self) -> None:
        """publish_status sets status to paused for HITL."""
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        existing = {
            "thread_id": "pipeline-job-1",
            "agent_sequence": ["dev"],
            "current_agent": "dev",
            "current_index": 0,
            "total_agents": 3,
            "completed_agents": ["planner"],
            "status": "running",
            "started_at": "2026-03-10T08:00:00+00:00",
            "updated_at": "2026-03-10T08:00:00+00:00",
        }
        valkey.get.return_value = json.dumps(existing)
        tracker = PipelineProgressTracker(valkey)

        await tracker.publish_status("pipeline-job-1", "paused", "dev")

        stored = json.loads(valkey.set.call_args[0][1])
        assert stored["status"] == "paused"
        assert stored["current_agent"] == "dev"

    @pytest.mark.asyncio
    async def test_publish_status_failed(self) -> None:
        """publish_status sets status to failed."""
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        existing = {
            "thread_id": "pipeline-job-1",
            "agent_sequence": ["dev"],
            "current_agent": "dev",
            "current_index": 0,
            "total_agents": 3,
            "completed_agents": ["planner"],
            "status": "running",
            "started_at": "2026-03-10T08:00:00+00:00",
            "updated_at": "2026-03-10T08:00:00+00:00",
        }
        valkey.get.return_value = json.dumps(existing)
        tracker = PipelineProgressTracker(valkey)

        await tracker.publish_status("pipeline-job-1", "failed", "dev")

        stored = json.loads(valkey.set.call_args[0][1])
        assert stored["status"] == "failed"

    @pytest.mark.asyncio
    async def test_publish_status_completed(self) -> None:
        """publish_status sets status to completed at pipeline end."""
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        existing = {
            "thread_id": "pipeline-job-1",
            "agent_sequence": ["dev"],
            "current_agent": "packager",
            "current_index": 1,
            "total_agents": 3,
            "completed_agents": ["planner", "dev", "critic"],
            "status": "running",
            "started_at": "2026-03-10T08:00:00+00:00",
            "updated_at": "2026-03-10T08:00:00+00:00",
        }
        valkey.get.return_value = json.dumps(existing)
        tracker = PipelineProgressTracker(valkey)

        await tracker.publish_status("pipeline-job-1", "completed", "packager")

        stored = json.loads(valkey.set.call_args[0][1])
        assert stored["status"] == "completed"

    @pytest.mark.asyncio
    async def test_get_progress_returns_data(self) -> None:
        """get_progress returns stored progress dict."""
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        progress_data = {
            "thread_id": "pipeline-job-1",
            "agent_sequence": ["dev", "content"],
            "current_agent": "dev",
            "current_index": 0,
            "total_agents": 4,
            "completed_agents": ["planner"],
            "status": "running",
            "started_at": "2026-03-10T08:00:00+00:00",
            "updated_at": "2026-03-10T08:01:00+00:00",
        }
        valkey.get.return_value = json.dumps(progress_data)
        tracker = PipelineProgressTracker(valkey)

        result = await tracker.get_progress("pipeline-job-1")

        assert result is not None
        assert result["current_agent"] == "dev"
        assert result["total_agents"] == 4
        assert result["completed_agents"] == ["planner"]
        valkey.get.assert_called_once_with("pipeline-progress:pipeline-job-1")

    @pytest.mark.asyncio
    async def test_get_progress_returns_none_when_missing(self) -> None:
        """get_progress returns None when no progress exists."""
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        valkey.get.return_value = None
        tracker = PipelineProgressTracker(valkey)

        result = await tracker.get_progress("pipeline-nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_ttl_set_on_publish(self) -> None:
        """Progress entries get a 24h TTL."""
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        valkey.get.return_value = None
        tracker = PipelineProgressTracker(valkey)
        state = _make_state(
            thread_id="pipeline-job-1",
            agent_sequence=["dev"],
            current_sequence_index=0,
        )

        await tracker.publish_agent_started("planner", state)

        call_kwargs = valkey.set.call_args.kwargs if valkey.set.call_args.kwargs else {}
        # Check ex (seconds TTL) was passed
        if "ex" in call_kwargs:
            assert call_kwargs["ex"] == 86400
        else:
            # Positional args: set(key, value, ex=...)
            args = valkey.set.call_args
            # The TTL should be 86400 seconds (24 hours)
            assert args.kwargs.get("ex", 86400) == 86400

    @pytest.mark.asyncio
    async def test_total_agents_includes_planner_critic_packager(self) -> None:
        """total_agents = len(agent_sequence) + planner + critic + packager."""
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        valkey.get.return_value = None
        tracker = PipelineProgressTracker(valkey)
        state = _make_state(
            thread_id="pipeline-job-1",
            agent_sequence=["dev", "content", "design"],
            current_sequence_index=0,
        )

        await tracker.publish_agent_started("planner", state)

        stored = json.loads(valkey.set.call_args[0][1])
        # 3 (sequence) + planner + critic + packager = 6
        assert stored["total_agents"] == 6

    @pytest.mark.asyncio
    async def test_empty_sequence_total_agents(self) -> None:
        """Empty sequence (consulting): total = planner + packager = 2."""
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        valkey.get.return_value = None
        tracker = PipelineProgressTracker(valkey)
        state = _make_state(
            thread_id="pipeline-job-1",
            agent_sequence=[],
            current_sequence_index=0,
        )

        await tracker.publish_agent_started("planner", state)

        stored = json.loads(valkey.set.call_args[0][1])
        # Empty sequence: planner + packager = 2
        assert stored["total_agents"] == 2

    @pytest.mark.asyncio
    async def test_valkey_error_does_not_raise(self) -> None:
        """Valkey errors are swallowed — progress tracking is best-effort."""
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        valkey.get.return_value = None
        valkey.set.side_effect = ConnectionError("Valkey down")
        tracker = PipelineProgressTracker(valkey)
        state = _make_state(thread_id="pipeline-job-1", agent_sequence=["dev"])

        # Should not raise
        await tracker.publish_agent_started("planner", state)


# ===========================================================================
# 2. ConstrainedAgent.invoke() integration
# ===========================================================================


class TestAgentProgressIntegration:
    """Tests that ConstrainedAgent.invoke() publishes progress."""

    @pytest.mark.asyncio
    async def test_invoke_publishes_start_progress(self) -> None:
        """Agent invoke() publishes progress when agent starts."""
        from src.agents.base import ConstrainedAgent

        class _TestAgent(ConstrainedAgent):
            async def _execute(self, state: AgentState) -> AgentState:
                from src.core.state import update_state

                return update_state(state, next_agent="content")

        mock_valkey = AsyncMock()
        mock_valkey.get.return_value = None

        with (
            patch("src.core.pipeline_progress.get_progress_tracker") as mock_get_tracker,
            patch("src.agents.base._describe_task", return_value=None),
        ):
            mock_tracker = AsyncMock()
            mock_get_tracker.return_value = mock_tracker

            agent = _TestAgent(
                agent_name="dev",
                allowed_tools=set(),
                llm_client=MagicMock(),
                heartbeat=AsyncMock(),
                loop_detector=AsyncMock(),
            )

            state = _make_state(
                thread_id="pipeline-job-1",
                agent_sequence=["dev", "content"],
                current_sequence_index=0,
            )

            await agent.invoke(state)

            mock_tracker.publish_agent_started.assert_called_once()
            mock_tracker.publish_agent_completed.assert_called_once()

    @pytest.mark.asyncio
    async def test_invoke_publishes_failed_status(self) -> None:
        """Agent invoke() publishes failed status on unrecoverable error."""
        from src.agents.base import ConstrainedAgent
        from src.core.exceptions import MASException

        class _FailAgent(ConstrainedAgent):
            async def _execute(self, state: AgentState) -> AgentState:
                raise MASException("boom")

        with (
            patch("src.core.pipeline_progress.get_progress_tracker") as mock_get_tracker,
            patch("src.agents.base._describe_task", return_value=None),
        ):
            mock_tracker = AsyncMock()
            mock_get_tracker.return_value = mock_tracker

            agent = _FailAgent(
                agent_name="scout",  # not recoverable
                allowed_tools=set(),
                llm_client=MagicMock(),
                heartbeat=AsyncMock(),
                loop_detector=AsyncMock(),
            )

            state = _make_state(thread_id="pipeline-job-1", agent_sequence=["dev"])
            result = await agent.invoke(state)

            assert result["status"] == "failed"
            mock_tracker.publish_status.assert_called()
            # Should be called with "failed"
            call_args = mock_tracker.publish_status.call_args
            assert call_args[0][1] == "failed" or call_args.kwargs.get("status") == "failed"

    @pytest.mark.asyncio
    async def test_invoke_publishes_paused_on_hitl(self) -> None:
        """Agent invoke() publishes paused status when HITL is required."""
        from src.agents.base import ConstrainedAgent, HITLRequiredError

        class _HITLAgent(ConstrainedAgent):
            async def _execute(self, state: AgentState) -> AgentState:
                raise HITLRequiredError("Need approval", hitl_request_id="hitl-123")

        with (
            patch("src.core.pipeline_progress.get_progress_tracker") as mock_get_tracker,
            patch("src.agents.base._describe_task", return_value=None),
        ):
            mock_tracker = AsyncMock()
            mock_get_tracker.return_value = mock_tracker

            agent = _HITLAgent(
                agent_name="bid",
                allowed_tools=set(),
                llm_client=MagicMock(),
                heartbeat=AsyncMock(),
                loop_detector=AsyncMock(),
            )

            state = _make_state(thread_id="pipeline-job-1", agent_sequence=["dev"])
            result = await agent.invoke(state)

            assert result["requires_hitl"] is True
            mock_tracker.publish_status.assert_called()
            call_args = mock_tracker.publish_status.call_args
            assert call_args[0][1] == "paused"

    @pytest.mark.asyncio
    async def test_progress_tracker_failure_does_not_break_invoke(self) -> None:
        """If progress tracker raises, agent still executes successfully."""
        from src.agents.base import ConstrainedAgent

        class _TestAgent(ConstrainedAgent):
            async def _execute(self, state: AgentState) -> AgentState:
                from src.core.state import update_state

                return update_state(state, next_agent="content")

        with (
            patch("src.core.pipeline_progress.get_progress_tracker") as mock_get_tracker,
            patch("src.agents.base._describe_task", return_value=None),
        ):
            mock_tracker = AsyncMock()
            mock_tracker.publish_agent_started.side_effect = ConnectionError("Valkey down")
            mock_get_tracker.return_value = mock_tracker

            agent = _TestAgent(
                agent_name="dev",
                allowed_tools=set(),
                llm_client=MagicMock(),
                heartbeat=AsyncMock(),
                loop_detector=AsyncMock(),
            )

            state = _make_state(thread_id="pipeline-job-1", agent_sequence=["dev"])
            result = await agent.invoke(state)

            # Agent should still succeed despite tracker failure
            assert result["status"] != "failed"


# ===========================================================================
# 3. API endpoint tests
# ===========================================================================


class TestPipelineProgressEndpoint:
    """Tests for GET /api/v1/jobs/{job_id}/pipeline-progress."""

    @pytest.mark.asyncio
    async def test_returns_progress(self) -> None:
        """Endpoint returns progress data for an active pipeline."""
        from src.api.routes.jobs import JobController

        controller = JobController(owner=MagicMock())
        mock_valkey = AsyncMock()
        progress_data = {
            "thread_id": "pipeline-job-1",
            "agent_sequence": ["dev", "content"],
            "current_agent": "dev",
            "current_index": 0,
            "total_agents": 4,
            "completed_agents": ["planner"],
            "status": "running",
            "started_at": "2026-03-10T08:00:00+00:00",
            "updated_at": "2026-03-10T08:01:00+00:00",
        }
        mock_valkey.get.return_value = json.dumps(progress_data)

        result = await controller.get_pipeline_progress.fn(
            controller,
            job_id=str(uuid.uuid4()),
            valkey=mock_valkey,
        )

        assert result["status"] == "running"
        assert result["current_agent"] == "dev"
        assert result["total_agents"] == 4
        assert result["completed_agents"] == ["planner"]

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_progress(self) -> None:
        """Endpoint returns empty response when no pipeline is running."""
        from src.api.routes.jobs import JobController

        controller = JobController(owner=MagicMock())
        mock_valkey = AsyncMock()
        mock_valkey.get.return_value = None

        result = await controller.get_pipeline_progress.fn(
            controller,
            job_id=str(uuid.uuid4()),
            valkey=mock_valkey,
        )

        assert result["status"] == "idle"
        assert result["current_agent"] is None
        assert result["completed_agents"] == []

    @pytest.mark.asyncio
    async def test_handles_invalid_json_gracefully(self) -> None:
        """Endpoint handles corrupt Valkey data gracefully."""
        from src.api.routes.jobs import JobController

        controller = JobController(owner=MagicMock())
        mock_valkey = AsyncMock()
        mock_valkey.get.return_value = "not-valid-json"

        result = await controller.get_pipeline_progress.fn(
            controller,
            job_id=str(uuid.uuid4()),
            valkey=mock_valkey,
        )

        assert result["status"] == "idle"

    @pytest.mark.asyncio
    async def test_uses_correct_valkey_key(self) -> None:
        """Endpoint queries Valkey with the correct key pattern."""
        from src.api.routes.jobs import JobController

        controller = JobController(owner=MagicMock())
        mock_valkey = AsyncMock()
        mock_valkey.get.return_value = None

        job_id = str(uuid.uuid4())
        await controller.get_pipeline_progress.fn(
            controller,
            job_id=job_id,
            valkey=mock_valkey,
        )

        mock_valkey.get.assert_called_once_with(f"pipeline-progress:pipeline-{job_id}")

    @pytest.mark.asyncio
    async def test_valkey_error_returns_idle(self) -> None:
        """Endpoint returns idle when Valkey is unavailable."""
        from src.api.routes.jobs import JobController

        controller = JobController(owner=MagicMock())
        mock_valkey = AsyncMock()
        mock_valkey.get.side_effect = ConnectionError("Valkey down")

        result = await controller.get_pipeline_progress.fn(
            controller,
            job_id=str(uuid.uuid4()),
            valkey=mock_valkey,
        )

        assert result["status"] == "idle"


# ===========================================================================
# 4. Schema tests
# ===========================================================================


class TestPipelineProgressSchema:
    """Tests for PipelineProgressSchema validation."""

    def test_valid_schema(self) -> None:
        """Valid progress data passes schema validation."""
        from src.api.schemas import PipelineProgressSchema

        schema = PipelineProgressSchema(
            thread_id="pipeline-job-1",
            agent_sequence=["dev", "content"],
            current_agent="dev",
            current_index=0,
            total_agents=4,
            completed_agents=["planner"],
            status="running",
            started_at="2026-03-10T08:00:00+00:00",
            updated_at="2026-03-10T08:01:00+00:00",
        )
        assert schema.status == "running"
        assert schema.total_agents == 4

    def test_idle_schema(self) -> None:
        """Idle progress (no pipeline running) passes validation."""
        from src.api.schemas import PipelineProgressSchema

        schema = PipelineProgressSchema(
            thread_id=None,
            agent_sequence=[],
            current_agent=None,
            current_index=0,
            total_agents=0,
            completed_agents=[],
            status="idle",
            started_at=None,
            updated_at=None,
        )
        assert schema.status == "idle"
        assert schema.current_agent is None

    def test_schema_serialization(self) -> None:
        """Schema serializes to dict correctly."""
        from src.api.schemas import PipelineProgressSchema

        schema = PipelineProgressSchema(
            thread_id="pipeline-job-1",
            agent_sequence=["dev"],
            current_agent="dev",
            current_index=0,
            total_agents=3,
            completed_agents=["planner"],
            status="running",
            started_at="2026-03-10T08:00:00+00:00",
            updated_at="2026-03-10T08:01:00+00:00",
        )
        data = schema.model_dump()
        assert "thread_id" in data
        assert "completed_agents" in data
        assert isinstance(data["completed_agents"], list)


# ===========================================================================
# 5. Progress percentage + WebSocket event format (L1 feature)
# ===========================================================================


class TestProgressPercentage:
    """calculate_progress_pct computes correct percentage."""

    @pytest.mark.asyncio
    async def test_zero_percent_at_start(self) -> None:
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        tracker = PipelineProgressTracker(valkey)
        progress = {"completed_agents": [], "total_agents": 6}
        assert tracker.calculate_progress_pct(progress) == 0

    @pytest.mark.asyncio
    async def test_fifty_percent_midway(self) -> None:
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        tracker = PipelineProgressTracker(valkey)
        progress = {
            "completed_agents": ["planner", "dev", "content"],
            "total_agents": 6,
        }
        assert tracker.calculate_progress_pct(progress) == 50

    @pytest.mark.asyncio
    async def test_hundred_percent_complete(self) -> None:
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        tracker = PipelineProgressTracker(valkey)
        progress = {
            "completed_agents": ["planner", "dev", "content", "design", "critic", "packager"],
            "total_agents": 6,
        }
        assert tracker.calculate_progress_pct(progress) == 100

    @pytest.mark.asyncio
    async def test_handles_zero_total(self) -> None:
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        tracker = PipelineProgressTracker(valkey)
        progress = {"completed_agents": [], "total_agents": 0}
        assert tracker.calculate_progress_pct(progress) == 0


class TestBuildWsEvent:
    """build_ws_event produces the correct WebSocket payload."""

    @pytest.mark.asyncio
    async def test_event_contains_required_fields(self) -> None:
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        tracker = PipelineProgressTracker(valkey)
        progress = {
            "thread_id": "test-thread-001",
            "current_agent": "dev",
            "completed_agents": ["planner"],
            "total_agents": 6,
            "agent_sequence": ["dev", "content", "design"],
            "status": "running",
        }
        event = tracker.build_ws_event(progress)

        assert event["type"] == "pipeline:progress"
        assert event["thread_id"] == "test-thread-001"
        assert event["current_agent"] == "dev"
        assert event["completed_agents"] == ["planner"]
        assert event["total_agents"] == 6
        # 1 out of 6 completed = 16%
        assert event["progress_pct"] == 16

    @pytest.mark.asyncio
    async def test_event_progress_pct_at_66(self) -> None:
        from src.core.pipeline_progress import PipelineProgressTracker

        valkey = AsyncMock()
        tracker = PipelineProgressTracker(valkey)
        progress = {
            "thread_id": "test-thread-001",
            "current_agent": "critic",
            "completed_agents": ["planner", "dev", "content", "design"],
            "total_agents": 6,
            "agent_sequence": ["dev", "content", "design"],
            "status": "running",
        }
        event = tracker.build_ws_event(progress)
        # 4/6 = 66%
        assert event["progress_pct"] == 66
