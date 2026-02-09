"""Unit tests for the Agents API routes.

Tests the AgentController endpoints at ``/api/v1/agents``:
- GET /api/v1/agents/status — agent fleet status overview
- GET /api/v1/agents/{name}/logs — agent log history
- POST /api/v1/agents/{name}/restart — force restart agent
- POST /api/v1/agents/{name}/pause — pause agent
- POST /api/v1/agents/{name}/resume — resume agent

We test route handlers directly via ``.fn()`` to avoid needing a full app instance.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar.exceptions import NotFoundException

from src.api.routes.agents import (
    _HEARTBEAT_DEAD_THRESHOLD,
    AgentController,
    _compute_system_health,
    _get_heartbeat_or_404,
    _infer_level,
)
from src.api.schemas import AgentStatusSchema

# ---------------------------------------------------------------------------
# Mock Factories
# ---------------------------------------------------------------------------


def _create_mock_heartbeat(
    agent_name: str = "scout",
    status: str = "idle",
    last_heartbeat: datetime | None = None,
) -> MagicMock:
    """Create a mock AgentHeartbeat ORM instance."""
    mock_heartbeat = MagicMock()
    mock_heartbeat.agent_name = agent_name
    mock_heartbeat.display_name = f"{agent_name.title()} Agent"
    mock_heartbeat.pipeline = "A"
    mock_heartbeat.status = status
    mock_heartbeat.last_heartbeat = last_heartbeat or datetime.now(UTC)
    mock_heartbeat.current_task = None
    mock_heartbeat.restart_count = 0
    mock_heartbeat.error_message = None
    return mock_heartbeat


def _create_mock_log(
    agent_name: str = "scout",
    event_type: str = "info",
    message: str = "Scanning jobs",
) -> MagicMock:
    """Create a mock AgentLog ORM instance."""
    mock_log = MagicMock()
    mock_log.id = uuid.uuid4()
    mock_log.agent_name = agent_name
    mock_log.event_type = event_type
    mock_log.message = message
    mock_log.details = {}
    mock_log.created_at = datetime.now(UTC)
    return mock_log


def _create_mock_db_session() -> AsyncMock:
    """Create a mock AsyncSession for database testing."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.flush = AsyncMock()
    return session


def _create_mock_valkey() -> AsyncMock:
    """Create a mock Valkey/Redis client."""
    valkey = AsyncMock()
    valkey.publish = AsyncMock(return_value=1)
    return valkey


def _create_mock_request(user_id: uuid.UUID | None = None) -> MagicMock:
    """Create a mock Request with authenticated user."""
    request = MagicMock()
    request.user = MagicMock()
    request.user.id = user_id or uuid.uuid4()
    return request


# ---------------------------------------------------------------------------
# Tests: _compute_system_health
# ---------------------------------------------------------------------------


class TestComputeSystemHealth:
    """Tests for the _compute_system_health helper function."""

    def test_empty_agents_list_returns_critical(self) -> None:
        """Should return 'critical' when no agents are present."""
        health = _compute_system_health([])
        assert health == "critical"

    def test_all_healthy_returns_healthy(self) -> None:
        """Should return 'healthy' when all agents are idle or active."""
        agents = [
            AgentStatusSchema(
                name="scout",
                display_name="Scout Agent",
                pipeline="A",
                status="idle",
                last_heartbeat=datetime.now(UTC),
                current_task=None,
                restart_count=0,
                error_message=None,
            ),
            AgentStatusSchema(
                name="bid",
                display_name="Bid Agent",
                pipeline="A",
                status="active",
                last_heartbeat=datetime.now(UTC),
                current_task="generating bid",
                restart_count=0,
                error_message=None,
            ),
        ]
        health = _compute_system_health(agents)
        assert health == "healthy"

    def test_some_dead_returns_degraded(self) -> None:
        """Should return 'degraded' when some agents are dead but others are healthy."""
        agents = [
            AgentStatusSchema(
                name="scout",
                display_name="Scout Agent",
                pipeline="A",
                status="idle",
                last_heartbeat=datetime.now(UTC),
                current_task=None,
                restart_count=0,
                error_message=None,
            ),
            AgentStatusSchema(
                name="bid",
                display_name="Bid Agent",
                pipeline="A",
                status="dead",
                last_heartbeat=datetime.now(UTC) - timedelta(seconds=300),
                current_task=None,
                restart_count=0,
                error_message=None,
            ),
        ]
        health = _compute_system_health(agents)
        assert health == "degraded"

    def test_all_dead_returns_critical(self) -> None:
        """Should return 'critical' when all agents are dead."""
        agents = [
            AgentStatusSchema(
                name="scout",
                display_name="Scout Agent",
                pipeline="A",
                status="dead",
                last_heartbeat=datetime.now(UTC) - timedelta(seconds=300),
                current_task=None,
                restart_count=0,
                error_message=None,
            ),
            AgentStatusSchema(
                name="bid",
                display_name="Bid Agent",
                pipeline="A",
                status="dead",
                last_heartbeat=datetime.now(UTC) - timedelta(seconds=300),
                current_task=None,
                restart_count=0,
                error_message=None,
            ),
        ]
        health = _compute_system_health(agents)
        assert health == "critical"

    def test_mix_of_error_and_healthy_returns_degraded(self) -> None:
        """Should return 'degraded' when some agents have errors but others are healthy."""
        agents = [
            AgentStatusSchema(
                name="scout",
                display_name="Scout Agent",
                pipeline="A",
                status="idle",
                last_heartbeat=datetime.now(UTC),
                current_task=None,
                restart_count=0,
                error_message=None,
            ),
            AgentStatusSchema(
                name="bid",
                display_name="Bid Agent",
                pipeline="A",
                status="error",
                last_heartbeat=datetime.now(UTC),
                current_task=None,
                restart_count=0,
                error_message="LLM timeout",
            ),
        ]
        health = _compute_system_health(agents)
        assert health == "degraded"

    def test_all_error_returns_critical(self) -> None:
        """Should return 'critical' when all agents are in error state."""
        agents = [
            AgentStatusSchema(
                name="scout",
                display_name="Scout Agent",
                pipeline="A",
                status="error",
                last_heartbeat=datetime.now(UTC),
                current_task=None,
                restart_count=0,
                error_message="API error",
            ),
            AgentStatusSchema(
                name="bid",
                display_name="Bid Agent",
                pipeline="A",
                status="error",
                last_heartbeat=datetime.now(UTC),
                current_task=None,
                restart_count=0,
                error_message="Database error",
            ),
        ]
        health = _compute_system_health(agents)
        assert health == "critical"


# ---------------------------------------------------------------------------
# Tests: _infer_level
# ---------------------------------------------------------------------------


class TestInferLevel:
    """Tests for the _infer_level helper function."""

    def test_event_type_error_returns_error(self) -> None:
        """Should return 'error' when event_type is 'error'."""
        log = _create_mock_log(event_type="error")
        level = _infer_level(log)
        assert level == "error"

    def test_event_type_warning_returns_warning(self) -> None:
        """Should return 'warning' when event_type is 'warning'."""
        log = _create_mock_log(event_type="warning")
        level = _infer_level(log)
        assert level == "warning"

    def test_event_type_info_returns_info(self) -> None:
        """Should return 'info' when event_type is 'info'."""
        log = _create_mock_log(event_type="info")
        level = _infer_level(log)
        assert level == "info"

    def test_event_type_with_fail_keyword_returns_error(self) -> None:
        """Should return 'error' when event_type contains 'fail'."""
        log = _create_mock_log(event_type="task_failed")
        level = _infer_level(log)
        assert level == "error"

    def test_event_type_with_warn_keyword_returns_warning(self) -> None:
        """Should return 'warning' when event_type contains 'warn'."""
        log = _create_mock_log(event_type="connection_warning")
        level = _infer_level(log)
        assert level == "warning"

    def test_unknown_event_type_returns_info(self) -> None:
        """Should return 'info' for unknown event types."""
        log = _create_mock_log(event_type="custom_event")
        level = _infer_level(log)
        assert level == "info"


# ---------------------------------------------------------------------------
# Tests: status
# ---------------------------------------------------------------------------


class TestStatus:
    """Tests for the GET /api/v1/agents/status endpoint."""

    @pytest.mark.asyncio
    async def test_returns_agents_with_computed_health(self) -> None:
        """Should return all agents with system health computed."""
        controller = AgentController(owner=MagicMock())
        db_session = _create_mock_db_session()

        heartbeat1 = _create_mock_heartbeat(agent_name="scout", status="idle")
        heartbeat2 = _create_mock_heartbeat(agent_name="bid", status="active")

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [heartbeat1, heartbeat2]
        db_session.execute.return_value = result_mock

        result = await controller.status.fn(controller, db_session=db_session)

        assert result.system_health == "healthy"
        assert len(result.agents) == 2
        assert result.agents[0].name == "scout"
        assert result.agents[1].name == "bid"

    @pytest.mark.asyncio
    async def test_dead_threshold_overrides_stored_status(self) -> None:
        """Should mark agent as 'dead' when last_heartbeat exceeds threshold."""
        controller = AgentController(owner=MagicMock())
        db_session = _create_mock_db_session()

        # Last heartbeat > 180s ago
        old_heartbeat = datetime.now(UTC) - _HEARTBEAT_DEAD_THRESHOLD - timedelta(seconds=10)
        heartbeat = _create_mock_heartbeat(agent_name="scout", status="active", last_heartbeat=old_heartbeat)

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [heartbeat]
        db_session.execute.return_value = result_mock

        result = await controller.status.fn(controller, db_session=db_session)

        assert result.agents[0].status == "dead"
        assert result.system_health == "critical"

    @pytest.mark.asyncio
    async def test_empty_agents_returns_critical_health(self) -> None:
        """Should return system_health='critical' when no agents exist."""
        controller = AgentController(owner=MagicMock())
        db_session = _create_mock_db_session()

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        db_session.execute.return_value = result_mock

        result = await controller.status.fn(controller, db_session=db_session)

        assert result.system_health == "critical"
        assert len(result.agents) == 0


# ---------------------------------------------------------------------------
# Tests: logs
# ---------------------------------------------------------------------------


class TestLogs:
    """Tests for the GET /api/v1/agents/{name}/logs endpoint."""

    @pytest.mark.asyncio
    async def test_returns_logs_for_valid_agent(self) -> None:
        """Should return paginated logs for a valid agent."""
        controller = AgentController(owner=MagicMock())
        db_session = _create_mock_db_session()

        heartbeat = _create_mock_heartbeat(agent_name="scout")
        log1 = _create_mock_log(agent_name="scout", event_type="info")
        log2 = _create_mock_log(agent_name="scout", event_type="warning")

        # Mock two execute calls: heartbeat check + count + fetch
        hb_result = MagicMock()
        hb_result.scalar_one_or_none.return_value = heartbeat

        count_result = MagicMock()
        count_result.scalar_one.return_value = 2

        fetch_result = MagicMock()
        fetch_result.scalars.return_value.all.return_value = [log1, log2]

        db_session.execute.side_effect = [hb_result, count_result, fetch_result]

        result = await controller.logs.fn(
            controller,
            name="scout",
            db_session=db_session,
            limit=50,
            level=None,
            since=None,
        )

        assert result.agent == "scout"
        assert result.total == 2
        assert len(result.logs) == 2
        assert result.logs[0].level == "info"
        assert result.logs[1].level == "warning"

    @pytest.mark.asyncio
    async def test_raises_404_for_unknown_agent(self) -> None:
        """Should raise NotFoundException when agent does not exist."""
        controller = AgentController(owner=MagicMock())
        db_session = _create_mock_db_session()

        hb_result = MagicMock()
        hb_result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = hb_result

        with pytest.raises(NotFoundException, match="Agent 'unknown' not found"):
            await controller.logs.fn(
                controller,
                name="unknown",
                db_session=db_session,
                limit=50,
                level=None,
                since=None,
            )

    @pytest.mark.asyncio
    async def test_filters_by_level(self) -> None:
        """Should filter logs by level parameter."""
        controller = AgentController(owner=MagicMock())
        db_session = _create_mock_db_session()

        heartbeat = _create_mock_heartbeat(agent_name="scout")
        error_log = _create_mock_log(agent_name="scout", event_type="error")

        hb_result = MagicMock()
        hb_result.scalar_one_or_none.return_value = heartbeat

        count_result = MagicMock()
        count_result.scalar_one.return_value = 1

        fetch_result = MagicMock()
        fetch_result.scalars.return_value.all.return_value = [error_log]

        db_session.execute.side_effect = [hb_result, count_result, fetch_result]

        result = await controller.logs.fn(
            controller,
            name="scout",
            db_session=db_session,
            limit=50,
            level="error",
            since=None,
        )

        assert result.total == 1
        assert result.logs[0].level == "error"

    @pytest.mark.asyncio
    async def test_filters_by_since_timestamp(self) -> None:
        """Should filter logs by since timestamp parameter."""
        controller = AgentController(owner=MagicMock())
        db_session = _create_mock_db_session()

        heartbeat = _create_mock_heartbeat(agent_name="scout")
        recent_log = _create_mock_log(agent_name="scout")
        recent_log.created_at = datetime.now(UTC)

        since = datetime.now(UTC) - timedelta(hours=1)

        hb_result = MagicMock()
        hb_result.scalar_one_or_none.return_value = heartbeat

        count_result = MagicMock()
        count_result.scalar_one.return_value = 1

        fetch_result = MagicMock()
        fetch_result.scalars.return_value.all.return_value = [recent_log]

        db_session.execute.side_effect = [hb_result, count_result, fetch_result]

        result = await controller.logs.fn(
            controller,
            name="scout",
            db_session=db_session,
            limit=50,
            level=None,
            since=since,
        )

        assert result.total == 1

    @pytest.mark.asyncio
    async def test_returns_total_count(self) -> None:
        """Should return total count of matching logs."""
        controller = AgentController(owner=MagicMock())
        db_session = _create_mock_db_session()

        heartbeat = _create_mock_heartbeat(agent_name="scout")
        log = _create_mock_log(agent_name="scout")

        hb_result = MagicMock()
        hb_result.scalar_one_or_none.return_value = heartbeat

        count_result = MagicMock()
        count_result.scalar_one.return_value = 42

        fetch_result = MagicMock()
        fetch_result.scalars.return_value.all.return_value = [log]

        db_session.execute.side_effect = [hb_result, count_result, fetch_result]

        result = await controller.logs.fn(
            controller,
            name="scout",
            db_session=db_session,
            limit=1,
            level=None,
            since=None,
        )

        assert result.total == 42


# ---------------------------------------------------------------------------
# Tests: restart
# ---------------------------------------------------------------------------


class TestRestart:
    """Tests for the POST /api/v1/agents/{name}/restart endpoint."""

    @pytest.mark.asyncio
    async def test_increments_restart_count_and_publishes_command(self) -> None:
        """Should increment restart_count, set idle status, and publish restart command."""
        controller = AgentController(owner=MagicMock())
        db_session = _create_mock_db_session()
        valkey = _create_mock_valkey()
        request = _create_mock_request()

        heartbeat = _create_mock_heartbeat(agent_name="scout", status="error")
        heartbeat.restart_count = 2

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = heartbeat
        db_session.execute.return_value = result_mock

        result = await controller.restart.fn(
            controller,
            name="scout",
            db_session=db_session,
            valkey=valkey,
            request=request,
        )

        assert result.agent == "scout"
        assert result.action == "restart"
        assert result.status == "ok"
        assert heartbeat.restart_count == 3
        assert heartbeat.status == "idle"
        assert heartbeat.error_message is None
        assert heartbeat.current_task is None

        db_session.flush.assert_awaited_once()
        valkey.publish.assert_awaited_once_with("agent:command:scout", "restart")

    @pytest.mark.asyncio
    async def test_raises_404_for_unknown_agent(self) -> None:
        """Should raise NotFoundException when agent does not exist."""
        controller = AgentController(owner=MagicMock())
        db_session = _create_mock_db_session()
        valkey = _create_mock_valkey()
        request = _create_mock_request()

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result_mock

        with pytest.raises(NotFoundException, match="Agent 'unknown' not found"):
            await controller.restart.fn(
                controller,
                name="unknown",
                db_session=db_session,
                valkey=valkey,
                request=request,
            )


# ---------------------------------------------------------------------------
# Tests: pause
# ---------------------------------------------------------------------------


class TestPause:
    """Tests for the POST /api/v1/agents/{name}/pause endpoint."""

    @pytest.mark.asyncio
    async def test_sets_status_to_paused_and_publishes_command(self) -> None:
        """Should set status to 'paused' and publish pause command."""
        controller = AgentController(owner=MagicMock())
        db_session = _create_mock_db_session()
        valkey = _create_mock_valkey()
        request = _create_mock_request()

        heartbeat = _create_mock_heartbeat(agent_name="scout", status="active")
        heartbeat.current_task = "scanning jobs"

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = heartbeat
        db_session.execute.return_value = result_mock

        result = await controller.pause.fn(
            controller,
            name="scout",
            db_session=db_session,
            valkey=valkey,
            request=request,
        )

        assert result.agent == "scout"
        assert result.action == "pause"
        assert result.status == "ok"
        assert heartbeat.status == "paused"
        assert heartbeat.current_task is None

        db_session.flush.assert_awaited_once()
        valkey.publish.assert_awaited_once_with("agent:command:scout", "pause")

    @pytest.mark.asyncio
    async def test_raises_404_for_unknown_agent(self) -> None:
        """Should raise NotFoundException when agent does not exist."""
        controller = AgentController(owner=MagicMock())
        db_session = _create_mock_db_session()
        valkey = _create_mock_valkey()
        request = _create_mock_request()

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result_mock

        with pytest.raises(NotFoundException, match="Agent 'unknown' not found"):
            await controller.pause.fn(
                controller,
                name="unknown",
                db_session=db_session,
                valkey=valkey,
                request=request,
            )


# ---------------------------------------------------------------------------
# Tests: resume
# ---------------------------------------------------------------------------


class TestResume:
    """Tests for the POST /api/v1/agents/{name}/resume endpoint."""

    @pytest.mark.asyncio
    async def test_sets_status_to_idle_and_publishes_command(self) -> None:
        """Should set status to 'idle' and publish resume command."""
        controller = AgentController(owner=MagicMock())
        db_session = _create_mock_db_session()
        valkey = _create_mock_valkey()
        request = _create_mock_request()

        heartbeat = _create_mock_heartbeat(agent_name="scout", status="paused")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = heartbeat
        db_session.execute.return_value = result_mock

        result = await controller.resume.fn(
            controller,
            name="scout",
            db_session=db_session,
            valkey=valkey,
            request=request,
        )

        assert result.agent == "scout"
        assert result.action == "resume"
        assert result.status == "ok"
        assert heartbeat.status == "idle"

        db_session.flush.assert_awaited_once()
        valkey.publish.assert_awaited_once_with("agent:command:scout", "resume")

    @pytest.mark.asyncio
    async def test_raises_404_for_unknown_agent(self) -> None:
        """Should raise NotFoundException when agent does not exist."""
        controller = AgentController(owner=MagicMock())
        db_session = _create_mock_db_session()
        valkey = _create_mock_valkey()
        request = _create_mock_request()

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result_mock

        with pytest.raises(NotFoundException, match="Agent 'unknown' not found"):
            await controller.resume.fn(
                controller,
                name="unknown",
                db_session=db_session,
                valkey=valkey,
                request=request,
            )


# ---------------------------------------------------------------------------
# Tests: _get_heartbeat_or_404
# ---------------------------------------------------------------------------


class TestGetHeartbeatOr404:
    """Tests for the _get_heartbeat_or_404 helper function."""

    @pytest.mark.asyncio
    async def test_returns_heartbeat_when_found(self) -> None:
        """Should return AgentHeartbeat when agent exists."""
        db_session = _create_mock_db_session()
        heartbeat = _create_mock_heartbeat(agent_name="scout")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = heartbeat
        db_session.execute.return_value = result_mock

        result = await _get_heartbeat_or_404("scout", db_session)

        assert result == heartbeat

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self) -> None:
        """Should raise NotFoundException when agent does not exist."""
        db_session = _create_mock_db_session()

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result_mock

        with pytest.raises(NotFoundException, match="Agent 'unknown' not found"):
            await _get_heartbeat_or_404("unknown", db_session)
